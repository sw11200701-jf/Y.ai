"""Public generated-file references retain the real artifact/source receipts."""
import hashlib
from pathlib import Path
from typing import Literal
from fastapi import Depends,Request
from fastapi.responses import Response
from urllib.parse import quote
from pydantic import Field
from app.db import get_session,get_resource
from app.settings import settings
from app.errors import AppError
from app.external_api.schemas import RuntimePolicy
from app.platform_api import runtime,platform_context

class PublicationPolicy(RuntimePolicy):
    allowed_file_types:list[Literal['text','image','audio','pdf','docx','xlsx','pptx','csv']]=Field(default_factory=list,max_length=8)

def checked_file(session,context,identifier):
    from app.application_files import authorize_export
    from app.platform_publications import authorized_publication
    artifact=get_resource(session,'artifact',identifier,context)
    run=get_resource(session,'run',artifact['run_id'],context)
    if identifier not in run.get('artifact_ids',[]):raise AppError('NOT_FOUND','文件不属于此运行',404)
    publication=authorized_publication(session,context,run['request']['platform_publication']['id'],execute=False)
    if artifact.get('artifact_kind')=='knowledge_csv':
        from app.knowledge.slicing_finalizer import checked_csv
        metadata,_=checked_csv(identifier,context=context,session=session);mime=metadata['mime_type']
    elif artifact.get('artifact_kind')=='generated_document':
        from app.reporting.document_artifacts import checked_artifact
        value=checked_artifact(artifact_id=identifier,context=context,session=session)
        metadata=value.model_dump();mime=value.mime_type
    else:
        authorize_export(session,context,'artifact',identifier)
        metadata=artifact
        mime={'.txt':'text/plain','.csv':'text/csv','.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.pdf':'application/pdf','.png':'image/png'}.get(Path(artifact['filename']).suffix.lower(),'application/octet-stream')
    path=Path(metadata['path']).resolve()
    if not path.is_relative_to(settings.artifact_dir.resolve()) or not path.is_file():raise AppError('NOT_FOUND','生成文件已不可用',404)
    policy=publication['bundle']['runtime_policy'];category={'.txt':'text','.csv':'csv','.docx':'docx','.xlsx':'xlsx','.pptx':'pptx','.pdf':'pdf','.png':'image','.jpg':'image'}.get(path.suffix.lower())
    # Stored files may use an opaque extension, so use the validated filename.
    category=category or {'.txt':'text','.csv':'csv','.docx':'docx','.xlsx':'xlsx','.pptx':'pptx','.pdf':'pdf','.png':'image','.jpg':'image'}.get(Path(metadata['filename']).suffix.lower())
    if category not in policy['allowed_file_types'] or path.stat().st_size>policy['max_file_bytes']:raise AppError('PLATFORM_FILE_DENIED','文件类型或大小不在发布策略中',403)
    content=path.read_bytes()
    if len(content)!=metadata['size_bytes'] or hashlib.sha256(content).hexdigest()!=metadata['sha256']:raise AppError('CONFLICT','文件完整性校验失败',409)
    public={'file_id':identifier,'filename':metadata['filename'],'media_type':mime,'size_bytes':metadata['size_bytes'],'sha256':metadata['sha256'],'created_at':metadata['created_at']}
    return public,content

def normalize_outputs(session,context,value,schema):
    if isinstance(schema,dict) and schema.get('format')=='application-file':
        identifier=(value.get('id') or value.get('artifact_id') or value.get('file_id')) if isinstance(value,dict) else value
        if not isinstance(identifier,str):raise AppError('APP_OUTPUT_INVALID','文件输出需要实际产物引用',422)
        return checked_file(session,context,identifier)[0]
    if isinstance(value,dict):return {key:normalize_outputs(session,context,child,schema.get('properties',{}).get(key,{})) for key,child in value.items()}
    if isinstance(value,list):return [normalize_outputs(session,context,child,schema.get('items',{})) for child in value]
    return value

@runtime.get('/spaces/{workspace_id}/files/{file_id}')
def file_metadata(workspace_id:str,file_id:str,request:Request,session=Depends(get_session)):
    return checked_file(session,platform_context(session,request.state.platform_token_id,workspace_id),file_id)[0]

@runtime.get('/spaces/{workspace_id}/files/{file_id}/download')
def download(workspace_id:str,file_id:str,request:Request,session=Depends(get_session)):
    metadata,content=checked_file(session,platform_context(session,request.state.platform_token_id,workspace_id),file_id)
    return Response(content,media_type=metadata['media_type'],headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(metadata['filename'],safe=''),'Cache-Control':'no-store'})
