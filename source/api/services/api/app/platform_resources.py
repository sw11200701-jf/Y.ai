"""Typed platform data/file adapters retain the existing domain ACL and filters."""
from fastapi import Depends, Request, UploadFile, File, Form, Query
from app.db import get_session, get_resource
from app.errors import AppError
from app.platform_api import runtime, platform_context, require_token_resource, _row, metadata, allowed
from app.knowledge.database_schemas import DatabaseWrite, TableQuery

def context(request,session,workspace_id):
    return platform_context(session,request.state.platform_token_id,workspace_id)

@runtime.get('/spaces/{workspace_id}/resources/{kind}/{identifier}')
def detail(workspace_id:str,kind:str,identifier:str,request:Request,session=Depends(get_session)):
    current=context(request,session,workspace_id)
    if kind in {'agent','workflow'}:
        value=get_resource(session,kind,identifier,current)
        if kind=='agent':
            from app.agents.types import agent_view
            return agent_view(value)
        from app.execution.node_settings import view
        return view(value)
    if kind=='attachment':
        from app.messages import public_attachment
        return public_attachment(get_resource(session,kind,identifier,current))
    if kind in {'database','data_table'}:
        from app.knowledge import database_service as s
        value=s.root(kind,identifier,current,session)
        return (s.database_metadata if kind=='database' else s.table_metadata)(value,current,session)
    if kind=='knowledge_base':
        from app.knowledge import service as s
        return s.library(identifier,current,session)
    if kind in {'knowledge_document','knowledge_dataset'}:
        from app.knowledge import service as s
        return s.public_entry(kind,s.entry(kind,identifier,current,session),current,session)
    # Never dump generic data/version JSON: it may contain unfiltered rows,
    # original bytes, prompts or internal file paths.
    raise AppError('RESOURCE_DETAIL_ROUTE_REQUIRED','请使用此资源的内容查询入口',422)

@runtime.post('/spaces/{workspace_id}/attachments',status_code=201)
async def upload_attachment(workspace_id:str,request:Request,file:UploadFile=File(...),session=Depends(get_session)):
    from app.attachments import upload
    request.state.business_context=context(request,session,workspace_id)
    return await upload(request,file,session)

@runtime.post('/spaces/{workspace_id}/databases',status_code=201)
async def create_database(workspace_id:str,body:DatabaseWrite,request:Request,session=Depends(get_session)):
    from app.knowledge.database_service import create_database
    return await create_database(definition=body.model_dump(),context=context(request,session,workspace_id),session=session)

@runtime.post('/spaces/{workspace_id}/databases/{database_id}/imports',status_code=201)
async def import_table(workspace_id:str,database_id:str,request:Request,file:UploadFile=File(...),name:str|None=Form(None),sheet_name:str|None=Form(None),session=Depends(get_session)):
    from app.knowledge.database_service import import_new_table
    return await import_new_table(database_id=database_id,filename=file.filename or 'upload.csv',content=await file.read(5*1024*1024+1),
        name=name,sheet_name=sheet_name,context=context(request,session,workspace_id),session=session)

@runtime.post('/spaces/{workspace_id}/database/tables/{table_id}/imports',status_code=201)
async def replace_table(workspace_id:str,table_id:str,request:Request,file:UploadFile=File(...),sheet_name:str|None=Form(None),session=Depends(get_session)):
    from app.knowledge.database_service import import_table
    return await import_table(table_id=table_id,filename=file.filename or 'upload.csv',content=await file.read(5*1024*1024+1),sheet_name=sheet_name,
        context=context(request,session,workspace_id),session=session)

@runtime.post('/spaces/{workspace_id}/database/batches/{batch_id}/publish')
async def publish_table(workspace_id:str,batch_id:str,request:Request,session=Depends(get_session)):
    from app.knowledge.database_service import publish_batch
    return await publish_batch(batch_id=batch_id,context=context(request,session,workspace_id),session=session)

@runtime.get('/spaces/{workspace_id}/databases/{database_id}/tables')
def tables(workspace_id:str,database_id:str,request:Request,session=Depends(get_session)):
    from app.knowledge.database_service import tables
    return tables(database_id=database_id,context=context(request,session,workspace_id),session=session)

@runtime.post('/spaces/{workspace_id}/database/tables/{table_id}/query')
async def query_table(workspace_id:str,table_id:str,body:TableQuery,request:Request,session=Depends(get_session)):
    from app.knowledge.database_service import query_table
    # POST is a transport choice; this operation requires read/use, never write.
    return await query_table(table_id=table_id,version_id=body.version_id,query=body.query.model_dump(),
        context=context(request,session,workspace_id),session=session)

@runtime.get('/spaces/{workspace_id}/knowledge/versions/{version_id}/chunks')
def chunks(workspace_id:str,version_id:str,request:Request,offset:int=Query(0,ge=0,le=5000),limit:int=Query(20,ge=1,le=100),session=Depends(get_session)):
    from app.knowledge.service import version_chunks
    return version_chunks(version_id=version_id,offset=offset,limit=limit,q='',enabled=None,context=context(request,session,workspace_id),session=session)
