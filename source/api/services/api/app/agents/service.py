"""Agent CRUD and conversion of validated plans to the engineering runner DSL."""

import asyncio
from copy import deepcopy
from sqlalchemy.orm import Session

from app.contracts import BusinessContext, FactQuery, ToolBinding, WorkflowDefinition
from app.db import get_resource, list_resources, new_id, save_resource, utc_now
from app.errors import AppError
from app.plugins.service import materialize_binding, resolve_binding
from app.plugins.listing import paginate
from .model_adapter import OpenAICompatibleAdapter, load_config, model_status
from .planner import plan as plan_tools
from .specs import AgentWrite, STUDIO_FIELDS, binding_id
from .runtime import check_inputs, prepare_tools
from .workflows import claim_workflow, validate_workflow_dependencies, workflow_snapshot
from .types import agent_type, agent_view, validate_type
from app.plugins.permissions import require_ai_access, require_page
from .data_tools import authorize_scope


def save_agent(data: AgentWrite, context: BusinessContext, session: Session, agent_id: str | None = None) -> dict:
    require_page(context, session, "page.ai")
    old = get_resource(session, "agent", agent_id, context, access="write") if agent_id else {}
    from .skill_bindings import resolve_guidance
    resolve_guidance(old,context=context,session=session)
    values = data.model_dump()
    if agent_id and 'skill_bindings' not in data.model_fields_set:
        values['skill_bindings']=old.get('skill_bindings',[])
    if agent_id and 'document_tools' not in data.model_fields_set:values['document_tools']=old.get('document_tools',[])
    if agent_id and "main_workflow_id" not in data.model_fields_set:
        values["main_workflow_id"] = old.get("main_workflow_id")
    if old.get("main_workflow_id") and values.get("main_workflow_id") is None:
        raise AppError("CONFLICT", "主流程不能隐式清空，请显式改绑已授权流程", 409)
    values["agent_type"] = data.agent_type or old.get("agent_type") or ("advanced" if values.get("main_workflow_id") else "simple")
    validate_type(values)
    from .document_bindings import authorize_config
    authorize_config(values,context=context,session=session)
    from .skill_bindings import resolve_guidance
    resolve_guidance(values,context=context,session=session)
    materialized = [materialize_binding(binding, context, session) for binding in data.bindings]
    replacement = {binding_id(old): binding_id(new) for old, new in zip(data.bindings, materialized)}
    values["bindings"] = [binding.model_dump() for binding in materialized]
    for setting in values["tool_settings"]:
        setting["binding_id"] = replacement[setting["binding_id"]]
    prepare_tools(values, context, session)
    authorize_scope(values, context, session)
    studio = bool(STUDIO_FIELDS & data.model_fields_set) or data.agent_type == "simple" or old.get("config_version") == "2.0"
    payload = {**values, "id": agent_id or new_id("agent"),
               "config_version": "2.0" if studio else "1.0", "status": "draft",
               "created_at": old.get("created_at", utc_now()), "updated_at": utc_now()}
    if payload.get("main_workflow_id"):
        workflow_snapshot(payload, context, session)
    save_resource(session, "agent", payload["id"], payload, context)
    if payload.get("main_workflow_id") or old.get("main_workflow_id"):
        claim_workflow(payload["id"], payload.get("main_workflow_id"), context, session)
    return payload


def get_agent_config(*, agent_id, context, session, agent_version_id=None):
    require_ai_access(context, session)
    get_resource(session, "agent", agent_id, context)
    if agent_version_id:
        version = get_resource(session, "agent_version", agent_version_id, context)
        if version["agent_id"] != agent_id or version.get("status") not in {"published", "run_snapshot"}:
            raise AppError("INVALID_BINDING", "智能体版本不属于所选智能体或已停用", 422)
        result = deepcopy(version["config"])
        if version.get("workflow_snapshot"):
            get_resource(session, "workflow", result["main_workflow_id"], context)
            validate_workflow_dependencies(version["workflow_snapshot"], result, context, session)
            result["_workflow_snapshot"] = deepcopy(version["workflow_snapshot"])
        from .document_bindings import authorize_config
        authorize_config(result,context=context,session=session)
        from .skill_bindings import resolve_guidance
        resolve_guidance(result,context=context,session=session)
        return result
    result=deepcopy(get_resource(session, "agent", agent_id, context))
    from .document_bindings import authorize_config
    authorize_config(result,context=context,session=session)
    from .skill_bindings import resolve_guidance
    resolve_guidance(result,context=context,session=session)
    return result


def publish_agent(*, agent_id, context, session):
    require_page(context, session, "page.ai")
    get_resource(session, "agent", agent_id, context, access="write")
    config = get_agent_config(agent_id=agent_id, context=context, session=session)
    validate_type(config, executing=True)
    snapshot, workflow_bindings = workflow_snapshot(config, context, session)
    prepare_tools(config, context, session)
    authorize_scope(config, context, session)
    if snapshot is None and config.get("planner_mode") == "model":
        if not config.get("model_config_id"):
            raise AppError("MODEL_NOT_CONFIGURED", "发布模型智能体前请选择管理员模型配置", 503)
        resolved = load_config(config["model_config_id"], context=context, session=session, expected_version=config.get("model_config_version"))
        config["model_config_version"] = resolved.config_version
    for resource in config.get("knowledge_resources", []):
        if resource.get("enabled", True):
            from app.data_access import authorize_data_resource
            kind = "knowledge_base" if resource["resource_id"].startswith("knowledge_base_") else "document"
            authorize_data_resource(session=session, context=context, kind=kind, resource_id=resource["resource_id"])
    versions = [v for v in list_resources(session, "agent_version", context)
                if v["agent_id"] == agent_id and v.get("status") == "published"]
    result = {"id": new_id("av"), "agent_id": agent_id, "version": max((v["version"] for v in versions), default=0) + 1,
              "name": config["name"], "status": "published", "config": deepcopy(config), "created_at": utc_now()}
    if snapshot is not None:
        result.update(workflow_snapshot=snapshot, tool_bindings=workflow_bindings)
    save_resource(session, "agent_version", result["id"], result, context)
    save_resource(session, "agent", agent_id, {**config, "status": "published", "latest_version": result["version"], "updated_at": utc_now()}, context)
    return result


def agent_references(*, agent_id, context, session):
    get_resource(session, "agent", agent_id, context)
    found = []
    def walk(nodes):
        for node in nodes:
            config = node.get("config", {})
            if node.get("type") == "agent" and config.get("agent_id") == agent_id:
                yield config
            if isinstance(config.get("body"), dict):
                yield from walk(config["body"].get("nodes", []))
    for kind in ("workflow", "run"):
        for resource in list_resources(session, kind, context):
            workflow = resource.get("workflow_snapshot", {}) if kind == "run" else resource
            for config in walk(workflow.get("nodes", [])):
                found.append({"kind": kind, "id": resource["id"], "name": resource.get("name", ""), "agent_version_id": config.get("agent_version_id")})
            if kind == "run" and resource.get("request", {}).get("agent_id") == agent_id and not any(x["id"] == resource["id"] for x in found):
                found.append({"kind": kind, "id": resource["id"], "agent_version_id": resource["request"].get("agent_version_id")})
    return found


def list_agents(*, context, session, page=1, page_size=20, q="", status=""):
    require_ai_access(context, session)
    versions = list_resources(session, "agent_version", context)
    items = []
    from app.resource_policy import directory
    directory_items=directory(session,context,'agent')
    for agent in list_resources(session, "agent", context):
        published = [v for v in versions if v["agent_id"] == agent["id"] and v.get("status") == "published"]
        latest=max(published,key=lambda value:value['version']) if published else None
        chat=latest.get('config',{}) if latest else {}
        chat_model=model_status(context=context,session=session,config_id=chat.get('model_config_id')) if chat.get('model_config_id') else {'configured':False}
        chat_ready=bool(latest and agent_type(chat)=='simple' and chat.get('planner_mode')=='model' and chat_model['configured'] and not any(chat.get(key) for key in ('main_workflow_id','skill_bindings','bindings','document_tools')))
        items.append({**agent_view(agent),"conversation_ready":chat_ready,"conversation_version_id":latest['id'] if chat_ready else None, "status": agent.get("status", "draft"), "tool_count": len(agent.get("bindings", [])),
                      "latest_version": max((v["version"] for v in published), default=0),
                      "reference_count": len(agent_references(agent_id=agent["id"], context=context, session=session)),
                      "model_configuration": model_status(context=context, session=session, config_id=agent.get("model_config_id")) if agent.get("planner_mode") == "model" else {"configured": False, "default_mode": "model"}})
    full_ids={item['id'] for item in items}
    access_by_id={item['id']:item['access'] for item in directory_items}
    for item in items:item['access']=access_by_id.get(item['id'],{})
    items.extend(item for item in directory_items if item['id'] not in full_ids)
    return paginate(items, page=page, page_size=page_size, q=q, status=status)


async def execute_agent(*, agent_id: str, prompt: str, query: FactQuery, inputs: dict,
                        context: BusinessContext, session: Session, run_id: str, agent_version_id: str | None = None,
                        message: dict | None = None) -> dict:
    if run_id != "debug":
        raise AppError("NODE_UNAVAILABLE", "Agent子调用须由父LangGraph编译并共享预算，不能另建运行", 503)
    from app.contracts import RunCreate
    from app.runner.service import scheduler
    from app.db import SessionLocal
    from app.execution.contracts import TERMINAL_STATUSES, WAITING_STATUSES
    from app.resource_policy import require_execute
    require_execute(session,context,"agent",agent_id)
    config = get_agent_config(agent_id=agent_id, agent_version_id=agent_version_id, context=context, session=session)
    validate_type(config, executing=True)
    run = await scheduler.submit(RunCreate(agent_id=agent_id, agent_version_id=agent_version_id, prompt=prompt,
                                           query=query, inputs=inputs, message=message), context, session)
    deadline = asyncio.get_running_loop().time() + 120
    while True:
        with SessionLocal() as read_session:
            current = get_resource(read_session, "run", run["id"], context)
        if current["status"] == "succeeded":
            output = current.get("outputs", {}).get("result")
            if not isinstance(output, dict):
                raise AppError("VARIABLE_TYPE_MISMATCH", "智能体运行没有返回预期结果", 422, {"run_id": run["id"]})
            return {**output, "run_id": run["id"], "debug_mode": "agent_run"}
        if current["status"] in TERMINAL_STATUSES | WAITING_STATUSES:
            error = current.get("error") or {"code": "EXECUTION_PAUSED", "message": "执行已暂停，请查看运行状态后继续"}
            code = error["code"]
            status = (503 if code in {"MODEL_NOT_CONFIGURED", "MODEL_CAPABILITY_UNAVAILABLE", "NODE_UNAVAILABLE"} else
                      504 if code in {"RUN_TIMEOUT", "AGENT_TIMEOUT", "MODEL_TIMEOUT"} else 409 if code in {"OUTCOME_UNKNOWN", "EXECUTION_PAUSED"} else
                      500 if code == "INTERNAL_ERROR" else 403 if code in {"FORBIDDEN", "PERMISSION_DENIED", "CONNECTION_BLOCKED"} else 422)
            raise AppError(code, error["message"], status, {"run_id": run["id"], "status": current["status"], "cause": error.get("details")})
        if asyncio.get_running_loop().time() >= deadline:
            with SessionLocal() as cancel_session:
                await scheduler.cancel(run["id"], context, cancel_session)
            raise AppError("AGENT_TIMEOUT", "智能体调试超过120秒，请查看运行记录", 504, {"run_id": run["id"]})
        await asyncio.sleep(.05)


async def plan(*, agent_id: str, prompt: str, query: FactQuery,
               context: BusinessContext, session: Session, agent_version_id: str | None = None,
               inputs: dict | None = None, message: dict | None = None) -> WorkflowDefinition:
    from app.resource_policy import require_execute
    require_execute(session,context,"agent",agent_id)
    agent = get_agent_config(agent_id=agent_id, agent_version_id=agent_version_id, context=context, session=session)
    validate_type(agent, executing=True)
    if agent.get("main_workflow_id"):
        snapshot = agent.get("_workflow_snapshot")
        if snapshot is None:
            snapshot, _ = workflow_snapshot(agent, context, session)
        definition, _ = validate_workflow_dependencies(snapshot, agent, context, session)
        return definition
    if agent.get("planner_mode","model")!="model":raise AppError("MODEL_NOT_CONFIGURED","请选择正式模型后再调用此智能体",503)
    if message is not None and agent.get("config_version") != "2.0":
        from .messages import agent_message
        resolved = await agent_message(message=message, prompt=prompt, inputs=inputs or {}, context=context, session=session)
        prompt, inputs = resolved["text"], resolved["inputs"]
    if agent.get("config_version") == "2.0":
        check_inputs(agent, inputs or {})
        prepare_tools(agent, context, session)
        authorize_scope(agent, context, session)
        if agent.get("planner_mode") == "model":
            if not agent.get("model_config_id"):
                raise AppError("MODEL_NOT_CONFIGURED", "智能体尚未选择模型配置", 503)
            load_config(agent["model_config_id"], context=context, session=session, expected_version=agent.get("model_config_version"))
        # A private immutable execution revision freezes draft settings before queueing.
        if not agent_version_id:
            agent_version_id = new_id("avs")
            save_resource(session, "agent_version", agent_version_id, {"id": agent_version_id, "agent_id": agent_id,
                "version": 0, "status": "run_snapshot", "config": deepcopy(agent), "created_at": utc_now()}, context)
        return WorkflowDefinition(schemaVersion="2.0", name=agent["name"], mode=agent.get("planner_mode", "demo"), nodes=[
            {"id": "start", "type": "start"},
            {"id": "agent", "type": "agent", "config": {"agent_id": agent_id, "agent_version_id": agent_version_id},
             "inputs": {"prompt": {"$ref": "nodes.start.prompt"}, "query": {"$ref": "nodes.start.query"},
                        "inputs": {"$ref": "nodes.start.inputs"}, "message": {"$ref": "nodes.start.message"}}},
            {"id": "answer", "type": "answer", "inputs": {"result": {"$ref": "nodes.agent"}}},
        ], edges=[{"source": "start", "target": "agent"}, {"source": "agent", "target": "answer"}])
    tools, bindings = [], {}
    for data in agent.get("bindings", []):
        binding = ToolBinding.model_validate(data)
        resolved = resolve_binding(binding, context, session)
        key = binding_id(binding)
        bindings[key] = binding.model_dump(include={"plugin_version_id", "tool_id", "connection_id"})
        tools.append({"binding_id": key, "name": resolved["tool_name"],
                      "description": resolved["tool_description"],
                      "input_schema": resolved["endpoint"]["input_schema"]})
    mode = agent.get("planner_mode", "demo")
    adapter = OpenAICompatibleAdapter(load_config(agent["model_config_id"], context=context, session=session,
        expected_version=agent.get("model_config_version"))) if mode == "model" else None
    from .model_access import bind_model_access
    with bind_model_access(context=context, session=session):
        selected = await plan_tools(prompt, tools, {"mode": mode, "query": query.model_dump(),
                                                   "allowed_entities": context.allowed_entities, "adapter": adapter})
    call = selected["tool_calls"][0]
    # Only these two fields cross into the runner; arbitrary model fields are discarded.
    plugin_config = {"binding": bindings[call["binding_id"]], "arguments": call["arguments"]}
    node_types = ["start", "plugin", "calculate", "chart", "report", "end"]
    nodes = [{"id": kind, "type": kind, "config": plugin_config if kind == "plugin" else {}} for kind in node_types]
    return WorkflowDefinition(name=agent["name"] + " / 年度财务分析", mode=selected["mode"], nodes=nodes,
                              edges=[{"source": left, "target": right} for left, right in zip(node_types, node_types[1:])],
                              planning={"intent": selected["intent"], "warnings": selected["warnings"], "agent_id": agent_id})
