"""Central deny-first data authorization and a bounded, non-SQL query adapter."""
import json
import math
from collections import Counter
from dataclasses import dataclass
from contextvars import ContextVar
from typing import Any,Literal
from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict,Field,field_validator,ValidationError
from sqlalchemy import select,delete,update,or_
from app.errors import AppError
from app.db import Resource,get_session,new_id
from app.auth_models import User,ResourceGrant
from app.data_models import DataGrant,DataResourceLink,DataPolicyState,DataDerivation,UserRoleMembership,DataResourceAlias,DataTableBinding

ACTIONS={"use","maintain","manage","download_original","export_detail"}
PRESETS={"use":["use"],"maintain":["use","maintain"],"manage":["use","maintain","manage"]}
DATA_KINDS={"knowledge_base","knowledge_document","knowledge_dataset","knowledge_file","knowledge_batch","knowledge_version","knowledge_chunk","document","database","data_table","data_table_version","data_batch","data_file","database_table","database_version"}
router=APIRouter(tags=["data-access"])
_derivation_guard=ContextVar("data_derivation_guard",default=frozenset())

class Strict(BaseModel):model_config=ConfigDict(extra="forbid")
class RowScope(Strict):
    template:Literal["all","self","department","companies","departments"]="all"
    field:str|None=Field(default=None,max_length=100,pattern=r"^[A-Za-z_\u3400-\u9fff][A-Za-z_0-9\u3400-\u9fff]*$")
    ids:list[str]=Field(default_factory=list,max_length=100)
class Columns(Strict):
    allow:list[str]|None=Field(default=None,max_length=256)
    hide:list[str]=Field(default_factory=list,max_length=256)
    mask:dict[str,Literal["redact","last4"]]=Field(default_factory=dict,max_length=256)
class GrantWrite(Strict):
    subject_type:Literal["user","role","service"]
    subject_id:str=Field(min_length=1,max_length=100)
    effect:Literal["allow","deny","restrict"]="allow"
    actions:list[str]=Field(default_factory=list,max_length=5)
    preset:Literal["use","maintain","manage"]|None=None
    row_scope:RowScope=Field(default_factory=RowScope)
    columns:Columns=Field(default_factory=Columns)
    delegable_actions:list[str]=Field(default_factory=list,max_length=5)
    @field_validator("actions","delegable_actions")
    @classmethod
    def valid_actions(cls,value):
        if len(value)!=len(set(value)) or not set(value)<=ACTIONS:raise ValueError("数据动作无效或重复")
        return value
class AccessWrite(Strict):
    grants:list[GrantWrite]=Field(max_length=100)
    remove_legacy_user_ids:list[str]=Field(default_factory=list,max_length=100)

def not_found():raise AppError("NOT_FOUND","资源不存在或无权访问",404)
def policy_version(session,context):
    row=session.get(DataPolicyState,(context.tenant_id,context.workspace_id),populate_existing=True)
    return row.revision if row else 0
def bump_policy_version(session,context):
    row=session.get(DataPolicyState,(context.tenant_id,context.workspace_id))
    if row is None:row=DataPolicyState(tenant_id=context.tenant_id,workspace_id=context.workspace_id,revision=1);session.add(row);session.flush()
    else:session.execute(update(DataPolicyState).where(DataPolicyState.tenant_id==context.tenant_id,DataPolicyState.workspace_id==context.workspace_id).values(revision=DataPolicyState.revision+1));session.flush()
    return policy_version(session,context)

def roles_for_user(session,user):
    if getattr(user,"principal_type","human")=="service":return set()
    from app.rbac import role_for
    primary=role_for(session,user.tenant_id,user.workspace_id,user.role_id or user.role)
    result={primary.id}
    from app.auth_models import AccessRole
    result.update(session.scalars(select(UserRoleMembership.role_id).join(AccessRole,AccessRole.id==UserRoleMembership.role_id).where(UserRoleMembership.user_id==user.id,AccessRole.tenant_id==user.tenant_id,AccessRole.workspace_id==user.workspace_id)))
    return result

def _row(session,context,kind,resource_id):
    row=session.scalar(select(Resource).where(Resource.kind==kind,Resource.resource_id==resource_id,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
    if row is None:
        alias=session.get(DataResourceAlias,(context.tenant_id,context.workspace_id,kind,resource_id))
        row=session.get(Resource,alias.resource_pk) if alias else None
    if row is None or (row.tenant_id,row.workspace_id)!=(context.tenant_id,context.workspace_id):not_found()
    return row
def _rule(actions,row_scope=None,columns=None,delegable=()):
    return {"actions":sorted(actions),"row_scope":row_scope or RowScope().model_dump(),"columns":columns or Columns().model_dump(),"delegable_actions":sorted(delegable)}
def _load_rule(grant):
    return _rule(json.loads(grant.actions),json.loads(grant.row_scope),json.loads(grant.columns),json.loads(grant.delegable_actions))

@dataclass
class DataDecision:
    session:Any
    context:Any
    kind:str
    resource_id:str
    action:str
    resource:Any
    layers:list
    identity:dict
    policy_revision:int
    agent_scope:Any=None

def authorize_data_resource(*,session,context,kind,resource_id,action="use",agent_scope=None):
    from app.auth import validate_context
    user=validate_context(context,session)
    if user.must_change_password:raise AppError("PASSWORD_CHANGE_REQUIRED","请先修改初始密码",403)
    decision=_authorize_subject_resource(session=session,context=context,user=user,kind=kind,resource_id=resource_id,action=action,agent_scope=agent_scope)
    return intersect_platform_grantor(decision,session,context,user)

def intersect_platform_grantor(decision,session,context,user):
    if getattr(context,'principal_type','human')=='platform':
        from copy import deepcopy
        from types import SimpleNamespace
        from app.platform_api import current_scope,space_subject
        _,key,_,_,_,_=current_scope(session,context.auth_key_id,context.workspace_id)
        grantor=session.get(User,key['created_by'],populate_existing=True)
        if grantor.id!=user.id:
            subject=space_subject(session,grantor,context.workspace_id)
            scope=SimpleNamespace(tenant_id=context.tenant_id,workspace_id=context.workspace_id,principal_type='delegation')
            ceiling=_authorize_subject_resource(session=session,context=scope,user=subject,kind=decision.kind,resource_id=decision.resource_id,action=decision.action,agent_scope=decision.agent_scope,check_derivation=False)
            layers=deepcopy(ceiling.layers)
            for layer in layers:
                for group in ('allows','denies','restricts'):
                    for rule in layer[group]:
                        row_scope=rule['row_scope'];template=row_scope['template']
                        if template in {'self','department'}:
                            value=subject.id if template=='self' else subject.department_id
                            row_scope.update(template='companies',field=row_scope.get('field') or ('owner_id' if template=='self' else 'department_id'),ids=[] if value is None else [value])
            decision.layers.extend(layers)
    return decision

def _authorize_subject_resource(*,session,context,user,kind,resource_id,action="use",agent_scope=None,check_derivation=True):
    if action not in ACTIONS:raise AppError("INVALID_DATA_ACTION","数据授权动作无效",422)
    row=_row(session,context,kind,resource_id)
    platform=getattr(context,"principal_type","human")=="platform"
    if platform:
        from app.platform_api import require_token_resource
        require_token_resource(session,context,row,'write' if action in {'maintain','manage'} else 'read')
    machine=getattr(user,"principal_type","human")=="service"
    if machine:
        from app.machine_auth import resource_granted,authority_resource_rule
        if not resource_granted(session,row,context,action):not_found()
    role_ids=roles_for_user(session,user);seen=set();layers=[];chain=[]
    def visit(current):
        if current.id in seen or len(seen)>=16:raise AppError("DATA_POLICY_INVALID","资源授权父链无效",403)
        seen.add(current.id);chain.append((current.kind,current.resource_id))
        if check_derivation and session.scalar(select(DataDerivation.source_pk).where(DataDerivation.resource_pk==current.id).limit(1)) is not None:
            require_derivation_access(session=session,context=context,kind=current.kind,resource_id=current.resource_id,completed_read=(action in {"use","download_original","export_detail"}))
        if action in ("use","download_original","export_detail") and json.loads(current.payload).get("status") in ("disabled","deleted","archived"):not_found()
        link=session.get(DataResourceLink,current.id,populate_existing=True)
        if link is None and current.kind.startswith("knowledge_") and current.kind!="knowledge_base":
            payload=json.loads(current.payload);parent_kind=None;parent_id=None
            if current.kind=="knowledge_version" and payload.get("resource_kind") in DATA_KINDS and payload.get("resource_id")!=current.resource_id:
                parent_kind=payload["resource_kind"];parent_id=payload.get("resource_id")
            elif payload.get("knowledge_base_id"):
                parent_kind="knowledge_base";parent_id=payload["knowledge_base_id"]
            if parent_id:
                from types import SimpleNamespace
                parent=_row(session,context,parent_kind,parent_id)
                link=SimpleNamespace(parent_pk=parent.id,inherit_access=False)
        parent_layer=None
        if link:
            parent=session.get(Resource,link.parent_pk,populate_existing=True)
            if parent is None or (parent.tenant_id,parent.workspace_id)!=(context.tenant_id,context.workspace_id):not_found()
            parent_layer=visit(parent)
        layer={"allows":[],"denies":[],"restricts":[]}
        if machine:
            from app.machine_auth import resource_granted,authority_resource_rule
            if not resource_granted(session,current,context,action):not_found()
        from app.resource_policy import role_access
        resource_level=role_access(session,user,current)[0] if not machine else 3
        if current.owner_id==user.id:
            owner_actions=ACTIONS if resource_level>=3 else {'use','download_original','export_detail'} if resource_level>=2 else set()
            layer["allows"].append(_rule(owner_actions,delegable=() if machine else owner_actions))
        if platform:require_token_resource(session,context,current,'write' if action in {'maintain','manage'} else 'read')
        for grant in ([] if machine else session.scalars(select(ResourceGrant).where(ResourceGrant.resource_pk==current.id,ResourceGrant.user_id==user.id))):
            layer["allows"].append(_rule(["use","maintain"] if grant.permission=="write" else ["use"] if grant.permission=="read" else []))
        subjects=((DataGrant.subject_type=="service") & (DataGrant.subject_id==user.id)) if machine else or_((DataGrant.subject_type=="user") & (DataGrant.subject_id==user.id),(DataGrant.subject_type=="role") & DataGrant.subject_id.in_(role_ids))
        matching=select(DataGrant).where(DataGrant.resource_pk==current.id,subjects)
        matching_grants=list(session.scalars(matching.execution_options(populate_existing=True)))
        for grant in matching_grants:
            rule=_load_rule(grant)
            if machine and grant.effect=="allow" and not current_service_delegation(session,context,current,grant,rule):continue
            if action in rule["actions"]:layer[{"allow":"allows","deny":"denies","restrict":"restricts"}[grant.effect]].append(rule)
        layer["allows"]=[rule for rule in layer["allows"] if action in rule["actions"]]
        if not layer["allows"] and parent_layer and link.inherit_access:layer["allows"]=parent_layer["allows"][:]
        # A generic role resource level never unions unrestricted rows into an
        # existing bounded domain allow. Explicit domain actions/rows/columns,
        # including inherited ones, continue to define the permitted content.
        if not machine and current.owner_id!=user.id and not layer['allows'] and not any(grant.effect=='allow' for grant in matching_grants):
            generic_actions=['use','maintain'] if resource_level>=3 else ['use'] if resource_level>=2 else []
            if action in generic_actions:layer['allows'].append(_rule(generic_actions))
        if not layer["allows"] or any(rule["row_scope"]["template"]=="all" or rule["row_scope"]["template"]=="department" and user.department_id is None for rule in layer["denies"]):not_found()
        if machine:
            restriction=authority_resource_rule(session,context,current,action)
            if restriction is not None:layer["restricts"].append(restriction)
        layers.append(layer);return layer
    visit(row)
    if (kind,resource_id) not in chain:chain.append((kind,resource_id))
    binding=session.get(DataTableBinding,row.id)
    if binding:
        catalog=session.get(Resource,binding.database_pk)
        if catalog and (catalog.tenant_id,catalog.workspace_id)==(context.tenant_id,context.workspace_id):chain.append(("database",catalog.resource_id))
    if agent_scope is not None:
        if not isinstance(agent_scope,list) or not any(isinstance(item,dict) and (item.get("kind"),item.get("resource_id")) in chain for item in agent_scope):not_found()
    identity={"user_id":user.id,"department_id":user.department_id,"company_id":user.company_id}
    return DataDecision(session,context,kind,resource_id,action,row,layers,identity,policy_version(session,context),agent_scope)

def current_service_delegation(session,context,resource,grant,rule):
    """Check a real grantor's current policy, never run as them or create a session.

    Deny/restrict rules are never dropped when their creator loses authority.
    This helper is only the current ceiling of an allow; actual service access
    separately traverses its own source/derivation rules.
    """
    from app.rbac import user_policy
    subject=session.get(User,grant.created_by,populate_existing=True)
    if subject is None or not subject.active or (subject.tenant_id,subject.workspace_id)!=(context.tenant_id,context.workspace_id):return False
    if "applications.keys.manage" not in user_policy(subject,session)[1]:return False
    try:
        current=_authorize_subject_resource(session=session,context=context,user=subject,kind=resource.kind,resource_id=resource.resource_id,
            action="manage",check_derivation=False)
        if not set(rule["actions"])<=_delegable(current):return False
        for action in rule["actions"]:
            own=_authorize_subject_resource(session=session,context=context,user=subject,kind=resource.kind,resource_id=resource.resource_id,
                action=action,check_derivation=False)
            if not _can_delegate(own,rule["row_scope"],rule["columns"]):return False
        return True
    except AppError:return False

def _scope(scope,row,identity):
    template=scope["template"]
    if template=="all":return True
    field=scope.get("field") or {"self":"owner_id","department":"department_id","companies":"company_id","departments":"department_id"}[template]
    if template=="self":return row.get(field)==identity["user_id"]
    if template=="department":return identity["department_id"] is not None and row.get(field)==identity["department_id"]
    return row.get(field) in scope.get("ids",[])
def _permitted_row(decision,row):
    for layer in decision.layers:
        if any(_scope(rule["row_scope"],row,decision.identity) for rule in layer["denies"]):return False
        if not any(_scope(rule["row_scope"],row,decision.identity) for rule in layer["allows"]):return False
        if not all(_scope(rule["row_scope"],row,decision.identity) for rule in layer["restricts"]):return False
    return True
def _column_policy(decision,fields):
    allowed=set(fields);hidden=set();masks={}
    for layer in decision.layers:
        for rule in layer["allows"]+layer["restricts"]:
            policy=rule["columns"]
            if policy["allow"] is not None:allowed.intersection_update(policy["allow"])
            hidden.update(policy["hide"])
            for field,mode in policy["mask"].items():
                masks[field]="redact" if mode=="redact" or masks.get(field)=="redact" else "last4"
    return allowed-hidden,{key:value for key,value in masks.items() if key in allowed-hidden}

class Condition(Strict):
    field:str
    op:Literal["eq","ne","in","gt","gte","lt","lte"]="eq"
    value:Any=None
class Aggregate(Strict):
    op:Literal["count","sum","min","max","avg"]
    field:str|None=None
    name:str=Field(min_length=1,max_length=100,pattern=r"^[A-Za-z_\u3400-\u9fff][A-Za-z_0-9\u3400-\u9fff]*$")
class Sort(Strict):field:str;descending:bool=False
class ControlledQuery(Strict):
    select:list[str]|None=Field(default=None,max_length=256)
    filters:list[Condition]=Field(default_factory=list,max_length=20)
    group_by:list[str]=Field(default_factory=list,max_length=8)
    aggregates:list[Aggregate]=Field(default_factory=list,max_length=16)
    order_by:list[Sort]=Field(default_factory=list,max_length=8)
    offset:int=Field(default=0,ge=0,le=100000)
    limit:int=Field(default=100,ge=1,le=1000)

def filter_rows(*,decision,rows,fields,query=None):
    from decimal import localcontext
    with localcontext() as arithmetic:
        arithmetic.prec=80
        return _filter_rows(decision=decision,rows=rows,fields=fields,query=query)

def _filter_rows(*,decision,rows,fields,query=None):
    from app.auth import validate_context
    validate_context(decision.context,decision.session)
    if policy_version(decision.session,decision.context)!=decision.policy_revision:not_found()
    allowed,masks=_column_policy(decision,fields);plain=allowed-set(masks)
    try:request=ControlledQuery.model_validate(query or {})
    except ValidationError as exc:
        from app.errors import validation_detail
        raise AppError("VALIDATION_ERROR","查询条件不符合要求",422,[validation_detail(error) for error in exc.errors()]) from None
    aggregate_names={aggregate.name for aggregate in request.aggregates}
    referenced={condition.field for condition in request.filters}|set(request.group_by)|{agg.field for agg in request.aggregates if agg.field}|{sort.field for sort in request.order_by if sort.field not in aggregate_names}
    if not referenced<=plain:raise AppError("QUERY_FIELD_DENIED","不能对隐藏、脱敏或未授权字段执行筛选、排序或统计",422)
    if request.select is not None and not set(request.select)<=allowed:raise AppError("QUERY_FIELD_DENIED","请求字段不存在或未授权",422)
    if len(rows)>100000:raise AppError("QUERY_LIMIT","当前受控查询超过行数上限",422)
    selected=[row for row in rows if _permitted_row(decision,row)]
    def matches(row,condition):
        value=condition.value;actual=row.get(condition.field)
        values=value if isinstance(value,list) else [value]
        if len(values)>100 or any(isinstance(item,(dict,list)) or isinstance(item,float) and not math.isfinite(item) for item in values):raise AppError("QUERY_INVALID","筛选值无效或超过上限",422)
        try:
            if condition.op=="eq":return actual==value
            if condition.op=="ne":return actual!=value
            if condition.op=="in":
                if not isinstance(value,list):raise AppError("QUERY_INVALID","包含筛选需要值列表",422)
                return actual in value
            if actual is None or value is None:return False
            return {"gt":lambda:actual>value,"gte":lambda:actual>=value,"lt":lambda:actual<value,"lte":lambda:actual<=value}[condition.op]()
        except TypeError:raise AppError("QUERY_INVALID","筛选字段和值的类型不一致",422) from None
    for condition in request.filters:selected=[row for row in selected if matches(row,condition)]
    if request.aggregates:
        from decimal import Decimal,InvalidOperation
        if len({agg.name for agg in request.aggregates})!=len(request.aggregates) or set(request.group_by)&{agg.name for agg in request.aggregates}:raise AppError("QUERY_INVALID","统计输出名称重复",422)
        groups={}
        for row in selected:
            key=tuple(row.get(field) for field in request.group_by);groups.setdefault(key,[]).append(row)
        if not request.group_by and not groups:groups[()]=[]
        result=[]
        for key,group in groups.items():
            output=dict(zip(request.group_by,key))
            for aggregate in request.aggregates:
                if aggregate.op=="count":output[aggregate.name]=sum(1 for row in group if aggregate.field is None or row.get(aggregate.field) is not None);continue
                if aggregate.field is None:raise AppError("QUERY_INVALID","此统计需要指定字段",422)
                try:
                    numbers=[Decimal(str(row[aggregate.field])) for row in group if row.get(aggregate.field) is not None]
                    if any(not number.is_finite() for number in numbers):raise InvalidOperation()
                    value=(sum(numbers) if aggregate.op=="sum" else min(numbers) if aggregate.op=="min" else max(numbers) if aggregate.op=="max" else sum(numbers)/len(numbers)) if numbers else None
                    output[aggregate.name]=str(value) if value is not None else None
                except (InvalidOperation,ValueError,TypeError):raise AppError("QUERY_INVALID","统计字段包含不支持的数值",422) from None
            result.append(output)
        projected=result
    else:
        columns=request.select if request.select is not None else [field for field in fields if field in allowed]
        projected=[]
        for row in selected:
            output={field:row.get(field) for field in columns}
            for field,mode in masks.items():
                if field in output and output[field] is not None:output[field]="***" if mode=="redact" else "***"+str(output[field])[-4:]
            projected.append(output)
    for sort in reversed(request.order_by):
        try:projected.sort(key=lambda row:(row.get(sort.field) is None,row.get(sort.field)),reverse=sort.descending)
        except TypeError:raise AppError("QUERY_INVALID","排序字段类型不一致",422) from None
    validate_context(decision.context,decision.session)
    if policy_version(decision.session,decision.context)!=decision.policy_revision:not_found()
    return {"rows":projected[request.offset:request.offset+request.limit],"total":len(projected),"policy_version":decision.policy_revision}

def _unrestricted(decision):
    empty=Columns().model_dump()
    return all(not layer["denies"] and not layer["restricts"] and all(rule["row_scope"]["template"]=="all" and rule["columns"]==empty for rule in layer["allows"]) for layer in decision.layers)
def _can_delegate(decision,scope,columns):
    if _unrestricted(decision):return True
    if len(decision.layers)!=1:return False
    layer=decision.layers[0]
    return not layer["denies"] and not layer["restricts"] and len(layer["allows"])==1 and scope["template"] in ("companies","departments") and layer["allows"][0]["row_scope"]==scope and layer["allows"][0]["columns"]==columns
def _delegable(decision):
    return set.intersection(*(set(action for rule in layer["allows"] for action in rule["delegable_actions"]) for layer in decision.layers))

def data_metadata(session,row,context):
    grants=[]
    for grant in session.scalars(select(DataGrant).where(DataGrant.resource_pk==row.id)):
        grants.append({"id":grant.id,"subject_type":grant.subject_type,"subject_id":grant.subject_id,"effect":grant.effect,**_load_rule(grant)})
    legacy=[{"user_id":grant.user_id,"permission":grant.permission,"actions":["use","maintain"] if grant.permission=="write" else ["use"] if grant.permission=="read" else []} for grant in session.scalars(select(ResourceGrant).where(ResourceGrant.resource_pk==row.id))]
    return {"kind":row.kind,"resource_id":row.resource_id,"owner_id":row.owner_id,"grants":grants,"legacy_grants":legacy,"legacy_grants_preserved":True,"policy_version":policy_version(session,context),**data_capabilities(session=session,context=context,kind=row.kind,resource_id=row.resource_id)}

def data_capabilities(*,session,context,kind,resource_id):
    decisions={}
    for action in sorted(ACTIONS):
        try:decision=authorize_data_resource(session=session,context=context,kind=kind,resource_id=resource_id,action=action)
        except AppError as exc:
            if exc.status_code==404:continue
            raise
        decisions[action]=decision
    if not decisions:not_found()
    use=decisions.get("use")
    if "download_original" in decisions and (use is None or not _unrestricted(decisions["download_original"]) or not _unrestricted(use)):
        decisions.pop("download_original")
    anchor=next(iter(decisions.values()))
    delegable=_delegable(decisions["manage"]) if "manage" in decisions else set()
    return {"available_actions":sorted(decisions),"delegable_actions":sorted(delegable),"is_owner":anchor.resource.owner_id==context.operator_id,"policy_version":anchor.policy_revision}

def replace_data_grants(*,session,context,kind,resource_id,grants,remove_legacy_user_ids=None):
    from app.rbac import role_for,audit
    body=AccessWrite.model_validate({"grants":grants,"remove_legacy_user_ids":remove_legacy_user_ids or []})
    if kind not in DATA_KINDS:raise AppError("INVALID_DATA_RESOURCE","此授权入口仅用于知识或结构化数据资源",422)
    decision=authorize_data_resource(session=session,context=context,kind=kind,resource_id=resource_id,action="manage")
    delegable=_delegable(decision);normalized=[]
    def signature(rule):
        return json.dumps({"subject_type":rule.subject_type,"subject_id":rule.subject_id,"effect":rule.effect,**_load_rule(rule)},sort_keys=True,separators=(',',':'))
    existing=list(session.scalars(select(DataGrant).where(DataGrant.resource_pk==decision.resource.id)))
    unchanged={signature(rule):rule for rule in existing}
    for grant in body.grants:
        actions=sorted(set(grant.actions)|set(PRESETS.get(grant.preset,[])))
        subject_id=grant.subject_id if grant.subject_type in {"user","service"} else role_for(session,context.tenant_id,context.workspace_id,grant.subject_id).id
        candidate=DataGrant(id=new_id("data_grant"),resource_pk=decision.resource.id,subject_type=grant.subject_type,subject_id=subject_id,effect=grant.effect,actions=json.dumps(actions),row_scope=grant.row_scope.model_dump_json(),columns=grant.columns.model_dump_json(),delegable_actions=json.dumps(grant.delegable_actions),created_by=context.operator_id)
        old=unchanged.get(signature(candidate))
        if old is not None:
            candidate.created_by=old.created_by;normalized.append(candidate);continue
        if not actions or not (set(actions)|set(grant.delegable_actions))<=delegable:raise AppError("DATA_DELEGATION_DENIED","不能委派超出自身范围的数据动作",403)
        if not set(grant.delegable_actions)<=set(actions):raise AppError("DATA_DELEGATION_DENIED","可委派动作必须在本次授权动作内",403)
        for action in actions:
            own=authorize_data_resource(session=session,context=context,kind=kind,resource_id=resource_id,action=action)
            if not _can_delegate(own,grant.row_scope.model_dump(),grant.columns.model_dump()):raise AppError("DATA_DELEGATION_DENIED","无法证明此行列范围在自身可委派范围内",403)
        if grant.subject_type=="user":
            user=session.get(User,subject_id)
            if user is None or not user.active or (user.tenant_id,user.workspace_id)!=(context.tenant_id,context.workspace_id):not_found()
        if grant.subject_type=="service":
            from app.machine_models import ServicePrincipal
            principal=session.get(ServicePrincipal,subject_id,populate_existing=True)
            if principal is None or not principal.active or (principal.tenant_id,principal.workspace_id)!=(context.tenant_id,context.workspace_id):not_found()
            if grant.delegable_actions:raise AppError("DATA_DELEGATION_DENIED","机器数据主体不能继续转授",403)
        normalized.append(candidate)
    # Removing deny/restrict (or a masking allow) can expand rights just as adding
    # an allow does. Never treat an empty replacement as automatically safe.
    if decision.resource.owner_id!=context.operator_id:
        retained=Counter(signature(rule) for rule in normalized)
        removed=[]
        for old in existing:
            key=signature(old)
            if retained[key]:retained[key]-=1
            else:removed.extend(json.loads(old.actions))
        for legacy in session.scalars(select(ResourceGrant).where(ResourceGrant.resource_pk==decision.resource.id,ResourceGrant.user_id.in_(body.remove_legacy_user_ids))):
            removed.extend(["use","maintain"] if legacy.permission=="write" else ["use"])
        for action in set(removed):
            if action not in delegable or not _unrestricted(authorize_data_resource(session=session,context=context,kind=kind,resource_id=resource_id,action=action)):
                raise AppError("DATA_DELEGATION_DENIED","不能删除或放宽超出自身可委派范围的授权限制",403)
    session.execute(delete(DataGrant).where(DataGrant.resource_pk==decision.resource.id))
    if body.remove_legacy_user_ids:session.execute(delete(ResourceGrant).where(ResourceGrant.resource_pk==decision.resource.id,ResourceGrant.user_id.in_(body.remove_legacy_user_ids)))
    session.add_all(normalized);bump_policy_version(session,context);audit(session,context,"replace_data_grants",kind,resource_id,{"grant_count":len(normalized)})
    session.flush();return data_metadata(session,decision.resource,context)

def link_data_resource(*,session,context,kind,resource_id,parent_kind,parent_id,inherit_access=False):
    child=_row(session,context,kind,resource_id)
    previous=session.get(DataResourceLink,child.id)
    first_creation=previous is None and child.owner_id==context.operator_id
    if not first_creation:
        manager=authorize_data_resource(session=session,context=context,kind=kind,resource_id=resource_id,action="manage")
        if not ACTIONS<=_delegable(manager):raise AppError("DATA_DELEGATION_DENIED","改绑资源父级需要完整可委派权限",403)
        for action in ACTIONS:
            if not _unrestricted(authorize_data_resource(session=session,context=context,kind=kind,resource_id=resource_id,action=action)):
                raise AppError("DATA_DELEGATION_DENIED","受限资源不能通过改绑父级扩大行列权限",403)
    parent=authorize_data_resource(session=session,context=context,kind=parent_kind,resource_id=parent_id,action="maintain" if first_creation else "manage").resource
    seen={child.id};current=parent
    while current:
        if current.id in seen or len(seen)>=16:raise AppError("DATA_POLICY_INVALID","不能创建循环或过深的资源父链",422)
        seen.add(current.id);link=session.get(DataResourceLink,current.id);current=session.get(Resource,link.parent_pk) if link else None
    if previous:previous.parent_pk=parent.id;previous.inherit_access=inherit_access
    else:session.add(DataResourceLink(resource_pk=child.id,parent_pk=parent.id,inherit_access=inherit_access))
    bump_policy_version(session,context);session.flush()
    return {"resource_id":resource_id,"parent_id":parent_id,"inherit_access":inherit_access}

def register_legacy_table_binding(*,session,context,knowledge_dataset_id,database_id,version_ids=None):
    table=authorize_data_resource(session=session,context=context,kind="knowledge_dataset",resource_id=knowledge_dataset_id,action="manage").resource
    database=authorize_data_resource(session=session,context=context,kind="database",resource_id=database_id,action="manage").resource
    payload=json.loads(table.payload);kb=_row(session,context,"knowledge_base",payload.get("knowledge_base_id"))
    if table.owner_id!=database.owner_id or kb.owner_id!=table.owner_id or table.owner_id!=context.operator_id:not_found()
    binding=session.get(DataTableBinding,table.id)
    if binding is not None and binding.database_pk!=database.id:raise AppError("DATA_BINDING_CONFLICT","历史表已归属其他数据库",409)
    targets=[("data_table",table)]
    for version_id in version_ids or []:
        version=_row(session,context,"knowledge_version",version_id);value=json.loads(version.payload)
        if value.get("resource_kind")!="knowledge_dataset" or value.get("resource_id")!=knowledge_dataset_id or version.owner_id!=table.owner_id:raise AppError("DATA_BINDING_CONFLICT","历史版本与数据表来源不一致",409)
        targets.append(("data_table_version",version))
    changed=False
    for alias_kind,physical in targets:
        key=(context.tenant_id,context.workspace_id,alias_kind,physical.resource_id);alias=session.get(DataResourceAlias,key)
        collision=session.scalar(select(Resource).where(Resource.kind==alias_kind,Resource.resource_id==physical.resource_id,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
        if collision is not None and collision.id!=physical.id or alias is not None and alias.resource_pk!=physical.id:raise AppError("DATA_BINDING_CONFLICT","历史资源别名已被占用",409)
        if alias is None:session.add(DataResourceAlias(tenant_id=context.tenant_id,workspace_id=context.workspace_id,kind=alias_kind,resource_id=physical.resource_id,resource_pk=physical.id));changed=True
    if binding is None:session.add(DataTableBinding(resource_pk=table.id,database_pk=database.id));changed=True
    if changed:bump_policy_version(session,context)
    session.flush()
    return {"id":knowledge_dataset_id,"logical_kind":"data_table","legacy_kind":"knowledge_dataset","database_id":database_id,"version_ids":[row.resource_id for _,row in targets[1:]],"policy_version":policy_version(session,context)}

def record_derivation(*,session,context,kind,resource_id,sources):
    target=_row(session,context,kind,resource_id)
    if not sources:not_found()
    if context.principal_type=='platform':
        from app.platform_api import authorize_derivation_target
        authorize_derivation_target(session,context,target)
        if kind=='run' and json.loads(target.payload).get('status') not in {'queued','running'}:raise AppError('DERIVATION_IMMUTABLE','已关闭的平台运行不可增补来源',409)
    elif target.owner_id!=context.operator_id:not_found()
    existing=list(session.scalars(select(DataDerivation).where(DataDerivation.resource_pk==target.id)))
    growing_run=kind=="run" and json.loads(target.payload).get("status") in ("queued","running")
    if existing and not growing_run:raise AppError("DERIVATION_IMMUTABLE","派生来源已固定，不能替换或扩大",409)
    retained={bound.source_pk:bound for bound in existing}
    if existing and any(bound.actor_id!=context.operator_id or bound.policy_revision!=policy_version(session,context) for bound in existing):not_found()
    for source in sources:
        action=source.get("action","use")
        decision=authorize_data_resource(session=session,context=context,kind=source["kind"],resource_id=source["resource_id"],action=action)
        stack=[decision.resource.id];seen=set()
        while stack:
            current=stack.pop()
            if current==target.id:raise AppError("DERIVATION_CYCLE","派生来源不能形成循环",422)
            if current in seen:continue
            seen.add(current)
            if len(seen)>128:raise AppError("RESOURCE_LIMIT","派生来源链超过允许范围",422)
            stack.extend(session.scalars(select(DataDerivation.source_pk).where(DataDerivation.resource_pk==current)))
            parent=session.get(DataResourceLink,current)
            if parent:stack.append(parent.parent_pk)
        if decision.resource.id in retained:
            if retained[decision.resource.id].required_action!=action:raise AppError("DERIVATION_IMMUTABLE","派生来源动作不能被替换",409)
            continue
        bound=DataDerivation(resource_pk=target.id,source_pk=decision.resource.id,actor_id=context.operator_id,policy_revision=decision.policy_revision,required_action=action)
        session.add(bound);retained[decision.resource.id]=bound
    session.flush()

def require_derivation_access(*,session,context,kind,resource_id,completed_read=False):
    from app.auth import validate_context
    validate_context(context,session)
    target=_row(session,context,kind,resource_id)
    seen=_derivation_guard.get()
    if target.id in seen or len(seen)>=32:not_found()
    token=_derivation_guard.set(seen|{target.id})
    try:
        bindings=list(session.scalars(select(DataDerivation).where(DataDerivation.resource_pk==target.id)))
        changed=any(bound.policy_revision!=policy_version(session,context) for bound in bindings)
        verdict=None
        if completed_read and (changed or not bindings):
            from app.completed_finance_read import validate_completed_finance_target
            verdict=validate_completed_finance_target(session,context,target)
            if verdict is None:
                from app.reporting.completed_documents import validate_completed_document_target
                verdict=validate_completed_document_target(session,context,target)
            if verdict is None:
                from app.knowledge.slicing_finalizer import validate_completed_csv_target
                verdict=validate_completed_csv_target(session,context,target)
            if verdict is False:not_found()
        if changed and verdict is not True:not_found()
        for bound in bindings:
            if bound.actor_id!=context.operator_id:not_found()
            source=session.get(Resource,bound.source_pk)
            if source is None:not_found()
            for action in {"use",bound.required_action}:
                decision=authorize_data_resource(session=session,context=context,kind=source.kind,resource_id=source.resource_id,action=action)
                if changed and not _unrestricted(decision):not_found()
    finally:_derivation_guard.reset(token)

def agent_sources(config):
    sources=[{"kind":item["kind"],"resource_id":item["resource_id"],"action":"use"} for item in config.get("data_resources",[]) if item.get("enabled",True)]
    sources.extend({"kind":"knowledge_base" if item["resource_id"].startswith("knowledge_base_") else "document","resource_id":item["resource_id"],"action":"use"} for item in config.get("knowledge_resources",[]) if item.get("enabled",True))
    return sources

def validate_role_delegation(session,context,role):
    for grant in session.scalars(select(DataGrant).where(DataGrant.subject_type=="role",DataGrant.subject_id==role.id,DataGrant.effect=="allow")):
        row=session.get(Resource,grant.resource_pk)
        if row is None:not_found()
        manager=authorize_data_resource(session=session,context=context,kind=row.kind,resource_id=row.resource_id,action="manage")
        for action in json.loads(grant.actions):
            if action not in _delegable(manager):raise AppError("DATA_DELEGATION_DENIED","该角色包含不可由当前操作者委派的数据授权",403)
            own=authorize_data_resource(session=session,context=context,kind=row.kind,resource_id=row.resource_id,action=action)
            if not _unrestricted(own):raise AppError("DATA_DELEGATION_DENIED","无法安全委派此角色包含的行列范围",403)

@router.get("/data-access/{kind}/{resource_id}")
def get_access(kind:str,resource_id:str,request:Request,session=Depends(get_session)):
    from app.auth import identity
    context=identity(request);decision=authorize_data_resource(session=session,context=context,kind=kind,resource_id=resource_id,action="manage")
    return data_metadata(session,decision.resource,context)
@router.put("/data-access/{kind}/{resource_id}")
def put_access(kind:str,resource_id:str,body:AccessWrite,request:Request,session=Depends(get_session)):
    from app.auth import identity
    return replace_data_grants(session=session,context=identity(request),kind=kind,resource_id=resource_id,grants=[grant.model_dump() for grant in body.grants],remove_legacy_user_ids=body.remove_legacy_user_ids)

@router.get("/data-access/{kind}/{resource_id}/capabilities")
def capabilities(kind:str,resource_id:str,request:Request,session=Depends(get_session)):
    from app.auth import identity
    return data_capabilities(session=session,context=identity(request),kind=kind,resource_id=resource_id)

@router.get("/data-access/{kind}/{resource_id}/subjects")
def subjects(kind:str,resource_id:str,request:Request,session=Depends(get_session)):
    from app.auth import identity
    from app.auth_models import AccessRole
    context=identity(request);authorize_data_resource(session=session,context=context,kind=kind,resource_id=resource_id,action="manage")
    users=list(session.scalars(select(User).where(User.tenant_id==context.tenant_id,User.workspace_id==context.workspace_id,User.active==True)))
    roles=list(session.scalars(select(AccessRole).where(AccessRole.tenant_id==context.tenant_id,AccessRole.workspace_id==context.workspace_id)))
    return {"users":[{"id":user.id,"display_name":user.display_name or user.username} for user in users],"roles":[{"id":role.id,"key":role.key,"name":role.name,"base_role":role.base_role} for role in roles],"policy_version":policy_version(session,context)}
@router.get("/users/{user_id}/data-access")
def account_access(user_id:str,request:Request,session=Depends(get_session)):
    from app.auth import require_admin,target_user
    context=require_admin(request);user=target_user(session,user_id,context);role_ids=roles_for_user(session,user)
    pks=set(session.scalars(select(DataGrant.resource_pk).where(or_((DataGrant.subject_type=="user") & (DataGrant.subject_id==user_id),(DataGrant.subject_type=="role") & DataGrant.subject_id.in_(role_ids)))))
    pks.update(session.scalars(select(ResourceGrant.resource_pk).where(ResourceGrant.user_id==user_id)))
    pks.update(session.scalars(select(Resource.id).where(Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id,Resource.owner_id==user_id,Resource.kind.in_(DATA_KINDS))))
    result=[]
    for pk in pks:
        row=session.get(Resource,pk)
        if row is None or row.kind not in DATA_KINDS:continue
        try:authorize_data_resource(session=session,context=context,kind=row.kind,resource_id=row.resource_id,action="manage")
        except AppError as exc:
            if exc.status_code==404:continue
            raise
        result.append(data_metadata(session,row,context))
    return {"items":result,"policy_version":policy_version(session,context)}
