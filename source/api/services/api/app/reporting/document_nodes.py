"""Fixed document nodes and deterministic forms use the original Run bridge."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from fastapi import APIRouter,Depends,Request
from app.context import get_context
from app.db import get_session,get_resource,save_resource
from app.auth import validate_context
from app.rbac import require_any_permission,RUN_PERMISSIONS
from app.errors import AppError
from . import document_artifacts as artifacts
from .document_specs import validate_content

ToolId=Literal['document.word.v1','document.excel.v1','document.powerpoint.v1']
SourceKind=Literal['knowledge_base','knowledge_document','knowledge_dataset','knowledge_file','knowledge_batch','knowledge_version','knowledge_chunk','document','database','data_table','data_table_version','data_batch','data_file','database_table','database_version']

class Source(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    kind:SourceKind
    resource_id:str=Field(min_length=1,max_length=160)

class Config(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    tool_id:ToolId

class Inputs(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    arguments:dict
    source_resources:list[Source]=Field(default_factory=list,max_length=32)

def validate_request(config,inputs,*,context,session,run_id=None):
    validate_context(context,session);require_any_permission(context,session,RUN_PERMISSIONS)
    try:chosen=Config.model_validate(config);values=Inputs.model_validate(inputs)
    except ValueError:raise AppError('DOCUMENT_INPUT_INVALID','文档工具、内容或来源配置不符合严格合同',422) from None
    validate_content(chosen.tool_id,values.arguments)
    sources=[{'kind':item.kind,'resource_id':item.resource_id,'action':'export_detail'} for item in values.source_resources]
    if len({(s['kind'],s['resource_id']) for s in sources})!=len(sources):raise AppError('DOCUMENT_INPUT_INVALID','来源不能重复',422)
    artifacts.authorize_sources(sources,context=context,session=session)
    if run_id is not None:
        if sources and context.principal_type=='platform':
            from app.data_access import _row
            from app.platform_api import authorize_derivation_target
            authorize_derivation_target(session,context,_row(session,context,'run',run_id))
        artifacts.preflight(tool_id=chosen.tool_id,arguments=values.arguments,run_id=run_id,context=context,session=session)
    return chosen,values,sources

async def execute_node(*,node_type,config,inputs,run_id,context,session):
    if node_type!='document_generate':raise AppError('NODE_UNAVAILABLE','不支持该固定文档节点',422)
    chosen,values,sources=validate_request(config,inputs,context=context,session=session,run_id=run_id)
    effect,_,run=artifacts.issued_state(context,session)
    if effect.run_id!=run_id:raise AppError('DOCUMENT_EFFECT_REQUIRED','运行与文档副作用不匹配',409)
    if sources:
        from app.data_access import record_derivation
        record_derivation(session=session,context=context,kind='run',resource_id=run_id,sources=sources)
        existing=run.setdefault('data_sources',[])
        for item in sources:
            if item not in existing:existing.append(item)
        save_resource(session,'run',run_id,run,context)
    result=artifacts.generate(tool_id=chosen.tool_id,arguments=values.arguments,context=context,session=session)
    metadata=[artifacts.DocumentArtifactStored.model_validate(get_resource(session,'artifact',identifier,context)).model_dump(mode='json',exclude={'path'}) for identifier in result['artifact_ids']]
    return {'outputs':{**result,'artifacts':metadata,'data_sources':sources},'source_refs':[],'warnings':[]}

router=APIRouter(tags=['document-jobs'])

@router.post('/tools/document-generators/{tool_id}/runs',status_code=202)
async def submit(tool_id:ToolId,body:Inputs,request:Request,session=Depends(get_session,scope='function'),context=Depends(get_context)):
    from app.contracts import RunCreate
    from app.runner.service import scheduler
    from app.execution.api import project_run
    validate_request({'tool_id':tool_id},body.model_dump(mode='json'),context=context,session=session)
    workflow={'name':'受控确定性文档生成','schemaVersion':'2.0',
        'input_schema':{'type':'object','properties':{'arguments':{'type':'object'},'source_resources':{'type':'array'}},'required':['arguments','source_resources'],'additionalProperties':False},
        'nodes':[{'id':'start','type':'start'},{'id':'document','type':'document_generate','config':{'tool_id':tool_id},'inputs':{'arguments':{'$ref':'inputs.arguments'},'source_resources':{'$ref':'inputs.source_resources'}}},
                 {'id':'output','type':'output','config':{'presentation':'files'},'inputs':{'files':{'$ref':'nodes.document.artifact_ids'}}}],
        'edges':[{'source':'start','target':'document'},{'source':'document','target':'output'}]}
    admitted=RunCreate(workflow=workflow,inputs=body.model_dump(mode='json'),prompt='受控确定性文档生成（无模型）',request_id=getattr(request.state,'request_id',None))
    return project_run(session,context,await scheduler.submit(admitted,context,session))
