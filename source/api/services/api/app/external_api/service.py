"""Management domain. Session-only authorization plus resource ownership/sharing."""
from sqlalchemy import select,func
from app.db import new_id,utc_now
from app.errors import AppError
from app.rbac import require_permission,audit
from .models import Application,ApplicationShare,ApplicationVersion,ApplicationClient,ApplicationCredential,CallRecord,KeyActivity
from .schemas import Quotas,RuntimePolicy,ReleaseBundleV1,ApplicationView,ClientView,KeyMetadata,VersionView,KeyCreate,ClientOptionsView,ResourceOption,OptionBlocker,CallView
from .storage import write_gate,fresh,dump,load,expiry,iso,epoch,conflict,require_subset
from .keys import issue_key
from .values import canonical_payload_digest

PERMISSIONS=('applications.read','applications.manage','applications.publish','applications.keys.manage','applications.records.read','applications.runs.control')


def human(session,context,permission):
    if getattr(context,'principal_type','human')!='human' or not context.session_id:
        raise AppError('AUTH_REQUIRED','应用管理需要有效的人类登录会话',401)
    require_permission(context,session,permission)


def visible(session,row,context,permission):
    if (row.tenant_id,row.workspace_id)!=(context.tenant_id,context.workspace_id):return False
    if row.owner_id==context.operator_id:return True
    sharing=session.scalar(select(ApplicationShare).where(ApplicationShare.application_id==row.id,ApplicationShare.user_id==context.operator_id))
    return sharing is not None and permission in load(sharing.permissions)


def application(session,context,app_id,permission='applications.read'):
    human(session,context,permission);row=fresh(session,Application,app_id)
    if row is None or row.status=='deleted' or not visible(session,row,context,permission):
        raise AppError('NOT_FOUND','应用不存在或无权访问',404)
    return row


def app_view(session,row,context):
    values=[p for p in PERMISSIONS if p in context.permissions and visible(session,row,context,p)]
    return ApplicationView(application_id=row.id,name=row.name,type=row.kind,description=row.description,status=row.status,revision=row.revision,
        active_version_id=row.active_version_id,created_at=row.created_at,updated_at=row.updated_at,permissions=values).model_dump(mode='json')


def page(items,page,page_size):return {'items':items[(page-1)*page_size:page*page_size],'total':len(items),'page':page,'page_size':page_size}


def list_applications(session,context,*,page_number=1,page_size=20):
    human(session,context,'applications.read')
    rows=session.scalars(select(Application).where(Application.tenant_id==context.tenant_id,Application.workspace_id==context.workspace_id,Application.status!='deleted').order_by(Application.created_at.desc(),Application.id))
    return page([app_view(session,r,context) for r in rows if visible(session,r,context,'applications.read')],page_number,page_size)


def create_application(session,context,data):
    write_gate(session);human(session,context,'applications.manage');now=utc_now()
    row=Application(id=new_id('app'),tenant_id=context.tenant_id,workspace_id=context.workspace_id,owner_id=context.operator_id,
        name=data.name,kind=data.type,description=data.description,status='active',revision=1,quotas=dump(Quotas().model_dump()),created_at=now,updated_at=now)
    session.add(row);session.flush();audit(session,context,'application_create','application',row.id)
    return app_view(session,row,context)


def patch_application(session,context,app_id,data):
    write_gate(session);row=application(session,context,app_id,'applications.manage')
    if row.revision!=data.expected_revision:conflict()
    for field in ('name','description','status'):
        value=getattr(data,field)
        if value is not None:setattr(row,field,value)
    if data.quotas is not None:row.quotas=dump(data.quotas.model_dump())
    row.revision+=1;row.updated_at=utc_now();session.flush()
    audit(session,context,'application_update','application',row.id,{'revision':row.revision,'status':row.status})
    return app_view(session,row,context)


def _version(session,app_id,version_id):
    row=fresh(session,ApplicationVersion,version_id)
    if row is None or row.application_id!=app_id or row.status!='published':
        raise AppError('APP_VERSION_UNAVAILABLE','应用发布版本不可用',422)
    return row


def version_view(row):
    bundle=load(row.bundle)
    return VersionView(version_id=row.id,application_id=row.application_id,version=row.sequence,status=row.status,source=load(row.source),
        source_sha256=row.source_sha256,bundle_sha256=row.bundle_sha256,input_schema=bundle['input_schema'],output_schema=bundle['output_schema'],
        runtime_policy=bundle['runtime_policy'],created_at=row.created_at).model_dump(mode='json')


def list_versions(session,context,app_id,*,page_number=1,page_size=20):
    application(session,context,app_id)
    rows=session.scalars(select(ApplicationVersion).where(ApplicationVersion.application_id==app_id).order_by(ApplicationVersion.sequence.desc()))
    return page([version_view(row) for row in rows],page_number,page_size)


def prepare_version(session,context,app_id,data,prepare_release):
    row=application(session,context,app_id,'applications.publish')
    if row.kind!=data.source.kind:raise AppError('APP_INPUT_INVALID','源类型与应用类型不一致',422)
    prepared=prepare_release(session,context,data.source.model_dump(mode='json'),data.runtime_policy.model_dump(mode='json'))
    if hasattr(prepared,'model_dump'):prepared=prepared.model_dump(mode='json')
    try:bundle=ReleaseBundleV1.model_validate(prepared).model_dump(mode='json')
    except (ValueError,TypeError):raise AppError('APP_RELEASE_INVALID','中央发布快照结构校验失败',503) from None
    source_hash=bundle['source_identity'].get('source_sha256')
    if not isinstance(source_hash,str) or len(source_hash)!=64 or any(c not in '0123456789abcdef' for c in source_hash):
        raise AppError('APP_RELEASE_INVALID','中央发布检查未返回准确源摘要',503)
    if data.source.expected_sha256 is not None and source_hash!=data.source.expected_sha256:
        raise AppError('APP_DEPENDENCY_CHANGED','源版本已变化，请重新预览',409)
    if bundle['runtime_policy']!=data.runtime_policy.model_dump(mode='json'):
        raise AppError('APP_RELEASE_INVALID','发布策略与检查结果不一致',503)
    # Strict finite JSON; the central preparer validates graph and IO schemas.
    bundle_hash=canonical_payload_digest(bundle,maximum_bytes=1048576,maximum_depth=32)
    return row,bundle,source_hash,bundle_hash


def preview_version(session,context,app_id,data,prepare_release):
    _,bundle,source_hash,bundle_hash=prepare_version(session,context,app_id,data,prepare_release)
    # Preview does not persist or generate credentials/runs.
    return {'valid':True,'source':data.source.model_dump(mode='json'),'source_sha256':source_hash,'bundle_sha256':bundle_hash,
        'input_schema':bundle['input_schema'],'output_schema':bundle['output_schema'],'runtime_policy':bundle['runtime_policy'],
        'dependencies':[{k:d[k] for k in ('kind','resource_id','version_id','revision','actions') if k in d} for d in bundle['dependency_manifest']],
        'blockers':[]}


def publish_version(session,context,app_id,data,prepare_release):
    write_gate(session)
    row,bundle,source_hash,bundle_hash=prepare_version(session,context,app_id,data,prepare_release)
    if row.revision!=data.expected_application_revision:conflict()
    if source_hash!=data.expected_source_sha256:raise AppError('APP_DEPENDENCY_CHANGED','发布源已变化，请重新预览',409)
    sequence=(session.scalar(select(func.max(ApplicationVersion.sequence)).where(ApplicationVersion.application_id==app_id)) or 0)+1
    version=ApplicationVersion(id=new_id('appver'),application_id=app_id,sequence=sequence,status='published',source=dump(data.source.model_dump(mode='json')),
        source_sha256=source_hash,bundle_sha256=bundle_hash,bundle=dump(bundle),published_by=context.operator_id,created_at=utc_now())
    session.add(version);session.flush()
    if row.active_version_id is None:row.active_version_id=version.id
    row.revision+=1;row.updated_at=utc_now();audit(session,context,'application_publish','application',app_id,{'version_id':version.id,'bundle_sha256':bundle_hash})
    return version_view(version)


def activate_version(session,context,app_id,data):
    write_gate(session);row=application(session,context,app_id,'applications.publish')
    if row.revision!=data.expected_revision:conflict()
    _version(session,app_id,data.version_id);row.active_version_id=data.version_id;row.revision+=1;row.updated_at=utc_now()
    audit(session,context,'application_activate_version','application',app_id,{'version_id':data.version_id});session.flush()
    return app_view(session,row,context)


def _scopes(scopes,kind):
    selected=sorted(set(scopes))
    if (kind=='agent' and 'workflow.invoke' in selected) or (kind=='workflow' and 'chat.invoke' in selected):
        raise AppError('APP_SCOPE_DENIED','调用scope与应用类型不一致',422)
    return selected


def _quota_subset(value,parent):
    if any(value[k]>parent[k] for k in parent):raise AppError('APP_SCOPE_DENIED','限额不能超出上级配置',403)


def client_view(row):
    return ClientView(client_id=row.id,application_id=row.application_id,name=row.name,status=row.status,revision=row.revision,
        default_version_id=row.default_version_id,scopes=load(row.scopes),external_user_mode=row.external_user_mode,expires_at=iso(row.expires_at),
        quotas=load(row.quotas),resource_constraints=load(row.resource_constraints),created_at=row.created_at).model_dump(mode='json')


def client(session,app_id,client_id):
    row=fresh(session,ApplicationClient,client_id)
    if row is None or row.application_id!=app_id:raise AppError('NOT_FOUND','客户端不存在或无权访问',404)
    return row


def list_clients(session,context,app_id,*,page_number=1,page_size=20):
    application(session,context,app_id)
    return page([client_view(r) for r in session.scalars(select(ApplicationClient).where(ApplicationClient.application_id==app_id).order_by(ApplicationClient.created_at.desc()))],page_number,page_size)


def client_options(session,context,app_id,version_id,delegable_options):
    """Read-only central ACL projection, never a tenant-wide resource selector.

    Central callback returns current delegable exact dependencies, safe labels,
    machine-constrained entities/blockers, and its trusted configured base URL.
    Client creation independently rechecks and grants; this offer grants nothing.
    """
    app=application(session,context,app_id,'applications.keys.manage');version=_version(session,app_id,version_id)
    from .bridge import _bundle
    bundle=_bundle(version)
    offered=delegable_options(session,context=context,application_id=app_id,version_id=version_id,bundle=bundle)
    if not isinstance(offered,dict) or set(offered)!={'resource_options','allowed_entities','blockers','public_base_url','public_base_url_status'}:
        raise AppError('APP_API_NOT_READY','中央授权选项返回结构尚未配对',503)
    try:
        options=[ResourceOption.model_validate(v) for v in offered['resource_options']]
        blockers=[OptionBlocker.model_validate(v).model_dump() for v in offered['blockers']]
    except (ValueError,TypeError):raise AppError('APP_AUTHORITY_INVALID','中央授权选项结构无效',503) from None
    manifest={(v['kind'],v['resource_id']):v for v in bundle['dependency_manifest']}
    allowed=[];seen=set();mapping={'read':'use','export':'export_detail'}
    for option in options:
        value=option.constraint.model_dump(mode='json');identity=(value['kind'],value['resource_id']);dependency=manifest.get(identity)
        if identity in seen or dependency is None or any(mapping.get(a,a) not in dependency.get('actions',['use']) for a in value['actions']):
            raise AppError('APP_AUTHORITY_INVALID','中央授权选项超出固定依赖',503)
        if dependency.get('version_id') is not None and value.get('version_id')!=dependency['version_id']:
            raise AppError('APP_AUTHORITY_INVALID','中央授权选项版本不匹配',503)
        if 'use' not in value['actions']:
            raise AppError('APP_AUTHORITY_INVALID','中央授权选项缺少依赖调用动作',503)
        seen.add(identity);allowed.append(value)
    if seen!=set(manifest):blockers.append({'code':'APP_RESOURCE_GRANT_REQUIRED','message':'部分发布依赖当前不可委托，请联系资源管理员'})
    if app.status!='active':blockers.append({'code':'APP_DISABLED','message':'应用当前已停用，不能创建调用客户端'})
    entities=offered['allowed_entities']
    if not isinstance(entities,list) or any(not isinstance(v,str) for v in entities):raise AppError('APP_AUTHORITY_INVALID','中央实体授权选项无效',503)
    entities=sorted(set(entities)&set(context.allowed_entities))
    policy=RuntimePolicy.model_validate(bundle['runtime_policy'])
    scopes=['app.read','chat.invoke' if app.kind=='agent' else 'workflow.invoke','runs.read','events.read','runs.cancel']
    default=list(scopes)
    if app.kind=='agent':scopes.append('conversations.read')
    if policy.allow_resume:scopes.append('runs.resume')
    if policy.expose_trace:scopes.append('trace.read')
    if policy.allowed_file_types:scopes.extend(['files.upload','files.read'])
    url=offered['public_base_url'];url_status=offered['public_base_url_status']
    if url_status=='not_configured':
        if url is not None:raise AppError('APP_AUTHORITY_INVALID','待配置状态不能提供调用地址',503)
    else:
        from urllib.parse import urlsplit
        try:
            parts=urlsplit(url)
            if parts.port is not None and not 1<=parts.port<=65535:raise ValueError()
        except (ValueError,TypeError):raise AppError('APP_AUTHORITY_INVALID','服务器调用地址配置无效',503) from None
        secure=parts.scheme=='https' and url_status=='configured'
        from app.settings import settings
        local=parts.scheme=='http' and parts.hostname in {'localhost','127.0.0.1','::1'} and url_status=='development' and settings.development_http
        if not isinstance(url,str) or any(c.isspace() or ord(c)<32 for c in url) or not (secure or local) or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path!='/api/open/v1':
            raise AppError('APP_AUTHORITY_INVALID','服务器调用地址配置无效',503)
    return ClientOptionsView(application_id=app_id,version_id=version_id,application_revision=app.revision,scopes=scopes,default_scopes=default,
        quotas=load(app.quotas),external_user_modes=['client','required'],resource_options=options,allowed_entities=entities,
        resource_constraints={'resources':allowed,'allowed_entities':entities},blockers=blockers,can_create_client=not blockers,
        public_base_url=url,public_base_url_status=url_status).model_dump(mode='json')


def create_client(session,context,app_id,data,provision_principal,authorize_resources):
    write_gate(session);app=application(session,context,app_id,'applications.keys.manage')
    version=_version(session,app_id,data.default_version_id)
    scopes=_scopes(data.scopes,app.kind)
    quotas=data.quotas.model_dump() if 'quotas' in data.model_fields_set else load(app.quotas)
    _quota_subset(quotas,load(app.quotas))
    # The central callback must check human delegability AND grant to the new
    # service principal atomically. Manifest subset alone never grants data ACL.
    client_id=new_id('appclient')
    principal=provision_principal(session,client_id=client_id,application_id=app_id,tenant_id=app.tenant_id,workspace_id=app.workspace_id,display_name=data.name,created_by=context.operator_id)
    principal_id=principal['id'] if isinstance(principal,dict) else principal.id
    constraints=data.resource_constraints.model_dump(mode='json')
    authorize_resources(session,context=context,principal_id=principal_id,application_id=app_id,version_id=version.id,
        bundle=load(version.bundle),resource_constraints=constraints)
    row=ApplicationClient(id=client_id,application_id=app_id,principal_id=principal_id,name=data.name,status='active',revision=1,
        default_version_id=version.id,version_ids=dump([version.id]),scopes=dump(scopes),external_user_mode=data.external_user_mode,
        expires_at=expiry(data.expires_at),quotas=dump(quotas),resource_constraints=dump(constraints),created_at=utc_now())
    session.add(row);session.flush();audit(session,context,'application_client_create','application',app_id,{'client_id':client_id,'principal_id':principal_id})
    return client_view(row)


def patch_client(session,context,app_id,client_id,data):
    write_gate(session);application(session,context,app_id,'applications.keys.manage');row=client(session,app_id,client_id)
    if row.revision!=data.expected_revision:conflict()
    if data.scopes is not None:
        if not set(data.scopes)<=set(load(row.scopes)):raise AppError('APP_SCOPE_DENIED','客户端scope只能收紧',403)
        row.scopes=dump(sorted(set(data.scopes)))
    if data.quotas is not None:
        value=data.quotas.model_dump();_quota_subset(value,load(row.quotas));row.quotas=dump(value)
    if data.resource_constraints is not None:
        value=data.resource_constraints.model_dump(mode='json');require_subset(value,load(row.resource_constraints));row.resource_constraints=dump(value)
    if 'expires_at' in data.model_fields_set:
        value=expiry(data.expires_at)
        if row.expires_at is not None and (value is None or value>row.expires_at):raise AppError('APP_SCOPE_DENIED','客户端有效期只能收紧',403)
        row.expires_at=value
    if data.name is not None:row.name=data.name
    if data.status is not None:row.status=data.status
    row.revision+=1;session.flush();audit(session,context,'application_client_update','application',app_id,{'client_id':client_id,'revision':row.revision})
    return client_view(row)


def key_view(row,session=None):
    activity=fresh(session,KeyActivity,row.id) if session is not None else None
    return KeyMetadata(key_id=row.id,name=row.name,prefix=row.prefix,last4=row.last4,status='expired' if row.status=='active' and row.expires_at is not None and row.expires_at<=epoch() else row.status,
        revision=row.revision,scopes=load(row.scopes),version_ids=load(row.version_ids),default_version_id=row.default_version_id,
        expires_at=iso(row.expires_at),quotas=load(row.quotas),resource_constraints=load(row.resource_constraints),created_at=row.created_at,
        last_used_at=activity.last_admitted_at if activity else None).model_dump(mode='json')


def _key(session,client_id,key_id):
    row=fresh(session,ApplicationCredential,key_id)
    if row is None or row.client_id!=client_id:raise AppError('NOT_FOUND','凭据不存在或无权访问',404)
    return row


def list_keys(session,context,app_id,client_id,*,page_number=1,page_size=20):
    application(session,context,app_id);client(session,app_id,client_id)
    return page([key_view(r,session) for r in session.scalars(select(ApplicationCredential).where(ApplicationCredential.client_id==client_id).order_by(ApplicationCredential.created_at.desc()))],page_number,page_size)


def _issue(session,app,row,data):
    scopes=_scopes(data.scopes if data.scopes is not None else load(row.scopes),app.kind)
    if not set(scopes)<=set(load(row.scopes)):raise AppError('APP_SCOPE_DENIED','Key的scope不能超出客户端',403)
    versions=data.version_ids or [row.default_version_id]
    default=data.default_version_id or row.default_version_id
    if default not in versions or not set(versions)<=set(load(row.version_ids)):raise AppError('APP_SCOPE_DENIED','Key的版本范围不能超出客户端',403)
    for v in versions:_version(session,app.id,v)
    quotas=data.quotas.model_dump() if data.quotas else load(row.quotas);_quota_subset(quotas,load(row.quotas))
    constraints=data.resource_constraints.model_dump(mode='json') if data.resource_constraints is not None else load(row.resource_constraints)
    require_subset(constraints,load(row.resource_constraints))
    expires=expiry(data.expires_at) if data.expires_at is not None else row.expires_at
    if row.expires_at is not None and (expires is None or expires>row.expires_at):raise AppError('APP_SCOPE_DENIED','Key有效期不能超出客户端',403)
    issued=issue_key();v=issued.verifier
    key=ApplicationCredential(id=v.key_id,client_id=row.id,selector=v.selector,digest=v.digest,algorithm=v.algorithm,prefix=v.prefix,last4=v.last4,
        name=data.name,status='active',revision=1,default_version_id=default,version_ids=dump(sorted(set(versions))),scopes=dump(scopes),expires_at=expires,
        quotas=dump(quotas),resource_constraints=dump(constraints),created_at=utc_now())
    session.add(key);session.flush()
    return key,issued


def create_key(session,context,app_id,client_id,data):
    write_gate(session);app=application(session,context,app_id,'applications.keys.manage');row=client(session,app_id,client_id)
    if app.status!='active' or row.status!='active' or row.expires_at is not None and row.expires_at<=epoch():
        raise AppError('APP_DISABLED','应用或客户端当前不可签发凭据',409)
    key,issued=_issue(session,app,row,data)
    audit(session,context,'application_key_create','application',app_id,{'client_id':client_id,'key_id':key.id,'prefix':key.prefix})
    return {'key':key_view(key,session),'api_key':issued.take_secret(),'old_key_valid_until':None}


def revoke_key(session,context,app_id,client_id,key_id):
    write_gate(session);application(session,context,app_id,'applications.keys.manage');client(session,app_id,client_id);row=_key(session,client_id,key_id)
    if row.status!='revoked':
        row.status='revoked';row.revision+=1;audit(session,context,'application_key_revoke','application',app_id,{'key_id':key_id})
    session.flush();return key_view(row,session)


def rotate_key(session,context,app_id,client_id,key_id,data):
    write_gate(session);app=application(session,context,app_id,'applications.keys.manage');row=client(session,app_id,client_id);old=_key(session,client_id,key_id)
    if old.revision!=data.expected_revision:conflict()
    if app.status!='active' or row.status!='active' or old.status!='active' or any(v is not None and v<=epoch() for v in [old.expires_at,row.expires_at]):
        raise AppError('APP_KEY_INVALID','当前凭据不可轮换',409)
    until=expiry(data.old_key_valid_until)
    if until is not None and (until>epoch()+86400 or old.expires_at is not None and until>old.expires_at or row.expires_at is not None and until>row.expires_at):
        raise AppError('APP_INPUT_INVALID','旧Key宽限期须在原有效期内且不超过24小时',422)
    # Rotation preserves old restrictions and intersects any later client tightening.
    from .storage import constraints_intersection,minimum_quotas
    scope=sorted(set(load(old.scopes))&set(load(row.scopes)));versions=sorted(set(load(old.version_ids))&set(load(row.version_ids)))
    request=KeyCreate(name=old.name,scopes=scope,version_ids=versions,default_version_id=old.default_version_id,
        expires_at=iso(min(v for v in [old.expires_at,row.expires_at] if v is not None)) if old.expires_at is not None or row.expires_at is not None else None,
        quotas=minimum_quotas(load(old.quotas),load(row.quotas)),resource_constraints=constraints_intersection(load(old.resource_constraints),load(row.resource_constraints)))
    key,issued=_issue(session,app,row,request)
    if until is None:old.status='revoked'
    else:old.expires_at=until
    old.revision+=1;session.flush()
    audit(session,context,'application_key_rotate','application',app_id,{'key_id':key.id,'previous_key_id':old.id,'old_key_valid_until':iso(until)})
    return {'key':key_view(key,session),'api_key':issued.take_secret(),'old_key_valid_until':iso(until)}


def list_calls(session,context,app_id,*,page_number=1,page_size=20,status=None,client_id=None):
    application(session,context,app_id,'applications.records.read')
    query=select(CallRecord).where(CallRecord.application_id==app_id)
    if status:query=query.where(CallRecord.status==status)
    if client_id:query=query.where(CallRecord.client_id==client_id)
    total=session.scalar(select(func.count()).select_from(query.subquery()))
    rows=session.scalars(query.order_by(CallRecord.created_at.desc(),CallRecord.id).offset((page_number-1)*page_size).limit(page_size))
    from .observability import usage_for_run,timing_for_run
    items=[]
    for row in rows:
        value={k:getattr(row,k) for k in ('request_id','run_id','application_id','client_id','key_id','key_prefix','version_id','status','safe_code','created_at','updated_at')}
        if value['status']=='reserved':value['status']='queued'  # Legacy admitted receipt.
        usage=usage_for_run(session,row.run_id)
        value.update(usage_status=usage['status'],usage=usage,timing=timing_for_run(session,row.run_id))
        items.append(CallView.model_validate(value).model_dump(mode='json',exclude_unset=True))
    return {'items':items,
        'total':total,'page':page_number,'page_size':page_size}
