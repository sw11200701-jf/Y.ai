import json
from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,delete
from app.db import Resource,OwnershipAudit,get_session,new_id,utc_now,get_resource
from app.auth_models import ResourceGrant,User
from app.auth import identity
from app.errors import AppError

router=APIRouter()
class Grant(BaseModel):
    model_config=ConfigDict(extra="forbid")
    user_id:str
    permission:str=Field(pattern="^(view|read|write)$")
class AccessWrite(BaseModel):
    model_config=ConfigDict(extra="forbid")
    grants:list[Grant]=Field(max_length=50)

def owned(session,kind,resource_id,context):
    if kind=='skill_version':raise AppError('SKILL_VERSION_IMMUTABLE','技能版本由父技能当前权限控制，不能直接修改或共享子版本',409)
    from app.auth import validate_context
    user=validate_context(context,session)
    if user.must_change_password:raise AppError('PASSWORD_CHANGE_REQUIRED','Password change required',403)
    row=session.scalar(select(Resource).where(Resource.kind==kind,Resource.resource_id==resource_id,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id,Resource.owner_id==context.operator_id))
    if row is None:raise AppError("NOT_FOUND","Owned resource not found",404)
    # Grant metadata is safe for the exact owner even if completed content is stale.
    if not (kind == 'run' and getattr(context,'principal_type','human') == 'human'
            and json.loads(row.payload).get('status') == 'succeeded'):
        get_resource(session,kind,resource_id,context)
    return row

def metadata(session,row):
    grants=[{"user_id":g.user_id,"permission":g.permission} for g in session.scalars(select(ResourceGrant).where(ResourceGrant.resource_pk==row.id))]
    return {"owner_id":row.owner_id,"scope":"shared" if grants else "private","grants":grants}

@router.get("/resources/{kind}/{resource_id}/access")
def get_access(kind:str,resource_id:str,request:Request,session=Depends(get_session)):
    return metadata(session,owned(session,kind,resource_id,identity(request)))

@router.put("/resources/{kind}/{resource_id}/access")
def put_access(kind:str,resource_id:str,body:AccessWrite,request:Request,session=Depends(get_session)):
    return replace_grants(session=session,kind=kind,resource_id=resource_id,context=identity(request),grants=[g.model_dump() for g in body.grants])

def replace_grants(*,session,kind,resource_id,context,grants):
    body=AccessWrite.model_validate({"grants":grants})
    row=owned(session,kind,resource_id,context)
    from app.data_access import DATA_KINDS,authorize_data_resource,_unrestricted
    if kind in DATA_KINDS:
        authorize_data_resource(session=session,context=context,kind=kind,resource_id=resource_id,action="manage")
        for action in ("use","maintain") if any(grant.permission=="write" for grant in body.grants) else ("use",):
            if not _unrestricted(authorize_data_resource(session=session,context=context,kind=kind,resource_id=resource_id,action=action)):
                raise AppError("DATA_DELEGATION_DENIED","旧版授权不能表达受限行列范围，请使用数据授权面板",403)
    if len({g.user_id for g in body.grants})!=len(body.grants):raise AppError("VALIDATION_ERROR","Duplicate grant",422)
    for grant in body.grants:
        user=session.get(User,grant.user_id)
        if user is None or not user.active or (user.tenant_id,user.workspace_id)!=(context.tenant_id,context.workspace_id) or user.id==context.operator_id:
            raise AppError("VALIDATION_ERROR","Grant target must be an active different user in this workspace",422)
    before=metadata(session,row)
    if kind == 'run' and json.loads(row.payload).get('status') == 'succeeded':
        existing={(g['user_id'],g['permission']) for g in before['grants']}
        requested={(g.user_id,g.permission) for g in body.grants}
        # Revocations need ownership, additions also need current content access.
        if not requested <= existing:get_resource(session,kind,resource_id,context)
    session.execute(delete(ResourceGrant).where(ResourceGrant.resource_pk==row.id))
    for grant in body.grants:session.add(ResourceGrant(resource_pk=row.id,user_id=grant.user_id,permission=grant.permission))
    from app.data_access import bump_policy_version
    bump_policy_version(session,context)
    session.add(OwnershipAudit(id=new_id("audit"),actor_id=context.operator_id,action="replace_explicit_grants",resource_kind=kind,resource_id=resource_id,details=json.dumps({"before":before["grants"],"after":[g.model_dump() for g in body.grants]}),created_at=utc_now()))
    session.flush()
    return metadata(session,row)
