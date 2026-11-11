from fastapi import APIRouter, Depends, File, UploadFile, Form, Query, Request
from typing import Literal
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.context import get_context
from app.contracts import BusinessContext
from app.db import get_session
from .data_store import list_resources
from app.errors import AppError
from . import service as s
from .schemas import LibraryWrite, DatasetWrite, BatchRevision, DocumentRevision, ParseRequest, PublishRequest, SearchRequest, QueryRequest, AccessWrite, ChunkPatch, DocumentOverview
from .slicing_image_reviews import ImageReviewWrite

router = APIRouter(tags=["knowledge"])

@router.get('/knowledge/documents/{document_id}/chunks/latest.xlsx',response_class=Response,summary='导出最新保存的完整知识')
def export_latest_knowledge(document_id:str,request:Request,expected_version_id:str=Query(min_length=1,max_length=160),columns:str|None=Query(default=None,max_length=256),session:Session=Depends(get_session),context:BusinessContext=Depends(get_context)):
    if set(request.query_params)-{'columns','expected_version_id'} or any(len(request.query_params.getlist(k))!=1 for k in request.query_params):
        raise AppError('VALIDATION_ERROR','仅接受版本核对与导出字段参数',422)
    from .curation import latest_export
    from .chunk_export import MIME
    from urllib.parse import quote
    content,meta=latest_export(document_id=document_id,expected_version_id=expected_version_id,columns=None if columns is None else columns.split(','),context=context,session=session)
    return Response(content,media_type=MIME,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(meta['filename'],safe=''),
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Knowledge-Version-Id':meta['version_id'],
        'X-Chunk-Count':str(meta['chunk_count']),'X-Source-Span-Count':str(meta['span_count']),'X-Export-Scope':meta['scope'],
        'X-Export-Format':meta['format'],'X-Export-Columns':','.join(meta['columns']),'X-Knowledge-Selection':'latest-saved'})

@router.get('/knowledge/documents/{document_id}/versions/{version_id}/chunks/customer.xlsx' ,response_class=Response,
    summary='导出序号、语义和原文段落',responses={200:{'description':'完整版本的三列语义知识切片Excel'}})
def export_customer_chunks(document_id:str,version_id:str,request:Request,session:Session=Depends(get_session),context:BusinessContext=Depends(get_context)):
    if request.query_params:raise AppError('VALIDATION_ERROR','导出完整版本，不接受分页或筛选参数',422)
    from .customer_export import export_customer
    from .chunk_export import MIME
    from urllib.parse import quote
    content,metadata=export_customer(document_id=document_id,version_id=version_id,context=context,session=session)
    return Response(content,media_type=MIME,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(metadata['filename'],safe=''),
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Knowledge-Version-Id':version_id,'X-Chunk-Count':str(metadata['chunk_count']),
        'X-Source-Span-Count':str(metadata['span_count']),'X-Export-Scope':metadata['scope'],'X-Export-Format':metadata['format']})

@router.get('/knowledge/documents/{document_id}/versions/{version_id}/chunks/selected.xlsx',response_class=Response,summary='按所选字段导出完整知识版本')
def export_selected_chunks(document_id:str,version_id:str,request:Request,columns:str=Query(default='sequence,semantic,original_paragraph',max_length=256),session:Session=Depends(get_session),context:BusinessContext=Depends(get_context)):
    if set(request.query_params)-{'columns'} or len(request.query_params.getlist('columns'))>1:raise AppError('VALIDATION_ERROR','仅接受columns选择字段，不接受分页或筛选',422)
    from .customer_export import export_customer
    from .chunk_export import MIME
    from urllib.parse import quote
    content,meta=export_customer(document_id=document_id,version_id=version_id,context=context,session=session,columns=columns.split(','))
    return Response(content,media_type=MIME,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(meta['filename'],safe=''),
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Knowledge-Version-Id':version_id,'X-Chunk-Count':str(meta['chunk_count']),
        'X-Source-Span-Count':str(meta['span_count']),'X-Export-Scope':meta['scope'],'X-Export-Format':meta['format'],'X-Export-Columns':','.join(meta['columns'])})

@router.get('/knowledge/documents/{document_id}/versions/{version_id}/chunks/export.xlsx',
    response_class=Response,
    summary='导出明确文档版本的全部切片和原文定位',
    description='完整validated/published版本，含历史版本；拒绝所有query参数。当前知识库、文档、版本、原文件均须全文use与export_detail；不调用模型、不发布、不改业务数据。',
    responses={200: {'description': '完整版本的真实XLSX：切片、来源明细、说明。',
        'content': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': {'schema': {'type': 'string', 'format': 'binary'}}},
        'headers': {
            'Content-Disposition': {'schema': {'type': 'string'}, 'description': 'UTF-8 attachment filename'},
            'Cache-Control': {'schema': {'type': 'string', 'const': 'no-store'}},
            'X-Knowledge-Version-Id': {'schema': {'type': 'string'}},
            'X-Chunk-Count': {'schema': {'type': 'integer', 'minimum': 1}},
            'X-Source-Span-Count': {'schema': {'type': 'integer', 'minimum': 1}},
            'X-Export-Scope': {'schema': {'type': 'string', 'const': 'complete-version'}}}},
        401: {'description': '需要当前人类会话'},
        403: {'description': '缺少当前全文使用或明细导出权限'},
        404: {'description': '文档、版本或原文件不可见'},
        409: {'description': '来源或版本快照完整性冲突'},
        422: {'description': '任何query参数、非法定位、Excel长度/XML限制均明确拒绝'}})
def export_document_chunks(document_id: str, version_id: str, request: Request,
        session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    if request.query_params:
        raise AppError('VALIDATION_ERROR', '导出完整明确版本，不接受分页或筛选参数', 422)
    from .chunk_export import export_chunks, MIME
    from urllib.parse import quote
    content, metadata = export_chunks(document_id=document_id, version_id=version_id, context=context, session=session)
    filename = 'chunks-' + version_id + '.xlsx'
    return Response(content, media_type=MIME, headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename,safe=''),
        'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff', 'X-Knowledge-Version-Id':version_id,
        'X-Chunk-Count':str(metadata['chunk_count']), 'X-Source-Span-Count':str(metadata['span_count']), 'X-Export-Scope':metadata['scope']})

@router.get("/knowledge/capabilities")
def knowledge_capabilities(session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    s.require_knowledge(context, session)
    return s.capabilities()

@router.get("/knowledge-bases")
def libraries(session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    s.require_knowledge(context, session)
    from app.resource_policy import merge_directory
    from .lifecycle import visible_libraries
    return {"items":visible_libraries(session,context,merge_directory(session,context,"knowledge_base",list_resources(session,"knowledge_base",context)))}

@router.get('/knowledge/recycle-bin')
def recycle(kb_id: str | None = None, session=Depends(get_session), context=Depends(get_context)):
    from .lifecycle import recycle_bin
    return recycle_bin(context=context, session=session, kb_id=kb_id)

@router.delete('/knowledge-bases/{kb_id}')
async def delete_library(kb_id: str, session=Depends(get_session), context=Depends(get_context)):
    from .lifecycle import remove
    return await remove('knowledge_base', kb_id, context=context, session=session)

@router.post('/knowledge-bases/{kb_id}/restore')
async def restore_library(kb_id: str, session=Depends(get_session), context=Depends(get_context)):
    from .lifecycle import restore
    return await restore('knowledge_base', kb_id, context=context, session=session)

@router.post("/knowledge-bases", status_code=201)
async def create(body: LibraryWrite, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await s.create_library(context=context, session=session, **body.model_dump())

@router.get("/knowledge-bases/{kb_id}")
def detail(kb_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return s.library(kb_id, context, session)

@router.patch("/knowledge-bases/{kb_id}")
async def edit(kb_id: str, body: LibraryWrite, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await s.edit_library(kb_id=kb_id, context=context, session=session, **body.model_dump())

@router.get("/knowledge-bases/{kb_id}/access")
def access(kb_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return s.access_metadata(kb_id=kb_id, context=context, session=session)

@router.put("/knowledge-bases/{kb_id}/access")
async def grant(kb_id: str, body: AccessWrite, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await s.set_access(kb_id=kb_id, context=context, session=session, **body.model_dump())

@router.get("/knowledge-bases/{kb_id}/documents")
def documents(kb_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return {"items": s.list_children("knowledge_document", kb_id, context, session)}

@router.get("/knowledge-bases/{kb_id}/overview")
def overview(kb_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return s.library_overview(kb_id=kb_id, context=context, session=session)

@router.patch("/knowledge/documents/{document_id}/overview")
async def edit_overview(document_id: str, body: DocumentOverview, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await s.edit_document_overview(document_id=document_id, overview=body.model_dump(), context=context, session=session)

@router.post("/knowledge-bases/{kb_id}/documents", status_code=201)
async def upload(kb_id: str, file: UploadFile = File(...), profile: Literal['legacy','portable-slicing-1.0.0'] = Query('legacy'), session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from app.imports.documents import upload_contract
    limit = upload_contract(profile)['max_bytes']
    return await s.upload_document(kb_id=kb_id, filename=file.filename or "upload.txt", content=await file.read(limit+1), profile=profile, context=context, session=session)

@router.get("/knowledge/documents/{document_id}")
def document(document_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return s.public_entry("knowledge_document", s.entry("knowledge_document", document_id, context, session), context, session)

@router.get('/knowledge/documents/{document_id}/runs/{run_id}/review')
def pending_slicing_review(document_id: str, run_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from .slicing_pending_read import pending_result
    return pending_result(document_id=document_id, run_id=run_id, context=context, session=session)

@router.get('/knowledge/documents/{document_id}/runs/{run_id}/progress')
def committed_window_preview(document_id:str,run_id:str,offset:int=Query(0,ge=0,le=5000),limit:int=Query(20,ge=1,le=100),session=Depends(get_session),context=Depends(get_context)):
    from .semantic_progress_read import progress
    return progress(document_id=document_id,run_id=run_id,offset=offset,limit=limit,context=context,session=session)

@router.get('/knowledge/documents/{document_id}/image-evidence')
def image_evidence_catalog(document_id:str,session=Depends(get_session),context=Depends(get_context)):
    from .slicing_image_reviews import review_catalog
    return review_catalog(document_id,context=context,session=session)

@router.get('/knowledge/documents/{document_id}/image-evidence/{occurrence_id}/{asset_id}/download')
def image_evidence_original(document_id:str,occurrence_id:str,asset_id:str,session=Depends(get_session),context=Depends(get_context)):
    from .slicing_image_reviews import original_image
    content,mime=original_image(document_id,occurrence_id,asset_id,context=context,session=session)
    return Response(content,media_type=mime,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

@router.put('/knowledge/documents/{document_id}/image-evidence/{occurrence_id}')
def write_image_evidence_review(document_id:str,occurrence_id:str,body:ImageReviewWrite,session=Depends(get_session),context=Depends(get_context)):
    from .slicing_image_reviews import save_review
    return save_review(document_id,occurrence_id,body,context=context,session=session)

@router.post("/knowledge/documents/{document_id}/parse")
@router.post("/knowledge/documents/{document_id}/rechunk")
async def parse(document_id: str, body: ParseRequest, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    try:
        return await s.parse_document(document_id=document_id, context=context, session=session, **body.model_dump())
    except AppError as exc:
        if exc.status_code not in (401, 403, 404):
            session.rollback()
            s.record_parse_failure(document_id=document_id, options=body.model_dump(), error=exc, context=context, session=session)
            session.commit()
        raise

@router.patch("/knowledge/documents/{document_id}/chunks/{chunk_id}")
async def patch_chunk(document_id: str, chunk_id: str, body: ChunkPatch, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    changes = body.model_dump(exclude_none=True)
    version_id = changes.pop("base_version_id")
    return await s.revise_document(document_id=document_id, base_version_id=version_id, corrections=[{"chunk_id": chunk_id, **changes}], context=context, session=session)

@router.get("/knowledge/documents/{document_id}/chunks")
def document_chunks(document_id: str, version_id: str | None = None, q: str = Query("", max_length=200), enabled: bool | None = None,
                    offset: int = Query(0, ge=0, le=5000), limit: int = Query(20, ge=1, le=100),
                    session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return s.document_chunks(document_id=document_id, version_id=version_id, q=q, enabled=enabled, offset=offset, limit=limit, context=context, session=session)

@router.post("/knowledge/documents/{document_id}/revisions", status_code=201)
async def revise_document(document_id: str, body: DocumentRevision, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await s.revise_document(document_id=document_id, context=context, session=session, **body.model_dump())

@router.post("/knowledge/documents/{document_id}/publish")
async def publish_document(document_id: str, body: PublishRequest, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await s.publish_document(document_id=document_id, context=context, session=session, **body.model_dump())

@router.get("/knowledge-bases/{kb_id}/datasets")
def datasets(kb_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return {"items": s.list_children("knowledge_dataset", kb_id, context, session)}

@router.get("/knowledge-bases/{kb_id}/batches")
def kb_batches(kb_id: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    values = s.library_batches(kb_id=kb_id, context=context, session=session)
    return {"items": values[(page-1)*page_size:page*page_size], "total": len(values), "page": page, "page_size": page_size, "knowledge_base_id": kb_id}

@router.post("/knowledge-bases/{kb_id}/datasets", status_code=201)
async def dataset(kb_id: str, body: DatasetWrite, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await s.create_dataset(kb_id=kb_id, definition=body.model_dump(), context=context, session=session)

@router.get("/knowledge/datasets/{dataset_id}")
def dataset_detail(dataset_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return s.public_entry("knowledge_dataset", s.entry("knowledge_dataset", dataset_id, context, session), context, session)

@router.post("/knowledge/datasets/{dataset_id}/batches", status_code=201)
async def batch(dataset_id: str, file: UploadFile = File(...), sheet_name: str | None = Form(None), session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await s.upload_batch(dataset_id=dataset_id, filename=file.filename or "upload.csv", content=await file.read(5*1024*1024+1), sheet_name=sheet_name, context=context, session=session)

@router.get("/knowledge/datasets/{dataset_id}/batches")
def batch_list(dataset_id: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    values = s.batches(dataset_id=dataset_id, context=context, session=session)
    return {"items": values[(page-1)*page_size:page*page_size], "total": len(values), "page": page, "page_size": page_size}

@router.get("/knowledge/batches/{batch_id}")
def batch_detail(batch_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    value, _ = s.get_batch(batch_id, context, session)
    if not s._unrestricted(s.decision(session, "knowledge_batch", batch_id, context, "maintain")):
        raise AppError("DATA_SCOPE_WRITE_DENIED", "治理批次预览需要完整维护范围", 403)
    return s.batch_preview(value)

@router.get("/knowledge/batches/{batch_id}/rows")
def batch_rows(batch_id: str, view: Literal["original", "working", "governed"] = "governed", offset: int = Query(0, ge=0, le=5000), limit: int = Query(20, ge=1, le=100), session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return s.batch_rows(batch_id=batch_id, view=view, offset=offset, limit=limit, context=context, session=session)

@router.post("/knowledge/batches/{batch_id}/revise", status_code=201)
async def revise_batch(batch_id: str, body: BatchRevision, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await s.revise_batch(batch_id=batch_id, revision=body.model_dump(exclude_none=True), context=context, session=session)

@router.post("/knowledge/batches/{batch_id}/publish")
async def publish_batch(batch_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await s.publish_batch(batch_id=batch_id, context=context, session=session)

@router.post("/knowledge/datasets/{dataset_id}/query")
async def query(dataset_id: str, body: QueryRequest, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await s.query_dataset(dataset_id=dataset_id, context=context, session=session, **body.model_dump())

@router.get("/knowledge/{resource_type}/{resource_id}/versions")
def versions(resource_type: str, resource_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    kind = {"documents": "knowledge_document", "datasets": "knowledge_dataset"}.get(resource_type)
    if kind is None: raise AppError("NOT_FOUND", "Resource not found", 404)
    return {"items": s.versions(kind=kind, resource_id=resource_id, context=context, session=session)}

@router.get("/knowledge/versions/{version_id}")
def version(version_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return s.version_preview(version_id=version_id, context=context, session=session)

@router.get("/knowledge/versions/{version_id}/chunks")
def chunks(version_id: str, offset: int = Query(0, ge=0, le=5000), limit: int = Query(20, ge=1, le=100), q: str = Query("", max_length=200), enabled: bool | None = None,
           session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return s.version_chunks(version_id=version_id, offset=offset, limit=limit, q=q, enabled=enabled, context=context, session=session)

@router.get("/knowledge/files/{file_id}/download")
def download(file_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    from urllib.parse import quote
    name, content = s.download_file(file_id=file_id, context=context, session=session)
    return Response(content, media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name, safe="")})

@router.post("/knowledge/search")
async def search(body: SearchRequest, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return await s.search_knowledge(context=context, session=session, **body.model_dump())

def state_routes(kind, plural):
    async def restore(resource_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
        from .lifecycle import restore
        return await restore(kind, resource_id, context=context, session=session)
    async def disable(resource_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
        return await s.change_status(kind=kind, resource_id=resource_id, status="disabled", context=context, session=session)
    async def delete(resource_id: str, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
        return await s.change_status(kind=kind, resource_id=resource_id, status="deleted", context=context, session=session)
    router.add_api_route(f"/knowledge/{plural}/{{resource_id}}/disable", disable, methods=["POST"], name="disable_" + plural)
    router.add_api_route(f"/knowledge/{plural}/{{resource_id}}", delete, methods=["DELETE"], name="delete_" + plural)
    router.add_api_route(f"/knowledge/{plural}/{{resource_id}}/restore", restore, methods=['POST'], name='restore_' + plural)

state_routes("knowledge_document", "documents")
state_routes("knowledge_dataset", "datasets")


@router.get('/knowledge/documents/{document_id}/versions/{version_id}/runs/{run_id}/images/{occurrence_id}/{asset_id}/download')
def download_recipe_image(document_id:str,version_id:str,run_id:str,occurrence_id:str,asset_id:str,session=Depends(get_session),context=Depends(get_context)):
    from fastapi.responses import Response
    from urllib.parse import quote
    from .slicing_asset_downloads import checked_image
    value=checked_image(document_id=document_id,version_id=version_id,run_id=run_id,occurrence_id=occurrence_id,asset_id=asset_id,context=context,session=session)
    return Response(value['content'],media_type=value['mime'],headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(value['filename'],safe=''),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
