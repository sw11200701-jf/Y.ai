from fastapi import APIRouter, Depends, File, UploadFile, Form
from fastapi.responses import Response
from urllib.parse import quote
from app.db import get_session
from app.context import get_context
from .database_schemas import DatabaseWrite, TableWrite, DataRevision, TableQuery
from . import database_service as s

router = APIRouter(tags=["databases"])


@router.get("/databases")
def databases(session=Depends(get_session), context=Depends(get_context)):
    return s.databases(context=context, session=session)


@router.post("/databases", status_code=201)
async def create(body: DatabaseWrite, session=Depends(get_session), context=Depends(get_context)):
    return await s.create_database(definition=body.model_dump(), context=context, session=session)


@router.get("/databases/{database_id}")
def detail(database_id: str, session=Depends(get_session), context=Depends(get_context)):
    return s.database_metadata(s.root("database", database_id, context, session), context, session)


@router.patch("/databases/{database_id}")
async def edit(database_id: str, body: DatabaseWrite, session=Depends(get_session), context=Depends(get_context)):
    return await s.edit_database(database_id=database_id, definition=body.model_dump(), context=context, session=session)


@router.get("/databases/{database_id}/tables")
def tables(database_id: str, session=Depends(get_session), context=Depends(get_context)):
    return s.tables(database_id=database_id, context=context, session=session)


@router.post("/databases/{database_id}/tables", status_code=201)
async def create_table(database_id: str, body: TableWrite, session=Depends(get_session), context=Depends(get_context)):
    return await s.create_table(database_id=database_id, definition=body.model_dump(), context=context, session=session)


@router.get("/database/tables/{table_id}")
def table(table_id: str, session=Depends(get_session), context=Depends(get_context)):
    return s.table_metadata(s.root("data_table", table_id, context, session), context, session)


@router.post("/databases/{database_id}/imports", status_code=201)
async def import_new(database_id: str, file: UploadFile = File(...), name: str | None = Form(None), sheet_name: str | None = Form(None),
                     session=Depends(get_session), context=Depends(get_context)):
    return await s.import_new_table(database_id=database_id, filename=file.filename or "upload.csv", content=await file.read(5*1024*1024+1),
        name=name, sheet_name=sheet_name, context=context, session=session)


@router.post("/database/tables/{table_id}/imports", status_code=201)
async def upload(table_id: str, file: UploadFile = File(...), sheet_name: str | None = Form(None), session=Depends(get_session), context=Depends(get_context)):
    return await s.import_table(table_id=table_id, filename=file.filename or "upload.csv", content=await file.read(5*1024*1024+1),
        sheet_name=sheet_name, context=context, session=session)


@router.get("/database/tables/{table_id}/batches")
def batches(table_id: str, session=Depends(get_session), context=Depends(get_context)):
    return s.batches(table_id=table_id, context=context, session=session)


@router.get("/database/batches/{batch_id}")
def batch(batch_id: str, session=Depends(get_session), context=Depends(get_context)):
    value, _ = s.load_batch(batch_id, context, session, True)
    return s.batch_result(value, context, session)


@router.post("/database/batches/{batch_id}/revisions", status_code=201)
async def revise(batch_id: str, body: DataRevision, session=Depends(get_session), context=Depends(get_context)):
    return await s.revise_batch(batch_id=batch_id, revision=body.model_dump(exclude_none=True), context=context, session=session)


@router.post("/database/batches/{batch_id}/publish")
async def publish(batch_id: str, session=Depends(get_session), context=Depends(get_context)):
    return await s.publish_batch(batch_id=batch_id, context=context, session=session)


@router.get("/database/tables/{table_id}/dictionary")
def dictionary(table_id: str, version_id: str | None = None, session=Depends(get_session), context=Depends(get_context)):
    return s.dictionary(table_id=table_id, version_id=version_id, context=context, session=session)


@router.get("/database/tables/{table_id}/versions")
def versions(table_id: str, session=Depends(get_session), context=Depends(get_context)):
    return s.versions(table_id=table_id, context=context, session=session)


@router.post("/database/tables/{table_id}/query")
async def query(table_id: str, body: TableQuery, session=Depends(get_session), context=Depends(get_context)):
    return await s.query_table(table_id=table_id, version_id=body.version_id, query=body.query.model_dump(), context=context, session=session)


@router.get("/database/files/{file_id}/download")
def download(file_id: str, session=Depends(get_session), context=Depends(get_context)):
    name, content = s.download_original(file_id=file_id, context=context, session=session)
    return Response(content, media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name, safe="")})


@router.post("/database/tables/{table_id}/export")
async def export(table_id: str, version_id: str | None = None, session=Depends(get_session), context=Depends(get_context)):
    content, result = await s.export_detail(table_id=table_id, version_id=version_id, context=context, session=session)
    return Response(content, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=authorized-data.csv",
        "X-Data-Version": result["version_id"], "X-Data-Snapshot": result["snapshot_id"], "X-Data-Policy": str(result["policy_version"]), "Cache-Control": "no-store"})
