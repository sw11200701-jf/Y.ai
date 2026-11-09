"""Alias-only directory adaptation. Never mutates a historical business payload or ACL."""
import hashlib
from sqlalchemy import select
from app.db import Resource
from app.auth_models import ResourceGrant
from app.data_models import DataTableBinding
from app.data_access import register_legacy_table_binding
from app.errors import AppError
from .data_store import decision, list_resources


def binding_for(table_id, context, session):
    table = decision(session, "knowledge_dataset", table_id, context).resource
    binding = session.get(DataTableBinding, table.id)
    database = session.get(Resource, binding.database_pk) if binding else None
    if database is None or (database.tenant_id, database.workspace_id) != (context.tenant_id, context.workspace_id):
        raise AppError("NOT_FOUND", "资源不存在或无权访问", 404)
    return {"database_id": database.resource_id}


def visible_legacy_tables(context, session):
    from . import service as legacy
    result = []
    for value in list_resources(session, "data_table", context):
        if "knowledge_base_id" not in value:
            continue
        try:
            legacy.entry("knowledge_dataset", value["id"], context, session)
            if value.get("status") in ("disabled", "deleted"):
                continue
            result.append({**value, **binding_for(value["id"], context, session),
                "logical_kind": "data_table", "legacy_kind": "knowledge_dataset"})
        except AppError as exc:
            if exc.status_code != 404:
                raise
    return result


def preserved_snapshot(session, context):
    resources = list(session.scalars(select(Resource).where(Resource.tenant_id == context.tenant_id,
        Resource.workspace_id == context.workspace_id).order_by(Resource.id)))
    pks = [r.id for r in resources]
    grants = list(session.scalars(select(ResourceGrant).where(ResourceGrant.resource_pk.in_(pks)).order_by(ResourceGrant.id)))
    return {"resources": [(r.id, r.kind, r.resource_id, r.owner_id, hashlib.sha256(r.payload.encode()).hexdigest()) for r in resources],
        "legacy_grants": [(g.id, g.resource_pk, g.user_id, g.permission) for g in grants]}


def bind_legacy_table(*, dataset_id, database_id, context, session):
    """Explicit operator action; caller supplies an already-backed-up temporary/session copy."""
    from . import service as legacy
    legacy.entry("knowledge_dataset", dataset_id, context, session, write=True)
    decision(session, "database", database_id, context, "manage")
    versions = [v["id"] for v in list_resources(session, "knowledge_version", context)
        if v.get("resource_kind") == "knowledge_dataset" and v.get("resource_id") == dataset_id]
    before = preserved_snapshot(session, context)
    with session.begin_nested():
        result = register_legacy_table_binding(session=session, context=context, knowledge_dataset_id=dataset_id,
            database_id=database_id, version_ids=versions)
        after = preserved_snapshot(session, context)
        if before != after:
            raise AppError("LEGACY_PRESERVATION_FAILED", "历史资源、授权或快照校验变化，已撤销本次绑定", 409)
    return result | {"preservation_verified": True, "resource_count": len(before["resources"]),
        "legacy_grant_count": len(before["legacy_grants"])}


def refresh_bound_versions(dataset_id, context, session):
    resource = decision(session, "knowledge_dataset", dataset_id, context).resource
    binding = session.get(DataTableBinding, resource.id)
    if binding is None:
        return
    database = session.get(Resource, binding.database_pk)
    versions = [v["id"] for v in list_resources(session, "knowledge_version", context)
        if v.get("resource_kind") == "knowledge_dataset" and v.get("resource_id") == dataset_id]
    register_legacy_table_binding(session=session, context=context, knowledge_dataset_id=dataset_id,
        database_id=database.resource_id, version_ids=versions)
