"""Typed platform data/file adapters retain the existing domain ACL and filters."""
from fastapi import Depends, Request, UploadFile, File, Query
from app.db import get_session, get_resource
from app.errors import AppError
from app.platform_api import runtime, platform_context

def context(request,session,workspace_id):
    current=platform_context(session,request.state.platform_token_id,workspace_id)
    request.state.business_context=current
    return current

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
    context(request,session,workspace_id)
    return await upload(request,file,session)

@runtime.get('/spaces/{workspace_id}/knowledge/versions/{version_id}/chunks')
def chunks(workspace_id:str,version_id:str,request:Request,offset:int=Query(0,ge=0,le=5000),limit:int=Query(20,ge=1,le=100),session=Depends(get_session)):
    from app.knowledge.service import version_chunks
    return version_chunks(version_id=version_id,offset=offset,limit=limit,q='',enabled=None,context=context(request,session,workspace_id),session=session)
