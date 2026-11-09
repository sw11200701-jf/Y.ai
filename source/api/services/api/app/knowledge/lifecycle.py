"""Reversible removal; never purge original files, versions or execution evidence."""
from sqlalchemy import select, func
from app.db import Resource, utc_now
from app.errors import AppError
from . import service as s

KINDS = {'bases': 'knowledge_base', 'documents': 'knowledge_document', 'datasets': 'knowledge_dataset'}


def visible_libraries(session, context, values):
    ids = set(session.scalars(select(Resource.resource_id).where(Resource.kind == 'knowledge_base',
        Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id,
        func.coalesce(func.json_extract(Resource.payload, '$.status'), '') != 'deleted')))
    return [v for v in values if v['id'] in ids]


def _target(kind, identifier, context, session):
    s.require_knowledge(context, session, True)
    value = s.get_resource(session, kind, identifier, context, access='write')
    s.require_full_resource_maintenance(kind, identifier, context, session)
    if kind != 'knowledge_base':
        s.library(value['knowledge_base_id'], context, session, write=True)
    return value


def _no_processing(kind, value, context, session):
    # Metadata-only SQL: no model input/response/credentials are materialized.
    from app.execution.models import ExecutionBlob
    document_ids = [value['id']] if kind == 'knowledge_document' else list(session.scalars(
        select(Resource.resource_id).where(Resource.kind == 'knowledge_document',
            Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id,
            func.json_extract(Resource.payload, '$.knowledge_base_id') == value['id']))) if kind == 'knowledge_base' else []
    if not document_ids:
        return
    active = session.scalar(select(Resource.resource_id).join(ExecutionBlob,
        ExecutionBlob.id == func.json_extract(Resource.payload, '$.semantic_job.plan_ref')).where(
        Resource.kind == 'run', Resource.tenant_id == context.tenant_id,
        Resource.workspace_id == context.workspace_id,
        func.json_extract(Resource.payload, '$.status').in_(('queued','running','resuming','waiting_approval','paused')),
        func.json_extract(ExecutionBlob.payload, '$.plan.document_id').in_(document_ids)).limit(1))
    if active:
        raise AppError('KNOWLEDGE_PROCESSING', '资料仍在处理中，请结束处理后再删除', 409)


async def remove(kind, identifier, *, context, session):
    from app.platform_api import write_lock
    write_lock(session)
    value = _target(kind, identifier, context, session)
    if value.get('status') == 'deleted':
        return s.public(value)
    _no_processing(kind, value, context, session)
    value.update(deletion={'previous_status': value.get('status'), 'deleted_at': utc_now()},
        status='deleted', updated_at=utc_now())
    s.save_resource(session, kind, identifier, value, context)
    return s.public(value)


async def restore(kind, identifier, *, context, session):
    from app.platform_api import write_lock
    write_lock(session)
    value = _target(kind, identifier, context, session)
    if value.get('status') != 'deleted':
        raise AppError('CONFLICT', '此资源不在回收站', 409)
    # Restoring published content must not silently make it retrievable again.
    previous = (value.get('deletion') or {}).get('previous_status')
    status = 'active' if kind == 'knowledge_base' else 'draft' if value.get('published_version_id') or previous in ('published','disabled') or kind == 'knowledge_dataset' else 'uploaded'
    value.update(status=status, updated_at=utc_now(), restored_at=utc_now())
    value.pop('deletion', None)
    s.save_resource(session, kind, identifier, value, context)
    return s.public(value)


def recycle_bin(*, context, session, kb_id=None):
    s.require_knowledge(context, session, True)
    if kb_id:
        s.library(kb_id, context, session)
    result = []
    for kind in (('knowledge_document','knowledge_dataset') if kb_id else ('knowledge_base',)):
        query = select(Resource.resource_id).where(Resource.kind == kind,
            Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id,
            func.json_extract(Resource.payload, '$.status') == 'deleted')
        if kb_id:
            query = query.where(func.json_extract(Resource.payload, '$.knowledge_base_id') == kb_id)
        for identifier in session.scalars(query):
            try:
                value = _target(kind, identifier, context, session)
            except AppError as exc:
                if exc.status_code in (403,404):
                    continue
                raise
            result.append({k: value[k] for k in ('id','name','filename','knowledge_base_id','deletion','updated_at') if k in value} | {'kind':kind})
    return {'items':result}
