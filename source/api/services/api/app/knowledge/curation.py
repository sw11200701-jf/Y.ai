"""Human revisions preserve generated parents and carry their own audit witness."""
from copy import deepcopy
import json
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from app.db import Resource, OwnershipAudit, get_resource, utc_now, new_id
from app.errors import AppError

CONTRACT = 'human-knowledge-revision-v1'

def lock_document(session, context, document_id):
    # Acquire the SQLite write lock before reading the current head. Concurrent
    # editors cannot both fork a stale head and silently replace each other.
    try:
        session.execute(update(Resource).where(Resource.kind == 'knowledge_document',
            Resource.resource_id == document_id, Resource.tenant_id == context.tenant_id,
            Resource.workspace_id == context.workspace_id).values(updated_at=Resource.updated_at))
        session.expire_all()
    except OperationalError as exc:
        if 'locked' not in str(exc).lower(): raise
        raise AppError('KNOWLEDGE_REVISION_CONFLICT', '知识正在更新，请刷新后保留并合并修改', 409) from None

def snapshot(version):
    from .service import digest
    return digest({k:v for k,v in version.items() if k not in ('status','published_at')})

def verify(version, *, context, session):
    """Validate immutable human revision witnesses, then the generated origin."""
    from . import service as s
    current = version
    seen = set()
    for _ in range(1000):
        if current['id'] in seen: break
        seen.add(current['id'])
        marker = current.get('human_revision')
        if marker is None:
            if current.get('portable_result'):
                from .slicing_retrieval import verified_published_version
                if not verified_published_version({**current,'status':'published'}, context=context, session=session): break
            return True
        if marker.get('contract') != CONTRACT: break
        records = list(session.scalars(select(OwnershipAudit).where(
            OwnershipAudit.action == 'knowledge_revision', OwnershipAudit.resource_kind == 'knowledge_version',
            OwnershipAudit.resource_id == current['id'])))
        if len(records) != 1: break
        record = records[0]; receipt = json.loads(record.details)
        if (receipt.get('actor') != record.actor_id or receipt.get('tenant_id') != context.tenant_id
                or receipt.get('workspace_id') != context.workspace_id
                or receipt.get('snapshot_sha256') != snapshot(current)
                or marker.get('author_id') != record.actor_id
                or s.digest(current['chunks']) != current['content_sha256']): break
        parent = get_resource(session, 'knowledge_version', current['parent_version_id'], context)
        if (snapshot(parent) != receipt.get('parent_sha256') or any(parent.get(k) != current.get(k)
                for k in ('resource_id','resource_kind','knowledge_base_id','file_id','source_sha256'))): break
        current = parent
    raise AppError('KNOWLEDGE_REVISION_INVALID', '知识修订或原始生成凭据校验失败，未使用或导出', 409)

async def revise(*, document_id, corrections, context, session, base_version_id=None, additions=(), deletions=()):
    from . import service as s
    from app.rbac import audit
    root = s.entry('knowledge_document', document_id, context, session, write=True)
    s.require_full_document_maintenance(root, context, session)
    lock_document(session, context, document_id)
    root = s.entry('knowledge_document', document_id, context, session, write=True)
    head = root.get('draft_version_id') or root.get('published_version_id')
    if not head: raise AppError('VALIDATION_ERROR', '请先生成知识版本', 422)
    if base_version_id != head:
        raise AppError('KNOWLEDGE_REVISION_CONFLICT', '当前知识已更新；请读取最新版本后合并修改，未覆盖已保存知识', 409)
    previous = s.checked_version(head, root, context, session, write=True)
    if previous["file_id"] != root["file_id"]:
        raise AppError('KNOWLEDGE_SOURCE_REPLACED', '原件已更换，请先处理新原件再修订知识', 409)
    verify(previous, context=context, session=session)
    chunks = deepcopy(previous['chunks']); by_id = {c['id']:c for c in chunks}; seen = set()
    for item in corrections:
        cid = item['chunk_id']
        if cid in seen or cid not in by_id or cid in deletions:
            raise AppError('VALIDATION_ERROR', '修订切片重复、已删除或不属于当前版本', 422)
        seen.add(cid); chunk = by_id[cid]
        for key in ('text','enabled'):
            if item.get(key) is not None: chunk[key] = item[key]
        if item.get('text') is not None:
            if not item['text'].strip(): raise AppError('VALIDATION_ERROR', '知识正文不可为空白', 422)
            # Never retain a stale machine summary as though it described new text.
            chunk['summary'] = ''
            chunk.pop('semantic_checks', None)
            chunk['content_origin'] = 'human_revision'
            if 'content' in chunk: chunk['content'] = chunk['text']
        chunk.update(revised=True, original_chunk_id=cid)
    if len(set(deletions)) != len(deletions) or any(cid not in by_id for cid in deletions):
        raise AppError('VALIDATION_ERROR', '删除切片重复或不属于当前版本', 422)
    chunks = [c for c in chunks if c['id'] not in deletions]
    for c in chunks:
        c.setdefault('knowledge_key', c.get('knowledge_id') or c['id'])
    for item in additions:
        if not item['text'].strip(): raise AppError('VALIDATION_ERROR', '新增知识正文不可为空白', 422)
        basis = by_id.get(item['source_chunk_id'])
        if basis is None: raise AppError('VALIDATION_ERROR', '新增知识须选择当前版本的来源切片', 422)
        c = deepcopy(basis)
        c.update(text=item['text'], topic=item.get('topic',''), summary='', enabled=item.get('enabled',True),
            revised=True, original_chunk_id=basis['id'], knowledge_key=new_id('knowledge'))
        c.pop('semantic_checks', None)
        c['content_origin'] = 'human_revision'
        c.pop('knowledge_id', None)
        if 'content' in c: c['content'] = c['text']
        chunks.append(c)
    if not chunks: raise AppError('VALIDATION_ERROR', '请至少保留一条知识；整份资料可停用或移入回收站', 422)
    if len(chunks) > 5000: raise AppError('VALIDATION_ERROR', '知识数量超过5000条', 422)
    changes = {'operation':'human_revision','corrections':corrections,'additions':list(additions),'deletions':list(deletions),
        'chunking':{'strategy':'human_curated','contract':CONTRACT}}
    result = s.make_document_version(root, chunks, head, context, session, changes, previous.get('warnings',[]))
    version = get_resource(session, 'knowledge_version', result['id'], context)
    if previous.get('portable_registry'):
        version['portable_registry'] = deepcopy(previous['portable_registry'])
    version['human_revision'] = {'contract':CONTRACT,'author_id':context.operator_id,'created_at':utc_now()}
    s.save_resource(session, 'knowledge_version', version['id'], version, context)
    audit(session, context, 'knowledge_revision', 'knowledge_version', version['id'], {
        'tenant_id':context.tenant_id,'workspace_id':context.workspace_id,'actor':context.operator_id,
        'parent_sha256':snapshot(previous),'snapshot_sha256':snapshot(version)})
    session.flush()
    return s.public(version) | {'preview':version['chunks'][:20], 'requires_publish':True,
        'message':'已保存为最新知识版本；发布后用于检索，默认导出包含本次修改'}

def latest_export(*, document_id, expected_version_id, context, session, columns=None):
    from . import service as s
    from .customer_export import export_customer
    # A write lock gives the export one consistent head, including concurrent
    # writes on another connection. It does not change knowledge or publication.
    root = s.entry('knowledge_document', document_id, context, session)
    lock_document(session, context, document_id)
    root = s.entry('knowledge_document', document_id, context, session)
    head = root.get('draft_version_id') or root.get('published_version_id')
    if not head: raise AppError('VALIDATION_ERROR', '暂无可导出的知识', 422)
    if expected_version_id != head:
        raise AppError('KNOWLEDGE_REVISION_CONFLICT', '知识已有更新，请刷新最新知识后导出', 409)
    return export_customer(document_id=document_id, version_id=head, context=context, session=session, columns=columns)
