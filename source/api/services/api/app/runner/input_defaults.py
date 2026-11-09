"""Missing input defaults only; never replace false/zero/empty/null or identity."""
from copy import deepcopy
from app.runner.variables import check_schema,check_size
from app.errors import AppError

def validate_defaults(schema,depth=0):
    if depth>16:raise AppError('INVALID_WORKFLOW','输入定义嵌套超过上限',422)
    if 'default' in schema:check_schema(schema['default'],schema,'输入默认值')
    for child in schema.get('properties',{}).values():validate_defaults(child,depth+1)
    if isinstance(schema.get('items'),dict):validate_defaults(schema['items'],depth+1)

def normalize_inputs(value,schema,*,partial=False):
    validate_defaults(schema)
    def apply(current,shape,depth=0):
        if depth>16:raise AppError('RESOURCE_LIMIT','输入嵌套超过上限',422)
        if isinstance(current,dict):
            result=deepcopy(current)
            for name,child in shape.get('properties',{}).items():
                if name not in result and 'default' in child:result[name]=deepcopy(child['default'])
                if name in result:result[name]=apply(result[name],child,depth+1)
            return result
        if isinstance(current,list) and isinstance(shape.get('items'),dict):return [apply(item,shape['items'],depth+1) for item in current]
        return deepcopy(current)
    check_size(value,1048576,'流程输入');result=apply(value,schema)
    check_size(result,1048576,'流程输入')
    check_schema(result,{**schema,'required':[]} if partial else schema,'流程输入')
    return result
