"""Explicit target-space configuration, without switching a human session."""
import json
from fastapi import Request,Depends
from pydantic import Field
from app.db import get_session,new_id,utc_now
from app.auth_models import AccessRole
from app.errors import AppError
from app.rbac import RoleWrite,RolePatch,role_for,role_view,role_permissions
from app.resource_policy import Policy,policy_view,save_policy
from app.platform_api import manage,_human,_space_manage,scoped_permissions

class Patch(RolePatch):
    expected_updated_at:str=Field(min_length=1,max_length=80)

def manager(request,session,workspace_id,role_id=None):
    context=_human(request,session,'spaces.manage');actor=_space_manage(session,context,workspace_id)
    _,permissions=scoped_permissions(session,actor,workspace_id)
    if 'roles.manage' not in permissions:raise AppError('PERMISSION_DENIED','没有此空间的角色配置权限',403)
    role=role_for(session,context.tenant_id,workspace_id,role_id) if role_id else None
    return context,permissions,role

@manage.post('/platform/spaces/{workspace_id}/roles',status_code=201)
def create(workspace_id:str,body:RoleWrite,request:Request,session=Depends(get_session)):
    context,permissions,_=manager(request,session,workspace_id)
    keys=role_permissions(body.base_role,body.permissions)
    if not set(keys)<=permissions:raise AppError('PERMISSION_GRANT_DENIED','不能授予超出自身范围的权限',403)
    role=AccessRole(id=new_id('role'),key=new_id('custom'),tenant_id=context.tenant_id,workspace_id=workspace_id,name=body.name,base_role=body.base_role,
        permissions=json.dumps(keys),builtin=False,created_at=utc_now(),updated_at=utc_now());session.add(role);session.flush()
    return {**role_view(role),'updated_at':role.updated_at}

@manage.patch('/platform/spaces/{workspace_id}/roles/{role_id}')
def update(workspace_id:str,role_id:str,body:Patch,request:Request,session=Depends(get_session)):
    context,permissions,role=manager(request,session,workspace_id,role_id)
    if body.expected_updated_at!=role.updated_at:raise AppError('ROLE_CONFLICT','角色已变化，请刷新',409)
    if body.name is not None:
        if role.builtin:raise AppError('BUILTIN_ROLE_PROTECTED','内置角色名称不能修改',409)
        role.name=body.name
    if body.permissions is not None:
        keys=role_permissions(role.base_role,body.permissions)
        if not set(keys)<=permissions:raise AppError('PERMISSION_GRANT_DENIED','不能授予超出自身范围的权限',403)
        role.permissions=json.dumps(keys)
    role.updated_at=utc_now()
    from app.data_access import bump_policy_version
    from types import SimpleNamespace
    bump_policy_version(session,SimpleNamespace(tenant_id=role.tenant_id,workspace_id=workspace_id))
    return {**role_view(role),'updated_at':role.updated_at}

@manage.get('/platform/spaces/{workspace_id}/roles/{role_id}/resource-policy')
def read_policy(workspace_id:str,role_id:str,request:Request,session=Depends(get_session)):
    _,_,role=manager(request,session,workspace_id,role_id)
    return policy_view(session,role)

@manage.put('/platform/spaces/{workspace_id}/roles/{role_id}/resource-policy')
def write_policy(workspace_id:str,role_id:str,body:Policy,request:Request,session=Depends(get_session)):
    context,_,role=manager(request,session,workspace_id,role_id)
    return save_policy(session,role,body,context.operator_id)
