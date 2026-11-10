"""Persistent plugin registry and frozen runner integration."""

from copy import deepcopy

from pydantic import PrivateAttr, ValidationError
from sqlalchemy.orm import Session

from app.contracts import BusinessContext, FinancialFact, ToolBinding, ToolExecutionResult
from app.db import get_resource, list_resources, new_id, save_resource, utc_now
from app.errors import AppError
from app.secrets import read_secret

from .errors import PluginError
from .executor import execute_tool as execute_resolved_tool
from .specs import EndpointDraft, PluginDraft, ToolDraft
from .listing import paginate
from .permissions import require_page, require_tool_access


class _ExecutionResult(ToolExecutionResult):
    # REST preview is intentionally excluded from runner/model serialization.
    _response_preview: object = PrivateAttr(default=None)


def create_plugin(draft: PluginDraft, context: BusinessContext, session: Session) -> dict:
    require_page(context, session, "page.plugins")
    if draft.default_connection_id:
        get_resource(session, "connection", draft.default_connection_id, context)
    payload = {"id": new_id("plugin"), **draft.model_dump(), "connection_id": draft.default_connection_id, "status": "draft", "revision": 1, "created_at": utc_now(), "updated_at": utc_now()}
    save_resource(session, "plugin", payload["id"], payload, context)
    return payload


def update_plugin(plugin_id: str, draft: PluginDraft, context: BusinessContext, session: Session) -> dict:
    require_page(context, session, "page.plugins")
    old = get_resource(session, "plugin", plugin_id, context, access="write")
    if draft.default_connection_id:
        get_resource(session, "connection", draft.default_connection_id, context)
    if old.get("status") == "archived":
        raise AppError("CONFLICT", "已归档插件不能修改；已发布版本仍可执行", 409)
    payload = {**old, **draft.model_dump(), "connection_id": draft.default_connection_id, "status": "draft", "revision": old.get("revision", 0) + 1, "updated_at": utc_now()}
    save_resource(session, "plugin", plugin_id, payload, context)
    return payload


def publish_plugin(plugin_id: str, context: BusinessContext, session: Session) -> dict:
    require_page(context, session, "page.plugins")
    draft = get_resource(session, "plugin", plugin_id, context, access="write")
    if draft.get("status") == "archived":
        raise AppError("CONFLICT", "已归档插件不能发布", 409)
    validated = PluginDraft.model_validate({key: draft.get(key) for key in ("name", "description", "endpoints", "tools", "default_connection_id")})
    if validated.default_connection_id:
        get_resource(session, "connection", validated.default_connection_id, context)
    if not validated.tools:
        raise AppError("VALIDATION_ERROR", "至少配置一个工具后才能发布", status_code=422)
    versions = [v for v in list_resources(session, "plugin_version", context) if v["plugin_id"] == plugin_id]
    endpoints = {e.id: e.model_dump() for e in validated.endpoints}
    tools = [{**tool.model_dump(), "endpoint": deepcopy(endpoints[tool.endpoint_id]),
              "input_schema": deepcopy(endpoints[tool.endpoint_id]["input_schema"]),
              "output_schema": deepcopy(endpoints[tool.endpoint_id]["output_schema"])} for tool in validated.tools]
    payload = {"id": new_id("pv"), "plugin_id": plugin_id, "version": max((v["version"] for v in versions), default=0) + 1,
               "name": validated.name, "description": validated.description, "default_connection_id": validated.default_connection_id,
               "connection_id": validated.default_connection_id,
               "tools": tools, "endpoints": deepcopy(list(endpoints.values())),
               "status": "published", "created_at": utc_now()}
    save_resource(session, "plugin_version", payload["id"], payload, context)
    save_resource(session, "plugin", plugin_id, {**draft, "status": "published", "latest_version": payload["version"],
                                               "published_version_id": payload["id"], "updated_at": utc_now()}, context)
    return payload


def version_view(version: dict) -> dict:
    result = deepcopy(version)
    if "endpoints" not in result:
        result["endpoints"] = list({tool["endpoint"]["id"]: deepcopy(tool["endpoint"]) for tool in result["tools"]}.values())
    for endpoint in result["endpoints"]:
        endpoint.setdefault("operation_id", endpoint["id"])
    return result


def resolve_binding(binding: ToolBinding, context: BusinessContext, session: Session) -> dict:
    require_tool_access(context, session)
    import hashlib
    version = get_resource(session, "plugin_version", binding.plugin_version_id, context)
    connection_id = binding.connection_id or version.get("default_connection_id")
    if not connection_id:
        raise AppError("INVALID_BINDING", "插件版本尚未配置默认连接，请选择有权限的连接", 422)
    binding = binding.model_copy(update={"connection_id": connection_id})
    key = "binding_" + hashlib.sha256("\x00".join([binding.plugin_version_id, binding.tool_id, binding.connection_id]).encode()).hexdigest()[:24]
    grants = getattr(context, "allowed_binding_ids", [])
    if grants and key not in grants:
        raise AppError("CONNECTION_BLOCKED", "无权使用该工具绑定", status_code=403)
    connection = get_resource(session, "connection", binding.connection_id, context)
    tool = next((t for t in version["tools"] if t["id"] == binding.tool_id), None)
    if not tool or version.get("status") != "published" or not connection.get("enabled", True):
        raise AppError("INVALID_BINDING", "工具、版本或连接不可用", status_code=422)
    return {**binding.model_dump(), "endpoint": deepcopy(tool["endpoint"]),
            "connection": connection, "version_status": version["status"],
            "tool_name": tool["name"], "tool_description": tool["description"]}


def materialize_binding(selection, context, session) -> ToolBinding:
    data = selection.model_dump() if hasattr(selection, "model_dump") else dict(selection)
    version = get_resource(session, "plugin_version", data["plugin_version_id"], context)
    data["connection_id"] = data.get("connection_id") or version.get("default_connection_id")
    if not data["connection_id"]:
        raise AppError("INVALID_BINDING", "插件版本尚未配置默认连接，请选择有权限的连接", 422)
    binding = ToolBinding.model_validate(data)
    resolve_binding(binding, context, session)
    return binding


def execution_context(context: BusinessContext, session: Session) -> dict:
    from app.tool_egress import origins_for_context
    trusted = context.model_dump()
    # Re-read the workspace policy immediately before the authorized tool's send.
    return {"allowed_origins": origins_for_context(session, context),
            "allowed_entities": trusted.get("allowed_entities", ["SYNTHETIC-DEMO"]),
            "secret_resolver": read_secret,
            "tool_timeout_seconds": trusted.get("tool_timeout_seconds", 10),
            "max_output_bytes": trusted.get("max_output_bytes", 1048576)}


async def execute_tool(*, binding: ToolBinding, arguments: dict,
                       context: BusinessContext, session: Session) -> ToolExecutionResult:
    grants = getattr(context, "allowed_bindings", None)
    if grants is not None and binding.model_dump(include={"plugin_version_id", "tool_id", "connection_id"}) not in grants:
        raise AppError("CONNECTION_BLOCKED", "智能体无权调用该工具绑定", status_code=403)
    resolved = resolve_binding(binding, context, session)
    try:
        result = await execute_resolved_tool(resolved, arguments, execution_context(context, session))
    except PluginError as exc:
        raise AppError(exc.code, exc.message, status_code=exc.status_code, details={"audit": {
            "status": "failed", "plugin_version_id": binding.plugin_version_id,
            "tool_id": binding.tool_id, "connection_id": binding.connection_id, "error_code": exc.code}}) from exc
    return map_execution_result(result, arguments)


def map_execution_result(result: dict, arguments: dict) -> ToolExecutionResult:
    body = result["data"]
    raw_facts = body.get("facts", []) if isinstance(body, dict) else []
    facts = []
    try:
        for item in raw_facts:
            if not isinstance(item, dict) or item.get("synthetic", True) is not True:
                raise ValueError("only synthetic facts supported")
            if item.get("entity") != arguments.get("entity", "SYNTHETIC-DEMO"):
                raise ValueError("wrong entity")
            if "periods" in arguments and item.get("period") not in arguments["periods"]:
                raise ValueError("wrong period")
            facts.append(FinancialFact.model_validate({**item, "source_ref": result["source_ref"], "synthetic": True}))
    except (ValidationError, ValueError, TypeError) as exc:
        raise AppError("TOOL_ERROR", "接口财务事实无效或超出查询范围", status_code=502) from exc
    # Keep the frozen runner result shape. Visual preview is a REST-only alias
    # of mapped data, added by test routes rather than widening node outputs.
    output = _ExecutionResult(facts=facts, data=body, source_refs=[result["source_ref"]], audit=result["audit"])
    output._response_preview = result.get("response_preview", body)
    return output


async def test_draft(*, plugin_id: str, endpoint_id: str, connection_id: str, arguments: dict,
                     context: BusinessContext, session: Session) -> ToolExecutionResult:
    require_page(context, session, "page.plugins")
    """Admin-only pre-publication test, never exposed to a model's allowed tools."""
    plugin = get_resource(session, "plugin", plugin_id, context)
    connection_id = connection_id or plugin.get("default_connection_id")
    if not connection_id:
        raise AppError("INVALID_BINDING", "请先为插件选择默认服务连接", 422)
    endpoint = next((e for e in plugin.get("endpoints", []) if e["id"] == endpoint_id), None)
    if endpoint is None:
        raise AppError("NOT_FOUND", "接口草稿不存在", 404)
    connection = get_resource(session, "connection", connection_id, context)
    resolved = {"plugin_version_id": "draft:" + plugin_id, "tool_id": endpoint_id,
                "connection_id": connection_id, "endpoint": endpoint, "connection": connection}
    try:
        result = await execute_resolved_tool(resolved, arguments, execution_context(context, session))
    except PluginError as exc:
        raise AppError(exc.code, exc.message, exc.status_code, details={"audit": {
            "status": "failed", "draft": True, "plugin_id": plugin_id,
            "endpoint_id": endpoint_id, "connection_id": connection_id, "error_code": exc.code}}) from exc
    output = map_execution_result(result, arguments)
    return output.model_copy(update={"draft": True})


def references(plugin_id: str, context: BusinessContext, session: Session) -> list[dict]:
    get_resource(session, "plugin", plugin_id, context)
    ids = {v["id"] for v in list_resources(session, "plugin_version", context) if v["plugin_id"] == plugin_id}
    refs = []
    def workflow_bindings(workflow):
        found = []
        for node in workflow.get("nodes", []):
            config = node.get("config", {})
            if isinstance(config.get("binding"), dict):
                found.append(config["binding"])
            if isinstance(config.get("body"), dict):
                found.extend(workflow_bindings(config["body"]))
        return found

    for kind in ("agent", "agent_version", "workflow", "run"):
        for resource in list_resources(session, kind, context):
            if kind == "agent":
                bindings = resource.get("bindings", [])
            elif kind == "agent_version":
                bindings = resource.get("config", {}).get("bindings", [])
            else:
                bindings = workflow_bindings(resource.get("workflow_snapshot", {}) if kind == "run" else resource)
            for binding in bindings:
                if binding.get("plugin_version_id") in ids:
                    refs.append({"kind": kind, "id": resource["id"], "name": resource.get("name", ""), "binding": binding})
    return refs


def list_plugins(*, context, session, page=1, page_size=20, q="", status="", connection_id=""):
    if connection_id:
        get_resource(session, "connection", connection_id, context)
    versions = list_resources(session, "plugin_version", context)
    items = []
    for plugin in list_resources(session, "plugin", context):
        if connection_id and plugin.get("default_connection_id") != connection_id:
            continue
        published = [v for v in versions if v["plugin_id"] == plugin["id"]]
        items.append({**plugin, "endpoint_count": len(plugin.get("endpoints", [])), "tool_count": len(plugin.get("tools", [])),
                      "status": plugin.get("status", "published" if published else "draft"),
                      "latest_version": max((v["version"] for v in published), default=0),
                      "reference_count": len(references(plugin["id"], context, session))})
    return paginate(items, page=page, page_size=page_size, q=q, status=status)


def edit_member(*, plugin_id, collection, member_id, value, context, session):
    require_page(context, session, "page.plugins")
    plugin = get_resource(session, "plugin", plugin_id, context)
    data = {key: deepcopy(plugin.get(key, [] if key in {"tools", "endpoints"} else ""))
            for key in ("name", "description", "endpoints", "tools")}
    data["default_connection_id"] = plugin.get("default_connection_id")
    members = data[collection]
    existing = next((item for item in members if item["id"] == member_id), None) if member_id else None
    if member_id and existing is None:
        raise AppError("NOT_FOUND", "接口或工具草稿不存在", 404)
    if value is None:
        if collection == "endpoints" and any(t["endpoint_id"] == member_id for t in data["tools"]):
            raise AppError("CONFLICT", "接口仍被草稿工具引用，请先删除或改绑工具；已发布版本不受影响", 409)
        data[collection] = [item for item in members if item["id"] != member_id]
    elif member_id:
        if value.id != member_id:
            raise AppError("CONFLICT", "接口或工具ID稳定，不能在更新时改名", 409)
        data[collection] = [value.model_dump() if item["id"] == member_id else item for item in members]
    else:
        if any(item["id"] == value.id for item in members):
            raise AppError("CONFLICT", "接口或工具ID已存在", 409)
        data[collection].append(value.model_dump())
    try:
        validated = PluginDraft.model_validate(data)
    except ValidationError as exc:
        raise AppError("VALIDATION_ERROR", "接口、operation_id或工具关联不合法", 422) from exc
    return update_plugin(plugin_id, validated, context, session)


def archive_plugin(plugin_id, context, session):
    require_page(context, session, "page.plugins")
    plugin = get_resource(session, "plugin", plugin_id, context, access="write")
    refs = references(plugin_id, context, session)
    save_resource(session, "plugin", plugin_id, {**plugin, "status": "archived", "updated_at": utc_now()}, context)
    return {"id": plugin_id, "status": "archived", "reference_count": len(refs),
            "reference_policy": "archived_preserves_published_versions", "references": refs}
