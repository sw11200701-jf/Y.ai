"""Service-principal context; never authenticates a human or fabricates a session."""
import importlib,json,re
from typing import Any,Literal
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from app.errors import AppError
from app.settings import settings
from app.db import new_id,utc_now,Resource
from app.contracts import BusinessContext
from app.machine_models import ServicePrincipal,ServiceResourceGrant,ServiceResourceBinding,MachineRunAuthorization
from sqlalchemy import select

SCOPES=frozenset({'app.read','workflow.invoke','chat.invoke','runs.read','events.read','runs.cancel','runs.resume','files.upload','files.read','trace.read','conversations.read'})
RUNTIME_FEATURES=('page.workbench','page.ai','page.knowledge','page.database','page.plugins','page.runs')
KEY_PATTERN=re.compile(r'zpa_[0-9a-f]{16}_[A-Za-z0-9_-]{43,86}')


class RuntimeAuthority(BaseModel):
    model_config=ConfigDict(extra='forbid')
    schema_version:Literal['application-authority-v1']='application-authority-v1'
    key_id:str=Field(min_length=1,max_length=100)
    client_id:str=Field(min_length=1,max_length=100)
    principal_id:str=Field(min_length=1,max_length=100)
    application_id:str=Field(min_length=1,max_length=100)
    version_id:str=Field(min_length=1,max_length=100)
    tenant_id:str=Field(min_length=1,max_length=100)
    workspace_id:str=Field(min_length=1,max_length=100)
    external_user_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    scopes:list[str]=Field(max_length=11)
    resource_constraints:dict[str,Any]=Field(default_factory=dict)
    runtime_policy:dict[str,Any]=Field(default_factory=dict)
    credential_revision:int=Field(ge=1)


def is_service(context):return getattr(context,'principal_type','human')=='service'
def enabled():
    if not settings.application_api_enabled:raise AppError('APP_API_DISABLED','对外应用API尚未启用',503)
def bridge():
    try:return importlib.import_module('app.external_api.bridge')
    except ModuleNotFoundError as exc:
        if exc.name not in {'app.external_api','app.external_api.bridge'}:raise
        raise AppError('APP_API_NOT_READY','对外应用凭据模块尚未就绪',503) from exc
def authority_value(value):
    if isinstance(value,BaseModel):value=value.model_dump(mode='json')
    try:result=RuntimeAuthority.model_validate(value)
    except ValidationError as exc:raise AppError('APP_AUTHORITY_INVALID','机器身份授权描述不完整',503) from exc
    if len(result.scopes)!=len(set(result.scopes)) or not set(result.scopes)<=SCOPES:
        raise AppError('APP_AUTHORITY_INVALID','机器身份能力范围无效',503)
    return result


def provision_principal(session,*,client_id,application_id,tenant_id,workspace_id,display_name,created_by):
    """Management callback, called after the caller's human permissions/ACL check."""
    from app.auth_models import User
    from app.rbac import user_policy
    creator=session.get(User,created_by,populate_existing=True)
    if creator is None or not creator.active or (creator.tenant_id,creator.workspace_id)!=(tenant_id,workspace_id) or 'applications.keys.manage' not in user_policy(creator,session)[1]:
        raise AppError('PERMISSION_DENIED','创建机器主体需要当前应用Key管理权限',403)
    row=session.scalar(select(ServicePrincipal).where(ServicePrincipal.client_id==client_id))
    if row:
        if (row.application_id,row.tenant_id,row.workspace_id)!=(application_id,tenant_id,workspace_id):
            raise AppError('APP_CLIENT_CONFLICT','集成客户端的机器主体范围不一致',409)
        return row
    row=ServicePrincipal(id=new_id('svc'),client_id=client_id,application_id=application_id,tenant_id=tenant_id,workspace_id=workspace_id,
        display_name=display_name[:120],active=True,revision=1,allowed_entities='[]',created_by=created_by,created_at=utc_now(),updated_at=utc_now())
    session.add(row);session.flush();return row


def context_from_authority(session,value):
    enabled();authority=authority_value(value)
    row=session.get(ServicePrincipal,authority.principal_id,populate_existing=True)
    if row is None or not row.active or (row.tenant_id,row.workspace_id,row.application_id,row.client_id)!=(authority.tenant_id,authority.workspace_id,authority.application_id,authority.client_id):
        raise AppError('APP_PRINCIPAL_REVOKED','应用机器主体已停用或无权访问',401)
    entities=json.loads(row.allowed_entities)
    restricted=authority.resource_constraints.get('allowed_entities')
    if restricted is not None:
        if not isinstance(restricted,list) or any(not isinstance(v,str) for v in restricted):raise AppError('APP_AUTHORITY_INVALID','机器数据范围无效',503)
        entities=[v for v in entities if v in restricted]
    # Synthetic framing is not a real data grant. Finance/data access still
    # requires explicit current Resource/DataGrant and entity checks.
    entities=list(dict.fromkeys(entities))
    return BusinessContext(principal_type='service',tenant_id=row.tenant_id,workspace_id=row.workspace_id,operator_id=row.id,actor_id=row.id,
        role='service',session_id=None,permissions=list(RUNTIME_FEATURES),allowed_entities=entities,
        allowed_origins=list(settings.tool_allowed_origins),allowed_tool_origins=list(settings.tool_allowed_origins),
        application_id=row.application_id,application_version_id=authority.version_id,client_id=row.client_id,auth_key_id=authority.key_id,
        external_user_hash=authority.external_user_hash,credential_revision=authority.credential_revision)


def current_authority(session,context):
    enabled()
    if not is_service(context) or context.session_id is not None or context.operator_id!=context.actor_id:
        raise AppError('APP_KEY_INVALID','应用机器身份无效',401)
    required=('auth_key_id','client_id','application_id','application_version_id','external_user_hash')
    if any(not getattr(context,name,None) for name in required):raise AppError('APP_KEY_INVALID','应用机器身份无效',401)
    authority=authority_value(bridge().authority_by_id(session,key_id=context.auth_key_id,client_id=context.client_id,
        application_id=context.application_id,version_id=context.application_version_id,external_user_hash=context.external_user_hash))
    expected=(context.auth_key_id,context.client_id,context.operator_id,context.application_id,context.application_version_id,context.tenant_id,context.workspace_id,context.external_user_hash)
    actual=(authority.key_id,authority.client_id,authority.principal_id,authority.application_id,authority.version_id,authority.tenant_id,authority.workspace_id,authority.external_user_hash)
    if actual!=expected:raise AppError('APP_KEY_INVALID','应用机器身份已失效',401)
    return authority


def validate_service_context(context,session):
    fresh=context_from_authority(session,current_authority(session,context))
    context.role='service';context.permissions=fresh.permissions;context.allowed_entities=fresh.allowed_entities
    context.credential_revision=fresh.credential_revision
    return session.get(ServicePrincipal,context.operator_id,populate_existing=True)


def require_scope(session,context,scope):
    authority=current_authority(session,context)
    validate_service_context(context,session)
    if scope not in authority.scopes:raise AppError('APP_SCOPE_DENIED','此应用Key没有所需调用能力',403)
    return authority


def binding_matches(binding,context):
    return binding is not None and (binding.principal_id,binding.tenant_id,binding.workspace_id,binding.application_id,binding.version_id,binding.client_id,binding.external_user_hash)==(
        context.operator_id,context.tenant_id,context.workspace_id,context.application_id,context.application_version_id,context.client_id,context.external_user_hash)


def bind_resource(session,row,context):
    if not is_service(context):return
    current=session.get(ServiceResourceBinding,row.id)
    if current is not None:
        if not binding_matches(current,context):raise AppError('NOT_FOUND','资源不存在或无权访问',404)
        return
    session.add(ServiceResourceBinding(resource_pk=row.id,principal_id=context.operator_id,tenant_id=context.tenant_id,workspace_id=context.workspace_id,
        application_id=context.application_id,version_id=context.application_version_id,client_id=context.client_id,external_user_hash=context.external_user_hash,
        created_by_key_id=context.auth_key_id,created_at=utc_now()));session.flush()


def validate_owned_resource(session,row,context):
    if row.owner_id==context.operator_id:
        if not binding_matches(session.get(ServiceResourceBinding,row.id,populate_existing=True),context):
            raise AppError('NOT_FOUND','资源不存在或无权访问',404)
        return True
    return False


def resource_granted(session,row,context,action='use'):
    authority=current_authority(session,context)
    row=session.scalar(select(Resource).where(Resource.id==row.id).execution_options(populate_existing=True))
    if row is None or (row.tenant_id,row.workspace_id)!=(context.tenant_id,context.workspace_id):return False
    state=json.loads(row.payload)
    if state.get('status') in {'disabled','deleted','archived'} or state.get('enabled') is False or state.get('active') is False:return False
    if validate_owned_resource(session,row,context):return True
    grant=session.scalar(select(ServiceResourceGrant).where(ServiceResourceGrant.principal_id==context.operator_id,
        ServiceResourceGrant.version_id==context.application_version_id,ServiceResourceGrant.resource_pk==row.id,ServiceResourceGrant.active==True).execution_options(populate_existing=True))
    if grant is None or action not in json.loads(grant.actions):return False
    if not delegation_anchor_current(session,row,grant.created_by,context):return False
    bundle=bridge().load_release(session,authority=authority.model_dump(mode='json'))
    if isinstance(bundle,BaseModel):bundle=bundle.model_dump(mode='json')
    dependencies=bundle.get('dependency_manifest',[]) if isinstance(bundle,dict) else []
    if not isinstance(dependencies,list) or len(dependencies)>512 or not any(isinstance(item,dict) and item.get('kind')==row.kind and item.get('resource_id')==row.resource_id and action in item.get('actions',['use']) for item in dependencies):return False
    constraints=authority.resource_constraints.get('resources')
    mapping={'export':'export_detail','read':'use'}
    if constraints is not None and not any(isinstance(v,dict) and v.get('kind')==row.kind and v.get('resource_id')==row.resource_id and action in {mapping.get(a,a) for a in v.get('actions',[])} for v in constraints):return False
    return True


def delegation_anchor_current(session,row,created_by,context):
    from app.auth_models import User,ResourceGrant
    from app.rbac import user_policy
    user=session.get(User,created_by,populate_existing=True)
    if user is None or not user.active or (user.tenant_id,user.workspace_id)!=(context.tenant_id,context.workspace_id):return False
    permissions=set(user_policy(user,session)[1])
    if 'applications.keys.manage' not in permissions:return False
    from app.data_access import DATA_KINDS,_authorize_subject_resource
    if row.kind in DATA_KINDS:
        try:_authorize_subject_resource(session=session,context=context,user=user,kind=row.kind,resource_id=row.resource_id,action='manage',check_derivation=False);return True
        except AppError:return False
    return row.owner_id==user.id or session.scalar(select(ResourceGrant.resource_pk).where(ResourceGrant.resource_pk==row.id,ResourceGrant.user_id==user.id,ResourceGrant.permission=='write')) is not None


def assert_release_dependencies(session,context):
    authority=current_authority(session,context)
    bundle=bridge().load_release(session,authority=authority.model_dump(mode='json'))
    if isinstance(bundle,BaseModel):bundle=bundle.model_dump(mode='json')
    refs=bundle.get('dependency_manifest',[]) if isinstance(bundle,dict) else None
    if not isinstance(refs,list) or len(refs)>512:raise AppError('APP_DEPENDENCY_CHANGED','应用发布依赖清单无效',409)
    from app.data_access import DATA_KINDS,authorize_data_resource
    for item in refs:
        if not isinstance(item,dict) or not isinstance(item.get('kind'),str) or not isinstance(item.get('resource_id'),str):raise AppError('APP_DEPENDENCY_CHANGED','应用发布依赖清单无效',409)
        row=session.scalar(select(Resource).where(Resource.kind==item['kind'],Resource.resource_id==item['resource_id'],Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id).execution_options(populate_existing=True))
        for action in item.get('actions',['use']):
            if row is None or not resource_granted(session,row,context,action):raise AppError('NOT_FOUND','应用依赖已不可访问',404)
            if row.kind in DATA_KINDS:authorize_data_resource(session=session,context=context,kind=row.kind,resource_id=row.resource_id,action=action)
        if item.get('revision') is not None and row.kind in {'model_provider','model_configuration','connection','plugin_version'}:
            if json.loads(row.payload).get('revision')!=item['revision']:raise AppError('APP_DEPENDENCY_CHANGED','应用固定依赖配置已变更，需要重新发布',409)
    return bundle


def authority_resource_rule(session,context,row,action):
    authority=current_authority(session,context)
    # Generated resources cannot appear in a pre-release static manifest. Their
    # exact machine/client/user binding and live source derivations are the
    # boundary; this does not confer access to a publisher-owned source.
    if validate_owned_resource(session,row,context):return None
    entries=authority.resource_constraints.get('resources')
    if entries is None:return None
    mapping={'export':'export_detail','read':'use'}
    values=[entry for entry in entries if isinstance(entry,dict) and (entry.get('kind'),entry.get('resource_id'))==(row.kind,row.resource_id) and action in {mapping.get(a,a) for a in entry.get('actions',[])}]
    if not values:raise AppError('NOT_FOUND','资源不存在或无权访问',404)
    # Duplicate constraints are not OR grants. Requiring a unique exact entry
    # prevents two independently broad rules from weakening an intersection.
    if len(values)!=1:raise AppError('APP_AUTHORITY_INVALID','机器资源范围重复或不明确',503)
    from app.data_access import RowScope,Columns
    return {'actions':[action],'row_scope':RowScope.model_validate(values[0].get('row_scope',{})).model_dump(),
        'columns':Columns.model_validate(values[0].get('columns',{})).model_dump(),'delegable_actions':[]}


def revalidate_application_binding(context,*,session,run_id=None,action='runs.read'):
    authority=require_scope(session,context,action)
    if run_id is not None:assert_run_partition(session,run_id,context)
    return authority


def authorize_application_result(context,run_id,*,session,operation='runs.read'):
    revalidate_application_binding(context,session=session,run_id=run_id,action=operation)
    from app.execution.trace import authorize_view
    return authorize_view(session,context,run_id)


def assert_run_partition(session,run_id,context):
    binding=session.get(MachineRunAuthorization,run_id,populate_existing=True)
    if not binding_matches(binding,context):raise AppError('NOT_FOUND','运行不存在或无权访问',404)
    import hashlib
    bundle=bridge().load_release(session,authority=current_authority(session,context).model_dump(mode='json'))
    body=json.dumps(bundle,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    if hashlib.sha256(body.encode()).hexdigest()!=binding.release_sha256:raise AppError('APP_DEPENDENCY_CHANGED','运行固定发布快照发生变化',409)
    return binding


def fresh_run_context(session,run_id):
    binding=session.get(MachineRunAuthorization,run_id,populate_existing=True)
    if binding is None:return None
    value=bridge().authority_by_id(session,key_id=binding.executing_key_id,client_id=binding.client_id,application_id=binding.application_id,
        version_id=binding.version_id,external_user_hash=binding.external_user_hash)
    authority=authority_value(value)
    if not set(authority.scopes)&{'workflow.invoke','chat.invoke'}:raise AppError('APP_SCOPE_DENIED','执行Key的调用能力已撤销',403)
    context=context_from_authority(session,authority);assert_run_partition(session,run_id,context);return context


def is_open_path(path):return path=='/api/open/v1' or path.startswith('/api/open/v1/')


def sanitize_credential_url(request):
    """Reject credential URLs before access logging can see their raw values."""
    from urllib.parse import parse_qsl,unquote
    scope=request.scope;query=scope.get('query_string',b'').decode('latin-1')
    path=scope.get('path','');decoded_path=path
    for _ in range(3):decoded_path=unquote(decoded_path)
    pattern=re.compile(r'zpa_[0-9a-f]{16,24}_[A-Za-z0-9_-]{43,86}')
    bad_path=bool(pattern.search(decoded_path));bad_query=False
    try:
        pairs=parse_qsl(query,keep_blank_values=True,max_num_fields=64)
        bad_query=any(re.sub('[^a-z0-9]','',key.lower()) in {'apikey','key','token','accesstoken','authorization','bearer'} or pattern.search(value) for key,value in pairs)
        bad_query=bad_query or bool(pattern.search(unquote(unquote(query)))) or len(query)>8192
    except ValueError:bad_query=True
    if bad_path or bad_query:
        if bad_path:scope['path']='/api/open/v1/[credential-redacted]';scope['raw_path']=b'/api/open/v1/[credential-redacted]'
        scope['query_string']=b'credential_redacted=1'
        if hasattr(request,'_url'):delattr(request,'_url')
        raise AppError('APP_KEY_INVALID','应用Key只能通过Authorization Bearer传递，不能放入URL',401)


def bearer_token(request):
    headers=request.headers.getlist('authorization')
    if len(headers)!=1:raise AppError('APP_KEY_INVALID','需要有效的应用Bearer Key',401)
    match=re.fullmatch(r'Bearer ([^\s,]+)',headers[0],re.IGNORECASE)
    if not match or not KEY_PATTERN.fullmatch(match[1]):raise AppError('APP_KEY_INVALID','需要有效的应用Bearer Key',401)
    return match[1]


def _authenticate_snapshot(token,version,user,operation):
    from app.db import SessionLocal
    with SessionLocal() as session:
        try:
            from app.external_api.storage import write_gate
            write_gate(session)
            authority=authority_value(bridge().verify_bearer(session,token=token,requested_version_id=version,external_user=user))
            context_from_authority(session,authority)
            bridge().check_http_quota(session,authority=authority.model_dump(mode='json'),operation=operation)
            session.commit()
            return authority.model_dump(mode='json')
        except AppError as exc:
            if exc.status_code==429:session.commit()
            else:session.rollback()
            raise


async def prepare_open_request(request):
    """Bounded JSON user alias, then worker-owned short credential/quota txn."""
    import asyncio
    sanitize_credential_url(request)
    if not is_open_path(request.scope.get('path','')):return
    enabled()
    if not settings.development_http and request.url.scheme!='https':raise AppError('HTTPS_REQUIRED','对外应用API需要HTTPS',426)
    token=bearer_token(request)
    content_type=request.headers.get('content-type','').split(';',1)[0].strip().lower()
    user=request.headers.get('x-external-user')
    if request.method in {'POST','PUT','PATCH'} and content_type=='application/json':
        chunks=[];size=0
        async for part in request.stream():
            size+=len(part)
            if size>262144:raise AppError('APP_REQUEST_TOO_LARGE','应用JSON请求超过256KiB限制',413)
            chunks.append(part)
        body=b''.join(chunks)
        # Starlette's cached receive sends this same bounded body to FastAPI.
        request._body=body
        try:value=json.loads(body or b'{}')
        except (ValueError,RecursionError):raise AppError('APP_INPUT_INVALID','应用请求不是有效JSON对象',422) from None
        if not isinstance(value,dict):raise AppError('APP_INPUT_INVALID','应用请求必须是JSON对象',422)
        alias=value.get('user')
        if alias is not None:
            if not isinstance(alias,str) or user is not None and alias!=user:raise AppError('APP_INPUT_INVALID','外部用户标识格式或请求头不一致',422)
            user=alias
        def inspect(item,depth=0):
            if depth>64:raise AppError('APP_INPUT_INVALID','应用输入嵌套过深',422)
            if isinstance(item,str) and KEY_PATTERN.search(item):raise AppError('APP_INPUT_INVALID','业务输入不能包含应用凭据',422)
            if isinstance(item,dict):
                for key,child in item.items():inspect(key,depth+1);inspect(child,depth+1)
            elif isinstance(item,list):
                for child in item:inspect(child,depth+1)
        inspect(value)
    if user is not None and (not 1<=len(user)<=128 or any(ord(char)<32 for char in user)):
        raise AppError('APP_INPUT_INVALID','外部用户标识须为1至128字符的不透明标签',422)
    version=request.headers.get('x-application-version')
    if version is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',version):raise AppError('APP_INPUT_INVALID','应用版本标识无效',422)
    operation='stream' if request.method=='GET' and request.url.path.endswith('/events') else 'read' if request.method=='GET' else 'write'
    request.state.open_verified_authority=await asyncio.to_thread(_authenticate_snapshot,token,version,user,operation)


def bind_open_request(request,session):
    enabled()
    authority=getattr(request.state,'open_verified_authority',None)
    if authority is None:raise AppError('APP_KEY_INVALID','应用请求尚未通过可信认证边界',401)
    request.state.business_context=context_from_authority(session,authority)
    request.state.external_authority=authority


def error_headers(request,exc):
    if is_open_path(request.scope.get('path','')) and exc.status_code==429:
        value=(exc.details or {}).get('retry_after',60) if isinstance(exc.details,dict) else 60
        if type(value) is not int:value=60
        return {'Retry-After':str(max(1,min(86400,value)))}
    return {}


class RuntimeBodyLimitMiddleware:
    """ASGI receive cap also covers chunked multipart before a handler writes."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or not is_open_path(scope.get('path','')):return await self.app(scope,receive,send)
        from starlette.responses import JSONResponse
        maximum=10*1024*1024+65536 if scope.get('path')=='/api/open/v1/files' else 262144
        class Limit(Exception):pass
        received=0;started=False
        async def bounded():
            nonlocal received
            message=await receive()
            if message['type']=='http.request':
                received+=len(message.get('body',b''))
                if received>maximum:
                    scope.setdefault('state',{})['application_body_too_large']=True
                    raise Limit()
            return message
        async def observe(message):
            nonlocal started
            if message['type']=='http.response.start':started=True
            await send(message)
        try:await self.app(scope,bounded,observe)
        except Limit:
            if started:raise
            response=JSONResponse({'error':{'code':'APP_REQUEST_TOO_LARGE','message':'应用请求超过大小限制','details':None},'request_id':scope.get('state',{}).get('request_id')},status_code=413)
            await response(scope,receive,send)
