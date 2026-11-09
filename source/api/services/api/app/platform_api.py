"""Platform credentials: opaque hashed secrets, multi-space tokens and live scope checks.
No human session is created, copied or impersonated by a platform credential.
"""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import hashlib,hmac,json,re,secrets
from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict,Field,SecretStr,model_validator
from sqlalchemy import select,or_,exists
from app.db import Resource,get_session,new_id,utc_now
from app.auth_models import User,AccessRole,ResourceGrant,Installation
from app.errors import AppError
KINDS={'agent','agent_version','workflow','document','attachment','knowledge_base','knowledge_document','knowledge_dataset','knowledge_file','knowledge_batch','knowledge_version','knowledge_chunk','data_table','data_table_version','database','data_file','data_batch','plugin_version','connection','model_configuration','model_provider'}
DIRECTORY_KINDS=KINDS-{'model_configuration','model_provider','connection'}
LEVELS={'none':0,'view':1,'read':2,'write':3}
PREFIX='/api/platform/v1'
manage=APIRouter(tags=['platform-management']);runtime=APIRouter(tags=['platform-api'])
def now():return datetime.now(timezone.utc)
def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def _row(session,kind,identifier,tenant_id=None,workspace_id=None):
 query=select(Resource).where(Resource.kind==kind,Resource.resource_id==identifier)
 if tenant_id is not None:query=query.where(Resource.tenant_id==tenant_id)
 if workspace_id is not None:query=query.where(Resource.workspace_id==workspace_id)
 rows=list(session.scalars(query.limit(2).execution_options(populate_existing=True)))
 if len(rows)>1:raise AppError('NOT_FOUND','资源标识缺少明确空间范围',404)
 return rows[0] if rows else None
def _value(row):return json.loads(row.payload)
def write_lock(session):
 if session.get_bind().dialect.name!='sqlite':raise AppError('PLATFORM_STORAGE_UNSUPPORTED','当前平台配置仅支持已验证的SQLite事务',503)
 connection=session.connection()
 if not connection.connection.driver_connection.in_transaction:connection.exec_driver_sql('BEGIN IMMEDIATE')
def _put(session,kind,identifier,value,tenant,space,owner):
 row=_row(session,kind,identifier,tenant,space)
 if row:
  if (row.tenant_id,row.workspace_id)!=(tenant,space):raise AppError('NOT_FOUND','记录不存在或无权访问',404)
  row.payload=json.dumps(value,ensure_ascii=False);row.updated_at=utc_now()
 else:row=Resource(kind=kind,resource_id=identifier,tenant_id=tenant,workspace_id=space,owner_id=owner,payload=json.dumps(value,ensure_ascii=False),created_at=utc_now(),updated_at=utc_now());session.add(row)
 session.flush();return row
class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class Rule(Strict):
 kind:str=Field(min_length=1,max_length=64)
 resource_id:str=Field(min_length=1,max_length=160)
 access:str=Field(default='view',pattern='^(none|view|read|write)$')
 execute:bool=False
 @model_validator(mode='after')
 def checked(self):
  if self.kind not in KINDS:raise ValueError('此资源类型不支持平台授权')
  return self
class SpaceGrant(Strict):
 workspace_id:str=Field(min_length=1,max_length=100)
 access:str=Field(default='view',pattern='^(none|view|read|write)$')
 execute:bool=False
 allow_create:bool=False
 all_resources:bool=False
 resources:list[Rule]=Field(default_factory=list,max_length=256)
 @model_validator(mode='after')
 def checked(self):
  if len({(r.kind,r.resource_id) for r in self.resources})!=len(self.resources):raise ValueError('资源授权重复')
  if self.allow_create and self.access!='write':raise ValueError('上传或导入新资源需要可编辑范围')
  return self
class KeyWrite(Strict):
 name:str=Field(min_length=1,max_length=100)
 owner_id:str|None=Field(default=None,max_length=100)
 spaces:list[SpaceGrant]=Field(min_length=1,max_length=32)
 expected_revision:int|None=Field(default=None,ge=1)
class Exchange(Strict):
 api_key:SecretStr=Field(min_length=16,max_length=256)
 workspace_ids:list[str]|None=Field(default=None,min_length=1,max_length=32)
class SpaceWrite(Strict):name:str=Field(min_length=1,max_length=100)
class MemberWrite(Strict):
 user_id:str=Field(min_length=1,max_length=100)
 role_id:str=Field(min_length=1,max_length=100)
 active:bool=True

def _human(request,session,permission='platform.keys.manage'):
 from app.auth import identity
 from app.rbac import require_any_permission
 if request.method in {'POST','PUT','PATCH','DELETE'}:write_lock(session)
 context=identity(request)
 if context.principal_type!='human':raise AppError('AUTH_SCHEME_NOT_ALLOWED','管理入口需要登录会话',401)
 required=('platform.keys.manage','platform.keys.admin','spaces.manage') if permission=='platform.directory' else (permission,'platform.keys.admin') if permission=='platform.keys.manage' else (permission,)
 require_any_permission(context,session,required);return context

def ensure_home(session,user):
 # Lazy GET initialization shares the same transaction lock as management writes.
 # Acquire it before lookup so concurrent first opens return the committed row.
 write_lock(session)
 row=_row(session,'platform_space',user.workspace_id,user.tenant_id,user.workspace_id)
 if row:return row
 installation=session.get(Installation,1);owner=session.get(User,installation.admin_user_id) if installation else None
 return _put(session,'platform_space',user.workspace_id,{'id':user.workspace_id,'name':'默认空间','active':True},user.tenant_id,user.workspace_id,owner.id if owner and owner.tenant_id==user.tenant_id else '__workspace__')

def space_subject(session,user,workspace_id):
 from app.rbac import role_for
 if not user.active or user.must_change_password:raise AppError('PLATFORM_SUBJECT_REVOKED','凭据所属账号已停用或需要重新确认',401)
 if workspace_id==user.workspace_id:role=role_for(session,user.tenant_id,workspace_id,user.role_id or user.role)
 else:
  member=_row(session,'platform_space_member',digest(user.id+'|'+workspace_id),user.tenant_id,workspace_id)
  if not member or member.tenant_id!=user.tenant_id or member.workspace_id!=workspace_id or not _value(member).get('active'):raise AppError('WORKSPACE_DENIED','没有该空间的授权',404)
  role=role_for(session,user.tenant_id,workspace_id,_value(member)['role_id'])
 space=_row(session,'platform_space',workspace_id,user.tenant_id,workspace_id)
 if space is None or space.tenant_id!=user.tenant_id or not _value(space).get('active'):raise AppError('WORKSPACE_DENIED','空间不可访问',404)
 if {'member':0,'admin':1,'super_admin':2}.get(role.base_role,99)>{'member':0,'admin':1,'super_admin':2}.get(user.role,-1):raise AppError('WORKSPACE_DENIED','空间角色高于账号当前授权级别',403)
 value={column.name:getattr(user,column.name) for column in User.__table__.columns};value.update(workspace_id=workspace_id,role=role.base_role,role_id=role.id)
 return SimpleNamespace(**value)

def scoped_permissions(session,user,workspace_id):
 from app.rbac import user_policy
 subject=space_subject(session,user,workspace_id);return subject,set(user_policy(subject,session)[1])

def _space_manage(session,context,identifier):
 user=session.get(User,context.operator_id,populate_existing=True);_,permissions=scoped_permissions(session,user,identifier)
 if 'spaces.manage' not in permissions:raise AppError('PERMISSION_DENIED','没有此空间的管理权限',403)
 return user

def _public_key(row):
 value=_value(row);return {key:value.get(key) for key in ('id','name','prefix','owner_id','spaces','active','revision','created_at','rotated_at')}

def _managed_key(session,context,identifier):
 row=_row(session,'platform_api_key',identifier,context.tenant_id,context.workspace_id)
 if not row or (row.tenant_id,row.workspace_id)!=(context.tenant_id,context.workspace_id) or row.owner_id!=context.operator_id and 'platform.keys.admin' not in context.permissions:raise AppError('NOT_FOUND','凭据不存在或无权管理',404)
 return row

def _validate_spaces(session,context,owner,grants):
 if len({g.workspace_id for g in grants})!=len(grants):raise AppError('PLATFORM_GRANT_INVALID','空间授权重复',422)
 actor=session.get(User,context.operator_id,populate_existing=True)
 for grant in grants:
  for user in (actor,owner):
   _,permissions=scoped_permissions(session,user,grant.workspace_id)
   if grant.execute and 'resources.execute' not in permissions:raise AppError('PLATFORM_GRANT_DENIED','不能授予超出账号范围的执行能力',403)
  for rule in grant.resources:
   row=_row(session,rule.kind,rule.resource_id,owner.tenant_id,grant.workspace_id)
   if not row or (row.tenant_id,row.workspace_id)!=(owner.tenant_id,grant.workspace_id):raise AppError('NOT_FOUND','授权资源不在所选空间',404)
   if any(not source_access(session,user,grant.workspace_id,row,rule.access) for user in (actor,owner)):raise AppError('PLATFORM_GRANT_DENIED','不能授予超出账号资源范围的权限',403)
 return [g.model_dump(mode='json') for g in grants]

@manage.get('/platform/spaces')
def spaces(request:Request,session=Depends(get_session)):
 context=_human(request,session,'platform.directory');user=session.get(User,context.operator_id);ensure_home(session,user);items=[]
 for row in session.scalars(select(Resource).where(Resource.kind=='platform_space',Resource.tenant_id==context.tenant_id)):
  try:_,permissions=scoped_permissions(session,user,row.resource_id)
  except AppError:continue
  items.append({**_value(row),'can_manage':'spaces.manage' in permissions})
 return {'items':items}
@manage.post('/platform/spaces',status_code=201)
def create_space(body:SpaceWrite,request:Request,session=Depends(get_session)):
 context=_human(request,session,'spaces.manage');identifier=new_id('workspace')
 from app.rbac import seed_roles
 seed_roles(session,context.tenant_id,identifier)
 # A creator never receives new capabilities merely by making another space.
 role=AccessRole(id=new_id('role'),key='space_creator',tenant_id=context.tenant_id,workspace_id=identifier,name='空间创建者',base_role=context.role,permissions=json.dumps(context.permissions),builtin=False,created_at=utc_now(),updated_at=utc_now());session.add(role);session.flush()
 _put(session,'platform_space',identifier,{'id':identifier,'name':body.name,'active':True},context.tenant_id,identifier,context.operator_id)
 _put(session,'platform_space_member',digest(context.operator_id+'|'+identifier),{'user_id':context.operator_id,'role_id':role.id,'active':True},context.tenant_id,identifier,context.operator_id)
 return {'id':identifier,'name':body.name,'active':True}
@manage.get('/platform/spaces/{identifier}/roles')
def space_roles(identifier:str,request:Request,session=Depends(get_session)):
 context=_human(request,session,'spaces.manage');_space_manage(session,context,identifier)
 return {'items':[{'id':r.id,'name':r.name,'base_role':r.base_role,'key':r.key,'builtin':r.builtin,'updated_at':r.updated_at,'permissions':json.loads(r.permissions)} for r in session.scalars(select(AccessRole).where(AccessRole.tenant_id==context.tenant_id,AccessRole.workspace_id==identifier))]}

@manage.get('/platform/spaces/{identifier}/resources')
def space_directory(identifier:str,request:Request,kind:str,session=Depends(get_session)):
 context=_human(request,session,'platform.directory');user=session.get(User,context.operator_id)
 subject,permissions=scoped_permissions(session,user,identifier)
 if kind not in KINDS:raise AppError('RESOURCE_KIND_DENIED','此资源类型不提供目录',422)
 rows=session.scalars(select(Resource).where(Resource.kind==kind,Resource.tenant_id==context.tenant_id,Resource.workspace_id==identifier))
 return {'items':[metadata(row) for row in rows if source_access(session,subject,identifier,row,'view')]}

@manage.get('/platform/spaces/{identifier}/members')
def space_members(identifier:str,request:Request,session=Depends(get_session)):
 context=_human(request,session,'spaces.manage');_space_manage(session,context,identifier);items=[]
 for row in session.scalars(select(Resource).where(Resource.kind=='platform_space_member',Resource.tenant_id==context.tenant_id,Resource.workspace_id==identifier)):
  value=_value(row);user=session.get(User,value['user_id']);role=session.get(AccessRole,value['role_id'])
  if user and role:items.append({**value,'username':user.username,'display_name':user.display_name,'role_name':role.name})
 return {'items':items}
@manage.put('/platform/spaces/{identifier}/members')
def set_member(identifier:str,body:MemberWrite,request:Request,session=Depends(get_session)):
 context=_human(request,session,'spaces.manage');actor=_space_manage(session,context,identifier);user=session.get(User,body.user_id,populate_existing=True);role=session.get(AccessRole,body.role_id,populate_existing=True)
 if not user or user.tenant_id!=context.tenant_id or not role or (role.tenant_id,role.workspace_id)!=(context.tenant_id,identifier):raise AppError('NOT_FOUND','账号或角色不在可授权范围',404)
 if {'member':0,'admin':1,'super_admin':2}[role.base_role]>{'member':0,'admin':1,'super_admin':2}[user.role]:raise AppError('PLATFORM_GRANT_DENIED','空间角色不能高于账号当前授权级别',403)
 _,permissions=scoped_permissions(session,actor,identifier)
 if not set(json.loads(role.permissions))<=permissions:raise AppError('PLATFORM_GRANT_DENIED','不能授予超出自身范围的空间角色',403)
 _put(session,'platform_space_member',digest(user.id+'|'+identifier),body.model_dump(),context.tenant_id,identifier,context.operator_id)
 return {'user_id':user.id,'workspace_id':identifier,'role_id':role.id,'active':body.active}
@manage.get('/platform/keys')
def keys(request:Request,session=Depends(get_session)):
 context=_human(request,session);query=select(Resource).where(Resource.kind=='platform_api_key',Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id)
 if not {'platform.keys.manage','platform.keys.admin'}&set(context.permissions):raise AppError('PERMISSION_DENIED','没有平台密钥管理权限',403)
 if 'platform.keys.admin' not in context.permissions:query=query.where(Resource.owner_id==context.operator_id)
 return {'items':[_public_key(row) for row in session.scalars(query)]}
@manage.post('/platform/keys',status_code=201)
def create_key(body:KeyWrite,request:Request,session=Depends(get_session)):
 context=_human(request,session);owner=session.get(User,body.owner_id or context.operator_id,populate_existing=True)
 if owner is None or owner.tenant_id!=context.tenant_id or owner.id!=context.operator_id and 'platform.keys.admin' not in context.permissions:raise AppError('NOT_FOUND','凭据所属账号不可用',404)
 ensure_home(session,owner);grants=_validate_spaces(session,context,owner,body.spaces);identifier=secrets.token_hex(16);raw='yak_'+identifier+'_'+secrets.token_urlsafe(32);value={'id':'pkey_'+identifier,'name':body.name,'prefix':raw[:16],'owner_id':owner.id,'created_by':context.operator_id,'spaces':grants,'active':True,'revision':1,'credential_revision':1,'secret_hash':digest(raw),'created_at':utc_now()};row=_put(session,'platform_api_key',value['id'],value,context.tenant_id,context.workspace_id,owner.id)
 return {'key':_public_key(row),'api_key':raw}
@manage.put('/platform/keys/{identifier}')
def update_key(identifier:str,body:KeyWrite,request:Request,session=Depends(get_session)):
 context=_human(request,session);row=_managed_key(session,context,identifier);value=_value(row)
 if body.expected_revision!=value['revision']:raise AppError('PLATFORM_KEY_CONFLICT','凭据配置已变化，请刷新后重试',409)
 if body.owner_id is not None and body.owner_id!=value['owner_id']:raise AppError('PLATFORM_KEY_OWNER_IMMUTABLE','变更主体请新建凭据',422)
 owner=session.get(User,value['owner_id'],populate_existing=True);value.update(name=body.name,spaces=_validate_spaces(session,context,owner,body.spaces),revision=value['revision']+1);row.payload=json.dumps(value,ensure_ascii=False);return _public_key(row)
@manage.post('/platform/keys/{identifier}/revoke')
def revoke_key(identifier:str,request:Request,session=Depends(get_session)):
 context=_human(request,session);row=_managed_key(session,context,identifier);value=_value(row);value.update(active=False,credential_revision=value['credential_revision']+1,revision=value['revision']+1);row.payload=json.dumps(value);return _public_key(row)
@manage.post('/platform/keys/{identifier}/rotate')
def rotate_key(identifier:str,request:Request,session=Depends(get_session)):
 context=_human(request,session);row=_managed_key(session,context,identifier);value=_value(row)
 if not value['active']:raise AppError('PLATFORM_KEY_REVOKED','已撤销的凭据不能轮换',409)
 raw='yak_'+identifier.removeprefix('pkey_')+'_'+secrets.token_urlsafe(32);value.update(secret_hash=digest(raw),credential_revision=value['credential_revision']+1,revision=value['revision']+1,rotated_at=utc_now());row.payload=json.dumps(value);return {'key':_public_key(row),'api_key':raw}

def _exchange(session,body):
 write_lock(session)
 raw=body.api_key.get_secret_value();match=re.fullmatch(r'yak_([0-9a-f]{32})_[A-Za-z0-9_-]{43}',raw);row=_row(session,'platform_api_key','pkey_'+match[1]) if match else None;key=_value(row) if row else {}
 if not row or not key.get('active') or not hmac.compare_digest(digest(raw),key.get('secret_hash','0'*64)):raise AppError('PLATFORM_KEY_INVALID','平台凭据无效或已撤销',401)
 owner=session.get(User,key['owner_id'],populate_existing=True)
 if not owner or not owner.active or owner.must_change_password:raise AppError('PLATFORM_SUBJECT_REVOKED','凭据所属账号已不可用',401)
 selected=body.workspace_ids;grants=[g for g in key['spaces'] if selected is None or g['workspace_id'] in selected]
 if selected and set(selected)!={g['workspace_id'] for g in grants}:raise AppError('WORKSPACE_DENIED','请求包含未授权空间',403)
 for grant in grants:space_subject(session,owner,grant['workspace_id'])
 identifier=secrets.token_hex(16);token='yat_'+identifier+'_'+secrets.token_urlsafe(32);expiry=(now()+timedelta(minutes=15)).isoformat();value={'id':'ptoken_'+identifier,'key_id':key['id'],'secret_hash':digest(token),'credential_revision':key['credential_revision'],'source_role':owner.role,'source_role_id':owner.role_id,'spaces':grants,'expires_at':expiry,'revoked':False,'created_at':utc_now()};_put(session,'platform_api_token',value['id'],value,row.tenant_id,row.workspace_id,owner.id)
 token_state(session,value['id'])
 return {'access_token':token,'token_type':'Bearer','expires_in':900,'expires_at':expiry,'workspace_ids':[g['workspace_id'] for g in grants]}

def revoke_subject_tokens(session,user_id):
 created={row.resource_id for row in session.scalars(select(Resource).where(Resource.kind=='platform_api_key')) if _value(row).get('created_by')==user_id}
 for row in session.scalars(select(Resource).where(Resource.kind=='platform_api_token')):
  value=_value(row)
  if row.owner_id==user_id or value.get('key_id') in created:
   value['revoked']=True;row.payload=json.dumps(value)

def token_state(session,identifier):
 row=_row(session,'platform_api_token',identifier);token=_value(row) if row else {}
 if not row or token.get('revoked') or datetime.fromisoformat(token['expires_at'])<=now():raise AppError('PLATFORM_TOKEN_INVALID','访问令牌无效或已到期',401)
 key_row=_row(session,'platform_api_key',token['key_id'],row.tenant_id,row.workspace_id);key=_value(key_row) if key_row else {}
 if not key_row or not key.get('active') or key['credential_revision']!=token['credential_revision']:raise AppError('PLATFORM_TOKEN_REVOKED','访问令牌已被撤销',401)
 user=session.get(User,key['owner_id'],populate_existing=True)
 if not user or not user.active or user.must_change_password or user.tenant_id!=row.tenant_id:raise AppError('PLATFORM_SUBJECT_REVOKED','凭据所属账号已不可用',401)
 if token.get('source_role')!=user.role or token.get('source_role_id')!=user.role_id:raise AppError('PLATFORM_SUBJECT_REVOKED','账号角色已变化，请重新交换访问令牌',401)
 creator=session.get(User,key['created_by'],populate_existing=True)
 if not creator or not creator.active or creator.must_change_password:raise AppError('PLATFORM_GRANTOR_REVOKED','凭据授权人已不可用',401)
 try:_,creator_permissions=scoped_permissions(session,creator,key_row.workspace_id)
 except AppError:raise AppError('PLATFORM_GRANTOR_REVOKED','凭据授权人已失去空间权限',401) from None
 if not {'platform.keys.manage','platform.keys.admin'}&creator_permissions:raise AppError('PLATFORM_GRANTOR_REVOKED','凭据授权人已失去密钥管理权限',401)
 return token,key,user

def current_scope(session,token_id,workspace_id):
 token,key,user=token_state(session,token_id);old=next((g for g in token['spaces'] if g['workspace_id']==workspace_id),None);current=next((g for g in key['spaces'] if g['workspace_id']==workspace_id),None)
 if not old or not current:raise AppError('WORKSPACE_DENIED','没有该空间的授权',404)
 subject,permissions=scoped_permissions(session,user,workspace_id)
 grantor=session.get(User,key['created_by'],populate_existing=True)
 if grantor is None:raise AppError('PLATFORM_GRANTOR_REVOKED','凭据授权人已不可用',401)
 _,grantor_permissions=scoped_permissions(session,grantor,workspace_id)
 return token,key,subject,permissions&grantor_permissions,old,current

def _rule(grant,kind,identifier):
 if grant['all_resources']:return LEVELS[grant['access']],grant['execute']
 item=next((r for r in grant['resources'] if r['kind']==kind and r['resource_id']==identifier),None)
 return (min(LEVELS[grant['access']],LEVELS[item['access']]),grant['execute'] and item['execute']) if item else (0,False)

def source_access(session,user,workspace_id,row,access):
 user,permissions=scoped_permissions(session,user,workspace_id)
 if row.kind in {'model_configuration','model_provider'}:
  if access=='write' or not {'page.ai','page.workbench'}&permissions:return False
  from app.resource_policy import access_level
  subject=space_subject(session,user,workspace_id);value=_value(row)
  if row.kind=='model_configuration':return not value.get('archived') and (bool(value.get('workspace_use')) or access_level(session,subject,row)>=2)
  return any(_value(model).get('provider_id')==row.resource_id and source_access(session,subject,workspace_id,model,'read') for model in session.scalars(select(Resource).where(Resource.kind=='model_configuration',Resource.tenant_id==user.tenant_id,Resource.workspace_id==workspace_id)))
 page={'agent':{'page.ai','page.workbench'},'agent_version':{'page.ai','page.workbench'},'workflow':{'page.ai','page.workbench'},'database':{'page.database'},'data_table':{'page.database'},'data_table_version':{'page.database'}}.get(row.kind,{'page.knowledge','page.ai','page.workbench'})
 if not page&permissions:return False
 from app.data_access import DATA_KINDS,_authorize_subject_resource
 if row.kind in DATA_KINDS and access!='view':
  subject=space_subject(session,user,workspace_id)
  scope=SimpleNamespace(tenant_id=subject.tenant_id,workspace_id=workspace_id,principal_type='delegation')
  try:_authorize_subject_resource(session=session,context=scope,user=subject,kind=row.kind,resource_id=row.resource_id,action='maintain' if access=='write' else 'use',check_derivation=False)
  except AppError:return False
  return True
 from app.resource_policy import access_level
 if access_level(session,user,row)>=LEVELS[access]:return True
 if row.kind in DATA_KINDS and access=='view':
  subject=space_subject(session,user,workspace_id);scope=SimpleNamespace(tenant_id=subject.tenant_id,workspace_id=workspace_id,principal_type='delegation')
  try:_authorize_subject_resource(session=session,context=scope,user=subject,kind=row.kind,resource_id=row.resource_id,action='use',check_derivation=False)
  except AppError:return False
  return True
 return False

def allowed(session,state,workspace_id,row,access='view'):
 token,key,subject,permissions,old,current=current_scope(session,state,workspace_id)
 return row.kind in KINDS and (row.tenant_id,row.workspace_id)==(subject.tenant_id,workspace_id) and min(_rule(old,row.kind,row.resource_id)[0],_rule(current,row.kind,row.resource_id)[0])>=LEVELS[access] and delegated_access(session,key,subject,workspace_id,row,access)

def delegated_access(session,key,subject,workspace_id,row,access):
 grantor=session.get(User,key['created_by'],populate_existing=True)
 return grantor is not None and source_access(session,subject,workspace_id,row,access) and source_access(session,grantor,workspace_id,row,access)

def metadata(row):
 value=_value(row)
 def text(key,default=''):
  v=value.get(key,default);return v[:160] if isinstance(v,str) else default
 return {'id':row.resource_id,'kind':row.kind,'name':text('name',text('title',text('filename',row.resource_id))),'status':text('status'),'created_at':row.created_at,'updated_at':row.updated_at}

def platform_context(session,token_id,workspace_id):
 from app.contracts import BusinessContext
 from app.settings import settings
 token,key,subject,permissions,old,current=current_scope(session,token_id,workspace_id)
 return BusinessContext(tenant_id=subject.tenant_id,workspace_id=workspace_id,operator_id=key['id'],actor_id=subject.id,
  principal_type='platform',role='service',session_id=None,auth_key_id=token_id,role_id=subject.role_id,
  permissions=sorted(permissions),allowed_entities=json.loads(subject.allowed_entities),
  allowed_origins=list(settings.tool_allowed_origins),allowed_tool_origins=list(settings.tool_allowed_origins))

def validate_platform_context(context,session):
 if context.session_id is not None or not context.auth_key_id:raise AppError('PLATFORM_TOKEN_INVALID','平台身份无效',401)
 token,key,subject,permissions,old,current=current_scope(session,context.auth_key_id,context.workspace_id)
 if (context.tenant_id,context.operator_id,context.actor_id)!=(subject.tenant_id,key['id'],subject.id):raise AppError('PLATFORM_TOKEN_INVALID','平台身份不匹配',401)
 context.permissions=sorted(permissions);context.allowed_entities=json.loads(subject.allowed_entities)
 return subject

def token_expression(session,context,kind,access='read'):
 from sqlalchemy import false
 token,key,subject,permissions,old,current=current_scope(session,context.auth_key_id,context.workspace_id)
 # Output resources are accessible only to the exact key that created them.
 needed='read' if kind in {'run','dataset','artifact','report_result','agent_version'} else access
 bindings=[_value(r)['resource_id'] for r in session.scalars(select(Resource).where(Resource.kind=='platform_resource_binding',Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id,Resource.owner_id==key['id'])) if _value(r).get('kind')==kind and min(LEVELS[old['access']],LEVELS[current['access']])>=LEVELS[needed]]
 conditions=[Resource.resource_id.in_(bindings)] if bindings else []
 if kind in KINDS:
  if old['all_resources'] and current['all_resources'] and min(LEVELS[old['access']],LEVELS[current['access']])>=LEVELS[access]:return Resource.id!=None
  selected={r['resource_id'] for g in (old,current) for r in g['resources'] if r['kind']==kind}
  ids=[identifier for identifier in selected if min(_rule(old,kind,identifier)[0],_rule(current,kind,identifier)[0])>=LEVELS[access]]
  if ids:conditions.append(Resource.resource_id.in_(ids))
 return or_(*conditions) if conditions else false()

def require_token_resource(session,context,row,access='read'):
 token,key,subject,permissions,old,current=current_scope(session,context.auth_key_id,context.workspace_id)
 if (row.tenant_id,row.workspace_id)!=(subject.tenant_id,subject.workspace_id):raise AppError('NOT_FOUND','资源不存在或无权访问',404)
 binding=_row(session,'platform_resource_binding',digest(row.kind+'|'+row.resource_id+'|'+key['id']),context.tenant_id,context.workspace_id)
 derived=row.kind in {'run','dataset','artifact','report_result','agent_version'}
 needed='read' if derived else access
 if binding and binding.owner_id==key['id'] and (binding.tenant_id,binding.workspace_id)==(row.tenant_id,row.workspace_id) and access!='execute' and min(LEVELS[old['access']],LEVELS[current['access']])>=LEVELS[needed] and (not derived or access!='write' or 'resources.execute' in permissions) and (derived or delegated_access(session,key,subject,context.workspace_id,row,access)):return
 from app.resource_policy import role_access
 previous,previous_execute=_rule(old,row.kind,row.resource_id);live,live_execute=_rule(current,row.kind,row.resource_id)
 required='read' if access=='execute' else access
 permitted=row.kind in KINDS and min(previous,live)>=LEVELS[required] and delegated_access(session,key,subject,context.workspace_id,row,required)
 if access=='execute':
  grantor=space_subject(session,session.get(User,key['created_by'],populate_existing=True),context.workspace_id)
  permitted=permitted and previous_execute and live_execute and 'resources.execute' in permissions and role_access(session,subject,row)[1] and role_access(session,grantor,row)[1]
 if not permitted:raise AppError('EXECUTE_DENIED' if access=='execute' else 'NOT_FOUND','资源未授权此操作',403 if access=='execute' else 404)

def authorize_save(session,context,kind,identifier,row,payload):
 token,key,subject,permissions,old,current=current_scope(session,context.auth_key_id,context.workspace_id)
 if row is not None:return require_token_resource(session,context,row,'write')
 derived={'run','dataset','artifact','agent_version','report_result'}
 if kind in derived:
  if 'resources.execute' not in permissions:raise AppError('EXECUTE_DENIED','没有执行授权',403)
  if kind=='agent_version' and payload.get('status')!='run_snapshot':raise AppError('PLATFORM_WRITE_DENIED','只能创建执行快照',403)
  return
 if kind not in {'attachment','document','knowledge_base','knowledge_document','knowledge_file','database','data_table','data_table_version','data_file','data_batch'} or not(old['allow_create'] and current['allow_create'] and old['access']=='write' and current['access']=='write'):
  raise AppError('PLATFORM_WRITE_DENIED','没有上传或创建此资源的授权',403)
 needed={'page.database'} if kind.startswith(('data_','database')) else {'page.knowledge','page.ai','page.workbench'}
 if not needed&permissions:raise AppError('PERMISSION_DENIED','账号没有相应资源功能权限',403)

def bind_created(session,context,row):
 identifier=digest(row.kind+'|'+row.resource_id+'|'+context.operator_id)
 existing=_row(session,'platform_resource_binding',identifier,context.tenant_id,context.workspace_id)
 if existing:return
 _put(session,'platform_resource_binding',identifier,{'kind':row.kind,'resource_id':row.resource_id,'key_id':context.operator_id},row.tenant_id,row.workspace_id,context.operator_id)

def fresh_run_context(session,run_id):
 row=_row(session,'platform_run_authorization',run_id)
 if not row:return None
 value=_value(row);context=platform_context(session,value['token_id'],row.workspace_id)
 if context.operator_id!=row.owner_id:raise AppError('NOT_FOUND','运行授权已变化',404)
 return context

def authorize_derivation_target(session,context,target):
 """Real key/resource/run partition fence; never replace the key with its user."""
 validate_platform_context(context,session)
 if (target.owner_id,target.tenant_id,target.workspace_id)!=(context.actor_id,context.tenant_id,context.workspace_id):raise AppError('NOT_FOUND','派生目标不属于当前平台主体',404)
 binding=_row(session,'platform_resource_binding',digest(target.kind+'|'+target.resource_id+'|'+context.operator_id),context.tenant_id,context.workspace_id)
 if not binding or binding.owner_id!=context.operator_id or _value(binding)!={'kind':target.kind,'resource_id':target.resource_id,'key_id':context.operator_id}:raise AppError('NOT_FOUND','派生目标没有当前Key的真实绑定',404)
 require_token_resource(session,context,target,'write')
 parent=target.resource_id if target.kind=='run' else _value(target).get('run_id')
 if target.kind=='artifact' and not parent:raise AppError('NOT_FOUND','产物缺少真实父运行',404)
 if parent:
  from app.execution.models import ExecutionRun
  run=_row(session,'run',parent,context.tenant_id,context.workspace_id)
  auth=_row(session,'platform_run_authorization',parent,context.tenant_id,context.workspace_id)
  execution=session.get(ExecutionRun,parent,populate_existing=True)
  if not run or run.owner_id!=context.actor_id or not auth or auth.owner_id!=context.operator_id or not execution or (execution.principal_id,execution.tenant_id,execution.workspace_id)!=(context.operator_id,context.tenant_id,context.workspace_id):raise AppError('NOT_FOUND','派生目标与真实平台运行分区不一致',404)
  admission=_row(session,'platform_api_token',_value(auth).get('token_id'),context.tenant_id)
  if not admission or admission.owner_id!=context.actor_id or _value(admission).get('key_id')!=context.operator_id:raise AppError('NOT_FOUND','运行准入主体不一致',404)

def bind_request(request,session):
 if not __import__('app.settings',fromlist=['settings']).settings.development_http and request.url.scheme!='https':raise AppError('HTTPS_REQUIRED','平台API需要HTTPS',426)
 if any(name.lower() in {'api_key','access_token','token','key'} for name in request.query_params):raise AppError('CREDENTIAL_LOCATION_INVALID','凭据不可放在URL中',400)
 if request.url.path==PREFIX+'/token':return
 header=request.headers.get('authorization','');match=re.fullmatch(r'Bearer (yat_([0-9a-f]{32})_[A-Za-z0-9_-]{43})',header)
 if not match:raise AppError('PLATFORM_TOKEN_REQUIRED','需要平台访问令牌',401)
 identifier='ptoken_'+match[2];row=_row(session,'platform_api_token',identifier)
 if row is None or not hmac.compare_digest(digest(match[1]),_value(row)['secret_hash']):raise AppError('PLATFORM_TOKEN_INVALID','访问令牌无效',401)
 token_state(session,identifier);request.state.platform_token_id=identifier
 parts=request.url.path.strip('/').split('/')
 if len(parts)>4 and parts[3]=='spaces':current_scope(session,identifier,parts[4])

@runtime.post('/token')
def exchange(body:Exchange,session=Depends(get_session)):return _exchange(session,body)
@runtime.post('/token/revoke')
def revoke_token(request:Request,session=Depends(get_session)):
 row=_row(session,'platform_api_token',request.state.platform_token_id);value=_value(row);value['revoked']=True;row.payload=json.dumps(value)
 return {'revoked':True}
@runtime.get('/spaces')
def runtime_spaces(request:Request,session=Depends(get_session)):
 identifier=request.state.platform_token_id;token,key,user=token_state(session,identifier);items=[]
 for grant in token['spaces']:
  try:current_scope(session,identifier,grant['workspace_id'])
  except AppError as error:
   if error.status_code in (403,404):continue
   raise
  row=_row(session,'platform_space',grant['workspace_id'],user.tenant_id,grant['workspace_id']);items.append({'id':row.resource_id,'name':_value(row)['name']})
 return {'items':items}
@runtime.get('/spaces/{workspace_id}/resources')
def runtime_resources(workspace_id:str,request:Request,kind:str,session=Depends(get_session)):
 if kind not in DIRECTORY_KINDS:raise AppError('RESOURCE_KIND_DENIED','不支持此资源目录',403)
 token_id=request.state.platform_token_id;current_scope(session,token_id,workspace_id);token,key,user=token_state(session,token_id);rows=session.scalars(select(Resource).where(Resource.tenant_id==user.tenant_id,Resource.workspace_id==workspace_id,Resource.kind==kind))
 return {'items':[metadata(row) for row in rows if allowed(session,token_id,workspace_id,row)]}
