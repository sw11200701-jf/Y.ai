"""User field contract is opt-in; historical graph snapshots keep their envelope."""
from copy import deepcopy
from app.runner.variables import resolve,references,check_schema,validate_json_schema
from app.errors import AppError

def business(workflow,node=None):return getattr(workflow,'ui',{}).get('field_contract')=='business_v1' or bool(node and getattr(node,'ui',{}).get('field_contract')=='business_v1')
def declared(node,kind):return getattr(node,kind+'_schema',None)
def input_schema(node,workflow=None):
 from app.runner.registry import definition
 return declared(node,'input') or definition(node.type)['input_schema']
def output_schema(node,workflow):
 from app.runner.registry import definition
 if not business(workflow,node):return definition(node.type)['output_schema']
 if node.type in ('llm','agent'):
  fmt=node.config.get('response_format',{'type':'text'})
  if fmt['type']!='text':return declared(node,'output') or fmt.get('schema') or {'type':'object'}
  return {'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}
 if node.type in ('start','input','trigger_text','trigger_image','trigger_audio','trigger_file','trigger_mixed'):return workflow.input_schema
 if node.type in ('output','answer','end') and not node.config.get('output_mapping'):return declared(node,'input') or declared(node,'output') or {'type':'object'}
 if node.type in ('transform','output','answer','end'):return declared(node,'output') or declared(node,'input') or {'type':'object'}
 return declared(node,'output') or definition(node.type)['output_schema']
def validate_declarations(node,workflow):
 for kind in ('input','output'):
  schema=declared(node,kind)
  if schema is not None:
   validate_json_schema(schema)
   if schema.get('type')!='object':raise AppError('INVALID_NODE_FIELDS','节点输入和输出定义必须是字段对象',422)
 if node.type in ('llm','agent') and business(workflow,node):
  fmt=node.config.get('response_format',{'type':'text'})
  if fmt.get('type')=='text' and declared(node,'output') not in (None,{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}):raise AppError('INVALID_NODE_FIELDS','文本回复固定输出text；请先选择JSON再定义结构',422)
 if node.type in ('llm','agent') and node.config.get('prompt_contract')=='local_inputs_v1':
  from app.agents.prompts import validate_local_templates
  schema=declared(node,'input') if declared(node,'input') is not None else node.config.get('prompt_input_schema')
  if schema is not None:
   validate_json_schema(schema)
   if schema.get('type')!='object':raise AppError('INVALID_NODE_FIELDS','提示词输入定义必须为字段对象',422)
   names=schema.get('properties',{})
  else:names=node.inputs.get('variables',{})
  validate_local_templates(node.config,names)
 local_references(node,workflow)
 for ref in references(node.config.get('input_mapping',{})):
  if not ref['$ref'].startswith('fields.'):raise AppError('INVALID_NODE_FIELDS','服务输入映射只能使用本节点已定义字段',422)
 for ref in references(node.config.get('output_mapping',{})):
  namespace='fields.' if node.type in ('transform','output','answer','end') else 'result.'
  if not ref['$ref'].startswith(namespace):raise AppError('INVALID_NODE_FIELDS','输出映射只能来自本节点的实际输入或服务输出',422)
def local_references(node,workflow):
 from app.runner.graph import schema_type
 from app.runner.registry import definition
 for key in ('input_mapping','output_mapping'):
  for ref in references(node.config.get(key,{})):
   parts=ref['$ref'].split('.')
   schema=input_schema(node,workflow) if parts[0]=='fields' else definition(node.type)['output_schema']
   schema_type(schema,parts[1:])

def service_values(node,raw):
 if declared(node,'input') is not None:
  from app.runner.input_defaults import normalize_inputs
  normalized=normalize_inputs(raw,declared(node,'input'));raw.clear();raw.update(normalized)
 mapping=node.config.get('input_mapping')
 values=resolve(mapping,{'fields':raw}) if mapping is not None else deepcopy(raw)
 if node.type=='llm' and declared(node,'input') is not None:
  if 'prompt_messages' in node.config and mapping is None:values={'variables':deepcopy(raw)}
  else:values.setdefault('variables',deepcopy(raw))
 if node.type=='agent' and 'user_prompt' in node.config:
  from app.agents.prompts import render
  values['prompt']=render(node.config['user_prompt'],raw,('config','user_prompt'))

 return values

def project(node,workflow,result):
 if not business(workflow,node):return result
 if node.type in ('llm','agent'):
  fmt=node.config.get('response_format',{'type':'text'})
  if fmt.get('type')=='text':value={'text':result['text']}
  else:
   # Both actual LLM and actual Agent replies are locally schema-checked.
   value=result.get('structured_output')
   if not isinstance(value,dict):
    import json
    try:value=json.loads(result['text'])
    except (ValueError,TypeError,KeyError):raise AppError('OUTPUT_SCHEMA_MISMATCH','实际回复不是有效JSON对象',422)
   check_schema(value,output_schema(node,workflow),node.id+' JSON输出')
  return value
 if node.type in ('output','answer','end'):return result['values']
 if node.type!='transform' and node.config.get('output_mapping') is not None:return resolve(node.config['output_mapping'],{'result':result})
 return result


def message_inputs(workflow,resolved,inputs):
 if not business(workflow):return inputs
 selected=next((n for n in workflow.nodes if n.type=='trigger_'+resolved['type']),None)
 if selected is None:return inputs
 value=deepcopy(inputs);props=workflow.input_schema.get('properties',{})
 for config_key,default,source in [('message_field','text','text'),('attachments_field','attachment_ids','attachment_ids')]:
  name=selected.config.get(config_key,default)
  if name in props:value[name]=deepcopy(resolved[source])
 return value


def service_config(node):
 # User field adapters belong to the graph contract, not tool/module configuration.
 return {k:deepcopy(v) for k,v in node.config.items() if k not in ('input_mapping','output_mapping')}
