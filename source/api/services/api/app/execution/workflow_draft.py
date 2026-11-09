"""Persist incomplete topology without weakening execution/publish validation."""
from jsonschema import Draft202012Validator
from app.runner.registry import definition
from app.runner.variables import NAME,references,check_schema,validate_json_schema,check_variable
from app.runner.fields import validate_declarations,input_schema,output_schema
from app.runner.graph import schema_type
from app.errors import AppError

def schema_issue(issue,node):
 path=list(map(str,issue.path))
 if issue.validator=='required':
  missing=[str(k) for k in issue.validator_value if isinstance(issue.instance,dict) and k not in issue.instance]
  fail('请填写必填字段: '+', '.join(missing),node,'.'.join(path+missing))
 reason='字段类型应为: '+str(issue.validator_value) if issue.validator=='type' else '字段不满足约束: '+str(issue.validator)
 fail(reason,node,'.'.join(path) or 'config')

def fail(message,node=None,field=None):raise AppError('NODE_DRAFT_INVALID',message,422,[{'loc':[node or 'workflow',field or 'config'],'message':message}])
def validate_draft(workflow,node_id=None,scope_path=()):
 nodes={n.id:n for n in workflow.nodes}
 if not nodes or len(nodes)!=len(workflow.nodes) or len(nodes)>64:fail('节点标识重复或数量不符合限制')
 for n in workflow.nodes:
  if not NAME.fullmatch(n.id) or definition(n.type) is None:fail('节点类型或标识无效',n.id)
  validate_declarations(n,workflow)
  for issue in (Draft202012Validator(definition(n.type)['config_schema']).iter_errors(n.config) if not scope_path and n.id==node_id else []):
   if issue.validator!='required':schema_issue(issue,n.id)
 validate_json_schema(workflow.input_schema)
 if workflow.input_schema.get('type')!='object':fail('流程输入必须定义为字段对象')
 for name,decl in workflow.variables.items():
  if not NAME.fullmatch(name):fail('全局变量名称无效',field=name)
  if decl.get('type') not in ('string','number','integer','boolean','array','object','null'):fail('全局变量类型无效',field=name)
  if 'default' in decl:
   if getattr(workflow,'ui',{}).get('field_contract')=='business_v1':check_schema(decl['default'],{'type':decl['type']},name+' 默认值')
   else:check_variable(decl['default'],decl)
 incoming={n:[] for n in nodes};outgoing={n:[] for n in nodes};seen=set()
 for e in workflow.edges:
  key=(e.source,e.target,e.source_port,e.target_port)
  if e.source not in nodes or e.target not in nodes or key in seen:fail('连线重复或目标节点不存在')
  seen.add(key);incoming[e.target].append(e.source);outgoing[e.source].append(e.target)
 degree={k:len(v) for k,v in incoming.items()};queue=[k for k,v in degree.items() if not v];ancestors={k:set() for k in nodes}
 for key in queue:
  for target in outgoing[key]:
   ancestors[target]|=ancestors[key]|{key};degree[target]-=1
   if degree[target]==0:queue.append(target)
 if len(queue)!=len(nodes):fail('不允许普通连线形成循环，请使用受控循环节点')
 if scope_path:
  parent=nodes.get(scope_path[0])
  if parent is None or parent.type not in ('loop','iteration'):fail('子流程作用域不存在')
  from app.contracts import WorkflowDefinition
  return validate_draft(WorkflowDefinition.model_validate(parent.config['body']),node_id,scope_path[1:])
 if node_id is None:return
 node=nodes.get(node_id)
 if node is None:fail('当前节点不存在',node_id)
 spec=definition(node.type);error=next(Draft202012Validator(spec['config_schema']).iter_errors(node.config),None)
 if error:schema_issue(error,node.id)
 if node.type in ('llm','agent') and not node.config.get('prompt_messages') and not node.config.get('user_prompt') and not any(k in node.inputs for k in ('prompt','message')) and not node.config.get('input_mapping',{}).get('prompt'):fail('请选择提问对应的输入字段',node.id,'input_mapping.prompt')
 schema=input_schema(node,workflow)
 for required in schema.get('required',[]):
  if required not in node.inputs and 'default' not in schema.get('properties',{}).get(required,{}):fail('请填写输入字段：'+required,node.id,required)
 for key,value in node.inputs.items():
  refs=list(references(value))
  if not refs:
   try:check_schema(value,schema.get('properties',{}).get(key,{}),node.id+'.'+key)
   except AppError:fail('输入字段类型不匹配：'+key,node.id,key)
  for ref in refs:
   parts=ref['$ref'].split('.')
   if parts[0]=='nodes':
    if len(parts)<3 or parts[1] not in ancestors[node.id]:fail('输入引用必须来自已连线的上游节点',node.id,key)
    schema_type(output_schema(nodes[parts[1]],workflow),parts[2:])
   elif parts[0]=='inputs':schema_type(workflow.input_schema,parts[1:])
   elif parts[0]=='vars' and (len(parts)<2 or parts[1] not in workflow.variables):fail('引用的全局变量未定义',node.id,key)
   elif parts[0] not in ('vars','user_vars','inputs','system','user','iteration','loop'):fail('引用作用域无效',node.id,key)


def guard_schema_changes(previous,current):
 old_nodes={n['id']:n for n in previous.get('nodes',[])}
 new_nodes={n['id']:n for n in current.get('nodes',[])}
 def scoped_refs(graph):
  from app.user_variables import workflow_references
  yield from workflow_references(graph,include_children=False)
 refs=list(scoped_refs(current))
 def guard(old,new,prefix,identifier):
  before=old.get('properties',{});after=new.get('properties',{})
  for name,decl in before.items():
   if name not in after or after[name].get('type')!=decl.get('type'):
    path=prefix+name
    if any(r['$ref']==path or r['$ref'].startswith(path+'.') for r in refs):fail('Referenced field was removed or its type changed; update dependent references first',identifier,name)
 guard(previous.get('input_schema',{}),current.get('input_schema',{}),'inputs.','workflow')
 guard({'properties':previous.get('variables',{})},{'properties':current.get('variables',{})},'vars.','workflow')
 for name,decl in previous.get('variables',{}).items():
  if name not in current.get('variables',{}) or current['variables'][name].get('type')!=decl.get('type'):
   if any(n['type']=='assign' and n.get('config',{}).get('name')==name for n in current.get('nodes',[])):fail('Assignment target variable was removed or its type changed; update assignments first','workflow',name)
 for identifier,n in old_nodes.items():
  next_node=new_nodes.get(identifier)
  if not next_node:continue
  if n['type'] in ('start','input','trigger_text','trigger_image','trigger_audio','trigger_file','trigger_mixed'):
   guard(previous.get('input_schema',{}),current.get('input_schema',{}),'nodes.'+identifier+'.',identifier)
   guard(previous.get('input_schema',{}),current.get('input_schema',{}),'nodes.'+identifier+'.inputs.',identifier)
  def effective_output(item):
   if item['type'] in ('llm','agent'):
    fmt=item.get('config',{}).get('response_format',{'type':'text'})
    if fmt.get('type')=='text':return {'properties':{'text':{'type':'string'}}}
    return item.get('output_schema') or fmt.get('schema') or {}
   return item.get('output_schema') or {}
  guard(effective_output(n),effective_output(next_node),'nodes.'+identifier+'.',identifier)
  if n['type'] in ('loop','iteration') and isinstance(n.get('config',{}).get('body'),dict) and isinstance(next_node.get('config',{}).get('body'),dict):guard_schema_changes(n['config']['body'],next_node['config']['body'])
