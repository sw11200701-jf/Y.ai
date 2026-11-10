"""Resource visibility, content access and execution are separate decisions."""
import json
from types import SimpleNamespace
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, or_, and_, exists, false
from app.db import Resource, get_session, utc_now
from app.auth_models import ResourceGrant
from app.errors import AppError

LEVELS = {'none':0, 'view':1, 'read':2, 'write':3}
KINDS = {'agent','agent_version','workflow','plugin','plugin_version','connection',
         'knowledge_base','knowledge_document','knowledge_dataset','knowledge_file','knowledge_batch',
         'knowledge_version','knowledge_chunk','document','database','data_table','data_table_version',
         'data_file','data_batch','attachment'}
router = APIRouter(tags=['resource-permissions'])

class Scope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=160)
    access: str = Field(default='view', pattern='^(none|view|read|write)$')
    execute: bool = False

class Policy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(default=0, ge=0)
    owned_access: str = Field(default='write', pattern='^(none|view|read|write)$')
    owned_execute: bool = True
    workspace_access: str = Field(default='none', pattern='^(none|view|read|write)$')
    workspace_execute: bool = False
    resources: list[Scope] = Field(default_factory=list, max_length=256)

def subject_roles(session, subject):
    from app.data_access import roles_for_user
    from app.auth_models import AccessRole
    return [r for rid in roles_for_user(session, subject)
            if (r := session.get(AccessRole, rid, populate_existing=True)) is not None
            and (r.tenant_id,r.workspace_id)==(subject.tenant_id,subject.workspace_id)]

def policies(session, subject):
    result=[]
    for role in subject_roles(session, subject):
        row=session.scalar(select(Resource).where(Resource.kind=='role_resource_policy',Resource.resource_id==role.id,
            Resource.tenant_id==subject.tenant_id,Resource.workspace_id==subject.workspace_id).execution_options(populate_existing=True))
        result.append(Policy.model_validate(json.loads(row.payload)) if row else Policy())
    return result

def role_access(session, subject, row):
    if row.kind not in KINDS:return (3 if row.owner_id==subject.id else 0, False)
    level=0; execute=False
    for policy in policies(session,subject):
        level=max(level,LEVELS[policy.workspace_access])
        execute |= policy.workspace_execute
        if row.owner_id==subject.id:
            level=max(level,LEVELS[policy.owned_access]); execute |= policy.owned_execute
        for scope in policy.resources:
            if (scope.kind,scope.resource_id)==(row.kind,row.resource_id):
                level=max(level,LEVELS[scope.access]);execute |= scope.execute
    return level,execute

def access_level(session, subject, row):
    if (row.tenant_id,row.workspace_id)!=(subject.tenant_id,subject.workspace_id):return 0
    level,_=role_access(session,subject,row)
    grant=session.scalar(select(ResourceGrant).where(ResourceGrant.resource_pk==row.id,ResourceGrant.user_id==subject.id))
    return max(level,LEVELS.get(grant.permission,0) if grant else 0)

def expression(session, subject, kind, access='read'):
    needed=LEVELS[access]; conditions=[]
    for policy in policies(session,subject):
        if kind not in KINDS or LEVELS[policy.owned_access]>=needed:conditions.append(Resource.owner_id==subject.id)
        if kind in KINDS:
            if LEVELS[policy.workspace_access]>=needed:return Resource.id!=None
            ids=[s.resource_id for s in policy.resources if s.kind==kind and LEVELS[s.access]>=needed]
            if ids:conditions.append(Resource.resource_id.in_(ids))
    levels=[level for level,value in LEVELS.items() if value>=needed]
    conditions.append(exists().where(ResourceGrant.resource_pk==Resource.id,ResourceGrant.user_id==subject.id,ResourceGrant.permission.in_(levels)))
    return or_(*conditions) if conditions else false()

def require_execute(session, context, kind=None, resource_id=None):
    from app.auth import validate_context
    subject=validate_context(context,session)
    if context.principal_type=='service':return  # Legacy application authority has independent invoke scopes.
    if 'resources.execute' not in context.permissions:raise AppError('EXECUTE_DENIED','没有执行授权',403)
    if kind is None:return
    from app.asset_archive import live_asset
    row=session.scalar(select(Resource).where(Resource.kind==kind,Resource.resource_id==resource_id,
        Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id,live_asset(kind)))
    if row is None:raise AppError('NOT_FOUND','资源不存在或无权访问',404)
    if context.principal_type=='platform':
        from app.platform_api import require_token_resource
        require_token_resource(session,context,row,'execute')
        return
    if not role_access(session,subject,row)[1]:raise AppError('EXECUTE_DENIED','此资源未授予执行权限',403)

def directory(session, context, kind):
    from app.auth import validate_context
    from app.database.lifecycle import directory_live
    from app.platform_api import metadata
    from app.asset_archive import live_asset
    subject=validate_context(context,session);result=[]
    for row in session.scalars(select(Resource).where(Resource.kind==kind,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id,live_asset(kind))):
        level=access_level(session,subject,row)
        if level<1 or not directory_live(session,row):continue
        result.append({**metadata(row),'metadata_only':True,'access':{'view':True,'read':level>=2,'edit':level>=3,
            'execute':'resources.execute' in context.permissions and role_access(session,subject,row)[1]}})
    return result

def capabilities(session,context,kind,identifier):
    from app.auth import validate_context
    subject=validate_context(context,session)
    row=session.scalar(select(Resource).where(Resource.kind==kind,Resource.resource_id==identifier,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
    if row is None:return {'view':False,'read':False,'edit':False,'execute':False}
    level=access_level(session,subject,row)
    return {'view':level>=1,'read':level>=2,'edit':level>=3,'execute':'resources.execute' in context.permissions and role_access(session,subject,row)[1]}

def merge_directory(session,context,kind,items,parent_field=None,parent_id=None):
    """Content DTOs keep their existing domain filtering; view DTOs add no body."""
    safe=directory(session,context,kind);ids={item['id'] for item in items}
    by_id={item['id']:item for item in safe}
    result=[{**item,'access':by_id.get(item['id'],{}).get('access',{'view':True,'read':True,'edit':False,'execute':False})} for item in items]
    for item in safe:
        if item['id'] in ids:continue
        if parent_field:
            row=session.scalar(select(Resource).where(Resource.kind==kind,Resource.resource_id==item['id'],Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
            if not row or json.loads(row.payload).get(parent_field)!=parent_id:continue
        result.append(item)
    return result

def _manager(request,session,role_id):
    from app.rbac import super_context,role_for
    if request.method in {'POST','PUT','PATCH','DELETE'}:
        from app.platform_api import write_lock
        write_lock(session)
    context=super_context(request,session);return context,role_for(session,context.tenant_id,context.workspace_id,role_id)

@router.get('/roles/{role_id}/resource-policy')
def get_policy(role_id:str, request:Request, session=Depends(get_session)):
    context,role=_manager(request,session,role_id)
    return policy_view(session,role)

def policy_view(session,role):
    row=session.scalar(select(Resource).where(Resource.kind=='role_resource_policy',Resource.resource_id==role.id,
        Resource.tenant_id==role.tenant_id,Resource.workspace_id==role.workspace_id))
    value=json.loads(row.payload) if row else Policy().model_dump()
    return {**value,'revision':value.get('expected_revision',0),'role_id':role.id}

@router.put('/roles/{role_id}/resource-policy')
def put_policy(role_id:str, body:Policy, request:Request, session=Depends(get_session)):
    context,role=_manager(request,session,role_id)
    return save_policy(session,role,body,context.operator_id)

def save_policy(session,role,body,actor_id):
    from app.platform_api import _put
    from app.data_access import bump_policy_version
    if any(scope.kind not in KINDS for scope in body.resources):raise AppError('RESOURCE_KIND_DENIED','此资源类型不支持角色范围授权',422)
    if len({(s.kind,s.resource_id) for s in body.resources})!=len(body.resources):raise AppError('VALIDATION_ERROR','资源范围重复',422)
    for scope in body.resources:
        if session.scalar(select(Resource.id).where(Resource.kind==scope.kind,Resource.resource_id==scope.resource_id,Resource.tenant_id==role.tenant_id,Resource.workspace_id==role.workspace_id)) is None:raise AppError('NOT_FOUND','指定资源不在当前空间',404)
    current=policy_view(session,role)
    if current['revision']!=body.expected_revision:raise AppError('RESOURCE_POLICY_CONFLICT','权限范围已变化，请刷新',409)
    value=body.model_dump();value['expected_revision']+=1
    _put(session,'role_resource_policy',role.id,value,role.tenant_id,role.workspace_id,actor_id)
    bump_policy_version(session,SimpleNamespace(tenant_id=role.tenant_id,workspace_id=role.workspace_id))
    return {**value,'revision':value['expected_revision'],'role_id':role.id}
