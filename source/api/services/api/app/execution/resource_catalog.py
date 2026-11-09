"""Workflow selectors receive only caller-visible runnable batch metadata."""
from typing import Literal
from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel,ConfigDict,Field
from app.context import get_context
from app.db import get_session,list_resources,get_resource
from app.auth import validate_context
from app.rbac import require_any_permission
from app.errors import AppError

router=APIRouter(tags=['workflow-resource-catalog'])
PAGES=('page.ai','page.workbench','page.knowledge')


class ImportBatchOption(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str
    name:str
    filename:str
    status:Literal['committed']


class ImportBatchOptions(BaseModel):
    items:list[ImportBatchOption]
    total:int
    offset:int
    limit:int


@router.get('/workflow-resource-catalog/import-batches',response_model=ImportBatchOptions)
def import_batches(q:str=Query(default='',max_length=100),offset:int=Query(default=0,ge=0,le=10000),
        limit:int=Query(default=200,ge=1,le=200),session=Depends(get_session,scope='function'),context=Depends(get_context)):
    validate_context(context,session);require_any_permission(context,session,PAGES)
    choices=[]
    # Ordinary Resource owner/grants + tenant/workspace scope. No admin bypass,
    # KB aliases, row/column policy changes, preview or financial facts projection.
    for item in list_resources(session,'import_batch',context):
        if item.get('status')!='committed':continue
        try:current=get_resource(session,'import_batch',item['id'],context)
        except AppError as exc:
            if exc.status_code==404:continue
            raise
        if current.get('status')!='committed':continue
        filename=current.get('filename')
        if not isinstance(filename,str):filename=current['id']
        if q.casefold() not in filename.casefold():continue
        choices.append(ImportBatchOption(id=current['id'],name=filename,filename=filename,status='committed'))
    choices.sort(key=lambda item:(item.filename.casefold(),item.id))
    validate_context(context,session);require_any_permission(context,session,PAGES)
    return ImportBatchOptions(items=choices[offset:offset+limit],total=len(choices),offset=offset,limit=limit)
