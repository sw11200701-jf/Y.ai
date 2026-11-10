from collections import deque
from functools import partial
from jsonschema import Draft202012Validator
from app.contracts import WorkflowDefinition
from app.errors import AppError
from app.runner.registry import definition, OPS
from app.runner.variables import references, NAME, check_variable, validate_json_schema

TERMINALS={"end","output","answer"}
TRIGGERS={"trigger_text","trigger_image","trigger_audio","trigger_file","trigger_mixed"}
STARTS={"start","input"}|TRIGGERS
def invalid(message, rule="graph", *, node_id=None):
    issue = {"rule": rule, "message": message}
    if node_id is not None:
        issue["node_id"] = node_id
    raise AppError("INVALID_WORKFLOW", message, details={"errors": [issue]})

def schema_type(schema,path):
    current=schema
    for part in path:
        if current.get("type")=="array" and part.isdigit(): current=current.get("items",{})
        elif part in current.get("properties",{}): current=current["properties"][part]
        elif current.get("additionalProperties",True) is not False: return None
        else: invalid("变量引用指向未声明的输入或输出字段", "undeclared_field")
    return current.get("type")

def validate_graph(workflow,depth=0,scope=None):
    if depth>3: invalid("循环或迭代最多允许嵌套三层", "nested_repeat")
    if workflow.schemaVersion!="2.0": invalid("子流程需要使用 2.0 版工作流", "schema_version")
    nodes={n.id:n for n in workflow.nodes}
    if not nodes or len(nodes)!=len(workflow.nodes) or len(nodes)>64: invalid("工作流需包含 1 到 64 个节点，节点标识不能重复", "node_count")
    if len(workflow.edges)>256: invalid("工作流最多允许 256 条连线", "edge_count")
    if any(not NAME.fullmatch(n.id) for n in workflow.nodes): invalid("节点标识须以英文字母或下划线开头，后续仅能使用英文字母、数字和下划线", "node_id")
    validate_json_schema(workflow.input_schema)
    from app.runner.input_defaults import validate_defaults
    validate_defaults(workflow.input_schema)
    if workflow.input_schema.get("type")!="object": invalid("工作流输入须定义为对象", "workflow_input")
    for name,decl in workflow.variables.items():
        if not NAME.fullmatch(name) or decl.get("type") not in ("string","number","integer","boolean","array","object","null"): invalid("全局变量的标识或字段类型无效", "variable_declaration")
        if "default" in decl:
            if getattr(workflow,'ui',{}).get('field_contract')=='business_v1':
                from app.runner.variables import check_schema
                check_schema(decl['default'],{'type':decl['type']},name+' 默认值')
            else:check_variable(decl["default"],decl)
    if not 1<=workflow.limits.get("max_steps",256)<=512 or not 1<=workflow.limits.get("timeout_seconds",120)<=120: invalid("工作流的步数或超时时间超出允许范围", "execution_limits")
    if not 1<=workflow.limits.get("max_concurrency",4)<=4: invalid("并行节点上限须为1至4")
    incoming={key:[] for key in nodes};outgoing={key:[] for key in nodes};edges=set()
    for edge in workflow.edges:
        key=(edge.source,edge.target,edge.source_port,edge.target_port)
        if key in edges or edge.source not in nodes or edge.target not in nodes: invalid("存在重复连线，或连线两端有节点不存在", "edge_endpoint")
        edges.add(key)
        if edge.target_port!="default": invalid("连线只能接入节点的默认输入端口", "target_port", node_id=edge.target)
        incoming[edge.target].append(edge);outgoing[edge.source].append(edge)
    starts=[n for n in workflow.nodes if n.type in STARTS]
    if not starts: invalid("请添加至少一个流程入口", "missing_entry")
    for start in starts:
        if incoming[start.id]: invalid("请删除指向入口节点的连线", "entry_predecessor", node_id=start.id)
    legacy_starts=[n for n in starts if n.type not in TRIGGERS]
    if legacy_starts and (len(starts)!=1 or len(legacy_starts)!=1):invalid("旧版开始或输入节点不能和消息触发节点混用", "mixed_entries")
    if len({n.type for n in starts})!=len(starts):invalid("同一种消息类型只能设置一个触发入口", "duplicate_entry")
    if depth and any(n.type in TRIGGERS for n in starts):invalid("循环或迭代子流程需要开始或输入节点作为入口", "subflow_entry")
    start_ids={n.id for n in starts}
    if not any(n.type in TERMINALS for n in workflow.nodes): invalid("请添加至少一个终端输出节点", "missing_terminal")
    indegree={key:len(v) for key,v in incoming.items()}
    queue=deque([key for key,degree in indegree.items() if degree==0]);ordered=[]
    ancestors={key:set() for key in nodes};dominators={key:set(nodes) for key in nodes}
    while queue:
        key=queue.popleft();ordered.append(nodes[key]);parents=[e.source for e in incoming[key]]
        dominators[key]=({key}|set.intersection(*(dominators[p] for p in parents))) if parents else {key}
        for edge in outgoing[key]:
            ancestors[edge.target]|=ancestors[key]|{key};indegree[edge.target]-=1
            if indegree[edge.target]==0: queue.append(edge.target)
    if len(ordered)!=len(nodes): invalid("工作流连线形成了环路，请用循环或迭代节点表达重复执行", "cycle")
    for node in ordered:
        reject = partial(invalid, node_id=node.id)
        from app.runner.fields import input_schema,output_schema,validate_declarations
        validate_declarations(node,workflow)
        spec=definition(node.type)
        if spec is None: reject("节点类型不可用，请从节点库重新选择", "unknown_node_type")
        if node.id not in start_ids and not incoming[node.id]: reject("此节点没有上游连线，请连接到流程入口下游", "disconnected_node")
        if node.id not in start_ids and not start_ids.intersection(ancestors[node.id]): reject("此节点无法从任何入口到达，请检查上游连线", "unreachable_node")
        error=next(Draft202012Validator(spec["config_schema"]).iter_errors(node.config),None)
        if error:
            message = {"required": "请补齐此节点的必填配置", "additionalProperties": "此节点包含不受支持的配置项"}.get(
                error.validator, "此节点的配置值不符合类型或取值要求")
            reject(message, "node_config")
        if node.type in TERMINALS:
            if outgoing[node.id]: reject("请删除终端节点的输出连线", "terminal_outgoing")
        elif node.type=="if_else":
            if {e.source_port for e in outgoing[node.id]}!={"false","true"}: reject("条件分支的「成立」和「不成立」端口都需要连线", "condition_edges")
        elif node.type=="question_classifier":
            ports=[e.source_port for e in outgoing[node.id]]
            if len(set(ports))<2 or "else" not in ports: reject("请为问题分类节点连接分类端口和「其他」端口", "classifier_edges")
            class_ids=[item.get("id") for item in node.config.get("classes",[])]
            if len(class_ids)!=len(set(class_ids)) or set(ports)!=set(class_ids)|{"else"} or "else" in class_ids: reject("问题分类节点的连线必须对应已配置的分类和「其他」端口", "classifier_ports")
        elif not outgoing[node.id] or any(e.source_port!="default" for e in outgoing[node.id]): reject("请从默认输出端口连接下游节点", "missing_outgoing")
        if node.type=="if_else":
            grouped="conditions" in node.inputs
            if not grouped and "left" not in node.inputs: reject("条件分支需要左侧值或条件组", "node_config")
            if grouped != ("logical_operator" in node.config) or grouped and ("left" in node.inputs or "right" in node.inputs):
                reject("条件组与旧单条件配置不能混合", "node_config")
        if node.type=="assign" and node.config["name"] not in workflow.variables: reject("赋值目标须先在全局变量中声明", "assignment_target")
        if node.type=="parameter_extractor": validate_json_schema(node.config["schema"])
        if node.type=="llm":
            messages=node.config.get("prompt_messages")
            if messages is not None:
                if "system_prompt" in node.config or (getattr(node,'input_schema',None) is None and node.inputs.get("prompt") not in (None, "")) or "prompt" in node.config.get("input_mapping",{}):
                    reject("LLM提示消息与旧提示字段不能同时使用", "node_config")
                if not any(item["role"]=="user" for item in messages) or any(item["role"]=="system" for item in messages[1:]):
                    reject("LLM提示需要User消息，System消息只能在首条", "node_config")
            elif "prompt" not in node.inputs and "message" not in node.inputs and "prompt" not in node.config.get("input_mapping",{}):
                reject("LLM节点需要User提示或消息输入", "node_config")
            response=node.config.get("response_format", {"type":"text"})
            if response["type"]=="json_schema" and "schema" not in response:reject("结构化输出需要JSON Schema", "node_config")
            if "schema" in response:validate_json_schema(response["schema"])
            if response["type"]=="text" and set(response)-{"type"}:reject("文本输出不接受Schema附加配置", "node_config")
            if response["type"]=="json_object" and response.get("strict"):
                reject("JSON对象模式不能声明原生严格Schema输出", "node_config")
        if node.type=="template" and ("{%" in node.config["template"] or "__" in node.config["template"]): reject("模板只支持字段插值，请移除函数和控制语句", "template_code")
        for name in input_schema(node,workflow).get("required",[]):
            if name not in node.inputs and 'default' not in input_schema(node,workflow).get('properties',{}).get(name,{}): reject(f"请填写必填输入「{name}」或为其配置默认值", "required_input")
        for field,value in node.inputs.items():
            expected=input_schema(node,workflow).get("properties",{}).get(field,{}).get("type")
            if not list(references(value)):
                field_schema=input_schema(node,workflow).get("properties",{}).get(field,{})
                if "$defs" in spec["input_schema"]: field_schema={**field_schema,"$defs":spec["input_schema"]["$defs"]}
                if next(Draft202012Validator(field_schema).iter_errors(value),None): reject("节点输入值与字段定义的类型或格式不符", "literal_input")
            for ref in references(value):
                parts=ref["$ref"].split(".");root=parts[0];actual=None
                if root=="nodes":
                    if len(parts)<2 or parts[1] not in ancestors[node.id]: reject("节点只能引用已连通的上游输出", "upstream_reference")
                    if parts[1] not in dominators[node.id] and (nodes[parts[1]].type in TRIGGERS or any(nodes[key].type in {"if_else","question_classifier"} or nodes[key].type in TRIGGERS for key in ancestors[parts[1]])) and not ref.get("optional"): reject("引用只在部分分支产生的输出时，请将该引用设为可选", "optional_reference")
                    actual=schema_type(output_schema(nodes[parts[1]],workflow),parts[2:])
                elif root=="inputs": actual=schema_type(workflow.input_schema,parts[1:])
                elif root=="vars":
                    if len(parts)<2 or parts[1] not in workflow.variables: reject("引用的全局变量尚未声明", "variable_reference")
                    actual=workflow.variables[parts[1]]["type"] if len(parts)==2 else None
                elif root=="user_vars":
                    if len(parts)<2 or not NAME.fullmatch(parts[1]):reject("自定义用户变量的引用格式无效", "user_variable_reference")
                elif root in ("system","user"):
                    fields={"system":{"run_id","tenant_id","workspace_id"},"user":{"id","role","permissions"}}
                    if len(parts)!=2 or parts[1] not in fields[root]: reject("引用的系统或用户只读字段不存在", "readonly_reference")
                    actual="array" if parts[1]=="permissions" else "string"
                elif root not in ("iteration","loop") or root!=scope: reject("变量引用超出节点可访问范围", "reference_scope")
                if value is ref and expected and actual:
                    target_types=set(expected if isinstance(expected,list) else [expected]);source_types=set(actual if isinstance(actual,list) else [actual])
                    if not source_types.issubset(target_types) and not (target_types=={'number'} and source_types=={'string'}):reject("引用的变量类型与此节点的输入类型不兼容", "reference_type")
        if node.type in ("iteration","loop"):
            try:
                validate_graph(WorkflowDefinition.model_validate(node.config["body"]),depth+1,node.type)
            except AppError as exc:
                if exc.code != "INVALID_WORKFLOW":
                    raise
                reject("请检查子流程。" + exc.as_dict()["message"], "subflow")
            for ref in references({k:v for k,v in node.config.items() if k not in ("body","until")}):
                if ref["$ref"].split(".")[0] not in ("inputs","vars","nodes"): reject("循环或迭代的初始值只能引用流程输入、全局变量或上游输出", "repeat_initializer")
            if node.type=="loop":
                condition=node.config["until"]
                if condition.get("operator") not in OPS or "left" not in condition: reject("请配置有效的循环退出条件", "loop_condition")
                for ref in references(condition):
                    if not ref["$ref"].startswith("loop.state.") and ref["$ref"]!="loop.index": reject("循环退出条件只能读取循环状态或序号", "loop_reference")
    writers={}
    for node in ordered:
        if node.type=="assign":
            for previous in writers.setdefault(node.config["name"],[]):
                if previous not in ancestors[node.id] and node.id not in ancestors[previous]:
                    raise AppError("VARIABLE_WRITE_CONFLICT", "并行节点不能同时修改同一全局变量，请通过连线确定写入顺序", 422)
            writers[node.config["name"]].append(node.id)
    return ordered
