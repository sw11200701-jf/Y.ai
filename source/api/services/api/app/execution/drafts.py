"""Draft admission uses the normal durable runner and current actor permissions."""
from copy import deepcopy
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from app.contracts import WorkflowDefinition, FactQuery, MessageEnvelope, RunCreate
from app.errors import AppError
from app.runner.variables import check_schema, check_size, check_variable, references, resolve
from app.runner.registry import definition


class TestVariables(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: dict[str, Any] = Field(default_factory=dict)
    vars: dict[str, Any] = Field(default_factory=dict)
    iteration: "IterationTestContext | None" = None
    loop: "LoopTestContext | None" = None


class IterationTestContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: Any
    index: int = Field(default=0, strict=True, ge=0)


class LoopTestContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: dict[str, Any]
    index: int = Field(default=0, strict=True, ge=0)


class NodeTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(min_length=1, max_length=128)
    scope_path: list[str] = Field(default_factory=list, max_length=3)
    variables: TestVariables = Field(default_factory=TestVariables)


class DebugTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow: WorkflowDefinition
    node_id: str = Field(min_length=1, max_length=128)
    scope_path: list[str] = Field(default_factory=list, max_length=3)


class DebugTargetDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_key: str
    node_id: str
    node_type: str
    scope_path: list[str]
    scope_type: Literal["iteration", "loop"] | None
    workflow_input_schema: dict[str, Any]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    upstream_schemas: dict[str, dict[str, Any]]
    variables_schema: dict[str, Any]
    scope_schema: dict[str, Any] | None
    limits: dict[str, int]


def select_target(workflow, test):
    """Resolve only actual structured-body IDs. Never infer an execution path."""
    from app.runner.variables import NAME
    current=workflow;kind=None;parent=None;parts=["root"]
    limits={"max_steps":workflow.limits.get("max_steps",256),
            "timeout_seconds":workflow.limits.get("timeout_seconds",120),
            "max_concurrency":workflow.limits.get("max_concurrency",4)}
    for identifier in test.scope_path:
        if not isinstance(identifier,str) or not NAME.fullmatch(identifier):
            raise AppError("INVALID_TEST_SCOPE", "测试作用域须使用真实循环节点标识", 422)
        parent=next((node for node in current.nodes if node.id==identifier),None)
        if parent is None or parent.type not in {"iteration","loop"} or not isinstance(parent.config.get("body"),dict):
            raise AppError("INVALID_TEST_SCOPE", "当前草稿中没有这条循环子流程路径", 422)
        kind=parent.type;parts.extend((parent.id,kind))
        limits["timeout_seconds"]=min(limits["timeout_seconds"],parent.config["timeout_seconds"])
        current=WorkflowDefinition.model_validate(parent.config["body"])
        for field in limits:limits[field]=min(limits[field],current.limits.get(field,{"max_steps":256,"timeout_seconds":120,"max_concurrency":4}[field]))
    target=next((node for node in current.nodes if node.id==test.node_id),None)
    if target is None:raise AppError("NODE_NOT_FOUND", "当前作用域中没有该测试节点", 422)
    return current,target,kind,parent,limits,"/".join(parts+[target.id])


def selected_snapshot(snapshot, scope_path):
    current=snapshot
    for identifier in scope_path:
        node=next((node for node in current["nodes"] if node["id"]==identifier),None)
        if node is None or node["type"] not in {"iteration","loop"}:
            raise AppError("INVALID_TEST_SCOPE", "持久草稿中的测试作用域已变化", 409)
        current=node["config"]["body"]
    return current


def describe_target(body):
    from app.runner.validation import validate_workflow
    from app.execution.storage import encode
    import hashlib
    check_size(body.workflow.model_dump(mode="json"),2097152,"测试草稿")
    validate_workflow(body.workflow)
    current,target,kind,parent,limits,key=select_target(body.workflow,body)
    known={node.id:node for node in current.nodes}
    refs={ref["$ref"].split(".")[1] for ref in references(target.inputs) if ref["$ref"].startswith("nodes.")}
    props={}
    for name,decl in current.variables.items():
        schema=deepcopy(decl)
        if decl["type"]=="number":
            schema={**schema,"oneOf":[{"type":"integer"},{"type":"string","pattern":r"^-?\d+(\.\d+)?$"}],"declared_type":"number"}
            schema.pop("type",None)
        props[name]=schema
    scope_schema=None
    if kind:
        scope_schema=(IterationTestContext if kind=="iteration" else LoopTestContext).model_json_schema()
        scope_schema["properties"]["index"]["maximum"]=parent.config["max_iterations"]-1
    return {"source_graph_sha256":hashlib.sha256(encode(body.workflow.model_dump(mode="json")).encode()).hexdigest(),
        "target_key":key,"node_id":target.id,"node_type":target.type,"scope_path":body.scope_path,"scope_type":kind,
        "workflow_input_schema":current.input_schema,"input_schema":definition(target.type)["input_schema"],
        "output_schema":definition(target.type)["output_schema"],
        "upstream_schemas":{name:definition(known[name].type)["output_schema"] for name in sorted(refs)},
        "variables_schema":{"type":"object","properties":props,"additionalProperties":False},
        "scope_schema":scope_schema,"limits":limits}


class DebugRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow: WorkflowDefinition
    agent_id: str | None = None
    revision: str | None = Field(default=None, max_length=128)
    inputs: dict[str, Any] = Field(default_factory=dict)
    prompt: str = Field(default="", max_length=16000)
    query: FactQuery = Field(default_factory=FactQuery)
    message: MessageEnvelope | None = None
    node_test: NodeTest | None = None

    def request(self):
        return RunCreate(workflow=self.workflow, draft_agent_id=self.agent_id,
            draft_revision=self.revision, run_kind="node_test" if self.node_test else "draft",
            node_test=self.node_test.model_dump(mode="json") if self.node_test else None,
            inputs=self.inputs, prompt=self.prompt, query=self.query, message=self.message)


def readonly_pool(context, run_id):
    from app.rbac import ALL
    return {"system": {"run_id": run_id, "tenant_id": context.tenant_id,
                       "workspace_id": context.workspace_id},
            "user": {"id": context.operator_id, "role": context.role,
                     "permissions": sorted(set(context.permissions) & ALL)[:128]}}


def variable_catalog(context):
    from app.rbac import ALL
    fields = [("system.run_id", "本次运行标识", {"type":"string"}),
        ("system.tenant_id", "当前租户", {"type":"string"}),
        ("system.workspace_id", "当前工作空间", {"type":"string"}),
        ("user.id", "当前用户标识", {"type":"string"}),
        ("user.role", "当前用户角色", {"type":"string","enum":["super_admin","admin","member","service"]}),
        ("user.permissions", "当前功能权限", {"type":"array","maxItems":128,"uniqueItems":True,"items":{"type":"string","enum":sorted(ALL)}})]
    return {"schema_version":"workflow-variables-v1", "items":[{"ref":ref,"label":label,"schema":schema,
        "readonly":True,"source":"server"} for ref,label,schema in fields]}


def seed_test(workflow, request, context, run_id):
    """Only business mock values are admitted; identity pools cannot be supplied."""
    test = NodeTest.model_validate(request.node_test)
    current,target,kind,parent,_,_=select_target(workflow,test)
    from app.runner.input_defaults import normalize_inputs
    request=request.model_copy(update={"inputs":normalize_inputs(request.inputs,current.input_schema,partial=True)})
    pool = {"inputs": deepcopy(request.inputs), "nodes": deepcopy(test.variables.nodes),
            "vars": {name: deepcopy(decl["default"]) for name, decl in current.variables.items() if "default" in decl},
            **readonly_pool(context, run_id)}
    known = {node.id: node for node in current.nodes}
    used = {ref["$ref"].split(".")[1] for ref in references(target.inputs)
            if ref["$ref"].startswith("nodes.")}
    if set(pool["nodes"]) - used:
        raise AppError("INVALID_TEST_VARIABLE", "只能填写此节点引用的上游输出", 422)
    for name, value in pool["nodes"].items():
        check_schema(value, definition(known[name].type)["output_schema"], "测试上游输出")
    for name, value in test.variables.vars.items():
        if name not in current.variables:
            raise AppError("INVALID_TEST_VARIABLE", "测试全局变量必须已在草稿中声明", 422)
        check_variable(value, current.variables[name])
        pool["vars"][name] = deepcopy(value)
    for scope_type in ("iteration","loop"):
        supplied=getattr(test.variables,scope_type)
        if supplied is not None:
            if scope_type!=kind:raise AppError("INVALID_TEST_SCOPE", "只能填写目标当前循环作用域的业务值", 422)
            if supplied.index>=parent.config["max_iterations"]:
                raise AppError("INVALID_TEST_SCOPE", "测试循环索引超过外围节点上限", 422)
            pool[scope_type]=supplied.model_dump(mode="json")
    check_size(pool, 1048576, "单节点测试变量")
    from app.execution.storage import encode
    encode(pool)  # reject secret/reference fields before persisting the request
    check_schema(resolve(target.inputs, pool), definition(target.type)["input_schema"], "测试节点输入")
    return pool
