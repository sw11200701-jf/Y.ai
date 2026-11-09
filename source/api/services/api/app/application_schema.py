"""Pure bounded inference for explicitly selected workflow output fields."""
from copy import deepcopy
from app.errors import AppError


def output_schema(workflow,agents):
    explicit=workflow.get('output_schema')
    if isinstance(explicit,dict) and explicit.get('type')=='object' and isinstance(explicit.get('properties'),dict):return deepcopy(explicit)
    from app.runner.registry import node_registry
    definitions={item['type']:item for item in node_registry()['items']}
    nodes={node['id']:node for node in workflow['nodes']}
    resolving=set()
    def missing():raise AppError('APP_SCHEMA_REQUIRED','公开输出无法推导确定字段类型，请在来源流程声明输出Schema或选择明确字段',422)
    def field(shape,path):
        current=shape
        for name in path:
            if not isinstance(current,dict):missing()
            current=current.get('properties',{}).get(name)
        if not isinstance(current,dict) or not current.get('type'):missing()
        # No automatic arbitrary dictionary exposure, even with object type.
        if current.get('type')=='object' and not isinstance(current.get('properties'),dict):missing()
        return deepcopy(current)
    def infer(value,depth=0):
        if depth>24:missing()
        if isinstance(value,dict):
            if set(value)=={'$ref'}:
                ref=value['$ref']
                if not isinstance(ref,str) or ref in resolving:missing()
                resolving.add(ref)
                try:
                    parts=ref.split('.')
                    if parts[0]=='inputs':return field(workflow.get('input_schema',{}),parts[1:])
                    if parts[0]=='vars':return field({'properties':workflow.get('variables',{})},parts[1:])
                    if len(parts)<3 or parts[0]!='nodes' or parts[1] not in nodes:missing()
                    node=nodes[parts[1]];kind=node['type'];path=parts[2:]
                    if kind in {'start','input','trigger_text','trigger_image','trigger_audio','trigger_file','trigger_mixed'} and path[0]=='inputs':return field(workflow.get('input_schema',{}),path[1:])
                    if kind=='llm' and path[0]=='structured_output':return field(node.get('config',{}).get('response_format',{}).get('schema',{}),path[1:])
                    if kind=='agent' and path[0]=='structured_output':return field(agents.get('root/'+node['id'],{}).get('output_schema',{}),path[1:])
                    if kind=='report' and path in (['artifacts'],['artifact_ids']):
                        from app.application_files import FILE_SCHEMA
                        return {'type':'array','items':deepcopy(FILE_SCHEMA)}
                    if kind in {'output','answer'} and path[:1]==['values'] and len(path)==2:return infer(node.get('inputs',{}).get(path[1]),depth+1)
                    return field(definitions.get(kind,{}).get('output_schema',{}),path)
                finally:resolving.discard(ref)
            return {'type':'object','properties':{key:infer(child,depth+1) for key,child in value.items()},'required':list(value),'additionalProperties':False}
        if isinstance(value,list):
            if not value:return {'type':'array','maxItems':0,'items':{}}
            items=[infer(item,depth+1) for item in value]
            if any(item!=items[0] for item in items):missing()
            return {'type':'array','items':items[0]}
        if value is None:return {'type':'null'}
        if type(value) is bool:return {'type':'boolean'}
        if type(value) is int:return {'type':'integer'}
        if type(value) is float:return {'type':'number'}
        if type(value) is str:return {'type':'string'}
        missing()
    properties={}
    for node in workflow['nodes']:
        if node['type'] not in {'output','answer'}:continue
        for name,value in node.get('inputs',{}).items():
            shape=infer(value)
            if name in properties and properties[name]!=shape:missing()
            properties[name]=shape
    return {'type':'object','properties':properties,'additionalProperties':False}
