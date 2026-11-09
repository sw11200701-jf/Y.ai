"""Safe product API projections and explicit same-actor session rebinding."""
import json
from sqlalchemy import select, update, func
from app.db import Resource, RunEventRecord, get_resource, utc_now
from app.auth_models import RunAuthorization
from app.errors import AppError
from app.execution.models import ExecutionRun, ExecutionApproval, ExecutionStep
from app.execution.contracts import capabilities, TERMINAL_STATUSES
from app.execution.storage import authorize


def project_run(session, context, run):
    from app.execution.trace import authorize_view, clean
    authorize_view(session, context, run["id"])
    binding = session.get(ExecutionRun, run["id"])
    actor = binding is not None and binding.principal_id == context.operator_id and (binding.tenant_id, binding.workspace_id)==(context.tenant_id, context.workspace_id)
    result = dict(run)
    result.pop("agent_snapshots", None)
    result.pop("resume_approval_id", None)
    result.pop("resume_approval_ids", None)
    result.pop("test_pool", None)
    result.pop("agent_data_scope", None)
    result.pop("agent_knowledge_scope", None)
    result.pop("scoped_bindings", None)
    if result.get('semantic_job'):
        result['semantic_job']={'job_id':result['semantic_job']['job_id']}
    result.pop('semantic_finalized',None)
    result["engine"] = run.get("engine", {"name": "legacy"})
    may_approve = False
    rows = list(session.scalars(select(ExecutionApproval).where(ExecutionApproval.run_id==run["id"]))) if binding else []
    if "runs.approve" in context.permissions:
        may_approve = any(row.status=="pending" and context.operator_id in json.loads(row.approver_ids) and (row.allow_self or context.operator_id != binding.principal_id) for row in rows)
    result["capabilities"] = capabilities(run, is_actor=actor, may_approve=may_approve)
    from app.execution.retained_intents import historical_notice
    retained=historical_notice(run['id'])
    if retained:
        result['retention']=retained
        result['capabilities'].update(can_cancel=False,can_resume=False,can_approve=False,can_reconcile=False)
    if run["status"] == "waiting_approval":
        result["capabilities"]["can_resume"] = actor and any(row.status=="approved" for row in rows) and not any(row.status=="pending" for row in rows)
    result["last_sequence"] = run.get("last_sequence", 0)
    return clean(result)


async def resume(scheduler, session, context, run_id):
    from app.execution.retained_intents import deny_execution
    deny_execution(run_id)
    from app.rbac import require_any_permission, RUN_PERMISSIONS, audit
    from app.execution.approvals import validate_approved
    from app.runner.service import emit
    require_any_permission(context, session, RUN_PERMISSIONS)
    async with scheduler.admission_lock:
        _, binding, run = authorize(session, run_id, context)
        if run["status"] == "failed" and session.scalar(select(ExecutionStep.execution_id).where(
                ExecutionStep.run_id == run_id, ExecutionStep.side_effect == True, ExecutionStep.status == "failed").limit(1)):
            raise AppError("EFFECT_NOT_REPLAYABLE", "该模型调用已明确失败，禁止恢复或重放，请修正后新建运行", 409,
                           {"response_received": True, "effect_issued": True, "automatic_retry": False})
        if run["status"] in {"queued", "running", "resuming"} or run["status"] in TERMINAL_STATUSES:
            return project_run(session, context, run)
        if run["status"] == "outcome_unknown": raise AppError("OUTCOME_UNKNOWN", "副作用结果未知，需核对后新建运行，不能自动恢复", 409)
        if run["status"] not in {"paused", "waiting_approval"}: raise AppError("RUN_NOT_RESUMABLE", "当前运行不可恢复", 409)
        approvals = list(session.scalars(select(ExecutionApproval).where(ExecutionApproval.run_id==run_id)))
        pending_approvals = []
        if run["status"] == "waiting_approval":
            rows = sorted([row for row in approvals if row.consumed_at is None], key=lambda row: row.expires_at)
            if not rows: raise AppError("APPROVAL_REQUIRED", "缺少审批记录", 409)
            for row in rows: validate_approved(session, row)
            pending_approvals = rows
        if not scheduler.accepting or scheduler.queue.full(): raise AppError("QUEUE_FULL", "运行队列暂不可用", 429)
        resource = session.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==run_id, Resource.owner_id==context.operator_id))
        original = resource.payload
        run.update(status="resuming", updated_at=utc_now())
        run.pop("error", None)
        if pending_approvals: run["resume_approval_ids"] = [row.id for row in pending_approvals]
        changed = session.execute(update(Resource).where(Resource.id==resource.id, Resource.payload==original).values(payload=json.dumps(run, ensure_ascii=False)))
        if changed.rowcount != 1: raise AppError("RUN_STATE_CHANGED", "运行状态已变化，请刷新", 409)
        if getattr(context,'principal_type','human')=='service':
            from app.machine_auth import require_scope,assert_run_partition
            authority=require_scope(session,context,'runs.resume')
            if not authority.runtime_policy.get('allow_resume'):raise AppError('APP_SCOPE_DENIED','应用发布版未开放恢复',403)
            if not set(authority.scopes)&{'workflow.invoke','chat.invoke'}:raise AppError('APP_SCOPE_DENIED','恢复Key缺少运行能力',403)
            if session.scalar(select(ExecutionStep.execution_id).where(ExecutionStep.run_id==run_id,ExecutionStep.side_effect==True,ExecutionStep.status=='issued').limit(1)):
                raise AppError('OUTCOME_UNKNOWN','存在未确认副作用，禁止重新绑定Key恢复',409)
            auth=assert_run_partition(session,run_id,context)
            auth.executing_key_id=context.auth_key_id
        else:
            auth = session.get(RunAuthorization, run_id)
            previous_session = auth.session_id
            auth.session_id = context.session_id
            audit(session, context, "run.resume_authorized", "run", run_id, {"same_principal": True, "session_rebound": previous_session != context.session_id})
        emit(session, run_id, context, "run.resuming", status="resuming")
        session.commit()
        scheduler.pending.add(run_id)
        scheduler.queue.put_nowait((run_id, context))
        return project_run(session, context, run)
