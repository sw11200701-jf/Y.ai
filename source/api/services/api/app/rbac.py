"""Fresh server-side capability policy; resource ACL remains a separate check."""
import json
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, func
from app.errors import AppError

CATALOG={
    "page.workbench":"工作台 · 页面访问", "page.ai":"AI工作室 · 页面访问", "page.knowledge":"数据中心 · 知识库 · 页面访问",
    "page.plugins":"插件与服务 · 页面访问", "page.runs":"运行记录 · 页面访问", "page.accounts":"账号与会话 · 页面访问", "page.database":"数据中心 · 数据库 · 页面访问",
    "page.roles":"角色与权限 · 页面访问", "accounts.manage":"账号与会话 · 管理", "roles.manage":"角色与权限 · 管理",
    "runs.approve":"运行记录 · 审批指定运行",
    "page.models":"模型管理 · 页面访问", "models.manage":"模型管理 · 管理",
}
CATALOG.update({"page.applications":"对外应用 · 页面访问","applications.read":"对外应用 · 查看","applications.manage":"对外应用 · 管理","applications.publish":"对外应用 · 发布固定版本","applications.keys.manage":"对外应用 · 管理客户端与Key","applications.records.read":"对外应用 · 安全调用记录","applications.runs.control":"对外应用 · 控制运行"})
CATALOG.update({'chunker.config.view':'知识切片配置 · 查看目录','chunker.config.read':'知识切片配置 · 读取内容','chunker.config.manage':'知识切片配置 · 管理','platform.keys.manage':'平台 API · 管理本人凭据','platform.keys.admin':'平台 API · 管理空间凭据','spaces.manage':'空间 · 管理成员与授权','platform.api.publish':'平台 API · 配置发布','resources.execute':'资源 · 独立执行授权'})
ALL=set(CATALOG)
MEMBER={"page.workbench","page.ai","page.knowledge","page.database","page.plugins","page.runs","page.applications","applications.read"}
DEFAULTS={"member":MEMBER,"admin":MEMBER|{"page.accounts","accounts.manage","chunker.config.view"},"super_admin":ALL}
LABELS={"member":"普通用户","admin":"管理员","super_admin":"超级管理员"}
RUN_PERMISSIONS=("page.workbench","page.ai")
router=APIRouter(tags=["roles"])

def check_keys(values):
    if not isinstance(values,list) or any(not isinstance(v,str) or v not in ALL for v in values) or len(values)!=len(set(values)):
        raise AppError("INVALID_PERMISSION","权限项无效或重复",422,{"allowed_keys":sorted(ALL)})
    return sorted(values)

def seed_roles(session,tenant_id,workspace_id):
    from app.auth_models import AccessRole
    from app.db import new_id,utc_now
    for key,permissions in DEFAULTS.items():
        if session.scalar(select(AccessRole).where(AccessRole.tenant_id==tenant_id,AccessRole.workspace_id==workspace_id,AccessRole.key==key)) is None:
            session.add(AccessRole(id=new_id("role"),key=key,tenant_id=tenant_id,workspace_id=workspace_id,name=LABELS[key],base_role=key,permissions=json.dumps(sorted(permissions)),builtin=True,created_at=utc_now(),updated_at=utc_now()))
    from app.feature_policy import seed_feature_permissions
    seed_feature_permissions(session,tenant_id,workspace_id)
    session.flush()

def role_for(session,tenant_id,workspace_id,selector):
    from app.auth_models import AccessRole
    key=AccessRole.key==selector if selector in DEFAULTS else AccessRole.id==selector
    role=session.scalar(select(AccessRole).where(AccessRole.tenant_id==tenant_id,AccessRole.workspace_id==workspace_id,key).execution_options(populate_existing=True))
    if role is None:raise AppError("ROLE_NOT_FOUND","角色不存在或不可用",404)
    return role

def user_policy(user,session):
    from app.data_models import UserRoleMembership
    role=role_for(session,user.tenant_id,user.workspace_id,user.role_id or user.role)
    if role.base_role!=user.role:raise AppError("ROLE_POLICY_INVALID","账号角色配置不一致，请联系超级管理员",403)
    permissions=set(json.loads(role.permissions))
    for role_id in session.scalars(select(UserRoleMembership.role_id).where(UserRoleMembership.user_id==user.id)):
        from app.auth_models import AccessRole
        extra=session.get(AccessRole,role_id,populate_existing=True)
        if extra is None or (extra.tenant_id,extra.workspace_id)!=(user.tenant_id,user.workspace_id):continue
        if extra.base_role=="super_admin" or extra.base_role=="admin" and user.role!="admin":raise AppError("ROLE_POLICY_INVALID","附加角色与账号级别不一致",403)
        permissions.update(json.loads(extra.permissions))
    for raw in (user.permission_overrides,user.permission_ceiling):
        if raw is not None:permissions.intersection_update(json.loads(raw))
    return role,sorted(permissions & ALL)

def require_any_permission(context,session,keys):
    from app.auth import validate_context
    user=validate_context(context,session)
    if user.must_change_password:raise AppError("PASSWORD_CHANGE_REQUIRED","请先修改初始密码",403)
    if not set(keys).intersection(context.permissions):
        raise AppError("PERMISSION_DENIED","没有此功能的访问权限",403,{"required_any":list(keys)})
    return user

def require_permission(context,session,key):
    return require_any_permission(context,session,(key,))

def audit(session,context,action,kind,resource_id,details=None):
    from app.db import OwnershipAudit,new_id,utc_now
    session.add(OwnershipAudit(id=new_id("audit"),actor_id=context.operator_id,action=action,resource_kind=kind,resource_id=resource_id,details=json.dumps(details or {},ensure_ascii=False),created_at=utc_now()))

def assignment(session,context,selector,overrides,target=None):
    from app.auth import validate_context
    actor=validate_context(context,session)
    if actor.role not in ("super_admin","admin"):raise AppError("FORBIDDEN","需要管理员权限",403)
    selectors=selector if isinstance(selector,list) else [selector]
    if not 1<=len(selectors)<=8 or len(selectors)!=len(set(selectors)):raise AppError("INVALID_ROLE_SET","角色列表无效或重复",422)
    roles=[role_for(session,context.tenant_id,context.workspace_id,key) for key in selectors]
    if len({role.id for role in roles})!=len(roles):raise AppError("INVALID_ROLE_SET","角色列表重复",422)
    roles.sort(key=lambda role:{"super_admin":0,"admin":1,"member":2}[role.base_role]);role=roles[0]
    allowed=set().union(*(set(json.loads(item.permissions)) for item in roles)) if role.base_role!="super_admin" else ALL
    if overrides is not None:
        overrides=check_keys(overrides)
        if not set(overrides)<=allowed:raise AppError("PERMISSION_GRANT_DENIED","个人权限只能限制角色权限",403)
    if role.base_role=="super_admin" and overrides is not None:
        raise AppError("SUPER_ADMIN_PROTECTED","超级管理员必须保留完整管理权限",403)
    ceiling=json.loads(target.permission_ceiling) if target is not None and target.permission_ceiling is not None else None
    if actor.role!="super_admin":
        if role.base_role!="member" or target is not None and target.role!="member":
            raise AppError("ROLE_GRANT_DENIED","管理员只能创建或管理普通用户角色的账号",403)
        effective=allowed if overrides is None else set(overrides)
        if not effective<=set(context.permissions):
            # Inherit is safely bounded by the creator; explicit over-grants reject.
            if overrides is not None:raise AppError("PERMISSION_GRANT_DENIED","不能授予超出自身授权范围的权限",403)
        if target is not None:
            _,current=user_policy(target,session)
            if not set(current)<=set(context.permissions):raise AppError("PERMISSION_GRANT_DENIED","不能管理权限超出自身范围的账号",403)
        ceiling=set(context.permissions)
        if target is not None and target.permission_ceiling is not None:ceiling.intersection_update(json.loads(target.permission_ceiling))
        ceiling=sorted(ceiling)
    from app.data_access import validate_role_delegation
    # Assignment can implicitly grant data, even if the actor manages only accounts.
    existing=set()
    if target:
        from app.data_access import roles_for_user
        existing=roles_for_user(session,target)
    for item in roles:
        if item.id not in existing:validate_role_delegation(session,context,item)
    return role,[item.id for item in roles[1:]],overrides,ceiling

def protect_last_super(session,user,*,role=None,active=None):
    from app.auth_models import User
    if user.role=="super_admin" and user.active and (role is not None and role!="super_admin" or active is False):
        count=session.scalar(select(func.count()).select_from(User).where(User.tenant_id==user.tenant_id,User.workspace_id==user.workspace_id,User.role=="super_admin",User.active==True))
        if count<=1:raise AppError("LAST_SUPER_ADMIN","不能禁用或降级最后一个有效超级管理员",409)

def route_permissions(path,method):
    """Each tuple is an OR group; groups must all pass. Unknown business paths deny."""
    if path=="/api/v1/workflow-resource-catalog/import-batches" and method=="GET":
        return [("page.ai","page.workbench","page.knowledge")]
    if path in ("/api/v1/capabilities","/api/v1/workflows/node-registry"):return []
    if path.startswith(("/docs","/redoc","/openapi.json")):return [tuple("page."+v for v in ("workbench","ai","knowledge","plugins","runs","accounts","roles"))]
    if not path.startswith("/api/v1/"):return [("__unclassified__",)]
    parts=path[len('/api/v1/'):].strip('/').split('/');head=parts[0]
    if head=="auth":return []
    if head=="platform":
        if len(parts)>1 and parts[1]=='assets':return [('page.ai','page.workbench')]
        return [("platform.keys.manage","platform.keys.admin","spaces.manage")]
    if head in ("model-catalog","user-variables"):return [RUN_PERMISSIONS]
    if head in ("model-providers","model-provider-presets","models"):
        return [("page.models",)] if method=="GET" else [("page.models",),("models.manage",)]
    if head=="applications":
        if 'client-options' in parts:return [('applications.keys.manage',)]
        if "calls" in parts:return [("applications.records.read",)]
        if "keys" in parts or "clients" in parts:return [("applications.read",)] if method=="GET" else [("applications.keys.manage",)]
        if "versions" in parts or "activate-version" in parts:return [("applications.read",)] if method=="GET" else [("applications.publish",)]
        return [("applications.read",)] if method=="GET" else [("applications.manage",)]
    if head=='builtin-agents':
        if parts[-1]=='workflow-preview' and method=='POST':return [('chunker.config.read','chunker.config.manage')]
        if parts[-1] in {'document-jobs','document-job-options','document-job-budget'}:return [('page.ai','page.knowledge')]
        return [('chunker.config.view','chunker.config.read','chunker.config.manage')] if method=='GET' and len(parts)==1 else [('chunker.config.read','chunker.config.manage')] if method=='GET' else [('chunker.config.manage',)]
    if head=="run-approvers":return [RUN_PERMISSIONS]
    if head=="approvals":return [("runs.approve",)]
    if head=="users":return [("page.accounts",),("accounts.manage",)]
    if head=="permissions":return [("page.accounts","page.roles")]
    if head=="roles":return [("page.accounts","page.roles")] if method=="GET" else [("page.roles",),("roles.manage",)]
    if head=="data-access":
        if parts[-1]=="capabilities":return [("page.knowledge","page.database","page.accounts","page.workbench","page.ai")]
        return [("page.knowledge","page.database","page.accounts")]
    if head in ("databases","data-tables","database","tables"):
        if method=="GET" or method=="POST" and parts[-1] in ("query","statistics","aggregate","export"):return [("page.database","page.workbench","page.ai")]
        return [("page.database",)]
    if head=="resources" and len(parts)>1:
        head={"plugin":"plugins","plugin_version":"plugins","skill":"plugins","skill_version":"plugins","connection":"connections","agent":"agents","agent_version":"agents","workflow":"workflows","run":"runs","dataset":"datasets","artifact":"artifacts","attachment":"attachments","database":"databases","data_table":"databases","data_table_version":"databases","model_provider":"model-providers","model_configuration":"models"}.get(parts[1],"knowledge" if parts[1].startswith(("knowledge","document","import")) else "__unclassified__")
        if head=="runs":return [("page.runs","page.ai","page.workbench")]
        if head in ("model-providers","models"):return [("page.models",)] if method=="GET" else [("page.models",),("models.manage",)]
    if head in ("plugins","connections"):
        return [("page.plugins","page.ai","page.workbench")] if method=="GET" else [("page.plugins",)]
    if head=="tools":return [("page.plugins","page.ai","page.workbench")]
    if head in ("agents","workflows"):
        if head=="workflows" and len(parts)>1 and parts[1] in {"debug-runs","debug-target"}:return [RUN_PERMISSIONS]
        return [("page.ai","page.workbench")] if method=="GET" else [("page.ai",)]
    if head=="runs":
        if "approvals" in parts:return [("page.runs","page.ai","page.workbench","runs.approve")]
        if len(parts)==1:return [("page.runs",)] if method=="GET" else [RUN_PERMISSIONS]
        return [("page.runs","page.ai","page.workbench")]
    if head in ("datasets","artifacts","document-artifacts","charts","reports","finance","metrics","facts"):
        return [("page.runs","page.workbench","page.ai","page.knowledge","page.database")]
    if head=="attachments":return [("page.ai","page.workbench","page.knowledge")]
    # This download is an exact read route; the domain rechecks all four full export scopes.
    if (method == "GET" and len(parts) == 7 and parts[0:2] == ["knowledge", "documents"]
            and parts[3] == "versions" and parts[5:] == ["chunks", "export.xlsx"]):
        return [("page.knowledge", "page.ai", "page.workbench")]
    if head=="knowledge" and len(parts)>1 and parts[1]=="search":return [("page.knowledge","page.ai","page.workbench")]
    if head.startswith("knowledge") or head=="documents":
        if method=="GET":return [("page.knowledge","page.ai","page.workbench")]
        if head=="knowledge" and len(parts)>1 and parts[1] in ("datasets","batches"):return [("page.knowledge","page.database")]
        return [("page.knowledge",)]
    if head=="imports":return [("page.knowledge","page.workbench")]
    return [("__unclassified__",)]

def authorize_request(request,session,context):
    for alternatives in route_permissions(request.url.path,request.method):require_any_permission(context,session,alternatives)

class RoleWrite(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name:str=Field(min_length=1,max_length=100)
    base_role:Literal["member","admin"]="member"
    permissions:list[str]=Field(max_length=len(CATALOG))
    @field_validator("permissions")
    @classmethod
    def valid_keys(cls,value):return check_keys(value)

class RolePatch(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name:str|None=Field(default=None,min_length=1,max_length=100)
    permissions:list[str]|None=Field(default=None,max_length=len(CATALOG))
    @field_validator("permissions")
    @classmethod
    def valid_keys(cls,value):return check_keys(value) if value is not None else None

def role_view(role):
    return {"id":role.id,"key":role.key,"name":role.name,"base_role":role.base_role,"builtin":role.builtin,"permissions":json.loads(role.permissions)}

def role_permissions(base,values):
    keys=set(check_keys(values))
    if base=="super_admin":
        if not {"roles.manage","page.roles"}<=keys:raise AppError("ROLE_RECOVERY_PROTECTED","需保留角色管理入口，以便恢复其他授权",409)
        return sorted(keys)
    if keys & {"roles.manage","page.roles"} or base=="member" and "accounts.manage" in keys:
        raise AppError("ROLE_GRANT_DENIED","该管理权限不能配置给此角色",403)
    return sorted(keys)

def super_context(request,session):
    from app.auth import identity
    context=identity(request);require_permission(context,session,"roles.manage")
    if context.role!="super_admin":raise AppError("FORBIDDEN","只有超级管理员可以配置角色和页面权限",403)
    return context

from app.db import get_session

@router.get("/permissions")
def permissions(request:Request,session=Depends(get_session)):
    return {"items":[{"key":key,"name":name} for key,name in CATALOG.items()]}

@router.get("/roles")
def roles(request:Request,session=Depends(get_session)):
    from app.auth import require_admin
    from app.auth_models import AccessRole
    context=require_admin(request)
    require_any_permission(context,session,("accounts.manage","roles.manage"))
    rows=list(session.scalars(select(AccessRole).where(AccessRole.tenant_id==context.tenant_id,AccessRole.workspace_id==context.workspace_id)))
    return {"items":[role_view(row) for row in rows if context.role=="super_admin" or row.base_role=="member"]}

@router.post("/roles",status_code=201)
def create_role(body:RoleWrite,request:Request,session=Depends(get_session)):
    from app.auth_models import AccessRole
    from app.db import new_id,utc_now
    context=super_context(request,session);keys=role_permissions(body.base_role,body.permissions)
    role=AccessRole(id=new_id("role"),key=new_id("custom"),name=body.name,base_role=body.base_role,permissions=json.dumps(keys),builtin=False,tenant_id=context.tenant_id,workspace_id=context.workspace_id,created_at=utc_now(),updated_at=utc_now())
    session.add(role);session.flush();audit(session,context,"role_create","role",role.id,{"permissions":keys})
    from app.data_access import bump_policy_version
    bump_policy_version(session,context)
    return role_view(role)

@router.patch("/roles/{role_id}")
def update_role(role_id:str,body:RolePatch,request:Request,session=Depends(get_session)):
    from app.db import utc_now
    context=super_context(request,session);role=role_for(session,context.tenant_id,context.workspace_id,role_id)
    if role.builtin and body.name is not None:raise AppError("BUILTIN_ROLE_PROTECTED","不能更改内建角色名称",409)
    if body.name is not None:role.name=body.name
    if body.permissions is not None:role.permissions=json.dumps(role_permissions(role.base_role,body.permissions))
    role.updated_at=utc_now();audit(session,context,"role_update","role",role.id)
    from app.data_access import bump_policy_version
    bump_policy_version(session,context)
    return role_view(role)

@router.delete("/roles/{role_id}")
def delete_role(role_id:str,request:Request,session=Depends(get_session)):
    from app.auth_models import User
    context=super_context(request,session);role=role_for(session,context.tenant_id,context.workspace_id,role_id)
    if role.builtin:raise AppError("BUILTIN_ROLE_PROTECTED","不能删除内建角色",409)
    from app.data_models import UserRoleMembership,DataGrant
    if session.scalar(select(User.id).where(User.role_id==role.id).limit(1)) or session.scalar(select(UserRoleMembership.user_id).where(UserRoleMembership.role_id==role.id).limit(1)) or session.scalar(select(DataGrant.id).where(DataGrant.subject_type=="role",DataGrant.subject_id==role.id).limit(1)):raise AppError("ROLE_IN_USE","角色仍被账号或数据授权使用，不能删除",409)
    session.delete(role);audit(session,context,"role_delete","role",role.id)
    from app.data_access import bump_policy_version
    bump_policy_version(session,context)
    return {"deleted":True}
