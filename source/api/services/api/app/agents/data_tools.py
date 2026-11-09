"""Agent scopes narrow central caller authorization; they never grant access."""

from pydantic import ValidationError

from app.errors import AppError
from app.plugins.validation import details
from .specs import StrictModel
from pydantic import Field


def scope(config):
    return [{"kind": r["kind"], "resource_id": r["resource_id"]}
            for r in config.get("data_resources", []) if r.get("enabled", True)]


def authorize_scope(config, context, session):
    from app.data_access import authorize_data_resource
    for item in config.get("knowledge_resources", []):
        if item.get("enabled", True):
            kind = "knowledge_base" if item["resource_id"].startswith("knowledge_base_") else "document"
            authorize_data_resource(session=session, context=context, kind=kind, resource_id=item["resource_id"])
    for item in config.get("data_resources", []):
        if not item.get("enabled", True):
            continue
        authorize_data_resource(session=session, context=context, kind=item["kind"], resource_id=item["resource_id"])
        if item.get("version_id"):
            data_dictionary(config=config, table_id=item["resource_id"], context=context, session=session)


def selected_version(config, table_id):
    selected = [item for item in config.get("data_resources", [])
                if item.get("enabled", True) and item["kind"] == "data_table" and item["resource_id"] == table_id]
    return selected[0].get("version_id") if selected else None


def _parent_version(*, config, table_id, parent_scope, context, session):
    version = selected_version(config, table_id)
    if parent_scope is not None:
        from app.data_access import authorize_data_resource
        authorize_data_resource(session=session, context=context, kind="data_table", resource_id=table_id, agent_scope=parent_scope)
        fixed = {item["version_id"] for item in parent_scope if item.get("kind") == "data_table" and item.get("resource_id") == table_id and item.get("version_id")}
        if len(fixed) > 1 or version and fixed and version not in fixed:
            raise AppError("INVALID_BINDING", "子智能体与父运行的数据表固定版本不一致", 422)
        version = version or next(iter(fixed), None)
    return version


def data_dictionary(*, config, table_id, context, session, parent_scope=None):
    from app.data_access import authorize_data_resource
    from app.knowledge.database_service import dictionary
    authorize_data_resource(session=session, context=context, kind="data_table", resource_id=table_id,
                            agent_scope=scope(config))
    version_id = _parent_version(config=config, table_id=table_id, parent_scope=parent_scope, context=context, session=session)
    result = dictionary(table_id=table_id, version_id=version_id, context=context, session=session, agent_scope=scope(config))
    result.setdefault("data_sources", [{"kind": "data_table", "resource_id": table_id, "action": "use"},
                                        {"kind": "data_table_version", "resource_id": result["version_id"], "action": "use"}])
    return result


def data_catalog(*, config, context, session, parent_scope=None):
    from app.knowledge.database_service import tables, root, table_metadata
    from app.data_access import authorize_data_resource
    visible = {}
    for item in scope(config):
        authorize_data_resource(session=session, context=context, kind=item["kind"], resource_id=item["resource_id"])
        candidates = (tables(database_id=item["resource_id"], context=context, session=session)["items"]
                      if item["kind"] == "database" else
                      [table_metadata(root("data_table", item["resource_id"], context, session), context, session)])
        for table in candidates:
            try:
                _parent_version(config=config, table_id=table["id"], parent_scope=parent_scope, context=context, session=session)
            except AppError as exc:
                if exc.status_code == 404:
                    continue
                raise
            visible[table["id"]] = {key: table[key] for key in ("id", "name", "status")}
            if "description" in table:
                #04 only supplies an overview when the caller's scope permits it.
                visible[table["id"]]["description"] = table["description"]
    return {"tables": list(visible.values()), "data_sources": [{"kind": "data_table", "resource_id": key, "action": "use"} for key in visible]}


async def data_query(*, config, table_id, query, context, session, parent_scope=None):
    from app.knowledge.database_service import query_table
    from app.data_access import ControlledQuery
    try:
        request = ControlledQuery.model_validate(query)
    except ValidationError as exc:
        raise AppError("VALIDATION_ERROR", "数据查询仅接受受控字段与查询条件", 422, details(exc.errors())) from exc
    version_id = _parent_version(config=config, table_id=table_id, parent_scope=parent_scope, context=context, session=session)
    return await query_table(table_id=table_id, version_id=version_id, query=request.model_dump(),
                             context=context, session=session, agent_scope=scope(config))


class DataCall(StrictModel):
    table_id: str = Field(min_length=1, max_length=100)
    query: dict = Field(default_factory=dict)


class DictionaryCall(StrictModel):
    table_id: str = Field(min_length=1, max_length=100)


class KnowledgeCall(StrictModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)


class CatalogCall(StrictModel):
    pass


async def retrieve_knowledge(*, config, query, context, session, top_k=5, parent_knowledge_scope=None, knowledge_pins=None):
    if knowledge_pins is None and getattr(context,'principal_type','human')=='service':
        knowledge_pins=config.get('_application_knowledge_pins')
        if knowledge_pins is None:raise AppError('APP_DEPENDENCY_CHANGED','应用知识范围缺少发布版本固定记录',409)
    selected = [r["resource_id"] for r in config.get("knowledge_resources", []) if r.get("enabled", True)]
    if parent_knowledge_scope is not None:
        from app.data_access import authorize_data_resource
        permitted = []
        for resource_id in selected:
            try:
                authorize_data_resource(session=session, context=context,
                    kind="knowledge_base" if resource_id.startswith("knowledge_base_") else "document",
                    resource_id=resource_id, agent_scope=parent_knowledge_scope)
                permitted.append(resource_id)
            except AppError as exc:
                if exc.status_code != 404:
                    raise
        selected = permitted
    if not selected:
        raise AppError("INVALID_BINDING", "智能体未配置知识检索范围", 422)
    files = [rid for rid in selected if not rid.startswith("knowledge_base_")]
    libraries = [rid for rid in selected if rid.startswith("knowledge_base_")]
    results = []
    if files:
        if knowledge_pins is None:
            from app.imports.documents import search_documents
            results.append(await search_documents(query=query, file_ids=files, top_k=top_k, context=context, session=session,
                                                 agent_scope=[{"kind": "document", "resource_id": rid} for rid in files]))
        else:
            from app.knowledge.service import search_knowledge
            selected_pins = [pin for pin in knowledge_pins if pin["file_id"] in files]
            if selected_pins:
                results.append(await search_knowledge(query=query, file_ids=files, knowledge_base_ids=list({p["knowledge_base_id"] for p in selected_pins}),
                    version_ids=[p["version_id"] for p in selected_pins], top_k=top_k, context=context, session=session,
                    agent_scope=[{"kind": "document", "resource_id": rid} for rid in files]))
    if libraries:
        from app.knowledge.service import search_knowledge
        versions = [pin["version_id"] for pin in knowledge_pins if pin["knowledge_base_id"] in libraries] if knowledge_pins is not None else None
        if knowledge_pins is None or versions:
            results.append(await search_knowledge(query=query, knowledge_base_ids=libraries, version_ids=versions, top_k=top_k, context=context, session=session,
                agent_scope=[{"kind": "knowledge_base", "resource_id": rid} for rid in libraries]))
    return {"method": "keyword_overlap_v1", **{key: [item for result in results for item in result.get(key, [])]
        for key in ("hits", "source_refs", "warnings", "used_versions", "used_chunks", "data_sources")}}


def tool_definitions(config):
    tools = []
    if any(r.get("enabled", True) for r in config.get("knowledge_resources", [])):
        tools.append({"type": "function", "function": {"name": "knowledge_retrieval", "description": "检索已配置知识库的授权已发布文档片段；关键词检索，不执行结构化统计。", "parameters": KnowledgeCall.model_json_schema()}})
    if scope(config):
        from app.data_access import ControlledQuery
        query_schema = ControlledQuery.model_json_schema()
        definitions = query_schema.pop("$defs", {})
        parameters = DataCall.model_json_schema()
        parameters["properties"]["query"] = query_schema
        if definitions:
            parameters["$defs"] = definitions
        tools.extend([
            {"type": "function", "function": {"name": "data_catalog", "description": "定位配置范围内且调用者有权使用的数据表，不返回未授权目录和原始数据。", "parameters": CatalogCall.model_json_schema()}},
            {"type": "function", "function": {"name": "data_query", "description": "在配置范围与真实调用者权限交集中查询或统计结构化表；禁止SQL，行列过滤由后端执行。", "parameters": parameters}},
            {"type": "function", "function": {"name": "data_dictionary", "description": "读取所选表真实已发布且经过权限过滤的字段字典与口径，辅助定位字段。", "parameters": DictionaryCall.model_json_schema()}},
        ])
    return tools


async def invoke(*, name, arguments, config, context, session, parent_scope=None, parent_knowledge_scope=None, knowledge_pins=None):
    models = {"data_query": DataCall, "data_dictionary": DictionaryCall, "data_catalog": CatalogCall, "knowledge_retrieval": KnowledgeCall}
    if name not in {item["function"]["name"] for item in tool_definitions(config)}:
        raise AppError("INVALID_BINDING", "智能体未配置所请求的数据或知识工具", 422)
    try:
        call = models[name].model_validate(arguments)
    except ValidationError as exc:
        raise AppError("VALIDATION_ERROR", "知识或数据工具参数无效", 422, details(exc.errors())) from exc
    if name == "data_query":
        return await data_query(config=config, context=context, session=session, parent_scope=parent_scope, **call.model_dump())
    if name == "data_dictionary":
        return data_dictionary(config=config, context=context, session=session, parent_scope=parent_scope, **call.model_dump())
    if name == "data_catalog":
        return data_catalog(config=config, context=context, session=session, parent_scope=parent_scope)
    return await retrieve_knowledge(config=config, context=context, session=session, parent_knowledge_scope=parent_knowledge_scope,
                                    knowledge_pins=knowledge_pins, **call.model_dump())
