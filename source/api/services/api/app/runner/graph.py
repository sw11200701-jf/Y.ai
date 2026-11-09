from collections import deque
from jsonschema import Draft202012Validator
from app.contracts import WorkflowDefinition
from app.errors import AppError
from app.runner.registry import definition, OPS
from app.runner.variables import references, NAME, check_variable, validate_json_schema

TERMINALS={"end","output","answer"}
TRIGGERS={"trigger_text","trigger_image","trigger_audio","trigger_file","trigger_mixed"}
STARTS={"start","input"}|TRIGGERS
def invalid(message): raise AppError("INVALID_WORKFLOW",message)

def schema_type(schema,path):
    current=schema
    for part in path:
        if current.get("type")=="array" and part.isdigit(): current=current.get("items",{})
        elif part in current.get("properties",{}): current=current["properties"][part]
        elif current.get("additionalProperties",True) is not False: return None
        else: invalid("Reference names an undeclared output/input: "+".".join(path))
    return current.get("type")

def validate_graph(workflow,depth=0,scope=None):
    if depth>3: invalid("Nested repetition depth exceeds3")
    if workflow.schemaVersion!="2.0": invalid("Repeated body requires schemaVersion2.0")
    nodes={n.id:n for n in workflow.nodes}
    if not nodes or len(nodes)!=len(workflow.nodes) or len(nodes)>64: invalid("Graph requires1..64 unique nodes")
    if len(workflow.edges)>256: invalid("Too many edges")
    if any(not NAME.fullmatch(n.id) for n in workflow.nodes): invalid("Node ids must be dotted-reference-safe identifiers")
    validate_json_schema(workflow.input_schema)
    from app.runner.input_defaults import validate_defaults
    validate_defaults(workflow.input_schema)
    if workflow.input_schema.get("type")!="object": invalid("Workflow input_schema must be object")
    for name,decl in workflow.variables.items():
        if not NAME.fullmatch(name) or decl.get("type") not in ("string","number","integer","boolean","array","object","null"): invalid("Invalid variable declaration")
        if "default" in decl:
            if getattr(workflow,'ui',{}).get('field_contract')=='business_v1':
                from app.runner.variables import check_schema
                check_schema(decl['default'],{'type':decl['type']},name+' 默认值')
            else:check_variable(decl["default"],decl)
    if not 1<=workflow.limits.get("max_steps",256)<=512 or not 1<=workflow.limits.get("timeout_seconds",120)<=120: invalid("Workflow execution limits outside permitted bounds")
    if not 1<=workflow.limits.get("max_concurrency",4)<=4: invalid("并行节点上限须为1至4")
    incoming={key:[] for key in nodes};outgoing={key:[] for key in nodes};edges=set()
    for edge in workflow.edges:
        key=(edge.source,edge.target,edge.source_port,edge.target_port)
        if key in edges or edge.source not in nodes or edge.target not in nodes: invalid("Duplicate edge or missing node")
        edges.add(key)
        if edge.target_port!="default": invalid("Only default target port is supported")
        incoming[edge.target].append(edge);outgoing[edge.source].append(edge)
    starts=[n for n in workflow.nodes if n.type in STARTS]
    if not starts or any(incoming[n.id] for n in starts):invalid("Entry nodes cannot have predecessors")
    legacy_starts=[n for n in starts if n.type not in TRIGGERS]
    if legacy_starts and (len(starts)!=1 or len(legacy_starts)!=1):invalid("Do not mix legacy start/input with message triggers")
    if len({n.type for n in starts})!=len(starts):invalid("At most one trigger per message type")
    if depth and any(n.type in TRIGGERS for n in starts):invalid("Repetition bodies use start/input, not external triggers")
    start_ids={n.id for n in starts}
    if not any(n.type in TERMINALS for n in workflow.nodes): invalid("At least one terminal output required")
    indegree={key:len(v) for key,v in incoming.items()}
    queue=deque([key for key,degree in indegree.items() if degree==0]);ordered=[]
    ancestors={key:set() for key in nodes};dominators={key:set(nodes) for key in nodes}
    while queue:
        key=queue.popleft();ordered.append(nodes[key]);parents=[e.source for e in incoming[key]]
        dominators[key]=({key}|set.intersection(*(dominators[p] for p in parents))) if parents else {key}
        for edge in outgoing[key]:
            ancestors[edge.target]|=ancestors[key]|{key};indegree[edge.target]-=1
            if indegree[edge.target]==0: queue.append(edge.target)
    if len(ordered)!=len(nodes): invalid("Cycles rejected; use structured loop/iteration body")
    for node in ordered:
        from app.runner.fields import input_schema,output_schema,validate_declarations
        validate_declarations(node,workflow)
        spec=definition(node.type)
        if spec is None: invalid("Unknown node type: "+node.type)
        if node.id not in start_ids and not incoming[node.id]: invalid("Disconnected node: "+node.id)
        if node.id not in start_ids and not start_ids.intersection(ancestors[node.id]): invalid("Unreachable node")
        if node.type in TERMINALS:
            if outgoing[node.id]: invalid("Terminal node cannot have outgoing edges")
        elif node.type=="if_else":
            if {e.source_port for e in outgoing[node.id]}!={"false","true"}: invalid("IF requires true and false edges")
        elif node.type=="question_classifier":
            ports=[e.source_port for e in outgoing[node.id]]
            if len(set(ports))<2 or "else" not in ports: invalid("Classifier needs class ports and else")
            class_ids=[item.get("id") for item in node.config.get("classes",[])]
            if len(class_ids)!=len(set(class_ids)) or set(ports)!=set(class_ids)|{"else"} or "else" in class_ids: invalid("Classifier ports must match configured classes plus else")
        elif not outgoing[node.id] or any(e.source_port!="default" for e in outgoing[node.id]): invalid(node.id+": default outgoing edges required")
        error=next(Draft202012Validator(spec["config_schema"]).iter_errors(node.config),None)
        if error: invalid(f"{node.id} config: {error.message}")
        if node.type=="if_else":
            grouped="conditions" in node.inputs
            if not grouped and "left" not in node.inputs: invalid("条件分支需要左侧值或条件组")
            if grouped != ("logical_operator" in node.config) or grouped and ("left" in node.inputs or "right" in node.inputs):
                invalid("条件组与旧单条件配置不能混合")
        if node.type=="assign" and node.config["name"] not in workflow.variables: invalid("Assignment target must be declared")
        if node.type=="parameter_extractor": validate_json_schema(node.config["schema"])
        if node.type=="llm":
            messages=node.config.get("prompt_messages")
            if messages is not None:
                if "system_prompt" in node.config or (getattr(node,'input_schema',None) is None and node.inputs.get("prompt") not in (None, "")) or "prompt" in node.config.get("input_mapping",{}):
                    invalid("LLM提示消息与旧提示字段不能同时使用")
                if not any(item["role"]=="user" for item in messages) or any(item["role"]=="system" for item in messages[1:]):
                    invalid("LLM提示需要User消息，System消息只能在首条")
            elif "prompt" not in node.inputs and "message" not in node.inputs and "prompt" not in node.config.get("input_mapping",{}):
                invalid("LLM节点需要User提示或消息输入")
            response=node.config.get("response_format", {"type":"text"})
            if response["type"]=="json_schema" and "schema" not in response:invalid("结构化输出需要JSON Schema")
            if "schema" in response:validate_json_schema(response["schema"])
            if response["type"]=="text" and set(response)-{"type"}:invalid("文本输出不接受Schema附加配置")
            if response["type"]=="json_object" and response.get("strict"):
                invalid("JSON对象模式不能声明原生严格Schema输出")
        if node.type=="template" and ("{%" in node.config["template"] or "__" in node.config["template"]): invalid("Template functions/control code prohibited")
        for name in input_schema(node,workflow).get("required",[]):
            if name not in node.inputs and 'default' not in input_schema(node,workflow).get('properties',{}).get(name,{}): invalid(f"{node.id}: required input {name}")
        for field,value in node.inputs.items():
            expected=input_schema(node,workflow).get("properties",{}).get(field,{}).get("type")
            if not list(references(value)):
                field_schema=input_schema(node,workflow).get("properties",{}).get(field,{})
                if "$defs" in spec["input_schema"]: field_schema={**field_schema,"$defs":spec["input_schema"]["$defs"]}
                if next(Draft202012Validator(field_schema).iter_errors(value),None): invalid(f"{node.id}.{field}: literal input type mismatch")
            for ref in references(value):
                parts=ref["$ref"].split(".");root=parts[0];actual=None
                if root=="nodes":
                    if len(parts)<2 or parts[1] not in ancestors[node.id]: invalid("Forward/non-ancestor node reference: "+ref["$ref"])
                    if parts[1] not in dominators[node.id] and any(nodes[key].type in {"if_else","question_classifier"} or nodes[key].type in TRIGGERS for key in ancestors[parts[1]]) and not ref.get("optional"): invalid("Branch-dependent reference requires optional=true")
                    actual=schema_type(output_schema(nodes[parts[1]],workflow),parts[2:])
                elif root=="inputs": actual=schema_type(workflow.input_schema,parts[1:])
                elif root=="vars":
                    if len(parts)<2 or parts[1] not in workflow.variables: invalid("Undeclared variable reference")
                    actual=workflow.variables[parts[1]]["type"] if len(parts)==2 else None
                elif root=="user_vars":
                    if len(parts)<2 or not NAME.fullmatch(parts[1]):invalid("Invalid custom user variable reference")
                elif root in ("system","user"):
                    fields={"system":{"run_id","tenant_id","workspace_id"},"user":{"id","role","permissions"}}
                    if len(parts)!=2 or parts[1] not in fields[root]: invalid("Unknown read-only variable: "+ref["$ref"])
                    actual="array" if parts[1]=="permissions" else "string"
                elif root not in ("iteration","loop") or root!=scope: invalid("Unknown or out-of-scope reference: "+ref["$ref"])
                if value is ref and expected and actual:
                    target_types=set(expected if isinstance(expected,list) else [expected]);source_types=set(actual if isinstance(actual,list) else [actual])
                    if not source_types.issubset(target_types) and not (target_types=={'number'} and source_types=={'string'}):invalid(f"{node.id}.{field}: incompatible reference type {actual} to {expected}")
        if node.type in ("iteration","loop"):
            validate_graph(WorkflowDefinition.model_validate(node.config["body"]),depth+1,node.type)
            for ref in references({k:v for k,v in node.config.items() if k not in ("body","until")}):
                if ref["$ref"].split(".")[0] not in ("inputs","vars","nodes"): invalid("Invalid repetition initializer reference")
            if node.type=="loop":
                condition=node.config["until"]
                if condition.get("operator") not in OPS or "left" not in condition: invalid("Invalid loop until condition")
                for ref in references(condition):
                    if not ref["$ref"].startswith("loop.state.") and ref["$ref"]!="loop.index": invalid("Loop condition may only read loop state/index")
    writers={}
    for node in ordered:
        if node.type=="assign":
            for previous in writers.setdefault(node.config["name"],[]):
                if previous not in ancestors[node.id] and node.id not in ancestors[previous]:
                    raise AppError("VARIABLE_WRITE_CONFLICT", "并行节点不能同时修改同一全局变量，请通过连线确定写入顺序", 422)
            writers[node.config["name"]].append(node.id)
    return ordered
