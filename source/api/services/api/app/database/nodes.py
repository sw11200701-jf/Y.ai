from jsonschema import Draft202012Validator
from app.errors import AppError
from app.knowledge.database_service import query_table, root


QUERY = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "select": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 100},
        "filters": {"type": "array", "maxItems": 20, "items": {"type": "object", "additionalProperties": False,
            "required": ["field"], "properties": {"field": {"type": "string"}, "op": {"enum": ["eq", "ne", "in", "gt", "gte", "lt", "lte"]}, "value": {}}}},
        "group_by": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "aggregates": {"type": "array", "maxItems": 16, "items": {"type": "object", "additionalProperties": False,
            "required": ["op", "name"], "properties": {"op": {"enum": ["count", "sum", "min", "max", "avg"]}, "field": {"type": ["string", "null"]}, "name": {"type": "string"}}}},
        "order_by": {"type": "array", "maxItems": 8, "items": {"type": "object", "additionalProperties": False, "required": ["field"],
            "properties": {"field": {"type": "string"}, "descending": {"type": "boolean"}}}},
        "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000}}
}

NODE_SCHEMAS = {"database_query": {
    "config_schema": {"type": "object", "additionalProperties": False, "properties": {
        "database_id": {"type": "string", "minLength": 1}, "table_version_id": {"type": "string", "minLength": 1}}},
    "input_schema": {"type": "object", "additionalProperties": False, "required": ["table_id", "query"], "properties": {
        "table_id": {"type": "string", "minLength": 1}, "query": QUERY}},
    "output_schema": {"type": "object", "required": ["rows", "columns", "total", "version_id", "policy_version", "source_refs"],
        "properties": {"rows": {"type": "array"}, "columns": {"type": "array"}, "total": {"type": "integer"}, "version_id": {"type": "string"},
            "policy_version": {"type": "integer"}, "source_refs": {"type": "array", "items": {"type": "string"}}}},
    "status": "supported", "reason": "发布版本、调用者行列权限与白名单结构化查询；不执行SQL"}}


async def execute_node(*, node_type, config, inputs, context, session, run_id):
    schema = NODE_SCHEMAS.get(node_type)
    if schema is None:
        raise AppError("NODE_UNAVAILABLE", "当前数据库节点未开放", 422)
    for data, key in ((config, "config_schema"), (inputs, "input_schema")):
        if not Draft202012Validator(schema[key]).is_valid(data):
            raise AppError("VALIDATION_ERROR", "数据库查询节点输入或配置无效；仅支持声明字段的结构化查询", 422)
    table = root("data_table", inputs["table_id"], context, session)
    if config.get("database_id") and config["database_id"] != table["database_id"]:
        raise AppError("NOT_FOUND", "资源不存在或无权访问", 404)
    result = await query_table(table_id=inputs["table_id"], version_id=config.get("table_version_id"),
        query=inputs["query"], context=context, session=session)
    # Runner binds table + resolved version itself; add only extra real sources here.
    result["data_sources"] = [source for source in result.get("data_sources", [])
        if source["kind"] not in ("data_table", "data_table_version")]
    return {"outputs": result, "source_refs": result["source_refs"], "warnings": []}
