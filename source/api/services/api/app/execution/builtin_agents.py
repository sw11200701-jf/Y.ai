"""Server-owned builtin definition with private caller configuration, no deletion.

The profile is not an Agent CRUD object or an execution identity. Every execution
still uses the current caller and that caller's live model and source authority.
"""
import hashlib,json
from copy import deepcopy
from pathlib import Path
from typing import Literal,Annotated
from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from app.context import get_context
from app.db import SessionLocal,get_session,get_resource,new_id,Resource,utc_now
from sqlalchemy import select
from app.auth import validate_context
from app.rbac import require_any_permission,audit
from app.errors import AppError
from app.agents.builtin_chunker import BUILTIN_ID,CONTRACT_VERSION,DEFAULT_PROMPT,builtin_definition,digest

from app.agents.slicing_specialists import SPECIALISTS,select as select_specialist,prompts as specialist_prompts

router=APIRouter(tags=['builtin-agents'])
FIELDS={'model_config_id','prompt_override','recipe_version','stage_prompts','source_scope','recipe_options','model_execution_mode'}
CUSTOM_PREFIX='slicing_agent_'


class SlicingAgentWrite(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=100)
    strategy:str=Field(min_length=1,max_length=100)
    formats:list[str]=Field(min_length=1,max_length=20)


def supported_formats(strategy):
    from app.imports.document_container import SEMANTIC_FORMATS,canonical_format
    spec=SPECIALISTS.get(strategy)
    return sorted({kind for kind in SEMANTIC_FORMATS.values() if spec and canonical_format(kind) in spec['formats']})


def check_formats(strategy, formats):
    if not formats or len(set(formats))!=len(formats) or set(formats)-set(supported_formats(strategy)):
        raise AppError('SLICING_FORMAT_MISMATCH','请选择与工作流策略相符的文件格式',422)

class RecipeOptions(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    max_context_rounds:int=Field(default=1,ge=0,le=8)
    max_repair_rounds:int=Field(default=1,ge=0,le=2)
    max_relation_pairs:int=Field(default=16,ge=0,le=512)
    source_group_bytes:int=Field(default=4000,ge=256,le=262144)
    csv_variant:Literal['original','excel_safe']='original'

class BuiltinSettingsPatch(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    expected_revision:int=Field(ge=0)
    model_config_id:str|None=Field(default=None,min_length=1,max_length=100)
    prompt_override:str|None=Field(default=None,min_length=1,max_length=8000)
    recipe_version:Literal['knowledge-chunker-v1','portable-slicing-1.0.0']|None=None
    stage_prompts:dict|None=None
    source_scope:list[str]|None=Field(default=None,max_length=32)
    recipe_options:RecipeOptions|None=None
    model_execution_mode:Literal['auto','direct','model_default']|None=None
    name:str|None=Field(default=None,min_length=1,max_length=100)
    formats:list[str]|None=Field(default=None,min_length=1,max_length=20)
    @field_validator('stage_prompts')
    @classmethod
    def stages_valid(cls,value):
        if value is not None:
            from app.knowledge.slicing_contracts import stage_settings
            stage_settings(value)
        return value
    @field_validator('source_scope')
    @classmethod
    def scope_valid(cls,value):
        if value is not None and (len(set(value))!=len(value) or any(not v or len(v)>100 for v in value)):raise ValueError('资料范围须为不重复的有效知识库ID')
        return value
    @field_validator('prompt_override')
    @classmethod
    def nonblank(cls,value):
        if value is not None and not value.strip():raise ValueError('任务提示不能为空白')
        return value

def require_builtin(identifier):
    if identifier!=BUILTIN_ID and identifier not in SPECIALISTS and not identifier.startswith(CUSTOM_PREFIX):
        raise AppError('NOT_FOUND','切片智能体不存在',404)

def authorize(session,context):
    validate_context(context,session)
    require_any_permission(context,session,('page.ai','page.knowledge'))

def profile_id(context):
    return 'builtin_chunker_profile_'+hashlib.sha256(context.operator_id.encode()).hexdigest()[:32]

def workspace_profile_id(context,identifier=BUILTIN_ID):
    if identifier!=BUILTIN_ID:
        return "builtin_specialist_"+hashlib.sha256((context.tenant_id+"|"+context.workspace_id+"|"+identifier).encode()).hexdigest()[:32]
    return 'builtin_chunker_space_'+hashlib.sha256((context.tenant_id+'|'+context.workspace_id).encode()).hexdigest()[:32]

def workspace_profile(session,context,identifier=BUILTIN_ID):
    validate_context(context,session)
    row=session.scalar(select(Resource).where(Resource.kind=='builtin_workspace_profile',Resource.resource_id==workspace_profile_id(context,identifier),Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
    if row:return json.loads(row.payload)
    if identifier.startswith(CUSTOM_PREFIX):raise AppError('NOT_FOUND','切片智能体不存在',404)
    if identifier in SPECIALISTS:
        inherited=workspace_profile(session,context)
        return {**portable_defaults(identifier),**inherited,'revision':0,'inherited_revision':inherited['revision'],
            'recipe_version':'portable-slicing-1.0.0'}
    return {'revision':0,'model_config_id':None,'model_config_version':None,'prompt_override':None}

def profile(session,context,identifier=BUILTIN_ID):
    authorize(session,context)
    shared=workspace_profile(session,context,identifier)
    if identifier in SPECIALISTS or shared.get('strategy'):
        return {**shared,'profile_scope':'workspace','specialist_agent_id':identifier}
    if shared['revision']>0:return {**shared,'profile_scope':'workspace'}
    # A prior personal profile is not exposed through the shared settings API.
    try:return {**get_resource(session,'builtin_agent_profile',profile_id(context),context),'profile_scope':'legacy_user'}
    except AppError as exc:
        if exc.status_code!=404:raise
        return {**shared,'profile_scope':'workspace'}

def resolve_model(session,context,identifier,expected=None):
    from app.model_catalog.service import resolve_runtime_model
    value=resolve_runtime_model(identifier,context=context,session=session,expected_version=expected)
    return value.config_version

def portable_defaults(identifier=BUILTIN_ID):
    from app.knowledge.slicing_contracts import DEFAULTS,RECIPE
    options=RecipeOptions().model_dump()
    if identifier in SPECIALISTS:options.update(max_context_rounds=8,max_repair_rounds=2,max_relation_pairs=512,source_group_bytes=96000)
    return {'recipe_version':RECIPE,'stage_prompts':{k:{'system_prompt':v['system_prompt'],'user_prompt':v['user_prompt']} for k,v in DEFAULTS['stages'].items()},'recipe_options':options}


def custom_profiles(session,context):
    rows=session.scalars(select(Resource).where(Resource.kind=='builtin_workspace_profile',
        Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id).order_by(Resource.id))
    return [value for row in rows if (value:=json.loads(row.payload)).get('id','').startswith(CUSTOM_PREFIX)]


def save_profile(session,context,identifier,value):
    row=session.scalar(select(Resource).where(Resource.kind=='builtin_workspace_profile',
        Resource.resource_id==workspace_profile_id(context,identifier),Resource.tenant_id==context.tenant_id,
        Resource.workspace_id==context.workspace_id))
    payload=json.dumps(value,ensure_ascii=False);stamp=utc_now()
    if row:
        row.payload=payload;row.updated_at=stamp
    else:
        session.add(Resource(kind='builtin_workspace_profile',resource_id=workspace_profile_id(context,identifier),
            tenant_id=context.tenant_id,workspace_id=context.workspace_id,owner_id='__system__',
            payload=payload,created_at=stamp,updated_at=stamp))


def apply_settings(identifier, value, body):
    for name in FIELDS&body.model_fields_set:
        item=getattr(body,name);value[name]=item.model_dump() if isinstance(item,BaseModel) else item
    metadata={'name','formats'}&body.model_fields_set
    if metadata and not value.get('strategy'):
        raise AppError('BUILTIN_AGENT_PROTECTED','内置切片智能体的名称和格式由系统管理',422)
    if 'name' in metadata:
        if not body.name or not body.name.strip():raise AppError('VALIDATION_ERROR','请填写智能体名称',422)
        value['name']=body.name.strip()
    if 'formats' in metadata:
        check_formats(value['strategy'],body.formats or [])
        value['formats']=body.formats
    if identifier in SPECIALISTS or value.get('strategy'):
        if body.recipe_version is not None and body.recipe_version!='portable-slicing-1.0.0':
            raise AppError('VALIDATION_ERROR','切片智能体使用语义审核工作流',422)
        value['recipe_version']='portable-slicing-1.0.0'
    return value


def public(session,context,identifier=BUILTIN_ID):
    from app.execution.semantic_jobs import execution_available
    value=workspace_profile(session,context,identifier);status='waiting_configuration'
    if value['model_config_id']:
        try:resolve_model(session,context,value['model_config_id'],value.get('model_config_version'));status='configured'
        except AppError as exc:
            if exc.status_code not in {403,404,409}:raise
            status='model_unavailable'
    from .slicing_workflow_view import workflow_view
    workflow=None;configuration_errors=[]
    try:workflow=workflow_view(identifier,value)
    except AppError as error:configuration_errors=[error.message]
    definition=builtin_definition()
    if identifier in SPECIALISTS:definition.update(id=identifier,name=SPECIALISTS[identifier]['name'],default_prompt=SPECIALISTS[identifier]['policy'])
    if value.get('strategy'):
        definition.update(id=identifier,name=value['name'],builtin=False,default_prompt=SPECIALISTS[value['strategy']]['policy'])
    return {**definition,'workflow':workflow,'configuration_errors':configuration_errors,'configuration_scope':'current_workspace','execution_identity':'current_caller',
        'execution_ready':execution_available() and status=='configured' and not configuration_errors,'capabilities':{'window_model_adapter':True,'durable_document_jobs':execution_available(),'atomic_document_draft':execution_available()},
        'configuration_status':status,'editable_fields':sorted(FIELDS),'portable_recipe':portable_defaults(value.get('strategy',identifier)),
        'formats':value.get('formats',supported_formats(identifier)),'format_options':supported_formats(value.get('strategy',identifier)),
        'config':{name:value.get(name) for name in ('revision','model_config_id','model_config_version',*FIELDS,'name','formats')}}

@router.get('/builtin-agents')
def builtin_agents(session=Depends(get_session),context=Depends(get_context)):
    require_any_permission(context,session,('chunker.config.view','chunker.config.read','chunker.config.manage'))
    custom=custom_profiles(session,context)
    if set(context.permissions)&{'chunker.config.read','chunker.config.manage'}:
        items=[public(session,context,identifier) for identifier in [*SPECIALISTS,*(value['id'] for value in custom)]]
        return {'items':items}
    profiles=[{'id':identifier,'name':spec['name'],'formats':supported_formats(identifier),
        'model_config_id':workspace_profile(session,context,identifier)['model_config_id']} for identifier,spec in SPECIALISTS.items()]
    return {'items':[{'id':value['id'],'name':value['name'],'formats':value['formats'],'builtin':value['id'] in SPECIALISTS,
        'deletable':False,'configuration_scope':'current_workspace','configuration_status':'configured' if value['model_config_id'] else 'waiting_configuration',
        'metadata_only':True} for value in profiles+custom]}


@router.post('/builtin-agents',status_code=201)
def create_slicing_agent(body:SlicingAgentWrite,session=Depends(get_session),context=Depends(get_context)):
    from app.rbac import require_permission
    require_permission(context,session,'chunker.config.manage')
    check_formats(body.strategy,body.formats)
    identifier=new_id('slicing_agent')
    value={**portable_defaults(body.strategy),**body.model_dump(),'id':identifier,'revision':1,
           'model_config_id':None,'model_config_version':None,'prompt_override':None,'source_scope':None}
    save_profile(session,context,identifier,value)
    audit(session,context,'slicing_agent_create','slicing_agent',identifier)
    session.flush()
    return public(session,context,identifier)


@router.get('/builtin-agents/document-agent-choices')
def document_agent_choices(document_id:str,session=Depends(get_session),context=Depends(get_context)):
    from app.knowledge.service import entry,raw_file
    authorize(session,context)
    document=entry('knowledge_document',document_id,context,session)
    kind=raw_file(document,context,session)['format']
    return {'items':[{'id':value['id'],'name':value['name']} for value in custom_profiles(session,context)
                     if kind in value['formats'] and (not value.get('source_scope') or document['knowledge_base_id'] in value['source_scope'])]}

@router.get('/builtin-agents/{identifier}')
def builtin_agent(identifier:str,session=Depends(get_session),context=Depends(get_context)):
    from app.rbac import require_permission
    require_builtin(identifier);require_any_permission(context,session,('chunker.config.read','chunker.config.manage'));return public(session,context,identifier)

@router.post('/builtin-agents/{identifier}/workflow-preview')
def preview_workflow(identifier:str,body:BuiltinSettingsPatch,session=Depends(get_session),context=Depends(get_context)):
    require_builtin(identifier);require_any_permission(context,session,('chunker.config.read','chunker.config.manage'))
    value=workspace_profile(session,context,identifier)
    if value['revision']!=body.expected_revision:raise AppError('BUILTIN_CONFIG_CONFLICT','配置已变化，请读取并合并最新配置后再预览',409)
    value=apply_settings(identifier,deepcopy(value),body)
    from .slicing_workflow_view import workflow_view
    return workflow_view(identifier,value)

@router.put('/builtin-agents/{identifier}/settings')
def update_settings(identifier:str,body:BuiltinSettingsPatch,context=Depends(get_context)):
    require_builtin(identifier);context=context.model_copy(deep=True)
    with SessionLocal() as session:
        if session.bind.dialect.name!='sqlite':raise AppError('BUILTIN_STORAGE_UNSUPPORTED','当前配置事务尚未验证此数据库',503)
        session.connection().exec_driver_sql('BEGIN IMMEDIATE')
        from app.rbac import require_permission
        require_permission(context,session,'chunker.config.manage')
        old=workspace_profile(session,context,identifier)
        if old['revision']!=body.expected_revision:raise AppError('BUILTIN_CONFIG_CONFLICT','配置已更新，请读取后合并',409)
        value=deepcopy(old)
        value.pop('inherited_revision',None)
        apply_settings(identifier,value,body)
        from .slicing_workflow_view import effective
        effective(identifier,value)
        value['model_config_version']=resolve_model(session,context,value['model_config_id']) if value['model_config_id'] else None
        if value!=old or old['revision']==0:
            value['revision']+=1
            save_profile(session,context,identifier,value)
            audit(session,context,'builtin_agent_configure','builtin_agent',identifier,{'revision':value['revision']})
        session.commit();return public(session,context,identifier)

@router.delete('/builtin-agents/{identifier}')
def delete_builtin(identifier:str,session=Depends(get_session),context=Depends(get_context)):
    from app.rbac import require_permission
    require_builtin(identifier);require_permission(context,session,'chunker.config.manage')
    raise AppError('BUILTIN_AGENT_PROTECTED','内置Agent不能删除、停用或更改固定能力结构',409)


class DocumentJobSubmit(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    document_id:str=Field(min_length=1,max_length=100)
    expected_revision:int=Field(ge=0)
    max_model_calls:int=Field(default=63,ge=2,le=127)
    max_total_tokens:int=Field(default=134217728,ge=1024,le=134217728)
    context_budget:int|None=Field(default=None,ge=512,le=1048576)
    output_tokens:int|None=Field(default=None,ge=1,le=393216)
    job_timeout_seconds:int|None=Field(default=None,ge=30,le=86400)
    model_config_id:str|None=Field(default=None,min_length=1,max_length=100)
    model_config_version:str|None=Field(default=None,min_length=1,max_length=200)
    model_execution_mode:Literal['auto','direct','model_default']|None=None

    @model_validator(mode='after')
    def one_job_model_pair(self):
        if bool(self.model_config_id)!=bool(self.model_config_version) or self.model_execution_mode is not None and self.model_config_id is None:
            raise ValueError('本次模型必须同时给出模型ID和明确版本；不会改写空间配置')
        return self

@router.get('/builtin-agents/{identifier}/document-job-options')
def document_job_options(identifier:str,document_id:str,model_config_id:Annotated[str|None,Query(min_length=1,max_length=100)]=None,model_config_version:Annotated[str|None,Query(min_length=1,max_length=200)]=None,model_execution_mode:Literal['auto','direct','model_default']|None=None,session=Depends(get_session),context=Depends(get_context)):
    """Operation readiness, never a configuration read permission or projection."""
    require_builtin(identifier);authorize(session,context)
    from app.knowledge.service import entry
    entry('knowledge_document',document_id,context,session)
    selected=document_agent(session,context,document_id,identifier)
    value=profile(session,context,selected);status='waiting_configuration';ready=False;reason='MODEL_NOT_CONFIGURED'
    if bool(model_config_id)!=bool(model_config_version) or model_execution_mode is not None and not model_config_id:
        raise AppError('DOCUMENT_MODEL_CHOICE_INVALID','本次模型须明确选择模型ID和版本',422)
    selected_model=model_config_id or value['model_config_id']
    selected_version=model_config_version if model_config_id else value.get('model_config_version')
    if selected_model:
        try:resolve_model(session,context,selected_model,selected_version);status='configured'
        except AppError:status='model_unavailable';reason='MODEL_NOT_AVAILABLE'
        if status=='configured':
            try:
                from .slicing_workflow_view import effective
                effective(selected,value)
            except AppError:
                return {'id':selected,'document_id':document_id,'configuration_scope':'document_job','execution_identity':'current_caller',
                    'revision':value['revision'],'configuration_status':status,'execution_ready':False,'reason_code':'SLICING_CONFIG_INVALID'}
            if 'resources.execute' not in context.permissions:reason='EXECUTE_DENIED'
            else:
                try:
                    from app.execution.semantic_jobs import check_finalizer
                    check_finalizer(plan={'document_id':document_id},context=context,session=session)
                    ready=True;reason=None
                except AppError as error:reason='SEMANTIC_FINALIZER_UNAVAILABLE' if error.status_code==503 else 'DOCUMENT_JOB_SOURCE_DENIED'
    return {'id':selected,'document_id':document_id,'configuration_scope':'document_job','execution_identity':'current_caller',
        'revision':value['revision'],'configuration_status':status,'execution_ready':ready,'reason_code':reason}


@router.get('/builtin-agents/{identifier}/document-jobs')
def active_builtin_document(identifier:str,document_id:str,session=Depends(get_session),context=Depends(get_context)):
    require_builtin(identifier);authorize(session,context)
    from app.knowledge.service import entry,require_full_document_maintenance
    source=entry('knowledge_document',document_id,context,session,write=True)
    require_full_document_maintenance(source,context,session)
    from app.execution.document_activity import active_document_job
    return {'active_job':active_document_job(session,context,document_id)}

@router.post('/builtin-agents/{identifier}/document-job-budget')
def preview_builtin_document_budget(identifier:str,body:DocumentJobSubmit,session=Depends(get_session),context=Depends(get_context)):
    require_builtin(identifier);authorize(session,context)
    from app.execution.semantic_jobs import preview_document_budget
    return preview_document_budget(context=context,session=session,agent_id=document_agent(session,context,body.document_id,identifier),**body.model_dump())


@router.post('/builtin-agents/{identifier}/document-jobs',status_code=202)
async def submit_builtin_document(identifier:str,body:DocumentJobSubmit,session=Depends(get_session),context=Depends(get_context)):
    require_builtin(identifier);authorize(session,context)
    from app.runner.service import scheduler
    from app.execution.semantic_jobs import submit_document
    return await submit_document(scheduler=scheduler,context=context,session=session,agent_id=document_agent(session,context,body.document_id,identifier),**body.model_dump())

def document_agent(session,context,document_id,identifier=BUILTIN_ID):
    from app.knowledge.service import entry,raw_file
    root=entry('knowledge_document',document_id,context,session)
    kind=raw_file(root,context,session)['format']
    if identifier.startswith(CUSTOM_PREFIX):
        value=workspace_profile(session,context,identifier)
        if kind not in value['formats']:raise AppError('SLICING_FORMAT_MISMATCH','此智能体未配置为处理该文件格式',422)
        if value.get('source_scope') and root['knowledge_base_id'] not in value['source_scope']:
            raise AppError('SLICING_SCOPE_DENIED','此知识库不在该智能体的允许范围内',422)
    return select_specialist(kind) if identifier==BUILTIN_ID else identifier

def execution_snapshot(session,context,*,document_model_choice=None,agent_id=BUILTIN_ID):
    require_builtin(agent_id)
    value=profile(session,context,agent_id)
    choice=None
    if document_model_choice is not None:
        if (not isinstance(document_model_choice,dict) or set(document_model_choice)!={'model_config_id','model_config_version','model_execution_mode'}
                or not isinstance(document_model_choice['model_config_id'],str) or not document_model_choice['model_config_id']
                or not isinstance(document_model_choice['model_config_version'],str) or not document_model_choice['model_config_version']
                or document_model_choice['model_execution_mode'] not in ('auto','direct','model_default')):
            raise AppError('DOCUMENT_MODEL_CHOICE_INVALID','本次模型选择无效',422)
        choice=deepcopy(document_model_choice)
    identifier=choice['model_config_id'] if choice else value['model_config_id']
    expected=choice['model_config_version'] if choice else value.get('model_config_version')
    if not identifier:raise AppError('MODEL_NOT_CONFIGURED','当前任务尚未选择可用切片模型',422)
    version=resolve_model(session,context,identifier,expected)
    snapshot={'agent_id':agent_id,'contract_version':CONTRACT_VERSION,'profile_revision':value['revision'],'profile_scope':value['profile_scope'],
        'model_config_id':identifier,'model_config_version':version,'prompt_override':value['prompt_override'],
        'prompt_sha256':digest({'prompt':value['prompt_override'] if value['prompt_override'] is not None else DEFAULT_PROMPT}),
        'code_sha256':hashlib.sha256(Path(__import__('app.agents.builtin_chunker',fromlist=['x']).__file__).read_bytes()).hexdigest()}
    snapshot['model_execution_mode']=choice['model_execution_mode'] if choice else value.get('model_execution_mode') or 'auto'
    if choice:snapshot['document_model_choice']=choice
    if value.get('recipe_version')=='portable-slicing-1.0.0':
        snapshot.update({k:value.get(k) for k in ('recipe_version','stage_prompts','source_scope','recipe_options')})
    strategy=value.get('strategy',agent_id)
    if strategy in SPECIALISTS:
        snapshot.update(recipe_options=value.get('recipe_options') or portable_defaults(strategy)['recipe_options'],recipe_version='portable-slicing-1.0.0',stage_prompts=specialist_prompts(strategy,value.get('stage_prompts')),
            specialist_policy_sha256=digest(SPECIALISTS[strategy]),inherited_revision=value.get('inherited_revision'))
        if strategy!=agent_id:snapshot.update(strategy=strategy,formats=value['formats'])
    return snapshot


def assert_snapshot(session,context,expected):
    if execution_snapshot(session,context,document_model_choice=expected.get('document_model_choice'),agent_id=expected.get('agent_id',BUILTIN_ID))!=expected:raise AppError('BUILTIN_CONFIG_CHANGED','切片Agent或本次模型配置变化，不能继续旧任务',409)

def protect_resource(kind,identifier,payload):
    if kind=='agent' and identifier in {BUILTIN_ID,*SPECIALISTS} or kind=='agent_version' and payload.get('agent_id') in {BUILTIN_ID,*SPECIALISTS}:
        raise AppError('BUILTIN_AGENT_PROTECTED','内置Agent不能通过普通Agent接口改写、停用或发布替代版本',409)
