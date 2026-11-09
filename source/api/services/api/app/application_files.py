"""Public generated-file references with current partition/source export gates."""
import json,hashlib
from pathlib import Path
from sqlalchemy import select
from app.db import Resource,new_id,utc_now,get_resource
from app.errors import AppError
from app.settings import settings

FILE_SCHEMA={'type':'object','format':'application-file','properties':{'file_id':{'type':'string'},'filename':{'type':'string'},
    'media_type':{'type':'string'},'size_bytes':{'type':'integer'},'sha256':{'type':'string'},'created_at':{'type':'string'}},
    'required':['file_id','filename','media_type','size_bytes','sha256','created_at'],'additionalProperties':False}


def contains_file_schema(schema):
    if isinstance(schema,dict):return schema.get('format')=='application-file' or any(contains_file_schema(value) for value in schema.values())
    if isinstance(schema,list):return any(contains_file_schema(value) for value in schema)
    return False


def authorize_export(session,context,kind,identifier):
    from app.data_access import DATA_KINDS,authorize_data_resource
    from app.data_models import DataDerivation
    from app.data_access import _row
    visited=set()
    def walk(row):
        if row.id in visited:return
        if len(visited)>=128:raise AppError('RESOURCE_LIMIT','文件来源链超过安全范围',422)
        visited.add(row.id);get_resource(session,row.kind,row.resource_id,context)
        if row.kind in DATA_KINDS:authorize_data_resource(session=session,context=context,kind=row.kind,resource_id=row.resource_id,action='export_detail')
        for bound in session.scalars(select(DataDerivation).where(DataDerivation.resource_pk==row.id)):
            source=session.get(Resource,bound.source_pk)
            if source is None:raise AppError('NOT_FOUND','文件来源已不可访问',404)
            walk(source)
    walk(_row(session,context,kind,identifier))


def artifact_bytes(session,context,identifier):
    from app.machine_auth import authorize_application_result
    value=get_resource(session,'artifact',identifier,context)
    run=authorize_application_result(context,value['run_id'],session=session,operation='files.read')
    if value.get('artifact_kind')=='knowledge_csv':
        from app.knowledge.slicing_finalizer import checked_csv
        metadata,data=checked_csv(identifier,context=context,session=session)
        return metadata,data,metadata['mime_type']
    dataset=get_resource(session,'dataset',value['dataset_id'],context)
    if identifier not in run.get('artifact_ids',[]) or value['dataset_id'] not in (set(run.get('dataset_ids',[]))|{run.get('dataset_id')}):raise AppError('NOT_FOUND','文件不属于此运行',404)
    authorize_export(session,context,'artifact',identifier);authorize_export(session,context,'dataset',dataset['id'])
    path=Path(value['path']).resolve()
    if not path.is_relative_to(settings.artifact_dir.resolve()) or not path.is_file():raise AppError('NOT_FOUND','生成文件已不可用',404)
    if path.stat().st_size>10485760:raise AppError('APP_REQUEST_TOO_LARGE','生成文件超过下载限制',413)
    data=path.read_bytes()
    if len(data)!=value['size_bytes'] or hashlib.sha256(data).hexdigest()!=value['sha256']:raise AppError('CONFLICT','生成文件校验失败',409)
    from app.attachments import validate_content
    mime,_=validate_content(value['filename'],'',data)
    return value,data,mime


def normalize_outputs(session,context,authority,outputs,schema):
    from app.external_api.models import ApplicationFile
    from app.external_api import bridge,files
    def walk(value,shape):
        if isinstance(shape,dict) and shape.get('format')=='application-file':
            identifier=value.get('id') or value.get('artifact_id') if isinstance(value,dict) else value
            if not isinstance(identifier,str):raise AppError('APP_OUTPUT_INVALID','生成文件输出需要受控产物引用',422)
            artifact,data,mime=artifact_bytes(session,context,identifier)
            category={'.pdf':'pdf','.docx':'docx','.xlsx':'xlsx','.png':'image','.jpg':'image','.txt':'text','.csv':'csv'}.get(Path(artifact['filename']).suffix.lower())
            if category not in authority['runtime_policy']['allowed_file_types'] or len(data)>authority['runtime_policy']['max_file_bytes']:raise AppError('APP_FILE_POLICY_DENIED','生成文件类型或大小未获发布策略允许',403)
            partition=bridge.partition(authority)
            current=session.scalar(select(ApplicationFile).where(ApplicationFile.partition==partition,ApplicationFile.resource_kind=='artifact',ApplicationFile.resource_id==identifier))
            if current is None:
                current=ApplicationFile(id=new_id('appfile'),partition=partition,resource_kind='artifact',resource_id=identifier,filename=artifact['filename'],media_type=mime,size_bytes=len(data),sha256=artifact['sha256'],created_at=utc_now())
                session.add(current);session.flush()
            return files.metadata(current)
        if isinstance(value,dict):return {key:walk(child,shape.get('properties',{}).get(key,{})) for key,child in value.items()}
        if isinstance(value,list):return [walk(child,shape.get('items',{})) for child in value]
        return value
    return walk(outputs,schema)
