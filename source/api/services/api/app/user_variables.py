"""Private persistent user-defined business variables, separate from trusted identity."""
import json,re
from copy import deepcopy
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import select
from fastapi import APIRouter,Depends,Query
from app.db import Resource,get_session,new_id,utc_now
from app.context import get_context
from app.errors import AppError
from app.runner.variables import check_schema,check_size,references,NAME
KIND='user_variable';router=APIRouter()
class VariableWrite(BaseModel):
 model_config=ConfigDict(extra='forbid')
 name:str=Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
 type:str
 description:str=Field(default='',max_length=1000)
 default:object=None
 value:object=None
 clear_default:bool=False
 clear_value:bool=False
 expected_revision:int|None=Field(default=None,ge=1)
 @model_validator(mode='after')
 def typed(self):
  if self.type not in ('string','number','integer','boolean','array','object','null') or self.name in {'id','role','permissions','tenant_id','workspace_id','__proto__','constructor','prototype'}:raise ValueError('Invalid custom variable name or type')
  if self.clear_default and 'default' in self.model_fields_set or self.clear_value and 'value' in self.model_fields_set:raise ValueError('Cannot set and clear the same field')
  for field in ('default','value'):
   if field in self.model_fields_set:check_schema(getattr(self,field),{'type':self.type},field);check_size(getattr(self,field),65536,field)
  return self

def authorize(context,session):
 from app.auth import validate_context
 from app.rbac import require_any_permission,RUN_PERMISSIONS
 if getattr(context,'principal_type','human')!='human':raise AppError('FORBIDDEN','用户变量仅属于当前登录账号',403)
 validate_context(context,session);require_any_permission(context,session,RUN_PERMISSIONS)
def rows(context,session):return session.scalars(select(Resource).where(Resource.kind==KIND,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id,Resource.owner_id==context.operator_id)).all()
def owned(identifier,context,session):
 row=next((r for r in rows(context,session) if r.resource_id==identifier),None)
 if row is None:raise AppError('NOT_FOUND','变量不存在或不属于当前账号',404)
 return row

def workflow_references(graph,*,include_children=True):
 if hasattr(graph,'model_dump'):graph=graph.model_dump(mode='json')
 if not isinstance(graph,dict):return
 for node in graph.get('nodes',[]):
  yield from references(node.get('inputs',{}))
  config=node.get('config',{})
  for key in ('input_mapping','output_mapping','state','until'):
   if key in config:yield from references(config[key])
  if include_children and node.get('type') in ('iteration','loop') and isinstance(config.get('body'),dict):yield from workflow_references(config['body'])

def resource_references(kind,value):
 graph=value if kind=='workflow' else value.get('workflow_snapshot',{})
 yield from workflow_references(graph)

def referenced(name,context,session):
 prefix='user_vars.'+name;found=[]
 from app.db import _resource_query,get_resource
 candidates=[row for kind in ('workflow','agent_version','run') for row in session.scalars(_resource_query(session,kind,None,context))]
 for row in candidates:
  try:value=get_resource(session,row.kind,row.resource_id,context)
  except AppError as error:
   if error.status_code==404:continue
   raise
  if row.kind=='run' and value.get('status') in ('succeeded','failed','cancelled'):continue
  if any(r['$ref']==prefix or r['$ref'].startswith(prefix+'.') for r in resource_references(row.kind,value)):found.append({'kind':row.kind,'id':row.resource_id})
 return found

def pool(context,session,workflow):
 if getattr(context,'principal_type','human')!='human':return {}
 used={r['$ref'].split('.')[1] for r in workflow_references(workflow) if r['$ref'].startswith('user_vars.') and len(r['$ref'].split('.'))>1}
 values={}
 for row in rows(context,session):
  item=json.loads(row.payload)
  if item['name'] not in used:continue
  if 'value' in item:values[item['name']]=deepcopy(item['value'])
  elif 'default' in item:values[item['name']]=deepcopy(item['default'])
 return values

def validate_references(workflow,context,session):
 catalog={json.loads(r.payload)['name']:json.loads(r.payload) for r in rows(context,session)}
 for ref in workflow_references(workflow):
  path=ref['$ref'].split('.')
  if path[0]=='user_vars' and (len(path)<2 or path[1] not in catalog):raise AppError('USER_VARIABLE_NOT_FOUND','当前账号未定义此用户变量：'+'.'.join(path[1:2]),422)

@router.get('/user-variables')
def listing(context=Depends(get_context),session=Depends(get_session)):
 authorize(context,session);return {'items':[json.loads(r.payload) for r in rows(context,session)]}
@router.post('/user-variables',status_code=201)
def create(body:VariableWrite,context=Depends(get_context),session=Depends(get_session)):
 session.connection().exec_driver_sql('BEGIN IMMEDIATE');authorize(context,session)
 if any(json.loads(r.payload)['name']==body.name for r in rows(context,session)):raise AppError('CONFLICT','当前账号已有同名用户变量',409)
 value=body.model_dump(exclude_unset=True,exclude={'expected_revision','clear_default','clear_value'});value.update(id=new_id('user_var'),revision=1,created_at=utc_now());session.add(Resource(kind=KIND,resource_id=value['id'],tenant_id=context.tenant_id,workspace_id=context.workspace_id,owner_id=context.operator_id,payload=json.dumps(value,ensure_ascii=False),created_at=utc_now(),updated_at=utc_now()));session.commit();return value
@router.put('/user-variables/{identifier}')
def update(identifier:str,body:VariableWrite,context=Depends(get_context),session=Depends(get_session)):
 session.connection().exec_driver_sql('BEGIN IMMEDIATE');authorize(context,session);row=owned(identifier,context,session);old=json.loads(row.payload)
 if body.expected_revision!=old['revision']:raise AppError('USER_VARIABLE_REVISION_CONFLICT','变量已变化，请重新读取后保存',409)
 if body.name!=old['name'] or body.type!=old['type']:
  used=referenced(old['name'],context,session)
  if used:raise AppError('USER_VARIABLE_REFERENCED','变量仍被流程、发布版本或待处理运行引用，请先显式更新引用或新建变量',409,used)
 if body.name!=old['name'] and any(json.loads(r.payload)['name']==body.name for r in rows(context,session)):raise AppError('CONFLICT','当前账号已有同名变量',409)
 value={**old,**body.model_dump(exclude_unset=True,exclude={'expected_revision','clear_default','clear_value'})}
 for field in ('default','value'):
  if getattr(body,'clear_'+field):value.pop(field,None)
 for field in ('value','default'):
  if field in value:check_schema(value[field],{'type':value['type']},field)
 value['revision']=old['revision']+1;row.payload=json.dumps(value,ensure_ascii=False);row.updated_at=utc_now();session.commit();return value
@router.delete('/user-variables/{identifier}')
def remove(identifier:str,expected_revision:int=Query(ge=1),context=Depends(get_context),session=Depends(get_session)):
 session.connection().exec_driver_sql('BEGIN IMMEDIATE');authorize(context,session);row=owned(identifier,context,session);value=json.loads(row.payload)
 if expected_revision!=value['revision']:raise AppError('USER_VARIABLE_REVISION_CONFLICT','变量已变化，请重新读取',409)
 if referenced(value['name'],context,session):raise AppError('USER_VARIABLE_REFERENCED','变量仍被引用，请先更新引用后删除',409)
 session.delete(row);session.commit();return {'deleted':True}
