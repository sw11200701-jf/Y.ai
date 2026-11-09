"""Local password/session authentication. No default account or anonymous context."""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import asyncio
from datetime import datetime,timedelta,timezone
from typing import Annotated
from fastapi import APIRouter,Depends,Request,Response
from pydantic import BaseModel,ConfigDict,Field,SecretStr,AliasChoices
from sqlalchemy import select,update,delete,func
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.exceptions import InvalidKey
from app.db import get_session,new_id,utc_now,Resource
from app.auth_models import User,AuthSession,Installation,LoginLimit,ResourceGrant
from app.contracts import BusinessContext
from app.errors import AppError
from app.settings import settings

_development_cookie_scope = os.environ.get('FINANCE_DEVELOPMENT_COOKIE_SCOPE', '') if settings.development_http else ''
if _development_cookie_scope and not re.fullmatch(r'[a-z0-9_]{1,24}', _development_cookie_scope):
    raise ValueError('INVALID_DEVELOPMENT_COOKIE_SCOPE')
_cookie_prefix = 'zhangpu' + ('_' + _development_cookie_scope if _development_cookie_scope else '')
COOKIE = _cookie_prefix + '_session'
PRECOOKIE = _cookie_prefix + '_preauth'
_account_lock=threading.RLock()
_hash_slots=threading.BoundedSemaphore(2)
_setup_lock=asyncio.Lock()
router=APIRouter()
PASSWORD_MIN_LENGTH=6
PASSWORD_MAX_LENGTH=128
NewPassword=Annotated[SecretStr,Field(min_length=PASSWORD_MIN_LENGTH,max_length=PASSWORD_MAX_LENGTH)]

def now(): return datetime.now(timezone.utc)
def stamp(value): return value.isoformat()
def expired(value): return datetime.fromisoformat(value)<=now()
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def csrf(token): return digest("zhangpu-csrf:"+token)

def password_hash(password):
    salt=secrets.token_bytes(16)
    key=Scrypt(salt=salt,length=32,n=2**17,r=8,p=1).derive(password.encode())
    return "scrypt$131072$8$1$"+base64.b64encode(salt).decode()+"$"+base64.b64encode(key).decode()

def verify_password(password,encoded):
    try:
        algorithm,n,r,p,salt,key=encoded.split("$")
        if (algorithm,n,r,p)!=("scrypt","131072","8","1"):return False
        Scrypt(salt=base64.b64decode(salt,validate=True),length=32,n=2**17,r=8,p=1).verify(password.encode(),base64.b64decode(key,validate=True))
        return True
    except (ValueError,InvalidKey):return False

def user_public(user,session):
    from app.rbac import user_policy
    role,permissions=user_policy(user,session)
    from app.data_access import roles_for_user,policy_version
    from types import SimpleNamespace
    return {"id":user.id,"username":user.username,"display_name":user.display_name,"role":user.role,"role_id":role.id,"role_ids":[role.id]+sorted(roles_for_user(session,user)-{role.id}),"role_name":role.name,"active":user.active,"tenant_id":user.tenant_id,"workspace_id":user.workspace_id,"permissions":permissions,"permission_overrides":json.loads(user.permission_overrides) if user.permission_overrides is not None else None,"permission_ceiling":json.loads(user.permission_ceiling) if user.permission_ceiling is not None else None,"must_change_password":bool(user.must_change_password),"department_id":user.department_id,"company_id":user.company_id,"policy_version":policy_version(session,SimpleNamespace(tenant_id=user.tenant_id,workspace_id=user.workspace_id))}

def context_for(user,auth_session,session=None):
    from sqlalchemy.orm import object_session
    from app.rbac import user_policy
    session=session or object_session(user)
    if session is None:raise AppError("AUTH_REQUIRED","身份上下文需要有效数据库会话",401)
    role,permissions=user_policy(user,session)
    return BusinessContext(tenant_id=user.tenant_id,workspace_id=user.workspace_id,operator_id=user.id,actor_id=user.id,role=user.role,role_id=role.id,permissions=permissions,must_change_password=bool(user.must_change_password),session_id=auth_session.id,allowed_entities=json.loads(user.allowed_entities),allowed_origins=list(settings.tool_allowed_origins),allowed_tool_origins=list(settings.tool_allowed_origins))

def validate_context(context,session):
    if getattr(context,"principal_type","human")=="platform":
        from app.platform_api import validate_platform_context
        return validate_platform_context(context,session)
    if getattr(context,"principal_type","human")=="service":
        from app.machine_auth import validate_service_context
        return validate_service_context(context,session)
    user=session.get(User,context.operator_id,populate_existing=True)
    auth_session=session.get(AuthSession,context.session_id,populate_existing=True)
    if (user is None or not user.active or auth_session is None or auth_session.revoked or auth_session.user_id!=user.id
            or not __import__('app.session_policy',fromlist=['valid']).valid(auth_session,user,session,now())
            or (context.tenant_id,context.workspace_id,context.actor_id)!=(user.tenant_id,user.workspace_id,user.id)):
        raise AppError("SESSION_REVOKED","Session expired or revoked",401)
    from app.rbac import user_policy
    role,permissions=user_policy(user,session)
    context.role=user.role;context.role_id=role.id;context.permissions=permissions;context.must_change_password=bool(user.must_change_password)
    return user

def resolve_session(request,session,preauth=False):
    token=request.cookies.get(PRECOOKIE if preauth else COOKIE,"")
    if not 32<=len(token)<=128:return None,None,None
    auth_session=session.scalar(select(AuthSession).where(AuthSession.token_hash==digest(token)))
    if auth_session is None or auth_session.revoked:return None,None,None
    if preauth:
        return (auth_session,None,token) if auth_session.user_id is None and not expired(auth_session.expires_at) else (None,None,None)
    user=session.get(User,auth_session.user_id) if auth_session.user_id else None
    from app.session_policy import valid as session_valid
    if user is None or not session_valid(auth_session,user,session,now()):return None,None,None
    return auth_session,user,token

PUBLIC={"/healthz","/api/v1/auth/status","/api/v1/auth/csrf","/api/v1/auth/setup","/api/v1/auth/login"}

def bind_request(request,session):
    from app.machine_auth import sanitize_credential_url, is_open_path
    sanitize_credential_url(request)
    path=request.url.path
    if path=='/api/platform/v1' or path.startswith('/api/platform/v1/'):
        from app.platform_api import bind_request as bind_platform_request
        return bind_platform_request(request,session)
    if is_open_path(path):
        from app.machine_auth import bind_open_request
        return bind_open_request(request,session)
    if request.headers.get("authorization"):
        raise AppError("AUTH_SCHEME_NOT_ALLOWED","控制台只接受人类会话，不接受应用Bearer凭据",401)
    if path!="/healthz" and path!="/api/v1/auth/status" and not settings.development_http and request.url.scheme!="https":
        raise AppError("HTTPS_REQUIRED","HTTPS required; localhost HTTP requires explicit development configuration",426)
    auth_session,user,token=resolve_session(request,session)
    if user is not None:request.state.business_context=context_for(user,auth_session,session)
    if path not in PUBLIC and user is None:raise AppError("AUTH_REQUIRED","Authentication required",401)
    if request.method in {"POST","PUT","PATCH","DELETE"}:
        if path in ("/api/v1/auth/setup","/api/v1/auth/login") and token is None:
            _,_,token=resolve_session(request,session,preauth=True)
        check_csrf(request,token)
    if user is not None and path not in PUBLIC:
        allowed_change_routes={"/api/v1/auth/me","/api/v1/auth/csrf","/api/v1/auth/password","/api/v1/auth/logout"}
        if user.must_change_password and path not in allowed_change_routes:raise AppError("PASSWORD_CHANGE_REQUIRED","请先修改初始密码",403)
        from app.rbac import authorize_request
        authorize_request(request,session,request.state.business_context)

def refresh_request(request,session):
    context=getattr(request.state,"business_context",None)
    if context and getattr(context,"principal_type","human")=="human":
        session.execute(update(AuthSession).where(AuthSession.id==context.session_id,AuthSession.revoked==False).values(last_seen_at=utc_now()))

def check_origin(request):
    origin=request.headers.get("origin")
    if origin is not None and origin not in settings.allowed_web_origins:
        raise AppError("CSRF_REJECTED","Origin not allowed",403)
    if origin is None and request.headers.get("sec-fetch-site") in ("cross-site","same-site"):
        raise AppError("CSRF_REJECTED","Origin required for browser mutation",403)

def check_csrf(request,token):
    check_origin(request)
    supplied=request.headers.get("x-csrf-token","")
    if not token or not hmac.compare_digest(supplied,csrf(token)):
        raise AppError("CSRF_REJECTED","Invalid CSRF token",403)

def set_cookie(response,name,token,max_age):
    response.set_cookie(name,token,max_age=max_age,httponly=True,secure=not settings.development_http,samesite="lax",path="/")

def new_session(session,response,user=None):
    from app.session_policy import policy,PERSISTENT,COOKIE_LIFETIME
    token=secrets.token_urlsafe(32);rules=policy(user,session)[1] if user else {};duration=COOKIE_LIFETIME if user and rules['persistent'] else rules['absolute_seconds'] if user else 900
    auth_session=AuthSession(id=new_id("session"),token_hash=digest(token),user_id=user.id if user else None,created_at=utc_now(),expires_at=PERSISTENT if user and rules['persistent'] else stamp(now()+timedelta(seconds=duration)),last_seen_at=utc_now(),revoked=False)
    session.add(auth_session);session.flush()
    set_cookie(response,COOKIE if user else PRECOOKIE,token,duration)
    if user:response.delete_cookie(PRECOOKIE,path="/",secure=not settings.development_http,httponly=True,samesite="lax")
    return auth_session,token

def session_public(auth_session):
    from app.session_policy import PERSISTENT
    persistent=auth_session.expires_at==PERSISTENT
    return {'persistent':persistent,'expires_at':None if persistent else auth_session.expires_at}


def revoke(session, *, user_id=None,session_id=None):
    q=select(AuthSession).where(AuthSession.user_id==user_id) if user_id else select(AuthSession).where(AuthSession.id==session_id)
    ids=[]
    for row in session.scalars(q):row.revoked=True;ids.append(row.id)
    if user_id:
        # Account security changes permanently invalidate issued platform tokens.
        # Platform keys stay independent of a particular interactive login.
        from app.platform_api import revoke_subject_tokens
        revoke_subject_tokens(session,user_id)
    session.flush()
    return ids

async def cancel_sessions(ids):
    from app.runner.service import scheduler
    for sid in ids:await scheduler.revoke_session(sid)

def identity(request):
    context=getattr(request.state,"business_context",None)
    if context is None:raise AppError("AUTH_REQUIRED","Authentication required",401)
    return context

def require_admin(request):
    context=identity(request)
    if context.role not in ("super_admin","admin"):raise AppError("FORBIDDEN","需要管理员权限",403)
    return context

def username(value):
    normalized=value.strip().casefold()
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,63}",normalized):raise AppError("VALIDATION_ERROR","Username must contain3..64 ASCII letters/digits/_.- and start with letter",422)
    return normalized

def throttle(session,request,account):
    peer=request.client.host if request.client else "unknown"
    for raw,limit in (("peer:"+peer,40),("account:"+account,10)):
        key=digest(raw);row=session.get(LoginLimit,key)
        if row is not None and row.blocked_until and not expired(row.blocked_until):raise AppError("LOGIN_RATE_LIMIT","Try again later",429)
        if row is None:
            row=LoginLimit(key=key,count=0,window_start=utc_now(),blocked_until=None);session.add(row)
        if datetime.fromisoformat(row.window_start)+timedelta(minutes=15)<=now():row.count=0;row.window_start=utc_now();row.blocked_until=None
        row.count+=1
        if row.count>limit:row.blocked_until=stamp(now()+timedelta(minutes=15))
    session.commit()  # security counters persist even when authentication is rejected
    if any(row.blocked_until and not expired(row.blocked_until) for row in session.scalars(select(LoginLimit).where(LoginLimit.key.in_([digest("peer:"+peer),digest("account:"+account)])))):
        raise AppError("LOGIN_RATE_LIMIT","Try again later",429)

class Strict(BaseModel):model_config=ConfigDict(extra="forbid")
class Credentials(Strict):
    username: str=Field(min_length=3,max_length=64)
    # Login verifies existing hashes; creation policy applies only to new passwords.
    password: SecretStr=Field(min_length=1,max_length=PASSWORD_MAX_LENGTH)
class Setup(Credentials):
    password: NewPassword
    display_name: str=Field(default="",max_length=100)
class UserCreate(Strict):
    username: str=Field(min_length=3,max_length=64)
    display_name: str=Field(default="",max_length=100)
    role_id: str=Field(default="member",min_length=1,max_length=100,validation_alias=AliasChoices("role_id","role"))
    role_ids: list[str]|None=Field(default=None,min_length=1,max_length=8)
    permissions: list[str]|None=Field(default=None,max_length=16)
    department_id: str|None=Field(default=None,max_length=100)
    company_id: str|None=Field(default=None,max_length=100)
class UserUpdate(Strict):
    display_name: str|None=Field(default=None,max_length=100)
    role_id: str|None=Field(default=None,min_length=1,max_length=100,validation_alias=AliasChoices("role_id","role"))
    role_ids: list[str]|None=Field(default=None,min_length=1,max_length=8)
    permissions: list[str]|None=Field(default=None,max_length=16)
    department_id: str|None=Field(default=None,max_length=100)
    company_id: str|None=Field(default=None,max_length=100)
    clear_permission_ceiling: bool=False
    active: bool|None=None
class PasswordReset(Strict):pass
class PasswordChange(Strict):
    current_password: SecretStr=Field(min_length=1,max_length=PASSWORD_MAX_LENGTH)
    new_password: NewPassword

@router.get("/auth/status")
def status(session=Depends(get_session)):
    from app.local_preview import local_preview_identity
    result = {"initialized":session.get(Installation,1) is not None,"authentication_required":True,"development_http":settings.development_http}
    local = local_preview_identity()
    if local is not None:
        result["local_preview"] = local
    return result

@router.get("/auth/csrf")
def csrf_token(request:Request,response:Response,session=Depends(get_session)):
    auth_session,user,token=resolve_session(request,session)
    if auth_session is None:auth_session,_,token=resolve_session(request,session,preauth=True)
    if auth_session is None:auth_session,token=new_session(session,response)
    return {"csrf_token":csrf(token)}

@router.post("/auth/setup",status_code=201)
async def setup(body:Setup,request:Request,response:Response,session=Depends(get_session)):
    peer=request.client.host if request.client else ""
    try:local=ipaddress.ip_address(peer).is_loopback
    except ValueError:local=False
    if not local:raise AppError("FORBIDDEN","Installation setup requires loopback access",403)
    async with _setup_lock:
        if session.get(Installation,1):raise AppError("ALREADY_INITIALIZED","Installation already initialized",409)
        name=username(body.username);throttle(session,request,"setup:"+name)
        if not _hash_slots.acquire(blocking=False):raise AppError("LOGIN_RATE_LIMIT","Authentication busy",429)
        try:encoded=await asyncio.to_thread(password_hash,body.password.get_secret_value())
        finally:_hash_slots.release()
        from app.rbac import seed_roles
        seed_roles(session,"demo-enterprise","demo-workspace")
        user=User(id=new_id("user"),username=name,display_name=body.display_name or name,password_hash=encoded,tenant_id="demo-enterprise",workspace_id="demo-workspace",role="super_admin",active=True,allowed_entities='[]',created_at=utc_now(),must_change_password=False)
        session.add(user);session.flush()
        session.add(Installation(id=1,admin_user_id=user.id,created_at=utc_now()))
        legacy=list(session.scalars(select(Resource).where(Resource.owner_id=="__legacy_unassigned__")))
        for row in legacy:row.owner_id=user.id
        # Record explicit assignment without copying/rewriting original business JSON.
        from app.db import OwnershipAudit
        session.add(OwnershipAudit(id=new_id("audit"),actor_id=user.id,action="installation_claim_legacy",resource_kind="*",resource_id="*",details=json.dumps({"count":len(legacy),"scope":"private","workspace_id":user.workspace_id}),created_at=utc_now()))
        auth_session,token=new_session(session,response,user)
        try:
            from app.knowledge.service import adopt_legacy_documents
        except ModuleNotFoundError as exc:
            if exc.name not in ("app.knowledge","app.knowledge.service"):raise
        else:
            if any(row.kind=="document" for row in legacy):
                await adopt_legacy_documents(context=context_for(user,auth_session,session),session=session)
        session.commit()
        return {"user":user_public(user,session),"csrf_token":csrf(token),**session_public(auth_session),"legacy_resources_assigned":len(legacy)}

@router.post("/auth/login")
async def login(body:Credentials,request:Request,response:Response,session=Depends(get_session)):
    name=username(body.username)
    with _account_lock:throttle(session,request,name)
    if not _hash_slots.acquire(blocking=False):raise AppError("LOGIN_RATE_LIMIT","Authentication busy",429)
    try:
        user=session.scalar(select(User).where(User.username==name))
        # Unknown and disabled accounts pay the same mature KDF cost; no enumeration detail.
        if user:valid=await asyncio.to_thread(verify_password,body.password.get_secret_value(),user.password_hash)
        else:
            await asyncio.to_thread(password_hash,body.password.get_secret_value())
            valid=False
    finally:_hash_slots.release()
    if not valid or user is None or not user.active:raise AppError("INVALID_CREDENTIALS","Invalid username or password",401)
    old,_,_=resolve_session(request,session)
    ids=revoke(session,session_id=old.id) if old else []
    auth_session,token=new_session(session,response,user);session.commit()
    await cancel_sessions(ids)
    return {"user":user_public(user,session),"csrf_token":csrf(token),**session_public(auth_session)}

@router.get("/auth/me")
def me(request:Request,response:Response,session=Depends(get_session)):
    context=identity(request);user=validate_context(context,session)
    auth_session=session.get(AuthSession,context.session_id)
    from app.session_policy import PERSISTENT,COOKIE_LIFETIME
    if auth_session.expires_at==PERSISTENT:set_cookie(response,COOKIE,request.cookies[COOKIE],COOKIE_LIFETIME)
    return {"user":user_public(user,session),"csrf_token":csrf(request.cookies[COOKIE]),**session_public(auth_session),"workspace":{"id":user.workspace_id,"tenant_id":user.tenant_id}}

@router.post("/auth/logout")
async def logout(request:Request,response:Response,session=Depends(get_session)):
    context=identity(request);ids=revoke(session,session_id=context.session_id);session.commit()
    await cancel_sessions(ids)
    response.delete_cookie(COOKIE,path="/",secure=not settings.development_http,httponly=True,samesite="lax")
    return {"logged_out":True}

@router.get("/users")
def users(request:Request,session=Depends(get_session)):
    context=require_admin(request)
    return {"items":[user_public(u,session) for u in session.scalars(select(User).where(User.tenant_id==context.tenant_id,User.workspace_id==context.workspace_id))]}

@router.post("/users",status_code=201)
async def create_user(body:UserCreate,request:Request,session=Depends(get_session)):
    context=require_admin(request);name=username(body.username)
    from app.rbac import assignment,audit
    with _account_lock:
        if body.role_ids is not None and "role_id" in body.model_fields_set:raise AppError("INVALID_ROLE_SET","不能同时提交单角色和多角色列表",422)
        selectors=body.role_ids if body.role_ids is not None else body.role_id
        role,extras,overrides,ceiling=assignment(session,context,selectors,body.permissions)
        check_identity_scope(session,context,body)
        if session.scalar(select(User).where(User.username==name)):raise AppError("CONFLICT","Username unavailable",409)
        if not _hash_slots.acquire(blocking=False):raise AppError("LOGIN_RATE_LIMIT","Authentication busy",429)
        initial_password=secrets.token_urlsafe(18)
        try:encoded=await asyncio.to_thread(password_hash,initial_password)
        finally:_hash_slots.release()
        from app.rbac import require_permission
        require_permission(context,session,"accounts.manage")
        require_permission(context,session,"page.accounts")
        role,extras,overrides,ceiling=assignment(session,context,selectors,body.permissions)
        user=User(id=new_id("user"),username=name,display_name=body.display_name or name,password_hash=encoded,tenant_id=context.tenant_id,workspace_id=context.workspace_id,role=role.base_role,role_id=role.id,permission_overrides=json.dumps(overrides) if overrides is not None else None,permission_ceiling=json.dumps(ceiling) if ceiling is not None else None,created_by=context.operator_id,must_change_password=True,active=True,allowed_entities='[]',created_at=utc_now(),department_id=body.department_id,company_id=body.company_id)
        session.add(user);session.flush();audit(session,context,"account_create","user",user.id,{"role_id":role.id})
        set_extra_roles(session,user.id,extras);bump_user_policy(session,context)
        session.commit();return {**user_public(user,session),"initial_password":initial_password}

def target_user(session,user_id,context):
    user=session.get(User,user_id)
    if user is None or (user.tenant_id,user.workspace_id)!=(context.tenant_id,context.workspace_id):raise AppError("NOT_FOUND","Account not found",404)
    return user

def set_extra_roles(session,user_id,role_ids):
    from app.data_models import UserRoleMembership
    session.execute(delete(UserRoleMembership).where(UserRoleMembership.user_id==user_id))
    for role_id in role_ids:session.add(UserRoleMembership(user_id=user_id,role_id=role_id))
    session.flush()

def bump_user_policy(session,context):
    from app.data_access import bump_policy_version
    bump_policy_version(session,context)

def check_identity_scope(session,context,body):
    actor=validate_context(context,session)
    if actor.role!="super_admin":
        for field in ("department_id","company_id"):
            value=getattr(body,field,None)
            if value is not None and value!=getattr(actor,field):raise AppError("DATA_DELEGATION_DENIED","不能配置超出自身主体范围的账号身份",403)

@router.patch("/users/{user_id}")
async def update_user(user_id:str,body:UserUpdate,request:Request,session=Depends(get_session)):
    context=require_admin(request)
    from app.rbac import assignment,protect_last_super,audit
    with _account_lock:
        user=target_user(session,user_id,context)
        if body.role_ids is not None and "role_id" in body.model_fields_set:raise AppError("INVALID_ROLE_SET","不能同时提交单角色和多角色列表",422)
        from app.data_access import roles_for_user
        selectors=body.role_ids if body.role_ids is not None else body.role_id if body.role_id is not None else list(roles_for_user(session,user))
        overrides=body.permissions if "permissions" in body.model_fields_set else (json.loads(user.permission_overrides) if user.permission_overrides is not None else None)
        role,extras,overrides,ceiling=assignment(session,context,selectors,overrides,target=user)
        if body.clear_permission_ceiling:
            if context.role!="super_admin":raise AppError("PERMISSION_GRANT_DENIED","只有超级管理员可显式解除创建者权限上限",403)
            ceiling=None
        check_identity_scope(session,context,body)
        protect_last_super(session,user,role=role.base_role,active=body.active)
        if body.display_name is not None:user.display_name=body.display_name
        if body.active is not None:user.active=body.active
        user.role=role.base_role;user.role_id=role.id;user.permission_overrides=json.dumps(overrides) if overrides is not None else None
        user.permission_ceiling=json.dumps(ceiling) if ceiling is not None else None
        for field in ("department_id","company_id"):
            if field in body.model_fields_set:setattr(user,field,getattr(body,field))
        set_extra_roles(session,user.id,extras)
        if body.model_fields_set & {"role_id","role_ids","permissions","active","department_id","company_id"} or body.clear_permission_ceiling:bump_user_policy(session,context)
        ids=revoke(session,user_id=user.id) if body.role_id is not None or body.role_ids is not None or body.active is not None else []
        audit(session,context,"account_update","user",user.id,{"fields":sorted(body.model_fields_set)})
        session.commit()
    await cancel_sessions(ids);return user_public(user,session)

@router.post("/users/{user_id}/reset-password")
async def reset_password(user_id:str,body:PasswordReset,request:Request,session=Depends(get_session)):
    context=require_admin(request);user=target_user(session,user_id,context)
    from app.rbac import assignment,audit
    from app.data_access import roles_for_user
    assignment(session,context,list(roles_for_user(session,user)),json.loads(user.permission_overrides) if user.permission_overrides is not None else None,target=user)
    if not _hash_slots.acquire(blocking=False):raise AppError("LOGIN_RATE_LIMIT","Authentication busy",429)
    initial_password=secrets.token_urlsafe(18)
    try:encoded=await asyncio.to_thread(password_hash,initial_password)
    finally:_hash_slots.release()
    from app.rbac import require_permission
    require_permission(context,session,"accounts.manage");require_permission(context,session,"page.accounts")
    user=session.get(User,user_id,populate_existing=True)
    assignment(session,context,list(roles_for_user(session,user)),json.loads(user.permission_overrides) if user.permission_overrides is not None else None,target=user)
    user.password_hash=encoded
    user.must_change_password=True
    bump_user_policy(session,context)
    audit(session,context,"account_password_reset","user",user.id)
    ids=revoke(session,user_id=user.id);session.commit();await cancel_sessions(ids)
    return {"password_changed":True,"sessions_revoked":True,"must_change_password":True,"initial_password":initial_password}

@router.post("/auth/password")
async def change_password(body:PasswordChange,request:Request,response:Response,session=Depends(get_session)):
    context=identity(request);user=validate_context(context,session)
    previous_hash=user.password_hash
    if hmac.compare_digest(body.current_password.get_secret_value().encode(),body.new_password.get_secret_value().encode()):
        raise AppError("PASSWORD_REUSED","新密码不能与当前密码相同",422)
    if not _hash_slots.acquire(blocking=False):raise AppError("LOGIN_RATE_LIMIT","Authentication busy",429)
    try:
        if not await asyncio.to_thread(verify_password,body.current_password.get_secret_value(),user.password_hash):raise AppError("INVALID_CREDENTIALS","Current password invalid",401)
        encoded=await asyncio.to_thread(password_hash,body.new_password.get_secret_value())
    finally:_hash_slots.release()
    user=validate_context(context,session)
    if user.password_hash!=previous_hash:raise AppError("CREDENTIALS_CHANGED","密码已被更改，请重新登录后操作",409)
    user.password_hash=encoded;ids=revoke(session,user_id=user.id)
    user.must_change_password=False
    from app.rbac import audit
    audit(session,context,"account_password_change","user",user.id)
    auth_session,token=new_session(session,response,user);session.commit();await cancel_sessions(ids)
    return {"user":user_public(user,session),"csrf_token":csrf(token),**session_public(auth_session)}
