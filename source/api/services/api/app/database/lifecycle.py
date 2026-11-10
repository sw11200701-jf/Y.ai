"""Reversible lifecycle for native databases and tables."""
from sqlalchemy import func, select

from app.data_models import DataTableBinding
from app.db import Resource, utc_now
from app.errors import AppError, register_error_messages
from app.platform_api import _row, _value, write_lock
from app.rbac import audit
from app.knowledge import database_service as s

INACTIVE = {"deleted", "disabled", "archived"}
DATA_KINDS = {"database", "data_table", "data_table_version", "data_batch", "data_file"}

register_error_messages({
    "DATA_REVISION_CONFLICT": "资源已更新，请重新读取后再提交",
    "DATA_RECYCLE_CONFLICT": "当前资源状态不允许此回收站操作",
    "DATA_LEGACY_BINDING": "历史表请通过原知识库管理。包含历史表的数据库不能移入回收站",
    "DATA_CHILD_SCOPE_DENIED": "删除或恢复数据库需要其现有数据表的完整维护权限",
})


def etag(value):
    return '"' + s.digest(value) + '"'


def check_etag(value, expected):
    if expected is None:
        raise AppError("PRECONDITION_REQUIRED", "请先读取资源并在 If-Match 中提交当前 ETag", 428)
    if expected != etag(value):
        raise AppError("DATA_REVISION_CONFLICT", "资源已更新，请重新读取后再提交", 412)


def _target(kind, identifier, context, session):
    s.require_page(context, session, True)
    value = s.get_resource(session, kind, identifier, context, access="write")
    if kind == "data_table":
        if "knowledge_base_id" in value:
            raise AppError("DATA_LEGACY_BINDING", "请通过原知识库管理历史表", 409)
        s.root("database", value["database_id"], context, session, True)
    s.require_full_maintenance(kind, identifier, context, session)
    return value


def _resources(kind, context):
    return select(Resource.resource_id).where(Resource.kind == kind,
        Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id)


def _check_children(database_id, context, session):
    database = s.decision(session, "database", database_id, context, "maintain").resource
    if session.scalar(select(DataTableBinding.resource_pk).where(
            DataTableBinding.database_pk == database.id).limit(1)) is not None:
        raise AppError("DATA_LEGACY_BINDING", "包含历史表的数据库不能移入回收站", 409)
    ids = session.scalars(_resources("data_table", context).where(
        func.json_extract(Resource.payload, "$.database_id") == database_id,
        ~func.json_extract(Resource.payload, "$.status").in_(INACTIVE)))
    for table_id in ids:
        try:
            s.require_full_maintenance("data_table", table_id, context, session)
        except AppError as exc:
            if exc.status_code not in (403, 404):
                raise
            raise AppError("DATA_CHILD_SCOPE_DENIED", "需要库中各有效表的完整维护权限", 403) from exc


def _receipt(kind, value):
    fields = ("id", "name", "database_id", "status", "deletion", "updated_at")
    return {k: value[k] for k in fields if k in value} | {"kind": kind, "etag": etag(value)}


def edit(kind, identifier, *, changes, expected, context, session):
    write_lock(session)
    value = _target(kind, identifier, context, session)
    if value.get("status") in INACTIVE:
        raise AppError("NOT_FOUND", "资源不存在或无权访问", 404)
    check_etag(value, expected)
    value.update(changes, updated_at=utc_now())
    s.save_resource(session, kind, identifier, value, context)
    audit(session, context, "data.edit", kind, identifier, {"fields": sorted(changes)})
    return value


def change(kind, identifier, *, restore, expected, context, session):
    write_lock(session)
    value = _target(kind, identifier, context, session)
    status = value.get("status")
    if not restore and status == "deleted" and value.get("deletion"):
        return _receipt(kind, value)
    if restore != (status == "deleted") or status in {"archived", "disabled"}:
        raise AppError("DATA_RECYCLE_CONFLICT", "当前资源状态不允许此回收站操作", 409)
    check_etag(value, expected)
    if kind == "database":
        _check_children(identifier, context, session)
    now = utc_now()
    if restore:
        marker = value.get("deletion") or {}
        previous = marker.get("previous_status")
        if previous not in ("active", "draft", "validated", "invalid", "published"):
            raise AppError("DATA_RECYCLE_CONFLICT", "无法核实删除前的状态", 409)
        value.update(status=previous, restored_at=now)
        value.pop("deletion")
    else:
        value.update(status="deleted", deletion={"previous_status": status, "deleted_at": now})
    value["updated_at"] = now
    s.save_resource(session, kind, identifier, value, context)
    audit(session, context, "data.restore" if restore else "data.recycle", kind, identifier)
    return _receipt(kind, value)


def recycle_bin(*, database_id, context, session):
    s.require_page(context, session, True)
    kind = "data_table" if database_id else "database"
    query = _resources(kind, context).where(
        func.json_extract(Resource.payload, "$.status") == "deleted").order_by(Resource.id.desc())
    if database_id:
        s.root("database", database_id, context, session, True)
        query = query.where(func.json_extract(Resource.payload, "$.database_id") == database_id)
    items = []
    for identifier in session.scalars(query):
        try:
            value = _target(kind, identifier, context, session)
        except AppError as exc:
            if exc.status_code in (403, 404):
                continue
            raise
        if value.get("deletion"):
            items.append(_receipt(kind, value))
    return items


def directory_live(session, row):
    if row.kind not in DATA_KINDS:
        return True
    value = _value(row)
    if value.get("status") in INACTIVE:
        return False
    for kind, field in (("database", "database_id"), ("data_table", "table_id")):
        if parent_id := value.get(field):
            parent = _row(session, kind, parent_id, row.tenant_id, row.workspace_id)
            if parent is not None and _value(parent).get("status") in INACTIVE:
                return False
    return True
