import json
import re
from decimal import Decimal, InvalidOperation
from jsonschema import Draft202012Validator
from app.errors import AppError

PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TEMPLATE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\s*}}")

def references(value):
    if isinstance(value,dict):
        if "$ref" in value:
            if set(value)-{"$ref","optional"} or not isinstance(value["$ref"],str) or not PATH.fullmatch(value["$ref"]) or ("optional" in value and not isinstance(value["optional"],bool)):
                raise AppError("INVALID_WORKFLOW","Invalid variable reference")
            yield value
        else:
            for item in value.values(): yield from references(item)
    elif isinstance(value,list):
        for item in value: yield from references(item)

def lookup(pool,path,optional=False):
    current=pool
    for part in path.split("."):
        if isinstance(current,dict) and part in current: current=current[part]
        elif isinstance(current,list) and part.isdigit() and int(part)<len(current): current=current[int(part)]
        elif optional: return None
        else: raise AppError("VARIABLE_NOT_FOUND",f"Variable unavailable: {path}")
    return current

def resolve(value,pool):
    if isinstance(value,dict):
        if "$ref" in value: return lookup(pool,value["$ref"],value.get("optional",False))
        return {key:resolve(item,pool) for key,item in value.items()}
    if isinstance(value,list): return [resolve(item,pool) for item in value]
    return value

def check_schema(value,schema,label):
    error=next(Draft202012Validator(schema).iter_errors(value),None)
    if error: raise AppError("VARIABLE_TYPE_MISMATCH",f"{label}: {error.message}")

def validate_json_schema(schema):
    try: Draft202012Validator.check_schema(schema)
    except Exception as exc: raise AppError("INVALID_WORKFLOW","Invalid JSON Schema") from exc
    def walk(value):
        if isinstance(value,dict):
            if "$dynamicRef" in value or "$recursiveRef" in value:
                raise AppError("INVALID_WORKFLOW","仅支持本地$defs静态Schema引用")
            if "$ref" in value and not str(value["$ref"]).startswith("#/$defs/"):
                raise AppError("INVALID_WORKFLOW","Only local JSON Schema references are permitted")
            for item in value.values(): walk(item)
        elif isinstance(value,list):
            for item in value: walk(item)
    walk(schema)

def decimal(value):
    if isinstance(value,bool) or not isinstance(value,(str,int,Decimal)):
        raise AppError("VARIABLE_TYPE_MISMATCH","Decimal operation requires decimal text or integer")
    try:
        if not re.fullmatch(r"-?\d{1,38}(\.\d{1,18})?",str(value)):
            raise InvalidOperation()
        result=Decimal(value)
        if not result.is_finite(): raise InvalidOperation()
        return result
    except InvalidOperation as exc:
        raise AppError("VARIABLE_TYPE_MISMATCH","Invalid decimal value") from exc

def matches(left,operator,right=None):
    if operator=="empty": return left is None or left=="" or left==[] or left=={}
    if operator=="not_empty": return not matches(left,"empty")
    if operator=="eq": return type(left)==type(right) and left==right
    if operator=="ne": return not matches(left,"eq",right)
    if operator=="contains":
        if not isinstance(left,(str,list,dict)): raise AppError("VARIABLE_TYPE_MISMATCH","contains requires text/list/object")
        if isinstance(left,str) and not isinstance(right,str): raise AppError("VARIABLE_TYPE_MISMATCH","text contains requires text")
        return right in left
    a,b=decimal(left),decimal(right)
    if operator=="gt": return a>b
    if operator=="gte": return a>=b
    if operator=="lt": return a<b
    if operator=="lte": return a<=b
    raise AppError("INVALID_WORKFLOW","Unsupported condition operator")


def condition_result(items, logical_operator, depth=0, count=None):
    if count is None: count=[0]
    if depth>3 or logical_operator not in {"and","or"} or not isinstance(items,list) or not 1<=len(items)<=16:
        raise AppError("INVALID_CONDITION_GROUP", "条件组最多嵌套3层，每组须有1至16项", 422)
    results=[]
    for item in items:
        count[0]+=1
        if count[0]>32 or not isinstance(item,dict):
            raise AppError("INVALID_CONDITION_GROUP", "条件组最多32项且每项须为条件对象", 422)
        if "conditions" in item:
            if set(item)!={"conditions","logical_operator"}:
                raise AppError("INVALID_CONDITION_GROUP", "子条件组字段不符合要求", 422)
            result=condition_result(item["conditions"],item["logical_operator"],depth+1,count)
        else:
            if set(item)-{"operator","left","right"} or "operator" not in item or "left" not in item:
                raise AppError("INVALID_CONDITION_GROUP", "条件需要比较方式与左侧值", 422)
            result=matches(item["left"],item["operator"],item.get("right"))
        results.append(result)
    return all(results) if logical_operator=="and" else any(results)

def template(text,inputs):
    def substitute(match):
        value=lookup(inputs,match.group(1))
        if value is None: return ""
        return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,allow_nan=False)
    result=TEMPLATE.sub(substitute,text)
    if "{{" in result or "}}" in result or "{%" in result:
        raise AppError("INVALID_WORKFLOW","Template only supports declared input substitutions")
    return result

def check_size(value,maximum,label):
    try: size=len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())
    except (TypeError,ValueError,RecursionError) as exc:
        raise AppError("VARIABLE_TYPE_MISMATCH",f"{label} is not finite JSON data") from exc
    if size>maximum: raise AppError("RESOURCE_LIMIT",f"{label} exceeds {maximum} bytes")

def check_variable(value,declaration):
    if declaration["type"]=="number": decimal(value)
    else: check_schema(value,{"type":declaration["type"]},"variable assignment")
