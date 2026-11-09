"""Durable designated approvals; decisions never impersonate the executor."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from sqlalchemy import select
from app.auth_models import User
from app.db import get_resource, save_resource, utc_now
from app.errors import AppError
from app.execution.models import ExecutionApproval, ExecutionRun
from app.execution.contracts import ExecutionContext
from app.execution.compiler import enabled, edge_key, finish_product


def approval_node(compiler, workflow, node, scope):
    async def execute(state, runtime: Runtime[ExecutionContext]):
        key = compiler.key(state, scope, node.id)
        async def request(execution):
            from app.runner.service import emit
            snapshot = execution.state
            local = snapshot["frames"][scope]
            run = get_resource(execution.session, "run", execution.run_id, execution.context)
            if not enabled(workflow, node, local, run["message"]):
                for edge in workflow.edges:
                    if edge.source == node.id: local["edges"][edge_key(edge)] = False
                snapshot.setdefault("approval_nodes", {})[key] = None
                emit(execution.session, execution.run_id, execution.context, "node.skipped", key, "skipped", {})
                return snapshot
            approvers = node.config["approver_ids"]
            for user_id in approvers:
                user = execution.session.get(User, user_id)
                if user is None or not user.active or (user.tenant_id, user.workspace_id) != (execution.context.tenant_id, execution.context.workspace_id):
                    raise AppError("APPROVER_UNAVAILABLE", "审批人不在当前空间或已停用", 422)
                if user_id == execution.context.operator_id and not node.config.get("allow_self", False):
                    raise AppError("SELF_APPROVAL_DENIED", "此审批不允许执行者本人审批", 422)
            approval_id = "approval_"+hashlib.sha256(execution.execution_id.encode()).hexdigest()[:32]
            row = execution.session.get(ExecutionApproval, approval_id)
            if row is None:
                row = ExecutionApproval(id=approval_id, run_id=execution.run_id, execution_id=execution.execution_id, node_key=key, title=node.config["title"], approver_ids=json.dumps(approvers), allow_self=node.config.get("allow_self", False), status="pending", expires_at=(datetime.now(timezone.utc)+timedelta(seconds=node.config["expires_seconds"])).isoformat())
                execution.session.add(row)
                from app.runner.variables import resolve
                emit(execution.session, execution.run_id, execution.context, "node.started", key, "running", {"node_type": "human_approval", "inputs": resolve(node.inputs, local["pool"]), "execution_id": execution.execution_id})
                emit(execution.session, execution.run_id, execution.context, "approval.requested", key, "waiting_approval", {"approval_id": approval_id, "title": row.title, "expires_at": row.expires_at})
            snapshot.setdefault("approval_nodes", {})[key] = approval_id
            return snapshot
        requested = await runtime.context.bridge.step(state, node_key=key+":approval_request", operation=request)
        approval_id = runtime.context.bridge.read(requested)["approval_nodes"][key]
        if approval_id is None: return requested
        # On resume LangGraph re-enters this node; the request ledger replays and
        # this exact interrupt consumes the server-generated Command decision.
        decision = interrupt({"type": "approval", "approval_id": approval_id, "title": node.config["title"]})
        async def complete(execution):
            row = execution.session.get(ExecutionApproval, approval_id, populate_existing=True)
            validate_approved(execution.session, row)
            if not isinstance(decision, dict) or decision.get("approval_id") != approval_id:
                raise AppError("APPROVAL_MISMATCH", "审批恢复记录不匹配", 409)
            row.consumed_at = utc_now()
            local = execution.state["frames"][scope]
            run = get_resource(execution.session, "run", execution.run_id, execution.context)
            finish_product(execution, workflow, node, local, run, {"approved": True, "approval_id": approval_id})
            return execution.state
        return await runtime.context.bridge.step(requested, node_key=key+":approval_complete", operation=complete)
    return execute


def validate_approved(session, row):
    from app.rbac import user_policy
    if row is None or row.status != "approved": raise AppError("APPROVAL_REQUIRED", "运行尚未获得批准", 409)
    if datetime.fromisoformat(row.expires_at) <= datetime.now(timezone.utc): raise AppError("APPROVAL_EXPIRED", "审批已过期，请新建运行", 409)
    approver = session.get(User, row.decided_by, populate_existing=True)
    binding = session.get(ExecutionRun, row.run_id)
    if approver is None or not approver.active or (approver.tenant_id, approver.workspace_id) != (binding.tenant_id, binding.workspace_id) or approver.id not in json.loads(row.approver_ids) or "runs.approve" not in user_policy(approver, session)[1]:
        raise AppError("APPROVAL_AUTHORITY_REVOKED", "审批人的当前权限已失效", 403)


def public_approval(row):
    return {"id": row.id, "run_id": row.run_id, "node_id": row.node_key, "title": row.title, "status": row.status, "expires_at": row.expires_at, "decided_by": row.decided_by, "decided_at": row.decided_at, "note": row.note, "consumed_at": row.consumed_at}


def expire_waiting(session):
    from app.db import Resource
    from app.runner.service import emit
    from types import SimpleNamespace
    now = datetime.now(timezone.utc)
    for row in session.scalars(select(ExecutionApproval).where(ExecutionApproval.status.in_(["pending", "approved"]), ExecutionApproval.consumed_at.is_(None))):
        if datetime.fromisoformat(row.expires_at) > now: continue
        binding = session.get(ExecutionRun, row.run_id)
        resource = session.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==row.run_id, Resource.owner_id==binding.principal_id))
        if resource is None: continue
        run = json.loads(resource.payload)
        if run["status"] not in {"waiting_approval", "paused"}: continue
        row.status = "expired"
        run.update(status="failed", updated_at=utc_now(), error={"code":"APPROVAL_EXPIRED","message":"审批已过期，请新建运行"})
        resource.payload = json.dumps(run, ensure_ascii=False)
        context = SimpleNamespace(tenant_id=binding.tenant_id, workspace_id=binding.workspace_id)
        emit(session, row.run_id, context, "approval.expired", row.node_key, "failed", {"approval_id":row.id})
        emit(session, row.run_id, context, "run.failed", status="failed", data={"error":run["error"]})


def list_approvals(session, context, run_id):
    from app.auth import validate_context
    validate_context(context, session)
    binding = session.get(ExecutionRun, run_id)
    if binding is None or (binding.tenant_id, binding.workspace_id) != (context.tenant_id, context.workspace_id): raise AppError("NOT_FOUND", "运行不存在", 404)
    rows = list(session.scalars(select(ExecutionApproval).where(ExecutionApproval.run_id==run_id)))
    if binding.principal_id != context.operator_id:
        from app.rbac import require_permission
        require_permission(context, session, "runs.approve")
        rows = [row for row in rows if context.operator_id in json.loads(row.approver_ids)]
        if not rows: raise AppError("NOT_FOUND", "审批不存在", 404)
    return rows


def decide(session, context, run_id, approval_id, decision, note=None):
    from app.execution.retained_intents import deny_execution
    deny_execution(run_id)
    from app.rbac import require_permission, audit
    from app.runner.service import emit
    from app.db import Resource
    require_permission(context, session, "runs.approve")
    rows = list_approvals(session, context, run_id)
    row = next((item for item in rows if item.id==approval_id), None)
    binding = session.get(ExecutionRun, run_id)
    if row is None or context.operator_id not in json.loads(row.approver_ids): raise AppError("NOT_FOUND", "审批不存在", 404)
    if context.operator_id==binding.principal_id and not row.allow_self: raise AppError("SELF_APPROVAL_DENIED", "不能审批自己的运行", 403)
    if row.status != "pending": raise AppError("APPROVAL_ALREADY_DECIDED", "审批已处理，请刷新状态", 409)
    resource = session.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==run_id, Resource.owner_id==binding.principal_id))
    if resource is None or json.loads(resource.payload)["status"] not in {"waiting_approval", "paused"}:
        raise AppError("RUN_NOT_WAITING_APPROVAL", "运行当前不再等待此审批", 409)
    if datetime.fromisoformat(row.expires_at) <= datetime.now(timezone.utc): raise AppError("APPROVAL_EXPIRED", "审批已过期", 409)
    # Conditional update fences concurrent decisions even on different API workers.
    from sqlalchemy import update
    result = session.execute(update(ExecutionApproval).where(ExecutionApproval.id==approval_id, ExecutionApproval.status=="pending").values(status="approved" if decision=="approve" else "rejected", decided_by=context.operator_id, decided_at=utc_now(), note=note))
    if result.rowcount != 1: raise AppError("APPROVAL_ALREADY_DECIDED", "审批已被处理", 409)
    emit(session, run_id, context, "approval.decided", row.node_key, "waiting_approval", {"approval_id": approval_id, "decision": decision})
    audit(session, context, "run.approval_decision", "run", run_id, {"approval_id": approval_id, "decision": decision})
    if decision == "reject":
        resource = session.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==run_id, Resource.owner_id==binding.principal_id))
        run = json.loads(resource.payload)
        run.update(status="failed", updated_at=utc_now(), error={"code": "APPROVAL_REJECTED", "message": "审批已拒绝"})
        resource.payload = json.dumps(run, ensure_ascii=False)
        emit(session, run_id, context, "run.failed", status="failed", data={"error": run["error"]})
    session.commit()
    session.refresh(row)
    return public_approval(row)


def approval_inbox(session, context, status="pending"):
    from app.rbac import require_permission
    from app.db import Resource
    require_permission(context, session, "runs.approve")
    query = select(ExecutionApproval).join(ExecutionRun, ExecutionRun.run_id==ExecutionApproval.run_id).where(ExecutionRun.tenant_id==context.tenant_id, ExecutionRun.workspace_id==context.workspace_id)
    if status != "all": query = query.where(ExecutionApproval.status==status)
    items=[]
    # Workspace-fenced metadata only; never generic run ACL or business outputs.
    for row in session.scalars(query.order_by(ExecutionApproval.expires_at.desc())):
        if context.operator_id not in json.loads(row.approver_ids): continue
        binding=session.get(ExecutionRun,row.run_id)
        resource=session.scalar(select(Resource).where(Resource.kind=="run",Resource.resource_id==row.run_id,Resource.owner_id==binding.principal_id))
        if resource is None: continue
        run_status=json.loads(resource.payload)["status"]
        if status=="pending" and (row.consumed_at is not None or run_status not in {"waiting_approval","paused"} or datetime.fromisoformat(row.expires_at)<=datetime.now(timezone.utc)):
            continue
        can_approve=row.status=="pending" and row.consumed_at is None and run_status in {"waiting_approval","paused"} and datetime.fromisoformat(row.expires_at)>datetime.now(timezone.utc) and (row.allow_self or binding.principal_id!=context.operator_id)
        items.append({**public_approval(row),"can_approve":can_approve,"run_status":run_status})
        if len(items)>=100: break
    return {"items":items,"limit":100}
