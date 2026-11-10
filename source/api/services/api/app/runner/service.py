from copy import deepcopy
import asyncio
import importlib
import inspect
import json
from types import SimpleNamespace
from sqlalchemy import select, func
from app.contracts import Run, RunCreate, WorkflowDefinition, ToolBinding, ToolExecutionResult, ResultDataset, Artifact, FinancialFact, BusinessContext
from app.db import SessionLocal, RunEventRecord, Resource, get_resource, list_resources, save_resource, new_id, utc_now
from app.auth_models import RunAuthorization
from app.settings import settings
from app.errors import AppError
from app.runner.validation import validate_workflow

def emit(session, run_id, context, event_type, node_id=None, status=None, data=None):
    from app.execution.models import ExecutionRun
    from sqlalchemy import update
    # A single DB UPDATE reserves a sequence, including API cancellation/approval
    # writers outside the worker. Rollback also rolls back the reservation.
    sequence = session.scalar(update(ExecutionRun).where(ExecutionRun.run_id==run_id).values(event_sequence=ExecutionRun.event_sequence+1).returning(ExecutionRun.event_sequence))
    if sequence is None: sequence = (session.scalar(select(func.max(RunEventRecord.sequence)).where(RunEventRecord.run_id == run_id)) or 0) + 1
    payload = {"run_id": run_id, "sequence": sequence, "type": event_type, "node_id": node_id, "status": status, "data": data or {}, "created_at": utc_now()}
    row = RunEventRecord(run_id=run_id, sequence=sequence, tenant_id=context.tenant_id, workspace_id=context.workspace_id, payload=json.dumps(payload, ensure_ascii=False))
    session.add(row)
    session.flush()
    # The full run snapshot carries the event cursor from the SAME transaction.
    # Reading payload and later MAX(sequence) could otherwise skip a concurrent
    # status transition in the client's snapshot+incremental protocol.
    resource = session.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==run_id, Resource.tenant_id==context.tenant_id, Resource.workspace_id==context.workspace_id))
    if resource is not None:
        snapshot = json.loads(resource.payload)
        snapshot["last_sequence"] = sequence
        resource.payload = json.dumps(snapshot, ensure_ascii=False)
        session.flush()
    if settings.application_api_enabled and status in {'queued','running','resuming','paused','waiting_approval','succeeded','failed','cancelled','interrupted','outcome_unknown'} and event_type.startswith(('run.','effect.','approval.')) and resource is not None:
        from app.application_runtime import settle_run
        snapshot=json.loads(resource.payload)
        # Approval transitions also have a lifecycle status; a completed step
        # never marks the whole run complete in the application ledger.
        snapshot['status']=status
        settle_run(session,run_id,snapshot)
    return {"id": row.id, **payload}

async def invoke(module, function, **kwargs):
    try:
        target = getattr(importlib.import_module(module), function)
    except (ImportError, AttributeError) as exc:
        raise AppError("SERVICE_UNAVAILABLE", f"Service {module}.{function} is not integrated", 503) from exc
    result = target(**kwargs)
    return await result if inspect.isawaitable(result) else result

class RunScheduler:
    def __init__(self):
        self.queue = None
        self.workers = []
        self.active = {}
        self.active_context = {}
        self.accepting = False
        self.admission_lock = asyncio.Lock()
        self.recovery_task = None
        self.pending = set()

    async def start(self):
        self.queue = asyncio.Queue(maxsize=settings.runner_queue_limit)
        with SessionLocal() as session:
            for row in session.scalars(select(Resource).where(Resource.kind=="run")):
                run=json.loads(row.payload)
                if run["status"] in ("queued", "running"):
                    if run.get("engine", {}).get("name") == "langgraph": continue
                    run.update(status="interrupted", updated_at=utc_now(), error={"code": "INTERRUPTED", "message": "API process stopped before completion; submit a new run"})
                    row.payload=json.dumps(run,ensure_ascii=False)
                    context=SimpleNamespace(tenant_id=row.tenant_id,workspace_id=row.workspace_id)
                    emit(session, run["id"], context, "run.interrupted", status="interrupted")
            session.commit()
        self.accepting = True
        self.workers = [asyncio.create_task(self.worker()) for _ in range(settings.runner_concurrency)]
        self.recovery_task = asyncio.create_task(self.recover())

    async def recover(self):
        import time
        from app.execution.models import ExecutionRun
        from app.execution.storage import fresh_context
        next_auth_cleanup = 0
        while self.accepting:
            with SessionLocal() as session:
                from app.execution.approvals import expire_waiting
                expire_waiting(session)
                if time.monotonic() >= next_auth_cleanup:
                    from app.auth import prune_expired_login_state
                    prune_expired_login_state(session)
                    next_auth_cleanup = time.monotonic() + 3600
                for binding in session.scalars(select(ExecutionRun).where(ExecutionRun.lease_until < time.time())):
                    from app.execution.retained_intents import is_retained
                    if is_retained(binding.run_id):continue
                    if binding.run_id in self.active or binding.run_id in self.pending or self.queue.full(): continue
                    row = session.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==binding.run_id))
                    if row is None or json.loads(row.payload)["status"] not in {"queued", "running", "resuming"}: continue
                    try: context = fresh_context(session, binding.run_id)
                    except AppError:
                        run = json.loads(row.payload)
                        run.update(status="paused", updated_at=utc_now(), error={"code":"AUTH_REFRESH_REQUIRED","message":"执行者需重新登录后继续"})
                        row.payload = json.dumps(run, ensure_ascii=False)
                        continue
                    self.pending.add(binding.run_id)
                    self.queue.put_nowait((binding.run_id, context))
                session.commit()
            await asyncio.sleep(2)

    async def stop(self):
        self.accepting = False
        if self.recovery_task: self.recovery_task.cancel()
        for task in self.active.values(): task.cancel()
        for worker in self.workers: worker.cancel()
        await asyncio.gather(*self.active.values(), *self.workers, *([self.recovery_task] if self.recovery_task else []), return_exceptions=True)
        with SessionLocal() as session:
            for row in session.scalars(select(Resource).where(Resource.kind=="run")):
                run=json.loads(row.payload)
                if run["status"] in ("queued", "running"):
                    if run.get("engine", {}).get("name") == "langgraph": continue
                    run.update(status="interrupted", updated_at=utc_now(), error={"code": "INTERRUPTED", "message": "Service shutdown"})
                    row.payload=json.dumps(run,ensure_ascii=False)
                    context=SimpleNamespace(tenant_id=row.tenant_id,workspace_id=row.workspace_id)
                    emit(session, run["id"], context, "run.interrupted", status="interrupted")
            session.commit()

    async def submit(self, request: RunCreate, context, session):
        # Serialize admissions across async planning; never persist an orphan if another
        # request filled the bounded queue while this request was awaiting a planner.
        async with self.admission_lock:
            return await self._submit(request, context, session)

    async def _submit(self, request: RunCreate, context, session, *, frozen_agents=None, semantic_plan=None, builtin_snapshot=None, application=None, prepared_message=None, proposed_run_id=None, commit=True, enqueue=True):
        from app.rbac import require_any_permission,RUN_PERMISSIONS
        require_any_permission(context,session,RUN_PERMISSIONS)
        from app.resource_policy import require_execute, require_run_entry
        from app.asset_publication import execution_version, execution_workflow
        require_run_entry(session,context,request.model_dump())
        for resource_kind,identifier in (('workflow',request.workflow_id),('agent',request.agent_id or request.draft_agent_id)):
            if identifier:require_execute(session,context,resource_kind,identifier)
        machine=getattr(context,'principal_type','human')=='service' 
        if request.run_kind=='application' and (not machine or application is None):raise AppError('APP_ADMISSION_REQUIRED','应用运行种类仅限受控机器预约入口',403)
        if machine and (application is None or commit or enqueue):raise AppError('APP_ADMISSION_REQUIRED','机器运行必须通过原子应用预约入口',403)
        if application is not None and not machine:raise AppError('APP_ADMISSION_REQUIRED','应用运行需要真实机器主体',403)
        from app.messages import resolve_message
        if prepared_message is not None:
            resolved=prepared_message
        elif request.run_kind in {"draft", "node_test"} and request.message is None and not request.prompt:
            resolved={"type":"text","text":"","attachment_ids":[],"attachments":[],"inputs":request.inputs,
                "source_refs":[],"capabilities":{"requires_vision":False,"requires_audio":False,"requires_document_extraction":False}}
        else:
            resolved=await resolve_message(message=request.message,prompt=request.prompt,inputs=request.inputs,context=context,session=session)
        request=request.model_copy(update={"prompt":resolved["text"],"inputs":resolved["inputs"]})
        if not self.accepting: raise AppError("SERVICE_UNAVAILABLE", "Runner not accepting requests", 503)
        if self.queue.full(): raise AppError("QUEUE_FULL", "Runner waiting queue is full", 429)
        if sum(value is not None for value in (request.workflow, request.workflow_id, request.agent_id)) != 1:
            raise AppError("VALIDATION_ERROR", "Choose exactly one workflow, workflow_id, or agent_id")
        if request.agent_version_id and not request.agent_id:
            raise AppError("VALIDATION_ERROR", "agent_version_id requires agent_id")
        if request.draft_agent_id and (request.workflow is None or request.agent_id or request.workflow_id):
            raise AppError("VALIDATION_ERROR", "草稿资源范围必须搭配当前内联流程", 422)
        if request.node_test and (request.workflow is None or request.run_kind != "node_test"):
            raise AppError("VALIDATION_ERROR", "单节点测试必须提交当前草稿", 422)

        workflow = request.workflow
        from_published_workflow = False
        if request.workflow_id:
            draft = get_resource(session, "workflow", request.workflow_id, context)
            source = execution_workflow(session, context, request.workflow_id, draft)
            from_published_workflow = source is not draft
            workflow = WorkflowDefinition.model_validate(source)
        if request.draft_agent_id:
            scoped_config = await invoke("app.agents.service", "get_agent_config", agent_id=request.draft_agent_id, context=context, session=session)
            from app.agents.data_tools import authorize_scope
            authorize_scope(scoped_config, context, session)
        if request.workflow is not None and request.workflow.id and context.principal_type=='human':
            # Inline drafts of saved assets must not bypass saved-resource ACLs.
            require_execute(session,context,'workflow',request.workflow.id)
            get_resource(session,'workflow',request.workflow.id,context,access='write')
        if request.agent_id:
            version = execution_version(session, context, request.agent_id, request.agent_version_id)
            request = request.model_copy(update={"agent_version_id": version})
            scoped_config=await invoke("app.agents.service","get_agent_config",agent_id=request.agent_id,agent_version_id=request.agent_version_id,context=context,session=session)
            if scoped_config.get('main_workflow_id'):require_execute(session,context,'workflow',scoped_config['main_workflow_id'])
            for binding in scoped_config.get('bindings',[]):require_execute(session,context,'plugin_version',binding['plugin_version_id'])
            try:
                async with asyncio.timeout(min(settings.run_timeout_seconds, 30)):
                    extras = {}
                    if request.agent_version_id: extras["agent_version_id"] = request.agent_version_id
                    if request.inputs: extras["inputs"] = request.inputs
                    if request.message is not None:extras["message"]=resolved
                    workflow = WorkflowDefinition.model_validate(await invoke("app.agents.service", "plan", agent_id=request.agent_id, prompt=request.prompt, query=request.query, context=context, session=session, **extras))
                    current_config=await invoke("app.agents.service","get_agent_config",agent_id=request.agent_id,agent_version_id=request.agent_version_id,context=context,session=session)
                    if current_config!=scoped_config:raise AppError("AGENT_CONFIG_CHANGED","智能体配置在提交期间发生变化，请重新提交",409)
            except TimeoutError as exc:
                raise AppError("RUN_TIMEOUT", "Agent planning exceeded deadline", 504) from exc
        elif workflow.mode == "model" and application is None and context.principal_type!='platform' and not (from_published_workflow and workflow.schemaVersion == "2.0"):
            raise AppError("VALIDATION_ERROR", "Model mode requires a configured agent planner; direct workflows use demo mode")
        ordered = validate_workflow(workflow)
        if any(node.type in {"query","calculate","chart","report"} for node in ordered) and request.query.entity not in context.allowed_entities:
            raise AppError("CONNECTION_BLOCKED","当前账号没有请求的财务业务范围",403)
        selected_nodes = ordered
        if request.node_test:
            from app.execution.drafts import NodeTest
            from pydantic import ValidationError
            try: test = NodeTest.model_validate(request.node_test)
            except ValidationError as exc:
                raise AppError("INVALID_TEST_VARIABLE", "单节点测试参数不符合要求", 422) from exc
            request = request.model_copy(update={"node_test": test.model_dump(mode="json")})
            from app.execution.drafts import select_target
            test_workflow,test_node,test_scope,test_parent,test_limits,test_key=select_target(workflow,test)
            selected_nodes = [test_node]
        from app.runner.graph import TRIGGERS
        trigger_types={n.type.removeprefix("trigger_") for n in ordered if n.type in TRIGGERS}
        if trigger_types and not request.node_test and resolved["type"] not in trigger_types:raise AppError("TRIGGER_NOT_MATCHED","No matching message entry in workflow",422)
        if workflow.schemaVersion == "2.0":
            from app.runner.registry import node_registry
            from app.runner.variables import check_schema, check_size
            available = {item["type"]: item for item in node_registry()["items"]}
            from app.runner.input_defaults import normalize_inputs
            from app.runner.fields import message_inputs
            actual_inputs=message_inputs(workflow,resolved,request.inputs)
            request=request.model_copy(update={"inputs":normalize_inputs(actual_inputs,workflow.input_schema,partial=bool(request.node_test))})
            resolved["inputs"]=request.inputs
            check_size(request.inputs, 1048576, "workflow inputs")
            for node in selected_nodes:
                if available[node.type]["status"] == "unavailable":
                    raise AppError("NODE_UNAVAILABLE", available[node.type]["reason"], 503)
        for node in selected_nodes:
            if node.type in ("plugin", "http"):
                binding = ToolBinding.model_validate(node.config["binding"])
                require_execute(session,context,'plugin_version',binding.plugin_version_id)
                version = get_resource(session, "plugin_version", binding.plugin_version_id, context)
                if binding.connection_id:get_resource(session, "connection", binding.connection_id, context)
                if version.get("status", "published") != "published" or not any(t["id"] == binding.tool_id for t in version["tools"]):
                    raise AppError("INVALID_BINDING", "Tool/version is unavailable")
        run = Run(id=proposed_run_id or new_id("run"), status="queued", mode=workflow.mode, created_at=utc_now(), updated_at=utc_now(), request=request)
        payload = run.model_dump(mode="json")
        if context.principal_type=='human' and request.workflow is not None and request.workflow.id:
            payload['source_resource_refs']=[{'kind':'workflow','resource_id':request.workflow.id}]
        if context.principal_type=='human' and request.agent_id and scoped_config.get('main_workflow_id'):
            payload['source_resource_refs']=[{'kind':'workflow','resource_id':scoped_config['main_workflow_id']}]
        payload["request_id"] = getattr(request, "request_id", None)
        if request.agent_id or request.draft_agent_id:
            config=scoped_config
            payload["scoped_bindings"] = config.get("bindings", [])
            if "data_resources" in config:payload["agent_data_scope"]=[{key:item[key] for key in ("kind","resource_id","version_id") if key in item and item[key] is not None} for item in config["data_resources"] if item.get("enabled",True)]
            if "data_resources" in config and "knowledge_resources" in config:payload["agent_knowledge_scope"]=[{"kind":"knowledge_base" if item["resource_id"].startswith("knowledge_base_") else "document","resource_id":item["resource_id"]} for item in config["knowledge_resources"] if item.get("enabled",True)]
        payload["workflow_snapshot"] = workflow.model_dump(mode="json")
        payload["message"]=resolved
        from app.execution.storage import engine_identity, engine_key
        from app.execution.storage import encode
        import hashlib
        source_hash = hashlib.sha256(encode(payload["workflow_snapshot"]).encode()).hexdigest()
        if request.node_test:
            from app.execution.drafts import seed_test
            payload["test_pool"] = seed_test(workflow, request, context, run.id)
            payload["test_limits"] = test_limits
        from app.execution.models import ExecutionRun
        from app.data_access import agent_sources
        payload["agent_snapshots"] = {}
        def freeze_agents(definition, scope="root"):
            for node in definition.nodes:
                if scope == "root" and request.node_test and node.id != request.node_test["node_id"]: continue
                if node.type == "agent":
                    require_execute(session,context,"agent",node.config["agent_id"])
                    from app.agents.service import get_agent_config
                    config = (frozen_agents or {}).get(scope+"/"+node.id)
                    if config is None: config = get_agent_config(agent_id=node.config["agent_id"], agent_version_id=node.config.get("agent_version_id"), context=context, session=session)
                    if config.get("planner_mode","model")!="model":raise AppError("MODEL_NOT_CONFIGURED","智能体尚未配置正式模型",503)
                    for binding in config.get("bindings",[]):require_execute(session,context,"plugin_version",binding["plugin_version_id"])
                    if config.get("main_workflow_id"): raise AppError("NODE_UNAVAILABLE", "嵌套主流程智能体尚未通过持久化预算适配验证", 503)
                    from app.runner.fields import business
                    if business(definition,node):
                        config=deepcopy(config)
                        if config.get('planner_mode')!='model':raise AppError('NODE_MODE_UNSUPPORTED','新对话节点只使用已发布的真实模型助手，不使用演示回答',422)
                        config['business_fields_mode']=True
                        fmt=node.config.get('response_format',{'type':'text'})
                        config['output_schema']=(getattr(node,'output_schema',None) or fmt.get('schema',{'type':'object'})) if fmt['type']!='text' else {'type':'object'}
                    payload["agent_snapshots"][scope+"/"+node.id] = config
                if node.type in {"iteration", "loop"}:
                    freeze_agents(WorkflowDefinition.model_validate(node.config["body"]), scope+"/"+node.id+"/"+node.type)
        freeze_agents(test_workflow if request.node_test else workflow)
        if request.agent_id or request.draft_agent_id:
            root_bindings = {(item["plugin_version_id"],item["tool_id"],item.get("connection_id")) for item in scoped_config.get("bindings", [])}
            from app.data_access import authorize_data_resource
            for config in payload["agent_snapshots"].values():
                if not {(item["plugin_version_id"],item["tool_id"],item.get("connection_id")) for item in config.get("bindings", [])} <= root_bindings:
                    raise AppError("CONNECTION_BLOCKED", "子智能体工具超出父智能体的固定范围", 403)
                for source in agent_sources(config):
                    selected_scope = payload.get("agent_data_scope") if source["kind"] in {"database", "data_table", "data_table_version"} else payload.get("agent_knowledge_scope")
                    authorize_data_resource(session=session, context=context, kind=source["kind"], resource_id=source["resource_id"], agent_scope=selected_scope)
        from app.model_catalog.service import pin_run_models
        if request.node_test:
            from app.execution.drafts import selected_snapshot
            snapshot = payload["workflow_snapshot"]
            chosen=selected_snapshot(snapshot,request.node_test.get("scope_path",[]))
            payload["workflow_snapshot"] = {**chosen, "nodes": [node for node in chosen["nodes"] if node["id"] == request.node_test["node_id"]]}
            pin_run_models(payload, context=context, session=session)
            payload["workflow_snapshot"] = snapshot
        else: pin_run_models(payload, context=context, session=session)
        if application is not None:
            if payload['model_bindings']!=application['bundle']['model_bindings']:raise AppError('APP_DEPENDENCY_CHANGED','应用固定模型绑定发生变化',409)
            if application['bundle']['source_identity']['kind']=='agent':
                for field in ('agent_data_scope','agent_knowledge_scope','scoped_bindings'):
                    payload[field]=application['bundle'][field]
            payload['application_request_id']=application['request_id']
            payload['application_history']=application.get('history',[])
            if application.get('conversation_id'):payload['conversation_id']=application['conversation_id']
        payload["engine"] = engine_identity(payload["workflow_snapshot"])
        payload["execution_identity"] = {"kind": "application" if application is not None else "node_test" if request.node_test else "draft" if request.run_kind == "draft" else "agent" if request.agent_id else "workflow",
            "graph_sha256": payload["engine"]["dsl_sha256"], "source_graph_sha256": source_hash,
            "revision": request.draft_revision, "workflow_id": workflow.id,
            "agent_id": request.agent_id or request.draft_agent_id,
            "node_id": request.node_test["node_id"] if request.node_test else None}
        if request.node_test:payload["execution_identity"].update(scope_path=request.node_test.get("scope_path",[]),target_key=test_key)
        if semantic_plan is not None:
            from app.execution.semantic_jobs import persist_plan
            persist_plan(session,payload,semantic_plan,builtin_snapshot,context)
        if application is not None:payload['execution_identity'].update(application_id=context.application_id,version_id=context.application_version_id)
        save_resource(session, "run", run.id, payload, context)
        from app.data_access import agent_sources,record_derivation
        if context.principal_type=='platform':
            # All real-key Run bindings precede source registration in this same
            # admission transaction. No work is queued until the whole commit.
            from app.platform_api import _put
            _put(session,'platform_run_authorization',run.id,{'token_id':context.auth_key_id},context.tenant_id,context.workspace_id,context.operator_id)
            session.add(ExecutionRun(run_id=run.id,tenant_id=context.tenant_id,workspace_id=context.workspace_id,principal_id=context.operator_id,engine_key=engine_key(payload['engine']),dsl_hash=payload['engine']['dsl_sha256']))
            session.flush()
        initial_sources=agent_sources(scoped_config) if request.agent_id or request.draft_agent_id else []
        for config in payload["agent_snapshots"].values(): initial_sources.extend(agent_sources(config))
        initial_sources.extend({"kind":"attachment","resource_id":attachment_id,"action":"use"} for attachment_id in resolved.get("attachment_ids",[]))
        if application is not None:initial_sources.extend(application.get('history_sources',[]))
        if initial_sources:
            record_derivation(session=session,context=context,kind="run",resource_id=run.id,sources=initial_sources)
            payload["data_sources"]=initial_sources
            save_resource(session,"run",run.id,payload,context)
        if machine:
            from app.machine_models import MachineRunAuthorization
            session.add(MachineRunAuthorization(run_id=run.id,principal_id=context.operator_id,tenant_id=context.tenant_id,workspace_id=context.workspace_id,
                application_id=context.application_id,version_id=context.application_version_id,client_id=context.client_id,external_user_hash=context.external_user_hash,
                admission_key_id=context.auth_key_id,executing_key_id=context.auth_key_id,release_sha256=application['release_sha256'],
                reservation_id=application['reservation_id'],created_at=utc_now()))
        elif context.principal_type!='platform':session.add(RunAuthorization(run_id=run.id,user_id=context.operator_id,session_id=context.session_id))
        if context.principal_type!='platform':session.add(ExecutionRun(run_id=run.id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, principal_id=context.operator_id, engine_key=engine_key(payload["engine"]), dsl_hash=payload["engine"]["dsl_sha256"]))
        emit(session, run.id, context, "run.queued", status="queued")
        if commit:session.commit()
        if enqueue:self.enqueue_committed(run.id,context)
        return payload

    def enqueue_committed(self,run_id,context):
        from app.execution.retained_intents import deny_execution
        deny_execution(run_id)
        # The durable recovery loop also discovers a committed admission if the
        # process stops here or another admission has filled the bounded queue.
        if run_id in self.pending or run_id in self.active or not self.accepting or self.queue.full():return
        self.pending.add(run_id);self.queue.put_nowait((run_id,context))

    async def cancel(self, run_id, context, session):
        from app.execution.retained_intents import deny_execution
        deny_execution(run_id)
        run = get_resource(session, "run", run_id, context)
        if run["status"] not in ("queued", "running", "resuming", "paused", "waiting_approval", "outcome_unknown"): return run
        if run.get("engine", {}).get("name") == "langgraph":
            from app.execution.storage import authorize
            authorize(session, run_id, context)
        run.update(status="cancelled", updated_at=utc_now(), error={"code": "CANCELLED", "message": "Cancelled by user"})
        save_resource(session, "run", run_id, run, context)
        emit(session, run_id, context, "run.cancelled", status="cancelled")
        session.commit()
        if run_id in self.active: self.active[run_id].cancel()
        return run

    async def worker(self):
        while True:
            run_id, context = await self.queue.get()
            self.pending.discard(run_id)
            if run_id in self.active:
                self.queue.task_done()
                continue
            task = asyncio.create_task(self.execute(run_id, context))
            self.active[run_id] = task
            self.active_context[run_id] = context
            try:
                await task
            except asyncio.CancelledError:
                if not self.accepting: raise
            except AppError:
                await self.revoke_session(context.session_id)
            finally:
                if self.active.get(run_id) is task:
                    self.active.pop(run_id, None)
                    self.active_context.pop(run_id,None)
                self.queue.task_done()

    async def revoke_session(self,session_id):
        # Security revocation is a trusted maintenance operation, not a user upsert.
        with SessionLocal() as session:
            for auth in session.scalars(select(RunAuthorization).where(RunAuthorization.session_id==session_id)):
                row=session.scalar(select(Resource).where(Resource.kind=="run",Resource.resource_id==auth.run_id,Resource.owner_id==auth.user_id))
                if row is None:continue
                payload=json.loads(row.payload)
                if payload.get("status") not in ("queued","running"):continue
                payload.update(status="cancelled",updated_at=utc_now(),error={"code":"SESSION_REVOKED","message":"Session revoked"})
                row.payload=json.dumps(payload,ensure_ascii=False)
                emit(session,auth.run_id,SimpleNamespace(tenant_id=row.tenant_id,workspace_id=row.workspace_id),"run.cancelled",status="cancelled",data={"reason":"session_revoked"})
                if auth.run_id in self.active:self.active[auth.run_id].cancel()
            session.commit()

    async def execute(self, run_id, context):
        with SessionLocal() as lookup:
            row = lookup.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==run_id))
            durable = row is not None and json.loads(row.payload).get("engine", {}).get("name") == "langgraph"
        if durable:
            from app.execution.runtime import execute
            return await execute(run_id)
        with SessionLocal() as session:
            run = get_resource(session, "run", run_id, context)
            if run["status"] != "queued": return
            run.update(status="running", updated_at=utc_now())
            save_resource(session, "run", run_id, run, context)
            emit(session, run_id, context, "run.started", status="running")
            session.commit()
            node_id = None
            try:
                async with asyncio.timeout(settings.run_timeout_seconds):
                    request = RunCreate.model_validate(run["request"])
                    from app.messages import resolve_message
                    run["message"]=await resolve_message(message=request.message,prompt=request.prompt,inputs=request.inputs,context=context,session=session)
                    workflow = WorkflowDefinition.model_validate(run["workflow_snapshot"])
                    if workflow.schemaVersion == "2.0":
                        from app.runner.runtime import GraphRuntime
                        def graph_event(event_type, scoped_node_id, status, data):
                            emit(session, run_id, context, event_type, scoped_node_id, status, data)
                            session.commit()
                        def graph_persist():
                            run["updated_at"] = utc_now()
                            save_resource(session, "run", run_id, run, context)
                            session.commit()
                        runtime = GraphRuntime(request=request, run=run, context=context, session=session, invoke=invoke, event=graph_event, persist=graph_persist)
                        async with asyncio.timeout(workflow.limits.get("timeout_seconds", 120)):
                            result = await runtime.execute(workflow)
                        run.update(status="succeeded", outputs=result["outputs"], variable_pool=result["pool"], updated_at=utc_now())
                        graph_persist()
                        emit(session, run_id, context, "run.completed", status="succeeded")
                        session.commit()
                        return
                    facts, dataset = [], None
                    for node in validate_workflow(workflow):
                        from app.rbac import require_any_permission,RUN_PERMISSIONS
                        require_any_permission(context,session,RUN_PERMISSIONS)
                        node_id = node.id
                        emit(session, run_id, context, "node.started", node.id, "running")
                        session.commit()
                        result = {}
                        kwargs = {"context": context, "session": session}
                        if node.type == "plugin":
                            arguments = {"entity": request.query.entity, "periods": request.query.periods, **node.config.get("arguments", {})}
                            output = await invoke("app.plugins.service", "execute_tool", binding=ToolBinding.model_validate(node.config["binding"]), arguments=arguments, **kwargs)
                            tool = ToolExecutionResult.model_validate(output)
                            facts = tool.facts
                            result = {"fact_count": len(facts), "source_refs": tool.source_refs, "warnings": tool.warnings, "audit": tool.model_dump().get("audit", {})}
                        elif node.type == "query":
                            facts = [FinancialFact.model_validate(x) for x in await invoke("app.finance.service", "query_facts", query=request.query, **kwargs)]
                            result = {"fact_count": len(facts)}
                        elif node.type == "calculate":
                            dataset = ResultDataset.model_validate(await invoke("app.finance.service", "calculate_dataset", facts=facts, query=request.query, **kwargs))
                            save_resource(session, "dataset", dataset.id, dataset.model_dump(mode="json"), context)
                            run["dataset_id"] = dataset.id
                            result = {"dataset_id": dataset.id, "snapshot_id": dataset.snapshot_id, "warnings": dataset.warnings}
                        elif node.type == "chart":
                            result = {"chart_spec": {"type": "bar", "dataset_id": dataset.id, "x": "period", "series": ["revenue", "net_profit"]}}
                            run["chart_spec"] = result["chart_spec"]
                        elif node.type == "report":
                            artifacts = await invoke("app.reporting.service", "generate_artifacts", dataset=dataset, run_id=run_id, **kwargs)
                            for item in artifacts:
                                artifact = Artifact.model_validate(item)
                                if artifact.run_id != run_id or artifact.dataset_id != dataset.id: raise AppError("VALIDATION_ERROR", "Artifact relationship mismatch")
                                save_resource(session, "artifact", artifact.id, artifact.model_dump(mode="json"), context)
                                run["artifact_ids"].append(artifact.id)
                            result = {"artifact_ids": run["artifact_ids"]}
                        save_resource(session, "run", run_id, run, context)
                        emit(session, run_id, context, "node.completed", node.id, "succeeded", result)
                        session.commit()
                    run.update(status="succeeded", updated_at=utc_now())
                    save_resource(session, "run", run_id, run, context)
                    emit(session, run_id, context, "run.completed", status="succeeded")
                    session.commit()
            except asyncio.CancelledError:
                session.rollback()
                raise
            except Exception as exc:
                session.rollback()
                if isinstance(exc, TimeoutError): error = AppError("RUN_TIMEOUT", "Run exceeded configured deadline", 504)
                elif isinstance(exc, AppError): error = exc
                else: error = AppError("INTERNAL_ERROR", "Node execution failed; inspect server diagnostics", 500)
                # Error details from service contracts must be sanitized by their owner.
                run.update(status="failed", updated_at=utc_now(), error=error.as_dict())
                row=session.scalar(select(Resource).where(Resource.kind=="run",Resource.resource_id==run_id,Resource.owner_id==(context.actor_id if context.principal_type=="platform" else context.operator_id),Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
                if row is None:return
                existing=json.loads(row.payload)
                if existing.get("status") in ("cancelled","interrupted"):return
                if error.code=="SESSION_REVOKED":run["status"]="cancelled"
                row.payload=json.dumps(run,ensure_ascii=False)
                if node_id is not None:
                    emit(session, run_id, context, "node.failed", node_id, "failed", {"error": error.as_dict()})
                emit(session, run_id, context, "run.failed", status="failed", data={"error": error.as_dict()})
                session.commit()

scheduler = RunScheduler()
