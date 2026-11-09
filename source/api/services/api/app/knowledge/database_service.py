"""Structured collections and immutable table revisions, independent of knowledge retrieval."""
import base64
import hashlib
import json
from dataclasses import replace
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import PurePath
from pydantic import ValidationError
from app.errors import AppError, register_error_messages
from app.db import new_id, utc_now
from app.data_access import filter_rows, ControlledQuery, _column_policy, _unrestricted
from app.rbac import require_permission, require_any_permission
from .data_store import get_resource, list_resources, save_resource, decision, link
from .database_schemas import DatabaseWrite, TableWrite, DataRevision
from .governance import parse_table, assess, corrected_rows, typed
from .execution_scope import scope_layers, require_sources_in_scope, table_version

register_error_messages({
    "DATA_SCOPE_WRITE_DENIED": "完整文件导入、解析或结构修订需要该资源的完整维护范围",
    "DATA_SCOPE_DOWNLOAD_DENIED": "原件包含范围外数据，请使用已授权明细导出",
    "DATA_FORMAT_UNSUPPORTED": "数据库建表仅支持CSV或Excel文件", "SOURCE_HASH_MISMATCH": "来源文件校验失败，请检查原文件",
    "SNAPSHOT_HASH_MISMATCH": "表版本快照校验失败", "QUERY_VALUE_INVALID": "筛选值与字段类型不一致",
    "QUERY_TYPE_INVALID": "此统计仅支持整数或金额字段", "DATA_SCOPE_SCHEMA_INVALID": "行范围字段未在受控表结构中声明",
    "DATA_POLICY_CHANGED": "导出期间授权已变化，请重新导出", "LEGACY_WRITE_REQUIRES_COMPAT": "历史表请使用兼容治理接口修订",
    "LEGACY_PRESERVATION_FAILED": "历史资源或授权校验变化，已撤销本次绑定",
})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def require_page(context, session, write=False):
    if write:
        require_permission(context, session, "page.database")
    else:
        require_any_permission(context, session, ["page.database", "page.workbench", "page.ai"])


def root(kind, resource_id, context, session, write=False):
    require_page(context, session, write)
    value = get_resource(session, kind, resource_id, context, access="write" if write else "read")
    if kind == "data_table" and "knowledge_base_id" in value:
        from . import service as legacy
        from .legacy_tables import binding_for
        legacy.entry("knowledge_dataset", resource_id, context, session, write=write)
        binding = binding_for(resource_id, context, session)
        return {**value, "database_id": binding["database_id"], "logical_kind": "data_table", "legacy_kind": "knowledge_dataset"}
    if value.get("status") in ("deleted", "disabled"):
        raise AppError("NOT_FOUND", "资源不存在或无权访问", 404)
    if kind == "data_table":
        root("database", value["database_id"], context, session, write)
    return value


def require_full_maintenance(kind, resource_id, context, session):
    # Full-file/schema replacement cannot safely preserve a partial row or column grant.
    authorized = decision(session, kind, resource_id, context, "maintain")
    if not _unrestricted(authorized):
        raise AppError("DATA_SCOPE_WRITE_DENIED", "完整文件导入和结构修订需要该资源的完整维护范围", 403)


async def create_database(*, definition, context, session):
    require_page(context, session, True)
    value = {"id": new_id("database"), **DatabaseWrite.model_validate(definition).model_dump(),
        "status": "active", "created_at": utc_now()}
    save_resource(session, "database", value["id"], value, context)
    return value


async def edit_database(*, database_id, definition, context, session):
    value = root("database", database_id, context, session, True)
    require_full_maintenance("database", database_id, context, session)
    value.update(DatabaseWrite.model_validate(definition).model_dump(), updated_at=utc_now())
    return save_resource(session, "database", database_id, value, context)


def databases(*, context, session):
    require_page(context, session)
    values = [database_metadata(v, context, session) for v in list_resources(session, "database", context) if v.get("status") == "active"]
    from .legacy_tables import visible_legacy_tables
    allowed_ids = {v["id"] for v in values}
    shared = [v for v in visible_legacy_tables(context, session) if v["database_id"] not in allowed_ids]
    if shared:
        values.append({"id": "authorized-data", "name": "已授权数据", "restricted": True,
            "tables": [{"id": v["id"], "name": v["name"], "logical_kind": "data_table", "legacy_kind": "knowledge_dataset"} for v in shared]})
    from app.resource_policy import merge_directory
    return {"items": merge_directory(session,context,"database",values)}


def database_metadata(value, context, session):
    if _unrestricted(decision(session, "database", value["id"], context)):
        return value
    return {k: value[k] for k in ("id", "name", "status", "created_at")}


async def create_table(*, database_id, definition, context, session):
    root("database", database_id, context, session, True)
    require_full_maintenance("database", database_id, context, session)
    value = {"id": new_id("data_table"), "database_id": database_id,
        **TableWrite.model_validate(definition).model_dump(), "status": "draft", "created_at": utc_now(),
        "published_version_id": None, "draft_batch_id": None, "relations": []}
    save_resource(session, "data_table", value["id"], value, context)
    link(session, context, "data_table", value["id"], "database", database_id)
    return value


def tables(*, database_id, context, session):
    from .legacy_tables import visible_legacy_tables
    if database_id == "authorized-data":
        visible = [table_metadata(v, context, session) | {"database_id": "authorized-data"} for v in visible_legacy_tables(context, session)
            if not _can_read_database(v["database_id"], context, session)]
        return {"database_id": database_id, "items": visible, "total": len(visible)}
    root("database", database_id, context, session)
    visible = []
    for value in list_resources(session, "data_table", context):
        if "knowledge_base_id" in value:
            continue
        if value.get("database_id") != database_id or value.get("status") in ("deleted", "disabled"):
            continue
        visible.append(table_metadata(value, context, session))
    visible.extend(table_metadata(v, context, session) for v in visible_legacy_tables(context, session) if v["database_id"] == database_id)
    return {"database_id": database_id, "items": visible, "total": len(visible)}


def infer_fields(parsed):
    fields = []
    for name in parsed["columns"]:
        if len(name) > 64 or any(ord(char) < 32 for char in name):
            raise AppError("MAPPING_REQUIRED", "来源表头过长或包含控制字符，请手动声明字段和映射", 422,
                {"field": name, "row": 1, "source": parsed["sheet_name"]})
        observed = [row["values"].get(name) for row in parsed["source_rows"] if row["values"].get(name) not in (None, "")]
        kind = "string"
        if observed:
            for candidate in ("boolean", "integer", "decimal", "date"):
                try:
                    for value in observed:
                        raw = str(value).lstrip("-")
                        if candidate in ("integer", "decimal") and len(raw) > 1 and raw.startswith("0") and not raw.startswith("0."):
                            raise ValueError("preserve leading zero identifiers")
                        typed(value, {"type": candidate, "max_length": 5000})
                    kind = candidate
                    break
                except (ValueError, TypeError):
                    continue
        fields.append({"name": name, "type": kind, "required": False, "meaning": "", "unit": "", "dimension": False, "caliber": ""})
    return fields


async def import_new_table(*, database_id, filename, content, context, session, name=None, sheet_name=None):
    root("database", database_id, context, session, True)
    require_full_maintenance("database", database_id, context, session)
    if PurePath(filename).suffix.lower() not in (".csv", ".xlsx"):
        raise AppError("DATA_FORMAT_UNSUPPORTED", "数据库建表仅支持CSV或Excel文件", 422)
    parsed = parse_table(filename, content, sheet_name)
    table = await create_table(database_id=database_id, definition={"name": name or PurePath(filename.replace("\\", "/")).stem[:120],
        "schema_fields": infer_fields(parsed)}, context=context, session=session)
    table["schema_origin"] = "deterministic_source_inference_v1"
    save_resource(session, "data_table", table["id"], table, context)
    batch = await import_table(table_id=table["id"], filename=filename, content=content, sheet_name=sheet_name, context=context, session=session)
    return {"table": table_metadata(root("data_table", table["id"], context, session), context, session), "batch": batch,
        "schema_fields": table["schema_fields"], "requires_publish": True, "schema_origin": table["schema_origin"]}


def table_metadata(value, context, session):
    result = {k: value[k] for k in ("id", "database_id", "name", "status", "published_version_id", "draft_batch_id", "created_at")}
    # Never expose draft schema, mapping or mixed-source summaries through a use grant.
    authorized = decision(session, "data_table", value["id"], context)
    if _unrestricted(authorized):
        result["description"] = value.get("description", "")
    if value.get("legacy_kind"):
        result.update(logical_kind="data_table", legacy_kind="knowledge_dataset")
    try:
        maintenance = decision(session, "data_table", value["id"], context, "maintain")
        if _unrestricted(maintenance):
            result.update({key: value[key] for key in ("schema_fields", "mapping", "quality_rules", "relations", "schema_origin") if key in value})
    except AppError as exc:
        if exc.status_code != 404:
            raise
    return result


def _can_read_database(database_id, context, session):
    try:
        decision(session, "database", database_id, context)
        return True
    except AppError as exc:
        if exc.status_code != 404:
            raise
        return False


def load_batch(batch_id, context, session, write=False):
    if batch_id.startswith("knowledge_batch_"):
        from . import service as legacy
        batch, old = legacy.get_batch(batch_id, context, session, write)
        table = root("data_table", old["id"], context, session, write)
        return {**batch, "table_id": old["id"], "database_id": table["database_id"], "legacy_kind": "knowledge_batch"}, table
    batch = get_resource(session, "data_batch", batch_id, context, access="write" if write else "read")
    table = root("data_table", batch["table_id"], context, session, write)
    if table["database_id"] != batch["database_id"]:
        raise AppError("NOT_FOUND", "资源不存在或无权访问", 404)
    return batch, table


def batch_result(batch, context, session):
    # Governance contains raw rows and cell errors, so require full maintenance before returning it.
    require_page(context, session, True)
    require_full_maintenance("knowledge_batch" if batch.get("legacy_kind") else "data_batch", batch["id"], context, session)
    return {k: v for k, v in batch.items() if k not in ("source_rows", "working_rows", "rows")} | {
        "original_preview": batch["source_rows"][:20], "working_preview": batch["working_rows"][:20], "preview": batch["rows"][:20]}


def make_batch(table, file, original, working, fields, mapping, rules, parent, corrections, cleaning, columns, sheet_name, context, session):
    assessed = assess(source_rows=working, fields=fields, mapping=mapping, quality_rules=rules, cleaning=cleaning)
    batch = {"id": new_id("data_batch"), "database_id": table["database_id"], "table_id": table["id"], "file_id": file["id"],
        "source_sha256": file["sha256"], "source_rows": deepcopy(original), "working_rows": deepcopy(working),
        "schema_fields": deepcopy(fields), "mapping": deepcopy(mapping), "quality_rules": deepcopy(rules),
        "parent_batch_id": parent, "corrections": corrections, "cleaning": cleaning, "columns": columns,
        "sheet_name": sheet_name, "published_version_id": None, "created_at": utc_now(), **assessed}
    save_resource(session, "data_batch", batch["id"], batch, context)
    link(session, context, "data_batch", batch["id"], "data_table", table["id"])
    table.update(draft_batch_id=batch["id"], updated_at=utc_now())
    if not table.get("published_version_id"):
        table["status"] = batch["status"]
    save_resource(session, "data_table", table["id"], table, context)
    return batch_result(batch, context, session)


async def import_table(*, table_id, filename, content, context, session, sheet_name=None):
    table = root("data_table", table_id, context, session, True)
    require_full_maintenance("data_table", table_id, context, session)
    if table.get("legacy_kind"):
        from . import service as legacy
        return await legacy.upload_batch(dataset_id=table_id, filename=filename, content=content, sheet_name=sheet_name,
            context=context, session=session)
    if PurePath(filename).suffix.lower() not in (".csv", ".xlsx"):
        raise AppError("DATA_FORMAT_UNSUPPORTED", "数据库建表仅支持CSV或Excel文件", 422)
    parsed = parse_table(filename, content, sheet_name)
    file = {"id": new_id("data_file"), "table_id": table_id, "database_id": table["database_id"],
        "filename": filename.replace("\\", "/").split("/")[-1][:150], "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"), "size_bytes": len(content), "created_at": utc_now()}
    save_resource(session, "data_file", file["id"], file, context)
    link(session, context, "data_file", file["id"], "data_table", table_id)
    return make_batch(table, file, parsed["source_rows"], parsed["source_rows"], table["schema_fields"], table["mapping"],
        table["quality_rules"], None, [], [], parsed["columns"], parsed["sheet_name"], context, session)


async def revise_batch(*, batch_id, revision, context, session):
    previous, table = load_batch(batch_id, context, session, True)
    if previous.get("legacy_kind"):
        from . import service as legacy
        return await legacy.revise_batch(batch_id=batch_id, revision=revision, context=context, session=session)
    require_full_maintenance("data_table", table["id"], context, session)
    require_full_maintenance("data_batch", batch_id, context, session)
    changes = DataRevision.model_validate(revision).model_dump(exclude_none=True)
    file = get_resource(session, "data_file", previous["file_id"], context)
    return make_batch(table, file, previous["source_rows"], corrected_rows(previous["working_rows"], changes["corrections"]),
        changes.get("schema_fields", previous["schema_fields"]), changes.get("mapping", previous["mapping"]),
        changes.get("quality_rules", previous["quality_rules"]), batch_id, changes["corrections"],
        changes["cleaning"] or previous["cleaning"], previous["columns"], previous["sheet_name"], context, session)


async def publish_batch(*, batch_id, context, session):
    batch, table = load_batch(batch_id, context, session, True)
    if batch.get("legacy_kind"):
        from . import service as legacy
        result = await legacy.publish_batch(batch_id=batch_id, context=context, session=session)
        return result | {"table_id": table["id"], "database_id": table["database_id"], "table_status": "published", "legacy_kind": "knowledge_version"}
    require_full_maintenance("data_table", table["id"], context, session)
    require_full_maintenance("data_batch", batch_id, context, session)
    if batch["errors"] or batch["status"] not in ("validated", "published"):
        raise AppError("QUALITY_FAILED", "错误批次不可发布，请按行号和字段修订", 422, {"errors": batch["errors"]})
    if batch.get("published_version_id"):
        version = get_resource(session, "data_table_version", batch["published_version_id"], context)
    else:
        file = get_resource(session, "data_file", batch["file_id"], context)
        if hashlib.sha256(base64.b64decode(file["content_base64"], validate=True)).hexdigest() != batch["source_sha256"]:
            raise AppError("SOURCE_HASH_MISMATCH", "导入来源校验失败", 409)
        version = {"id": new_id("data_table_version"), "table_id": table["id"], "database_id": table["database_id"],
            "batch_id": batch_id, "file_id": batch["file_id"], "source_sha256": batch["source_sha256"],
            "schema_fields": deepcopy(batch["schema_fields"]), "rows": deepcopy(batch["rows"]),
            "mapping": deepcopy(batch["mapping"]), "quality_rules": deepcopy(batch["quality_rules"]), "relations": [],
            "parent_version_id": table.get("published_version_id"), "status": "published", "published_at": utc_now()}
        version["content_sha256"] = digest({k: version[k] for k in ("rows", "schema_fields", "source_sha256", "mapping", "quality_rules", "relations")})
        save_resource(session, "data_table_version", version["id"], version, context)
        # New-model versions inherit the current parent policy without copying grants.
        link(session, context, "data_table_version", version["id"], "data_table", table["id"], True)
        batch.update(status="published", published_version_id=version["id"])
        save_resource(session, "data_batch", batch_id, batch, context)
    table.update(status="published", published_version_id=version["id"], last_published_batch_id=batch_id, updated_at=utc_now())
    save_resource(session, "data_table", table["id"], table, context)
    return {k: v for k, v in version.items() if k != "rows"} | {"table_status": "published", "dataset_id": table["id"], "dataset_status": "published"}


def checked_version(table, version_id, context, session, agent_scope=None, action="use"):
    vid = version_id or table.get("published_version_id")
    if not vid:
        raise AppError("NOT_PUBLISHED", "数据表尚未发布", 422)
    authorized = decision(session, "data_table_version", vid, context)
    if action != "use":
        extra = decision(session, "data_table_version", vid, context, action)
        authorized = replace(authorized, layers=authorized.layers + extra.layers)
    version = json.loads(authorized.resource.payload)
    if table.get("legacy_kind"):
        from . import service as legacy
        legacy_version = legacy.published_version(table, context, session, vid)
        # Historical resources have no new inheritance links. Keep every original source
        # policy as an AND layer, then let the central filter execute all row/column rules.
        sources = [("knowledge_base", table["knowledge_base_id"]), ("knowledge_dataset", table["id"]),
            ("knowledge_file", legacy_version["file_id"])]
        layers = list(authorized.layers)
        for kind, resource_id in sources:
            layers.extend(decision(session, kind, resource_id, context).layers)
            if action != "use":
                layers.extend(decision(session, kind, resource_id, context, action).layers)
        authorized = replace(authorized, layers=layers)
        return {**legacy_version, "table_id": table["id"], "database_id": table["database_id"]}, authorized
    if (version["table_id"], version["database_id"], version["status"]) != (table["id"], table["database_id"], "published"):
        raise AppError("NOT_FOUND", "资源不存在或无权访问", 404)
    source = decision(session, "data_file", version["file_id"], context)
    file = json.loads(source.resource.payload)
    if (file["table_id"], file["database_id"], file["sha256"]) != (table["id"], table["database_id"], version["source_sha256"]):
        raise AppError("SOURCE_HASH_MISMATCH", "版本与导入文件来源不一致", 409)
    authorized = replace(authorized, layers=authorized.layers + source.layers)
    if action != "use":
        authorized = replace(authorized, layers=authorized.layers + decision(session, "data_file", version["file_id"], context, action).layers)
    if digest({k: version[k] for k in ("rows", "schema_fields", "source_sha256", "mapping", "quality_rules", "relations")}) != version["content_sha256"]:
        raise AppError("SNAPSHOT_HASH_MISMATCH", "表版本快照校验失败", 409)
    return version, authorized


def normalize_query(query, fields):
    try:
        request = ControlledQuery.model_validate(query or {})
    except ValidationError:
        raise AppError("QUERY_INVALID", "仅接受白名单字段、条件、统计与分页；不支持SQL或表达式", 422) from None
    if request.group_by and not request.aggregates:
        raise AppError("QUERY_INVALID", "分组查询须同时指定统计项", 422)
    by_name = {f["name"]: f for f in fields}
    for condition in request.filters:
        field = by_name.get(condition.field)
        if field is None:
            raise AppError("QUERY_FIELD_DENIED", "筛选字段不存在或未授权", 422)
        values = condition.value if condition.op == "in" else [condition.value]
        if not isinstance(values, list) or len(values) > 100:
            raise AppError("QUERY_INVALID", "包含筛选需要有限值列表", 422)
        try:
            normalized = [typed(v, {**field, "required": False}) for v in values]
        except (ValueError, TypeError):
            raise AppError("QUERY_VALUE_INVALID", "筛选值与字段类型不一致", 422, {"field": condition.field}) from None
        if field["type"] == "decimal":
            normalized = [Decimal(v) if v is not None else None for v in normalized]
        condition.value = normalized if condition.op == "in" else normalized[0]
    for aggregate in request.aggregates:
        if aggregate.op != "count" and (aggregate.field not in by_name or by_name[aggregate.field]["type"] not in ("integer", "decimal")):
            raise AppError("QUERY_TYPE_INVALID", "此统计仅支持整数或金额字段", 422, {"field": aggregate.field})
    return request


def wire(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, list):
        return [wire(v) for v in value]
    return value


async def query_table(*, table_id, context, session, version_id=None, query=None, agent_scope=None, action="use"):
    table = root("data_table", table_id, context, session)
    layers = scope_layers(agent_scope)
    version_id = table_version(table, version_id, layers)
    chosen = version_id or table.get("published_version_id")
    require_sources_in_scope(sources=[("data_table", table_id)] + ([("data_table_version", chosen)] if chosen else []),
        layers=layers, context=context, session=session, version_id=chosen)
    version, authorized = checked_version(table, version_id, context, session, agent_scope, action)
    require_sources_in_scope(sources=[("data_table", table_id), ("data_table_version", version["id"])],
        layers=layers, context=context, session=session, version_id=version["id"])
    fields = [f["name"] for f in version["schema_fields"]]
    # Check column permissions before type errors could reveal a hidden field's schema.
    try:
        filter_rows(decision=authorized, rows=[], fields=fields, query=query or {})
    except ValidationError:
        raise AppError("QUERY_INVALID", "仅接受结构化白名单查询，不支持SQL或表达式", 422) from None
    request = normalize_query(query, version["schema_fields"])
    # Validate every policy's scope field against the actual schema before reading values.
    for layer in authorized.layers:
        for rule in layer["allows"] + layer["denies"] + layer["restricts"]:
            scope = rule["row_scope"]
            name = scope.get("field") or {"self": "owner_id", "department": "department_id", "companies": "company_id", "departments": "department_id"}.get(scope["template"])
            if name is not None and name not in fields:
                raise AppError("DATA_SCOPE_SCHEMA_INVALID", "行范围字段未在受控表结构中声明", 403)
    decimal_fields = {f["name"] for f in version["schema_fields"] if f["type"] == "decimal"}
    rows = [{k: Decimal(v) if k in decimal_fields and v is not None else v for k, v in r["values"].items()} for r in version["rows"]]
    # Central filter is the sole row/column policy executor, before query/pagination/aggregation.
    with localcontext() as arithmetic:
        # 38 integer + 18 fractional digits, up to 5000 rows; no default-28 rounding.
        arithmetic.prec = 80
        if request.aggregates and request.order_by:
            outputs = set(request.group_by) | {a.name for a in request.aggregates}
            if any(sort.field not in outputs for sort in request.order_by):
                raise AppError("QUERY_INVALID", "统计排序只能引用分组字段或统计输出", 422)
            full_query = request.model_dump() | {"order_by": [], "offset": 0, "limit": 1000}
            result = filter_rows(decision=authorized, rows=rows, fields=fields, query=full_query)
            all_results = list(result["rows"])
            for offset in range(1000, result["total"], 1000):
                all_results.extend(filter_rows(decision=authorized, rows=rows, fields=fields,
                    query=full_query | {"offset": offset})["rows"])
            aggregate_names = {a.name for a in request.aggregates if a.op != "count"}
            for sort in reversed(request.order_by):
                def key(row):
                    value = row.get(sort.field)
                    if sort.field in aggregate_names and value is not None:
                        value = Decimal(value)
                    return value is None, value
                all_results.sort(key=key, reverse=sort.descending)
            result["rows"] = all_results[request.offset:request.offset + request.limit]
        else:
            if request.order_by and request.select is not None and any(sort.field not in request.select for sort in request.order_by):
                # Central policy checks permission before the internal projection adds sort columns.
                augmented = list(dict.fromkeys(request.select + [s.field for s in request.order_by]))
                result = filter_rows(decision=authorized, rows=rows, fields=fields,
                    query=request.model_dump() | {"select": augmented})
                result["rows"] = [{f: row[f] for f in request.select} for row in result["rows"]]
            else:
                result = filter_rows(decision=authorized, rows=rows, fields=fields, query=request.model_dump())
    for row in result["rows"]:
        for aggregate in request.aggregates:
            if aggregate.op != "count" and row.get(aggregate.name) is not None:
                row[aggregate.name] = format(Decimal(row[aggregate.name]), "f")
    source = f"database:{table['database_id']}:table:{table_id}:version:{version['id']}:sha256:{version['source_sha256']}"
    allowed, masks = _column_policy(authorized, fields)
    columns = ([{"name": name, "type": next(f["type"] for f in version["schema_fields"] if f["name"] == name)} for name in request.group_by]
        + [{"name": a.name, "type": "integer" if a.op == "count" else "decimal"} for a in request.aggregates]) if request.aggregates else [
        ({"name": f["name"], "type": f["type"], "masked": True} if f["name"] in masks else f) for f in version["schema_fields"]
        if f["name"] in allowed and (request.select is None or f["name"] in request.select)]
    return wire({**result, "columns": columns, "database_id": table["database_id"], "table_id": table_id, "version_id": version["id"],
        "snapshot_id": version["content_sha256"], "source_sha256": version["source_sha256"], "source_refs": [source],
        "data_sources": [{"kind": "data_table", "resource_id": table_id, "action": "use"},
            {"kind": "data_table_version", "resource_id": version["id"], "action": "use"},
            {"kind": "knowledge_file" if table.get("legacy_kind") else "data_file", "resource_id": version["file_id"], "action": "use"}],
        "query": request.model_dump(), "method": "controlled_table_query_v1"})


def dictionary(*, table_id, context, session, version_id=None, agent_scope=None):
    table = root("data_table", table_id, context, session)
    layers = scope_layers(agent_scope)
    version_id = table_version(table, version_id, layers)
    chosen = version_id or table.get("published_version_id")
    require_sources_in_scope(sources=[("data_table", table_id)] + ([("data_table_version", chosen)] if chosen else []),
        layers=layers, context=context, session=session, version_id=chosen)
    version, authorized = checked_version(table, version_id, context, session)
    require_sources_in_scope(sources=[("data_table", table_id), ("data_table_version", version["id"])],
        layers=layers, context=context, session=session, version_id=version["id"])
    fields = [f["name"] for f in version["schema_fields"]]
    allowed, masks = _column_policy(authorized, fields)
    # Column restrictions apply to names and explanations too; no row examples or raw counts.
    return {"table_id": table_id, "database_id": table["database_id"] , "version_id": version["id"],
        "snapshot_id": version["content_sha256"], "policy_version": authorized.policy_revision,
        "schema_fields": [f for f in version["schema_fields"] if f["name"] in allowed and f["name"] not in masks],
        "relations": [], "schema_source": "published_version"}


def batches(*, table_id, context, session):
    table = root("data_table", table_id, context, session, True)
    require_full_maintenance("data_table", table_id, context, session)
    if table.get("legacy_kind"):
        from . import service as legacy
        values = legacy.batches(dataset_id=table_id, context=context, session=session)
        return {"table_id": table_id, "items": values, "total": len(values), "legacy_kind": "knowledge_batch"}
    values = []
    for v in list_resources(session, "data_batch", context):
        if v.get("table_id") == table_id:
            require_full_maintenance("data_batch", v["id"], context, session)
            values.append({k: val for k, val in v.items() if k not in ("rows", "source_rows", "working_rows")})
    return {"table_id": table_id, "items": values, "total": len(values)}


def versions(*, table_id, context, session):
    table = root("data_table", table_id, context, session)
    if table.get("legacy_kind"):
        from . import service as legacy
        values = legacy.versions(kind="knowledge_dataset", resource_id=table_id, context=context, session=session)
        return {"table_id": table_id, "items": [{"id": v["id"], "status": v["status"], "content_sha256": v["content_sha256"],
            "parent_version_id": v.get("parent_version_id"), "legacy_kind": "knowledge_version"} for v in values], "total": len(values)}
    values = []
    for v in list_resources(session, "data_table_version", context):
        if v.get("table_id") != table_id:
            continue
        checked_version(table, v["id"], context, session)
        values.append({k: v[k] for k in ("id", "table_id", "database_id", "status", "published_at", "content_sha256", "source_sha256", "parent_version_id")})
    return {"table_id": table_id, "items": values, "total": len(values)}


def download_original(*, file_id, context, session):
    file = get_resource(session, "data_file", file_id, context, action="download_original")
    root("data_table", file["table_id"], context, session)
    authorized = decision(session, "data_file", file_id, context, "download_original")
    # Whole original files cannot safely represent a partial row/column policy.
    if not _unrestricted(authorized) or not _unrestricted(decision(session, "data_table", file["table_id"], context)):
        raise AppError("DATA_SCOPE_DOWNLOAD_DENIED", "原件包含范围外数据，请使用已授权明细导出", 403)
    content = base64.b64decode(file["content_base64"], validate=True)
    if hashlib.sha256(content).hexdigest() != file["sha256"]:
        raise AppError("SOURCE_HASH_MISMATCH", "导入来源校验失败", 409)
    return file["filename"], content


async def export_detail(*, table_id, context, session, version_id=None):
    import csv
    from io import StringIO
    first = await query_table(table_id=table_id, version_id=version_id, query={"limit": 1000},
        context=context, session=session, action="export_detail")
    rows = list(first["rows"])
    for offset in range(1000, first["total"], 1000):
        page = await query_table(table_id=table_id, version_id=first["version_id"], query={"offset": offset, "limit": 1000},
            context=context, session=session, action="export_detail")
        if page["policy_version"] != first["policy_version"]:
            raise AppError("DATA_POLICY_CHANGED", "导出期间授权已变化，请重新导出", 409)
        rows.extend(page["rows"])
    fields = list(rows[0]) if rows else [f["name"] for f in first["columns"]]
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    # Spreadsheet formula injection is escaped only in the derived CSV, never in stored source rows.
    writer.writerows({key: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "@", "\t", "\r")) else value
        for key, value in row.items()} for row in rows)
    return stream.getvalue().encode("utf-8-sig"), first
