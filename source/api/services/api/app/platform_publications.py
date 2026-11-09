"""Immutable API publications over the existing durable workflow runner."""
from copy import deepcopy
import asyncio, json, re
from fastapi import Depends, Request, Header
from pydantic import Field
from sqlalchemy import select
from app.db import Resource, SessionLocal, get_session, get_resource, new_id, utc_now
from app.errors import AppError
from app.platform_api import (manage, runtime, Strict, _row, _value, _put, digest, platform_context,
                              require_token_resource, current_scope)
from app.platform_files import PublicationPolicy
from app.application_release import prepare_release, digest as bundle_digest
from app.contracts import MessageEnvelope

class Publish(Strict):
    expected_revision: int = Field(default=0, ge=0)
    agent_version_id: str | None = None
    runtime_policy: PublicationPolicy = Field(default_factory=PublicationPolicy)

class Enabled(Strict):
    expected_revision: int = Field(ge=1)
    enabled: bool

class Invocation(Strict):
    parameters: dict = Field(default_factory=dict)
    prompt: str = Field(default='', max_length=20000)
    message: MessageEnvelope | None = None

def config_id(kind, identifier):return digest(kind+'|'+identifier)

def _asset(request, session, kind, identifier, write=False):
    from app.auth import identity
    from app.rbac import require_permission
    if kind not in {'agent','workflow'}:raise AppError('RESOURCE_KIND_DENIED','此资源不能发布API',422)
    if write:
        from app.platform_api import write_lock
        write_lock(session)
    context=identity(request)
    if context.principal_type!='human':raise AppError('AUTH_SCHEME_NOT_ALLOWED','发布需要登录会话',401)
    if write:require_permission(context,session,'platform.api.publish')
    get_resource(session,kind,identifier,context,access='write' if write else 'read')
    row=_row(session,'platform_api_config',config_id(kind,identifier),context.tenant_id,context.workspace_id)
    if row and (row.tenant_id,row.workspace_id)!=(context.tenant_id,context.workspace_id):raise AppError('NOT_FOUND','API配置不存在',404)
    return context,row

def publication_summary(row):
    value=_value(row);bundle=value['bundle'];space=row.workspace_id
    return {'id':row.resource_id,'version':value['version'],'created_at':row.created_at,
        'source':bundle['source_identity'],'input_schema':bundle['input_schema'],'output_schema':bundle['output_schema'],
        'invoke_url':f'/api/platform/v1/spaces/{space}/publications/{row.resource_id}/invoke',
        'result_url':f'/api/platform/v1/spaces/{space}/runs/{{run_id}}','snapshot_sha256':value['bundle_sha256']}

@manage.get('/platform/assets/{kind}/{identifier}/api')
def asset_api(kind:str, identifier:str, request:Request, session=Depends(get_session)):
    context,row=_asset(request,session,kind,identifier)
    value=_value(row) if row else {'revision':0,'enabled':False,'versions':[]}
    items=[]
    for version_id in value['versions']:
        version=_row(session,'platform_publication',version_id,context.tenant_id,context.workspace_id)
        if version and (version.tenant_id,version.workspace_id)==(context.tenant_id,context.workspace_id):items.append(publication_summary(version))
    return {'source_kind':kind,'source_id':identifier,'workspace_id':context.workspace_id,
        'revision':value['revision'],'enabled':value['enabled'],'versions':items,
        'token_url':'/api/platform/v1/token','key_manager_url':'/?page=platform-access'}

@manage.post('/platform/assets/{kind}/{identifier}/api/publish',status_code=201)
def publish(kind:str,identifier:str,body:Publish,request:Request,session=Depends(get_session)):
    context,row=_asset(request,session,kind,identifier,True)
    value=_value(row) if row else {'revision':0,'enabled':False,'versions':[]}
    if value['revision']!=body.expected_revision:raise AppError('PUBLICATION_CONFLICT','发布配置已变化，请刷新',409)
    source={'kind':kind,'id':identifier,'version_id':body.agent_version_id}
    bundle=prepare_release(session,context,source,body.runtime_policy.model_dump(),permission='platform.api.publish')
    publication_id=new_id('pub');version=len(value['versions'])+1
    publication=_put(session,'platform_publication',publication_id,{'id':publication_id,'version':version,
        'config_id':config_id(kind,identifier),'bundle':bundle,'bundle_sha256':bundle_digest(bundle)},context.tenant_id,context.workspace_id,context.operator_id)
    value.update(revision=value['revision']+1,enabled=True,versions=value['versions']+[publication_id])
    _put(session,'platform_api_config',config_id(kind,identifier),value,context.tenant_id,context.workspace_id,context.operator_id)
    from app.rbac import audit
    audit(session,context,'platform_api.publish',kind,identifier,{'publication_id':publication_id,'version':version})
    return publication_summary(publication)

@manage.put('/platform/assets/{kind}/{identifier}/api')
def enable(kind:str,identifier:str,body:Enabled,request:Request,session=Depends(get_session)):
    context,row=_asset(request,session,kind,identifier,True)
    if row is None:raise AppError('NOT_FOUND','尚未发布API版本',404)
    value=_value(row)
    if body.expected_revision!=value['revision']:raise AppError('PUBLICATION_CONFLICT','发布配置已变化，请刷新',409)
    value.update(enabled=body.enabled,revision=value['revision']+1);row.payload=json.dumps(value)
    return {'enabled':value['enabled'],'revision':value['revision']}

def authorized_publication(session,context,publication_id,execute=True):
    row=_row(session,'platform_publication',publication_id,context.tenant_id,context.workspace_id)
    if not row or (row.tenant_id,row.workspace_id)!=(context.tenant_id,context.workspace_id):raise AppError('NOT_FOUND','发布版本不存在',404)
    value=_value(row);config=_row(session,'platform_api_config',value['config_id'],context.tenant_id,context.workspace_id)
    if not config or not _value(config)['enabled'] or publication_id not in _value(config)['versions']:raise AppError('PUBLICATION_DISABLED','此API已停用',403)
    bundle=value['bundle']
    if bundle_digest(bundle)!=value['bundle_sha256']:raise AppError('PUBLICATION_CHANGED','发布快照校验失败',409)
    source=bundle['source_identity'];source_row=_row(session,source['kind'],source['id'],context.tenant_id,context.workspace_id)
    if source_row is None:raise AppError('NOT_FOUND','发布来源已不可用',404)
    require_token_resource(session,context,source_row,'execute' if execute else 'read')
    # Every dependency is still checked against this token and the real account's
    # current domain grants. Saved snapshot fields never confer access.
    for dependency in bundle['dependency_manifest']:
        kind,identifier=dependency['kind'],dependency['resource_id']
        resource=get_resource(session,kind,identifier,context)
        if dependency.get('revision') is not None and resource.get('revision')!=dependency['revision']:raise AppError('PUBLICATION_DEPENDENCY_CHANGED','发布依赖版本已变化，请重新发布',409)
        if kind=='agent_version' and resource.get('status')!='published':raise AppError('PUBLICATION_DEPENDENCY_CHANGED','智能体发布版本已停用',409)
        if kind=='agent' and execute:
            from app.resource_policy import require_execute
            require_execute(session,context,kind,identifier)
    return value

def authorize_run_publication(session,context,run,*,execute=False):
    identity=run.get('request',{}).get('platform_publication')
    if not isinstance(identity,dict):raise AppError('PLATFORM_ADMISSION_REQUIRED','缺少平台发布调用授权',403)
    publication=authorized_publication(session,context,identity.get('id'),execute=execute)
    if publication['bundle_sha256']!=identity.get('sha256'):raise AppError('PUBLICATION_CHANGED','运行发布身份不匹配',409)

def project_result(session,context,run):
    from app.execution.storage import authorize_dependencies
    from app.external_api.projection import public_outputs
    authorize_dependencies(session,context,run)
    identity=run['request']['platform_publication'];value=authorized_publication(session,context,identity['id'],execute=False)
    result={'run_id':run['id'],'publication_id':identity['id'],'status':run['status'],
        'result_url':f'/api/platform/v1/spaces/{context.workspace_id}/runs/{run["id"]}'}
    if run['status']=='succeeded':
        from app.platform_files import normalize_outputs
        result['outputs']=public_outputs(normalize_outputs(session,context,run.get('outputs',{}),value['bundle']['output_schema']),value['bundle']['output_schema'])
    if run.get('error'):result['error']={'code':run['error'].get('code','EXECUTION_FAILED'),'message':'执行未完成，请联系资源管理员查看运行记录'}
    return result

@runtime.get('/spaces/{workspace_id}/publications/{publication_id}')
def publication_detail(workspace_id:str,publication_id:str,request:Request,session=Depends(get_session)):
    context=platform_context(session,request.state.platform_token_id,workspace_id)
    authorized_publication(session,context,publication_id,execute=False)
    return publication_summary(_row(session,'platform_publication',publication_id,context.tenant_id,context.workspace_id))

@runtime.post('/spaces/{workspace_id}/publications/{publication_id}/invoke',status_code=202)
async def invoke(workspace_id:str,publication_id:str,body:Invocation,request:Request,idempotency_key:str=Header(alias='Idempotency-Key')):
    from app.runner.service import scheduler
    from app.contracts import RunCreate, WorkflowDefinition
    from app.runner.variables import check_size
    if not re.fullmatch(r'[\x21-\x7e]{8,128}',idempotency_key):raise AppError('IDEMPOTENCY_INVALID','请提供8至128字符的幂等标识',422)
    check_size(body.model_dump(),1048576,'API parameters')
    token_id=request.state.platform_token_id
    # The bounded DB admission runs off the ASGI loop. It commits before enqueue.
    async with scheduler.admission_lock:
        result,run_id,current=await asyncio.to_thread(_admit,token_id,workspace_id,publication_id,body,idempotency_key)
        if run_id is not None:scheduler.enqueue_committed(run_id,current)
    return result

def _admit(token_id,workspace_id,publication_id,body,idempotency_key):
    from app.runner.service import scheduler
    from app.contracts import RunCreate, WorkflowDefinition
    with SessionLocal() as session:
        session.connection().exec_driver_sql('BEGIN IMMEDIATE')
        context=platform_context(session,token_id,workspace_id)
        value=authorized_publication(session,context,publication_id)
        claim_id=digest(context.operator_id+'|'+workspace_id+'|'+publication_id+'|'+idempotency_key)
        claim=_row(session,'platform_invocation',claim_id,context.tenant_id,context.workspace_id);body_hash=bundle_digest(body.model_dump())
        if claim:
            saved=_value(claim)
            if saved['body_hash']!=body_hash:raise AppError('IDEMPOTENCY_CONFLICT','幂等标识已用于其他参数',409)
            return {**project_result(session,context,get_resource(session,'run',saved['run_id'],context)),'replayed':True},None,None
        bundle=value['bundle'];definition=WorkflowDefinition.model_validate(bundle['workflow_snapshot'])
        run_request=RunCreate(workflow=definition,prompt=body.prompt,inputs=body.parameters,message=body.message,
            platform_publication={'id':publication_id,'sha256':value['bundle_sha256']})
        prepared={'type':'text','text':body.prompt,'inputs':body.parameters,'attachment_ids':[],'attachments':[],'source_refs':[],
            'capabilities':{'requires_vision':False,'requires_audio':False,'requires_document_extraction':False}}
        if body.message is not None:
            from app.messages import resolve_message
            prepared=asyncio.run(resolve_message(message=body.message,prompt=body.prompt,inputs=body.parameters,context=context,session=session))
        run=asyncio.run(scheduler._submit(run_request,context,session,frozen_agents=deepcopy(bundle['agent_snapshots']),
            prepared_message=prepared,commit=False,enqueue=False))
        _put(session,'platform_invocation',claim_id,{'run_id':run['id'],'body_hash':body_hash},context.tenant_id,workspace_id,context.operator_id)
        result=project_result(session,context,run);session.commit()
    return {**result,'replayed':False},run['id'],context

@runtime.get('/spaces/{workspace_id}/runs/{run_id}')
def result(workspace_id:str,run_id:str,request:Request,session=Depends(get_session)):
    context=platform_context(session,request.state.platform_token_id,workspace_id)
    row=_row(session,'platform_run_authorization',run_id,context.tenant_id,context.workspace_id)
    if not row or row.owner_id!=context.operator_id or row.workspace_id!=workspace_id:raise AppError('NOT_FOUND','运行不存在或无权访问',404)
    return project_result(session,context,get_resource(session,'run',run_id,context))
