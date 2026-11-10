"""Knowledge API for scoped platform tokens. Domain services own content and authorization."""
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import Depends, File, Header, Path, Query, Request, Response, UploadFile
from pydantic import Field, model_validator

from app.db import get_session, utc_now
from app.errors import AppError
from app.imports.documents import upload_contract
from app.knowledge import service as s
from app.knowledge.schemas import DocumentRevision, LibraryWrite, ParseRequest, PublishRequest, SearchRequest
from app.knowledge.source_revision import check_etag, etag, replace_original
from app.platform_api import Strict, _put, _row, _value, platform_context, runtime, write_lock
from app.rbac import audit

BASE = '/spaces/{workspace_id}/knowledge-bases'
DOC = '/spaces/{workspace_id}/knowledge/documents/{document_id}'
EXTERNAL = BASE + '/{kb_id}/documents/by-external-id/{external_id}'
Profile = Literal['legacy', 'portable-slicing-1.0.0']
ExternalId = Annotated[str, Path(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')]
Expected = Annotated[str | None, Header(alias='If-Match', max_length=128)]
Offset = Annotated[int, Query(ge=0, le=5000)]
Limit = Annotated[int, Query(ge=1, le=100)]


class LibraryPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode='after')
    def changed(self):
        if self.name is None and self.description is None:
            raise ValueError('请提供知识库名称或说明')
        return self


class DocumentPatch(Strict):
    name: str = Field(min_length=1, max_length=150)


def context(request, session, workspace_id):
    value = platform_context(session, request.state.platform_token_id, workspace_id)
    request.state.business_context = value
    return value


def page(values, offset, limit):
    return {'items': values[offset:offset + limit], 'total': len(values), 'offset': offset, 'limit': limit}


def representation(kind, value, response, current, session):
    response.headers['ETag'] = etag(value)
    return s.public_entry(kind, value, current, session)


def document_result(root, response, current, session, **metadata):
    return {**representation('knowledge_document', root, response, current, session), **metadata}


def external_key(current, kb_id, external_id):
    return s.digest([current.tenant_id, current.workspace_id, kb_id, external_id])


def external_row(session, current, identifier):
    return _row(session, 'platform_knowledge_external_id', identifier, current.tenant_id, current.workspace_id)


async def contents(file, profile):
    limit = upload_contract(profile)['max_bytes']
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError('DOCUMENT_TOO_LARGE', '文件超过该处理方式的上传上限', 413, {'max_bytes': limit})
    return file.filename or 'upload.txt', data


@runtime.get('/spaces/{workspace_id}/knowledge/capabilities', summary='读取本机可用格式、大小限制和解析能力')
def capabilities(workspace_id: str, request: Request, session=Depends(get_session)):
    s.require_knowledge(context(request, session, workspace_id), session)
    return s.capabilities()


@runtime.get(BASE, summary='列出当前令牌可读的知识库')
def libraries(workspace_id: str, request: Request, offset: Offset = 0, limit: Limit = 20, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    s.require_knowledge(current, session)
    values = [s.public_entry('knowledge_base', value, current, session)
              for value in s.list_resources(session, 'knowledge_base', current) if value.get('status') != 'deleted']
    return page(values, offset, limit)


@runtime.post(BASE, status_code=201, summary='创建知识库')
async def create_library(workspace_id: str, body: LibraryWrite, request: Request, response: Response, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    write_lock(session)
    value = await s.create_library(**body.model_dump(), context=current, session=session)
    return representation('knowledge_base', value, response, current, session)


@runtime.get(BASE + '/{kb_id}', summary='读取知识库与当前 ETag')
def read_library(workspace_id: str, kb_id: str, request: Request, response: Response, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    return representation('knowledge_base', s.library(kb_id, current, session), response, current, session)


@runtime.patch(BASE + '/{kb_id}', summary='修改知识库名称或说明')
async def edit_library(workspace_id: str, kb_id: str, body: LibraryPatch, request: Request, response: Response,
                       expected: Expected = None, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    write_lock(session)
    value = s.library(kb_id, current, session, write=True)
    check_etag(value, expected)
    updated = await s.edit_library(kb_id=kb_id, name=body.name if body.name is not None else value['name'],
        description=body.description if body.description is not None else value.get('description', ''),
        context=current, session=session)
    return representation('knowledge_base', updated, response, current, session)


@runtime.get(BASE + '/{kb_id}/documents', summary='列出知识库中已授权的文档')
def documents(workspace_id: str, kb_id: str, request: Request, offset: Offset = 0, limit: Limit = 20, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    return page(s.list_children('knowledge_document', kb_id, current, session), offset, limit)


@runtime.post(BASE + '/{kb_id}/documents', status_code=201, summary='上传文档原件')
async def upload_document(workspace_id: str, kb_id: str, request: Request, response: Response, file: UploadFile = File(...),
                          profile: Profile = 'legacy', session=Depends(get_session)):
    current = context(request, session, workspace_id)
    filename, content = await contents(file, profile)
    write_lock(session)
    value = await s.upload_document(kb_id=kb_id, filename=filename, content=content, profile=profile,
                                    context=current, session=session)
    root = s.entry('knowledge_document', value['id'], current, session)
    return document_result(root, response, current, session, duplicate=value.get('duplicate', False))


@runtime.get(DOC, summary='读取文档、当前原件及版本标识')
def read_document(workspace_id: str, document_id: str, request: Request, response: Response, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    return document_result(s.entry('knowledge_document', document_id, current, session), response, current, session)


@runtime.patch(DOC, summary='修改文档显示名称')
def edit_document(workspace_id: str, document_id: str, body: DocumentPatch, request: Request, response: Response,
                  expected: Expected = None, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    write_lock(session)
    root = s.entry('knowledge_document', document_id, current, session, write=True)
    s.require_full_document_maintenance(root, current, session)
    check_etag(root, expected)
    name = body.name.strip()
    if not name:
        raise AppError('VALIDATION_ERROR', '文档名称不能为空', 422)
    if name != root['name']:
        root.update(name=name, updated_at=utc_now())
        s.save_resource(session, 'knowledge_document', document_id, root, current)
        audit(session, current, 'knowledge_document_rename', 'knowledge_document', document_id)
    return document_result(root, response, current, session)


@runtime.get(EXTERNAL, summary='用业务方自定义 ID 查询文档')
def read_external(workspace_id: str, kb_id: str, external_id: ExternalId, request: Request, response: Response,
                  session=Depends(get_session)):
    current = context(request, session, workspace_id)
    s.library(kb_id, current, session)
    row = external_row(session, current, external_key(current, kb_id, external_id))
    if not row:
        s.missing()
    root = s.entry('knowledge_document', _value(row)['document_id'], current, session)
    if root['knowledge_base_id'] != kb_id:
        s.missing()
    return document_result(root, response, current, session, external_id=external_id)


@runtime.put(EXTERNAL, summary='按业务 ID 创建或覆盖原件', responses={201: {'description': '首次创建'}})
async def upsert_external(workspace_id: str, kb_id: str, external_id: ExternalId, request: Request, response: Response,
                          file: UploadFile = File(...), profile: Profile = 'portable-slicing-1.0.0',
                          expected: Expected = None, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    filename, content = await contents(file, profile)
    write_lock(session)
    s.library(kb_id, current, session, write=True)
    s.require_full_resource_maintenance('knowledge_base', kb_id, current, session)
    identifier = external_key(current, kb_id, external_id)
    row = external_row(session, current, identifier)
    if row:
        document_id = _value(row)['document_id']
        root = s.entry('knowledge_document', document_id, current, session, write=True)
        if root['knowledge_base_id'] != kb_id:
            s.missing()
        root, changed = await replace_original(document_id=document_id, filename=filename, content=content,
            profile=profile, expected=expected, context=current, session=session)
        return document_result(root, response, current, session, external_id=external_id, changed=changed)
    if expected is not None:
        raise AppError('KNOWLEDGE_REVISION_CONFLICT', '业务 ID 尚无文档可供覆盖', 412)
    root = await s.upload_document(kb_id=kb_id, filename=filename, content=content, context=current,
                                   session=session, profile=profile, deduplicate=False)
    _put(session, 'platform_knowledge_external_id', identifier, {'document_id': root['id']},
         current.tenant_id, current.workspace_id, current.actor_id)
    response.status_code = 201
    return document_result(root, response, current, session, external_id=external_id, changed=True)


@runtime.put(DOC + '/original', summary='通过现有文档 ID 覆盖原件')
async def update_original(workspace_id: str, document_id: str, request: Request, response: Response,
                          file: UploadFile = File(...), profile: Profile = 'portable-slicing-1.0.0',
                          expected: Expected = None, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    filename, content = await contents(file, profile)
    root, changed = await replace_original(document_id=document_id, filename=filename, content=content,
        profile=profile, expected=expected, context=current, session=session)
    return document_result(root, response, current, session, changed=changed)


@runtime.post(DOC + '/parse', summary='按长度或自然段解析当前原件，生成草稿')
async def parse(workspace_id: str, document_id: str, body: ParseRequest, request: Request, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    return await s.parse_document(document_id=document_id, context=current, session=session, **body.model_dump())


@runtime.post(DOC + '/revisions', summary='新增、修改、启停或删除切片，生成新知识草稿')
async def revise(workspace_id: str, document_id: str, body: DocumentRevision, request: Request, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    return await s.revise_document(document_id=document_id, context=current, session=session, **body.model_dump())


@runtime.post(DOC + '/publish', summary='发布当前已验证的知识版本')
async def publish(workspace_id: str, document_id: str, body: PublishRequest, request: Request, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    return await s.publish_document(document_id=document_id, version_id=body.version_id, context=current, session=session)


@runtime.get(DOC + '/versions', summary='列出文档知识版本')
def versions(workspace_id: str, document_id: str, request: Request, offset: Offset = 0, limit: Limit = 20, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    values = s.versions(kind='knowledge_document', resource_id=document_id, context=current, session=session)
    fields = ('id', 'file_id', 'status', 'chunk_count', 'content_sha256', 'source_sha256', 'created_at', 'published_at')
    return page([{key: value[key] for key in fields if key in value} for value in values], offset, limit)


@runtime.get(DOC + '/chunks', summary='分页读取当前或指定版本的知识切片')
def document_chunks(workspace_id: str, document_id: str, request: Request,
                    version_id: str | None = Query(None, min_length=1, max_length=160),
                    offset: Offset = 0, limit: Limit = 20, q: str = Query('', max_length=500),
                    enabled: bool | None = None, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    return s.document_chunks(document_id=document_id, version_id=version_id, offset=offset, limit=limit, q=q,
                             enabled=enabled, context=current, session=session)


@runtime.get(DOC + '/chunks/latest.xlsx', response_class=Response, summary='导出最新保存的完整知识 XLSX')
def latest_xlsx(workspace_id: str, document_id: str, request: Request,
                expected_version_id: str = Query(min_length=1, max_length=160),
                columns: str | None = Query(None, max_length=256), session=Depends(get_session)):
    from app.knowledge.router import export_latest_knowledge
    return export_latest_knowledge(document_id=document_id, request=request, expected_version_id=expected_version_id,
        columns=columns, context=context(request, session, workspace_id), session=session)


@runtime.get('/spaces/{workspace_id}/knowledge/files/{file_id}/download', response_class=Response, summary='下载当前或历史版本原件')
def download(workspace_id: str, file_id: str, request: Request, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    filename, content = s.download_file(file_id=file_id, context=current, session=session)
    return Response(content, media_type='application/octet-stream', headers={
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename, safe=''), 'Cache-Control': 'no-store'})


@runtime.post('/spaces/{workspace_id}/knowledge/search', summary='在已发布的授权知识中检索')
async def search(workspace_id: str, body: SearchRequest, request: Request, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    return await s.search_knowledge(**body.model_dump(), context=current, session=session)


@runtime.delete(DOC, summary='将文档移入回收站')
async def remove_document(workspace_id: str, document_id: str, request: Request, session=Depends(get_session)):
    from app.knowledge.lifecycle import remove
    return await remove('knowledge_document', document_id, context=context(request, session, workspace_id), session=session)


@runtime.post(DOC + '/restore', summary='从回收站恢复文档')
async def restore_document(workspace_id: str, document_id: str, request: Request, session=Depends(get_session)):
    from app.knowledge.lifecycle import restore
    return await restore('knowledge_document', document_id, context=context(request, session, workspace_id), session=session)


@runtime.delete(BASE + '/{kb_id}', summary='将知识库移入回收站')
async def remove_library(workspace_id: str, kb_id: str, request: Request, session=Depends(get_session)):
    from app.knowledge.lifecycle import remove
    return await remove('knowledge_base', kb_id, context=context(request, session, workspace_id), session=session)


@runtime.post(BASE + '/{kb_id}/restore', summary='从回收站恢复知识库')
async def restore_library(workspace_id: str, kb_id: str, request: Request, session=Depends(get_session)):
    from app.knowledge.lifecycle import restore
    return await restore('knowledge_base', kb_id, context=context(request, session, workspace_id), session=session)


@runtime.get('/spaces/{workspace_id}/knowledge/recycle-bin', summary='列出有维护权限的回收站记录')
def recycle_bin(workspace_id: str, request: Request, kb_id: str | None = None, session=Depends(get_session)):
    from app.knowledge.lifecycle import recycle_bin as list_deleted
    return list_deleted(kb_id=kb_id, context=context(request, session, workspace_id), session=session)
