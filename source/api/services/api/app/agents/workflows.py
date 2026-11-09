"""Merged Agent/workflow binding and dependency snapshots, with repository ACLs."""

from copy import deepcopy

from app.contracts import ToolBinding, WorkflowDefinition
from app.db import get_resource
from app.errors import AppError
from app.plugins.service import materialize_binding
from .specs import binding_id
from .data_tools import authorize_scope, scope, selected_version


def validate_workflow_dependencies(workflow, agent, context, session):
    from app.data_access import authorize_data_resource
    authorize_scope(agent, context, session)
    from app.runner.validation import validate_workflow
    definition = WorkflowDefinition.model_validate(workflow)
    validate_workflow(definition)
    bindings = {}
    permitted = {binding_id(b) for b in agent.get("bindings", [])}

    def walk(nodes):
        for node in nodes:
            config, inputs = node.get("config", {}), node.get("inputs", {})
            candidates = [config.get("binding")]
            for source in inputs.get("sources", []) if isinstance(inputs.get("sources"), list) else []:
                if isinstance(source, dict) and source.get("type") == "plugin":
                    candidates.append(source.get("binding"))
            for raw in candidates:
                if not isinstance(raw, dict) or "$ref" in raw:
                    continue
                binding = materialize_binding(raw, context, session)
                key = binding_id(binding)
                if permitted and key not in permitted:
                    raise AppError("INVALID_BINDING", "主流程工具不在智能体允许的绑定范围内", 422)
                bindings[key] = binding.model_dump()
            if node.get("type") == "agent":
                target = config.get("agent_id")
                if target == agent.get("id"):
                    raise AppError("INVALID_WORKFLOW", "主流程不能递归调用自身智能体", 422)
                get_resource(session, "agent", target, context)
                if config.get("agent_version_id"):
                    version = get_resource(session, "agent_version", config["agent_version_id"], context)
                    if version.get("agent_id") != target:
                        raise AppError("INVALID_BINDING", "子智能体版本不匹配", 422)
            if node.get("type") in {"knowledge_retrieval", "document_extractor"}:
                ids = inputs.get("file_ids", [inputs.get("file_id")])
                if isinstance(ids, list):
                    for file_id in ids:
                        if isinstance(file_id, str):
                            authorize_data_resource(session=session, context=context, kind="document", resource_id=file_id)
                for key, kind in (("knowledge_base_ids", "knowledge_base"), ("version_ids", "knowledge_version")):
                    for resource_id in inputs.get(key, []) if isinstance(inputs.get(key), list) else []:
                        if isinstance(resource_id, str):
                            authorize_data_resource(session=session, context=context, kind=kind, resource_id=resource_id)
            if node.get("type") == "database_query":
                table_id = inputs.get("table_id")
                if "data_resources" in agent and not isinstance(table_id, str):
                    raise AppError("NODE_UNAVAILABLE", "智能体动态数据表引用需运行器逐节点传递配置范围，当前请显式选择数据表", 503)
                if isinstance(table_id, str):
                    authorize_data_resource(session=session, context=context, kind="data_table", resource_id=table_id,
                                            agent_scope=scope(agent) if "data_resources" in agent else None)
                    version_id = selected_version(agent, table_id)
                    if version_id and config.get("table_version_id") != version_id:
                        raise AppError("INVALID_BINDING", "主流程的数据表版本不在智能体固定版本范围内", 422)
            message = inputs.get("message")
            if isinstance(message, dict) and "$ref" not in message:
                for attachment_id in message.get("attachment_ids", []):
                    if isinstance(attachment_id, str):
                        get_resource(session, "attachment", attachment_id, context)
            for artifact_id in inputs.get("files", []) if isinstance(inputs.get("files"), list) else []:
                if isinstance(artifact_id, str):
                    get_resource(session, "artifact", artifact_id, context)
            if isinstance(config.get("body"), dict):
                walk(config["body"].get("nodes", []))
    walk(definition.model_dump()["nodes"])
    for resource in agent.get("knowledge_resources", []):
        if resource.get("enabled", True):
            kind = "knowledge_base" if resource["resource_id"].startswith("knowledge_base_") else "document"
            authorize_data_resource(session=session, context=context, kind=kind, resource_id=resource["resource_id"])
    return definition, list(bindings.values())


def workflow_snapshot(agent, context, session):
    workflow_id = agent.get("main_workflow_id")
    if not workflow_id:
        return None, []
    workflow = get_resource(session, "workflow", workflow_id, context)
    definition, bindings = validate_workflow_dependencies(workflow, agent, context, session)
    return deepcopy(definition.model_dump(mode="json")), bindings


def claim_workflow(agent_id, workflow_id, context, session):
    # Engineering owns the unique association and its transaction/migration.
    from app import db
    helper = getattr(db, "claim_agent_workflow", None)
    if helper is None:
        raise AppError("SERVICE_UNAVAILABLE", "主流程独占绑定服务尚未就绪", 503)
    return helper(session=session, agent_id=agent_id, workflow_id=workflow_id, context=context)
