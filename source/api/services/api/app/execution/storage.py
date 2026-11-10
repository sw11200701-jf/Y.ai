"""JSON state ACL, fenced leases and durable per-step intent/result ledger."""
import hashlib
import json
import time
import asyncio
from pathlib import Path
from copy import deepcopy
from sqlalchemy import select, update, or_
from app.db import SessionLocal, Resource, utc_now
from app.auth_models import RunAuthorization, User, AuthSession
from app.auth import context_for, validate_context
from app.errors import AppError
from app.execution.models import ExecutionRun, ExecutionBlob, ExecutionStep
from app.execution.contracts import NodeExecution
from app.execution.effects import current_effect, EffectContext
from app.settings import settings

SECRET_KEYS = {"password", "initialpassword", "passwordhash", "apikey", "accesstoken", "refreshtoken", "authorization", "cookie", "cookies", "csrf", "clientsecret", "credential", "credentials", "secretref", "credentialref"}


def encode(value):
    def inspect(item, depth=0):
        if depth > 64: raise AppError("RESOURCE_LIMIT", "执行状态嵌套过深", 422)
        if type(item) is dict:
            for key, child in item.items():
                if not isinstance(key, str): raise AppError("INVALID_EXECUTION_STATE", "执行状态键必须是文本", 422)
                if "".join(c.lower() for c in key if c.isalnum()) in SECRET_KEYS:
                    raise AppError("EXECUTION_SECRET_REJECTED", "执行状态禁止保存凭据字段", 422)
                inspect(child, depth+1)
        elif type(item) is list:
            for child in item: inspect(child, depth+1)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise AppError("INVALID_EXECUTION_STATE", "执行状态只允许JSON数据", 422)
    inspect(value)
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(body.encode()) > 8388608: raise AppError("RESOURCE_LIMIT", "执行状态超过字节限额", 422)
    return body


def engine_identity(workflow):
    from importlib.metadata import version
    digest = hashlib.sha256(encode(workflow).encode()).hexdigest()
    root = Path(__file__).resolve().parents[1]
    paths = [*(root/"execution").glob("*.py"), *(root/"runner"/name for name in ("runtime.py", "graph.py", "variables.py", "registry.py")), root/"agents/langgraph_runtime.py", root/"agents/langchain_adapter.py", root/"agents/model_requirements.py", root/"agents/builtin_chunker.py", root/"agents/document_overview.py", *(root/"knowledge").glob("*.py"),*(root/"knowledge"/"slicing_assets").glob("*.json"),*(root/"knowledge"/"slicing_assets").glob("*.ps1"),root/'agents/document_bindings.py',*(root/'reporting'/name for name in ('document_specs.py','document_renderers.py','document_tools.py','document_artifacts.py','document_nodes.py','completed_documents.py','assets/document-base-v1.pptx')), root/"agents/skill_bindings.py", root/"plugins/skill_service.py", root/"plugins/skill_packages.py", root/'agents/model_adapter.py', root/'agents/reasoning.py', root/'agents/reasoning_profiles.py', root/'agents/model_sse.py', root/'agents/vision.py', root/'agents/nodes.py', root/'agents/messages.py', root/'model_catalog/schemas.py', root/'attachments.py',root/'runner/fields.py',root/'runner/input_defaults.py',root/'user_variables.py']
    build = hashlib.sha256()
    for path in sorted(paths):
        build.update(path.relative_to(root).as_posix().encode());build.update(path.read_bytes())
    return {"name": "langgraph", "version": "1", "compiler_version": "1", "compiler_sha256": build.hexdigest(), "langgraph_version": version("langgraph"), "langchain_version": version("langchain"), "langchain_core_version": version("langchain-core"), "checkpoint_version": version("langgraph-checkpoint"), "sqlite_saver_version": version("langgraph-checkpoint-sqlite"), "dsl_sha256": digest}


def engine_key(engine):
    return ":".join(engine[k] for k in ("name", "version", "compiler_version", "langgraph_version", "compiler_sha256"))


def fresh_context(session, run_id):
    from app.platform_api import fresh_run_context as fresh_platform_run_context
    platform=fresh_platform_run_context(session,run_id)
    if platform is not None:return platform
    from app.machine_auth import fresh_run_context
    machine=fresh_run_context(session,run_id) if settings.application_api_enabled else None
    if machine is not None:return machine
    auth = session.get(RunAuthorization, run_id, populate_existing=True)
    if auth is None: raise AppError("NOT_FOUND", "运行授权不存在", 404)
    user = session.get(User, auth.user_id, populate_existing=True)
    login = session.get(AuthSession, auth.session_id, populate_existing=True)
    if user is None or login is None: raise AppError("SESSION_REVOKED", "执行用户需重新登录", 401)
    context = context_for(user, login, session)
    from app.rbac import require_any_permission, RUN_PERMISSIONS
    require_any_permission(context, session, RUN_PERMISSIONS)
    return context


def authorize(session, run_id, context=None, *, execute=True):
    fresh = context if context is not None else fresh_context(session, run_id)
    if context is not None: validate_context(context, session)
    binding = session.get(ExecutionRun, run_id, populate_existing=True)
    if binding is None or (binding.principal_id, binding.tenant_id, binding.workspace_id) != (fresh.operator_id, fresh.tenant_id, fresh.workspace_id):
        raise AppError("NOT_FOUND", "执行状态不可访问", 404)
    if context is not None:
        validate_context(context, session)
        if (context.operator_id, context.tenant_id, context.workspace_id) != (binding.principal_id, binding.tenant_id, binding.workspace_id):
            raise AppError("NOT_FOUND", "执行状态不可访问", 404)
    from app.db import get_resource
    run = get_resource(session, "run", run_id, fresh)
    if run.get("engine") != engine_identity(run["workflow_snapshot"]) or binding.engine_key != engine_key(run["engine"]) or binding.dsl_hash != run["engine"]["dsl_sha256"]:
        raise AppError("ENGINE_MISMATCH", "执行引擎或流程快照已变化，请新建运行", 409)
    if getattr(fresh,"principal_type","human")=="service":
        from app.machine_auth import assert_run_partition
        assert_run_partition(session,run_id,fresh)
    authorize_dependencies(session, fresh, run, execute=execute)
    return fresh, binding, run


def authorize_dependencies(session, context, run, *, models=False, execute=False):
    """The selected execution resources also guard every cached read surface."""
    if getattr(context,'principal_type','human')=='service':
        from app.machine_auth import assert_release_dependencies
        assert_release_dependencies(session,context,execute=execute)
    from app.resource_policy import require_execute
    if execute:require_execute(session,context)
    from app.db import get_resource
    from app.contracts import ToolBinding
    from app.reporting.document_artifacts import authorize_run_outputs
    authorize_run_outputs(session,context,run)
    from app.agents.skill_bindings import resolve_guidance
    for config in run.get('agent_snapshots',{}).values():
        resolve_guidance(config,context=context,session=session)
    selected = []
    request = run.get("request", {})
    for source in run.get('source_resource_refs',[]):
        get_resource(session,source['kind'],source['resource_id'],context,include_archived=not execute)
        if execute:require_execute(session,context,source['kind'],source['resource_id'])
    if execute and request.get('workflow_id'):require_execute(session,context,'workflow',request['workflow_id'])
    if execute and (request.get('agent_id') or request.get('draft_agent_id')):require_execute(session,context,'agent',request.get('agent_id') or request['draft_agent_id'])
    if context.principal_type=='platform':
        from app.platform_publications import authorize_run_publication
        authorize_run_publication(session,context,run,execute=execute)
    target = (request.get("node_test") or {}).get("node_id")
    def walk(nodes):
        for node in nodes:
            config = node.get("config", {})
            if node.get("type") in {"plugin", "http"}:
                if execute:require_execute(session,context,'plugin_version',config['binding']['plugin_version_id'])
                selected.append(config["binding"])
            if node.get("type") == "agent":
                if execute:require_execute(session,context,"agent",config["agent_id"])
                get_resource(session, "agent", config["agent_id"], context, include_archived=not execute)
                if config.get("agent_version_id"): get_resource(session, "agent_version", config["agent_version_id"], context)
            if node.get("type") in {"iteration", "loop"}: walk(config["body"]["nodes"])
    snapshot=run.get("workflow_snapshot", {})
    if target:
        from app.execution.drafts import selected_snapshot
        snapshot=selected_snapshot(snapshot,(request.get("node_test") or {}).get("scope_path",[]))
    nodes = snapshot.get("nodes", [])
    walk([node for node in nodes if node["id"] == target] if target else nodes)
    for field in ("agent_id", "draft_agent_id"):
        if request.get(field): get_resource(session, "agent", request[field], context, include_archived=not execute)
    if request.get("workflow_id"): get_resource(session, "workflow", request["workflow_id"], context, include_archived=not execute)
    for config in run.get("agent_snapshots", {}).values(): selected.extend(config.get("bindings", []))
    for raw in selected:
        tool = ToolBinding.model_validate(raw)
        if execute:require_execute(session,context,'plugin_version',tool.plugin_version_id)
        version = get_resource(session, "plugin_version", tool.plugin_version_id, context)
        if version.get("status", "published") != "published" or not any(item["id"]==tool.tool_id for item in version.get("tools", [])):
            raise AppError("INVALID_BINDING", "已固定的工具版本已停用或不可访问", 409)
        if tool.connection_id:
            connection = get_resource(session, "connection", tool.connection_id, context)
            if not connection.get("enabled", True) or connection.get("status") in {"disabled", "deleted"}: raise AppError("INVALID_BINDING", "已固定的连接已停用", 409)
    if models:
        from app.model_catalog.service import resolve_runtime_model
        for identifier in run.get("model_bindings", {}):
            resolve_runtime_model(identifier, context=context, session=session)
    if run.get('semantic_job'):
        from app.execution.semantic_jobs import load_plan
        load_plan(session,context,run,completed_read=run.get('status')=='succeeded',
            historical_metadata=run.get('status') in {'failed','cancelled','outcome_unknown'})


def put_blob(session, run_id, state):
    body = encode(state)
    ref = hashlib.sha256((run_id+":"+body).encode()).hexdigest()
    if session.get(ExecutionBlob, ref) is None:
        session.add(ExecutionBlob(id=ref, run_id=run_id, payload=body, created_at=utc_now()))
        session.flush()
    binding = session.get(ExecutionRun, run_id)
    binding.state_ref = ref
    if binding.initial_ref is None: binding.initial_ref = ref
    return {"state_ref": ref}


def read_blob(session, run_id, state):
    row = session.get(ExecutionBlob, state.get("state_ref"))
    if row is None or row.run_id != run_id: raise AppError("NOT_FOUND", "检查点状态不可访问", 404)
    return json.loads(row.payload)


LEASE_SECONDS = 20


def check_active_run(session, run_id):
    # Read a scalar column, bypassing a Session's cached Resource object. A
    # cancellation committed by another worker during network I/O must fence
    # the result before a stale in-memory run payload can overwrite it.
    with session.no_autoflush:
        body = session.scalar(select(Resource.payload).where(Resource.kind=="run", Resource.resource_id==run_id))
    if body is None or json.loads(body)["status"] not in {"running", "resuming"}:
        raise AppError("RUN_NOT_ACTIVE", "运行已暂停或终止", 409)


def claim(run_id, owner):
    from app.execution.retained_intents import deny_execution
    deny_execution(run_id)
    with SessionLocal() as session:
        result = session.execute(update(ExecutionRun).where(ExecutionRun.run_id==run_id, or_(ExecutionRun.lease_until < time.time(), ExecutionRun.lease_owner==owner)).values(lease_owner=owner, lease_until=time.time()+LEASE_SECONDS, generation=ExecutionRun.generation+1))
        session.commit()
        return result.rowcount == 1


def heartbeat(run_id, owner):
    with SessionLocal() as session:
        result = session.execute(update(ExecutionRun).where(ExecutionRun.run_id==run_id, ExecutionRun.lease_owner==owner, ExecutionRun.lease_until>=time.time()).values(lease_until=time.time()+LEASE_SECONDS))
        session.commit()
        return result.rowcount == 1


def release(run_id, owner):
    with SessionLocal() as session:
        session.execute(update(ExecutionRun).where(ExecutionRun.run_id==run_id, ExecutionRun.lease_owner==owner).values(lease_owner=None, lease_until=0))
        session.commit()


COMPLETION_KEY='__system__:workflow_completion:v1'


def product_completed_state_ref(session,context,run):
    binding=session.get(ExecutionRun,run['id'],populate_existing=True)
    identifier=hashlib.sha256((run['id']+':'+binding.dsl_hash+':'+COMPLETION_KEY).encode()).hexdigest()
    row=session.get(ExecutionStep,identifier)
    if row is None:return None
    if (row.run_id!=run['id'] or row.node_key!=COMPLETION_KEY or row.status!='succeeded'
            or row.side_effect or row.result_ref!=row.input_ref or not row.completed_at):
        raise AppError('EXECUTION_COMPLETION_INVALID','流程最终提交凭据无效',409)
    read_blob(session,run['id'],{'state_ref':row.result_ref})
    return {'state_ref':row.result_ref}


def commit_product_completion(bridge,state):
    """An internal completion witness, not a charged business/model step."""
    with SessionLocal() as session:
        context,binding,run=bridge._check(session)
        snapshot=read_blob(session,run['id'],state)
        now=time.time()
        if now>snapshot['budget']['deadline_epoch']:
            raise AppError('RUN_TIMEOUT','流程已超过原始截止时间',504)
        outstanding=session.scalar(select(ExecutionStep.execution_id).where(
            ExecutionStep.run_id==run['id'],ExecutionStep.side_effect==True,ExecutionStep.status=='issued').limit(1))
        if outstanding:raise AppError('OUTCOME_UNKNOWN','仍有未知副作用，不能标记完成',409)
        previous=product_completed_state_ref(session,context,run)
        if previous:
            if previous['state_ref']!=state['state_ref']:raise AppError('STEP_INPUT_CHANGED','流程最终输入与提交凭据不符',409)
            return previous
        fenced=session.execute(update(ExecutionRun).where(ExecutionRun.run_id==run['id'],
            ExecutionRun.lease_owner==bridge.lease_owner,ExecutionRun.lease_until>=now).values(lease_until=now+LEASE_SECONDS))
        if fenced.rowcount!=1:raise AppError('LEASE_LOST','最终提交租约已失效',409)
        identifier=hashlib.sha256((run['id']+':'+binding.dsl_hash+':'+COMPLETION_KEY).encode()).hexdigest()
        session.add(ExecutionStep(execution_id=identifier,run_id=run['id'],node_key=COMPLETION_KEY,
            input_ref=state['state_ref'],result_ref=state['state_ref'],side_effect=False,status='succeeded',
            created_at=utc_now(),completed_at=utc_now()))
        session.commit()
        return {'state_ref':state['state_ref']}


class DurableBridge:
    def __init__(self, run_id, lease_owner, max_concurrency=4):
        self.run_id, self.lease_owner = run_id, lease_owner
        self.operation_limit = asyncio.Semaphore(max_concurrency)

    def _check(self, session):
        context, binding, run = authorize(session, self.run_id)
        if binding.lease_owner != self.lease_owner or binding.lease_until < time.time():
            raise AppError("LEASE_LOST", "运行执行租约已失效", 409)
        check_active_run(session, self.run_id)
        return context, binding, run

    def read(self, state):
        with SessionLocal() as session:
            self._check(session)
            return read_blob(session, self.run_id, state)

    async def step(self, state, *, node_key, operation, side_effect=False, preflight=None, tool_call=False):
        async with self.operation_limit:
            return await self._step(state, node_key=node_key, operation=operation, side_effect=side_effect, preflight=preflight, tool_call=tool_call)

    async def _step(self, state, *, node_key, operation, side_effect=False, preflight=None, tool_call=False):
        from app.execution.retained_intents import deny_execution
        deny_execution(self.run_id)
        from app.runner.service import emit, invoke
        with SessionLocal() as session:
            context, binding, run = self._check(session)
            snapshot = read_blob(session, self.run_id, state)
            budget = snapshot["budget"]
            if time.time() > budget["deadline_epoch"]: raise AppError("RUN_TIMEOUT", "运行已超过持久化截止时间", 504)
            # node_key must include the product scope + iteration path. Agent nodes
            # may repeat internally; include their durable round in the supplied key.
            for static, dynamic in sorted(snapshot.get("dynamic_scopes", {}).items(), key=lambda pair: -len(pair[0])):
                if node_key.startswith(static+":"):
                    node_key = dynamic+node_key[len(static):]
                    break
            deadline = budget["deadline_epoch"]
            for local in snapshot.get("frames", {}).values():
                for repeat_id, repeat in local["repeats"].items():
                    if node_key.startswith(local["prefix"]+repeat_id+"["):
                        deadline = min(deadline, repeat["deadline"])
            if time.time() >= deadline: raise AppError("RUN_TIMEOUT", "执行已超过全局或循环截止时间", 504)
            execution_id = hashlib.sha256((self.run_id+":"+binding.dsl_hash+":"+node_key).encode()).hexdigest()
            previous = session.get(ExecutionStep, execution_id)
            if previous is not None:
                if previous.input_ref != state["state_ref"]:
                    raise AppError("STEP_INPUT_CHANGED", "重放输入与持久执行记录不一致", 409)
                if previous.status == "succeeded": return {"state_ref": previous.result_ref}
                if previous.status == "failed" and previous.side_effect:
                    failed = session.get(ExecutionBlob, previous.result_ref) if previous.result_ref else None
                    if failed is not None and failed.run_id == self.run_id:
                        saved = json.loads(failed.payload)
                        if (saved.get("kind") in ("observed_model_failure", "proven_model_connection_failure") and saved.get("execution_id") == execution_id
                                and saved.get("replay_allowed") is False):
                            error = saved["error"]
                            # A crash after failure-ledger commit but before run
                            # status commit must expose the recorded failure,
                            # never repeat the model request.
                            raise AppError(error["code"], error["message"], saved.get("http_status", 422), error["details"])
                    raise AppError("EFFECT_NOT_REPLAYABLE", "该模型调用已明确失败，禁止重放，请修正后新建运行", 409,
                                   {"response_received": True, "effect_issued": True, "automatic_retry": False})
                if previous.side_effect:
                    raise AppError("OUTCOME_UNKNOWN", "上次副作用调用结果未知，禁止自动重试", 409, {"execution_id": execution_id, "node_key": node_key})
            if binding.steps >= budget["max_steps"]: raise AppError("RESOURCE_LIMIT", "超过全局执行步数限额", 422)
            from app.execution.message_inputs import scope_for_run, bind_message_attachments
            if "scoped_bindings" in run:
                context = context.model_copy(update={"allowed_bindings": run["scoped_bindings"]})
            issuing = False
            async def guarded_invoke(module, function, **kwargs):
                if not issuing:
                    raise AppError("EXECUTION_PREFLIGHT_ONLY", "前置检查不能调用业务处理器", 409)
                self._check(session)
                kwargs.update(context=context, session=session)
                session.commit()  # no inherited writer survives an awaited service call
                result = await invoke(module, function, **kwargs)
                self._check(session)
                return result
            execution = NodeExecution(context=context, session=session, run_id=self.run_id, node_key=node_key,
                                      execution_id=execution_id, state=deepcopy(snapshot), invoke=guarded_invoke,
                                      agent_scope=run.get("agent_data_scope"), knowledge_scope=run.get("agent_knowledge_scope"),
                                      deadline=budget["deadline"], budget=deepcopy(budget),
                                      message_attachments=scope_for_run(run, context))
            from app.execution.drafts import readonly_pool
            for local in execution.state.get("frames", {}).values():
                local["pool"].update(readonly_pool(context, self.run_id))
            # This callback is a trusted read-only admission check, not a catch
            # around the operation. Once issued, ANY failure remains conservative.
            if preflight is not None:
                preflight_effect = current_effect.set(None)
                try:
                    with bind_message_attachments(execution.message_attachments):
                        async with asyncio.timeout(max(0, deadline-time.time())):
                            await preflight(execution)
                    if session.new or session.dirty or session.deleted:
                        raise AppError("EXECUTION_PREFLIGHT_ONLY", "前置检查不能修改持久数据", 409)
                    self._check(session)
                    if time.time() >= deadline:
                        raise AppError("RUN_TIMEOUT", "前置检查已超过执行截止时间", 504)
                except TimeoutError as exc:
                    raise AppError("RUN_TIMEOUT", "前置检查已超过执行截止时间", 504,
                                   {"phase": "preflight", "effect_issued": False, "http_status": 504}) from exc
                except AppError as exc:
                    raise AppError(exc.code, exc.message, exc.status_code,
                                   {"phase": "preflight", "effect_issued": False, "http_status": exc.status_code, "cause": exc.details}) from exc
                finally:
                    current_effect.reset(preflight_effect)
            if execution.model_context_reservation is not None:
                from app.execution.model_reservations import reserve_context
                reserve_context(session,execution,lease_owner=self.lease_owner,lease_seconds=LEASE_SECONDS)
            if previous is None:
                session.add(ExecutionStep(execution_id=execution_id, run_id=self.run_id, node_key=node_key, input_ref=state["state_ref"], side_effect=side_effect, status="issued", created_at=utc_now()))
            binding.steps += 1
            if ":model:" in node_key or execution.model_context_reservation is not None: binding.model_calls += 1
            if tool_call or ":tool:" in node_key: binding.tool_calls += 1
            budget["steps"] = binding.steps
            budget.update(model_calls=binding.model_calls, tool_calls=binding.tool_calls)
            emit(session, self.run_id, context, "execution.step_started", node_key, "running", {"execution_id": execution_id})
            session.commit()  # durable intent BEFORE any external/file mutation
            context, binding, run = self._check(session)
            issuing = True
            execution.budget = deepcopy(budget)
            effect = EffectContext(execution_id, self.run_id, node_key, context.operator_id, context.tenant_id, context.workspace_id)
            token = current_effect.set(effect)
            try:
                from app.knowledge.execution_scope import bind_execution
                from app.execution.model_network import bind_model_network
                with bind_execution(execution), bind_message_attachments(execution.message_attachments), bind_model_network(execution, effect):
                    async with asyncio.timeout(max(0, deadline-time.time())):
                        result = await operation(execution)
                self._check(session)  # cancellation/revocation fences result commit
                if not isinstance(result, dict): raise AppError("INVALID_EXECUTION_STATE", "执行节点必须返回JSON状态", 422)
                result["budget"] = snapshot["budget"]  # child cannot reset limits
                fenced = session.execute(update(ExecutionRun).where(ExecutionRun.run_id==self.run_id, ExecutionRun.lease_owner==self.lease_owner, ExecutionRun.lease_until>=time.time()).values(lease_until=time.time()+LEASE_SECONDS))
                if fenced.rowcount != 1: raise AppError("LEASE_LOST", "执行结果提交租约已失效", 409)
                # Bind actual Agent-produced sources before exposing cached messages
                # to a later model/tool node. Existing run guards enforce revocation.
                from app.data_access import record_derivation
                sources = [source for slot in result.get("agents", {}).values() for source in slot.get("data_sources", [])]
                if sources:
                    record_derivation(session=session, context=context, kind="run", resource_id=self.run_id, sources=sources)
                    current = session.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==self.run_id, Resource.owner_id==(context.actor_id if context.principal_type=="platform" else context.operator_id)))
                    payload = json.loads(current.payload)
                    existing = payload.setdefault("data_sources", [])
                    for source in sources:
                        if source not in existing: existing.append(source)
                    current.payload = json.dumps(payload, ensure_ascii=False)
                output = put_blob(session, self.run_id, result)
                record = session.get(ExecutionStep, execution_id)
                record.status, record.result_ref, record.completed_at = "succeeded", output["state_ref"], utc_now()
                emit(session, self.run_id, context, "execution.step_completed", node_key, "succeeded", {"execution_id": execution_id})
                session.commit()
                return output
            except Exception as exc:
                from app.execution.effects import is_observed_model_failure
                from app.execution.model_network import is_proven_model_connection_failure
                connection_failed = is_proven_model_connection_failure(exc, effect)
                if side_effect and (connection_failed or is_observed_model_failure(exc, effect)):
                    # Only fenced bookkeeping for this exact issued failure may commit.
                    session.rollback()
                    now = time.time()
                    fenced = session.execute(update(ExecutionRun).where(
                        ExecutionRun.run_id == self.run_id, ExecutionRun.lease_owner == self.lease_owner,
                        ExecutionRun.lease_until >= now).values(lease_until=now + LEASE_SECONDS))
                    if fenced.rowcount == 1:
                        kind = "proven_model_connection_failure" if connection_failed else "observed_model_failure"
                        failure = {"kind": kind, "execution_id": execution_id,
                                   "error": exc.as_dict(), "http_status": exc.status_code, "replay_allowed": False}
                        body = encode(failure)
                        ref = hashlib.sha256((self.run_id + ":observed-failure:" + body).encode()).hexdigest()
                        if session.get(ExecutionBlob, ref) is None:
                            session.add(ExecutionBlob(id=ref, run_id=self.run_id, payload=body, created_at=utc_now()))
                            session.flush()
                        changed = session.execute(update(ExecutionStep).where(
                            ExecutionStep.execution_id == execution_id, ExecutionStep.run_id == self.run_id,
                            ExecutionStep.status == "issued", ExecutionStep.side_effect == True)
                            .values(status="failed", result_ref=ref, completed_at=utc_now()))
                        if changed.rowcount == 1:
                            emit(session, self.run_id, context, "execution.step_failed", node_key, "failed",
                                 {"execution_id": execution_id, "error": exc.as_dict()})
                            session.commit()
                        else:
                            session.rollback()
                    else:
                        session.rollback()
                raise
            finally:
                current_effect.reset(token)
