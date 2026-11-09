"""Live role session rules; persistent super-admin sessions remain revocable."""
import json
from datetime import datetime,timedelta
from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from app.db import Resource,get_session,new_id,utc_now
from app.errors import AppError
PERSISTENT='9999-12-31T23:59:59+00:00'
COOKIE_LIFETIME=315360000
KIND='role_session_policy'
router=APIRouter(tags=['session-policy'])
def policy(user,session):
 from app.settings import settings
 from app.rbac import role_for
 role=role_for(session,user.tenant_id,user.workspace_id,user.role_id or user.role)
 row=session.scalar(select(Resource).where(Resource.kind==KIND,Resource.resource_id==role.id,Resource.tenant_id==user.tenant_id,Resource.workspace_id==user.workspace_id))
 base={'absolute_seconds':settings.session_absolute_seconds,'idle_seconds':settings.session_idle_seconds,'revision':0}
 return role,{**base,**(json.loads(row.payload) if row else {}),'persistent':user.role=='super_admin'}
def valid(auth_session,user,session,now):
 if auth_session.revoked or not user.active or auth_session.user_id!=user.id:return False
 if user.role=='super_admin' and auth_session.expires_at==PERSISTENT:return True
 if datetime.fromisoformat(auth_session.expires_at)<=now:return False
 role,rules=policy(user,session)
 if datetime.fromisoformat(auth_session.created_at)+timedelta(seconds=rules['absolute_seconds'])<=now:return False
 if datetime.fromisoformat(auth_session.last_seen_at)+timedelta(seconds=rules['idle_seconds'])<=now:return False
 if user.role=='super_admin':auth_session.expires_at=PERSISTENT
 return True
class PolicyWrite(BaseModel):
 model_config=ConfigDict(extra='forbid')
 absolute_seconds:int=Field(ge=900,le=2592000)
 idle_seconds:int=Field(ge=300,le=604800)
 expected_revision:int=Field(ge=0)
@router.get('/auth/session-policies')
def listing(request:Request,session=Depends(get_session)):
 from app.auth import identity
 from app.rbac import require_permission
 from app.auth_models import AccessRole
 context=identity(request);require_permission(context,session,'roles.manage')
 result=[]
 for role in session.scalars(select(AccessRole).where(AccessRole.tenant_id==context.tenant_id,AccessRole.workspace_id==context.workspace_id)):
  from types import SimpleNamespace
  _,value=policy(SimpleNamespace(role=role.base_role,role_id=role.id,tenant_id=context.tenant_id,workspace_id=context.workspace_id),session)
  result.append({'role_id':role.id,'role_name':role.name,**value})
 return {'items':result}
@router.put('/auth/session-policies/{role_id}')
def update(role_id:str,body:PolicyWrite,request:Request,session=Depends(get_session)):
 from app.auth import identity
 from app.rbac import require_permission,role_for,audit
 session.connection().exec_driver_sql('BEGIN IMMEDIATE')
 context=identity(request);require_permission(context,session,'roles.manage');role=role_for(session,context.tenant_id,context.workspace_id,role_id)
 if role.base_role=='super_admin':raise AppError('PERSISTENT_ADMIN_SESSION','超级管理员会话不按时间到期，可通过退出、禁用、改密或降权撤销',422)
 if body.idle_seconds>body.absolute_seconds:raise AppError('SESSION_POLICY_INVALID','空闲时长不能超过会话总时长',422)
 row=session.scalar(select(Resource).where(Resource.kind==KIND,Resource.resource_id==role.id,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
 old=json.loads(row.payload) if row else {'revision':0}
 if old['revision']!=body.expected_revision:raise AppError('SESSION_POLICY_CONFLICT','会话规则已变化，请刷新后重试',409)
 value={**body.model_dump(exclude={'expected_revision'}),'revision':old['revision']+1}
 if row:row.payload=json.dumps(value);row.updated_at=utc_now()
 else:session.add(Resource(kind=KIND,resource_id=role.id,tenant_id=context.tenant_id,workspace_id=context.workspace_id,owner_id='__system__',payload=json.dumps(value),created_at=utc_now(),updated_at=utc_now()))
 audit(session,context,'session_policy_update','role',role.id,{'revision':value['revision']});session.flush();return {'role_id':role.id,**value,'persistent':False}
