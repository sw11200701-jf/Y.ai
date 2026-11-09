"""Compile product nodes and structured-loop backedges into real LangGraph tasks."""
import time
from copy import deepcopy
from datetime import datetime, timezone
from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from app.contracts import WorkflowDefinition, RunCreate
from app.errors import AppError
from app.db import get_resource, save_resource, utc_now
from app.runner.graph import validate_graph, STARTS, TRIGGERS, TERMINALS
from app.runner.variables import resolve, check_schema, check_size, matches
from app.runner.registry import definition
from app.runner.runtime import GraphRuntime
from app.execution.contracts import ExecutionState, ExecutionContext


EFFECT_TYPES = {"plugin", "http", "query", "calculate", "report", "document_generate", "document_extractor", "llm", "parameter_extractor", "question_classifier"}
BUSINESS_TYPES = {"query", "calculate", "chart", "report", "database_query", "knowledge_retrieval", "document_extractor"}


def merge_snapshots(base, branches):
    """Three-way merge disjoint branch writes; completion order is irrelevant."""
    original = deepcopy(base)
    written = set()
    missing = object()
    def apply(before, after, target, path=()):
        for key in sorted(set(before) | set(after)):
            if not path and key in {"budget", "parallel_branch"}: continue
            old, new = before.get(key, missing), after.get(key, missing)
            if old is not missing and new is not missing and type(old) is type(new) and old == new: continue
            current_path = path+(key,)
            if new is missing:
                raise AppError("PARALLEL_STATE_CONFLICT", "并行分支不能删除共享状态", 422)
            if isinstance(old, dict) and isinstance(new, dict):
                apply(old, new, target.setdefault(key, {}), current_path)
            elif old is missing and isinstance(new, dict):
                apply({}, new, target.setdefault(key, {}), current_path)
            else:
                if current_path in written:
                    raise AppError("OUTPUT_FIELD_CONFLICT" if "outputs" in current_path else "PARALLEL_STATE_CONFLICT", "多个执行节点写入同名字段："+".".join(current_path)+"；请修改字段名称或通过连线确定顺序", 422)
                written.add(current_path)
                target[key] = deepcopy(new)
    for branch in branches: apply(original, branch, base)


def bind_sources(execution, run, node, values, outputs=None):
    from app.data_access import record_derivation
    sources = []
    if node.type == "database_query":
        sources.append({"kind": "data_table", "resource_id": values["table_id"], "action": "use"})
        version_id = (outputs or {}).get("version_id") or node.config.get("table_version_id")
        if version_id: sources.append({"kind": "data_table_version", "resource_id": version_id, "action": "use"})
    if node.type in {"knowledge_retrieval", "document_extractor"}:
        for field, kind in (("file_id", "document"), ("file_ids", "document"), ("knowledge_base_ids", "knowledge_base"), ("version_ids", "knowledge_version")):
            resource_ids = values.get(field, [])
            for resource_id in resource_ids if isinstance(resource_ids, list) else [resource_ids]: sources.append({"kind": kind, "resource_id": resource_id, "action": "use"})
    if node.type == "query":
        batches = [item["batch_id"] for item in values.get("sources", []) if item.get("type")=="batch"]
        if values.get("query", {}).get("batch_id"): batches.append(values["query"]["batch_id"])
        for batch_id in batches: sources.append({"kind": "knowledge_batch" if batch_id.startswith("knowledge_batch_") else "import_batch", "resource_id": batch_id, "action": "use"})
    sources.extend((outputs or {}).get("data_sources", []))
    if sources:
        record_derivation(session=execution.session, context=execution.context, kind="run", resource_id=run["id"], sources=sources)
        existing = run.setdefault("data_sources", [])
        for source in sources:
            if source not in existing: existing.append(source)
        if not execution.state.get("parallel_branch"):
            save_resource(execution.session, "run", run["id"], run, execution.context)


def frame(workflow, inputs, *, prefix="", scope=None):
    from app.runner.input_defaults import normalize_inputs
    inputs=normalize_inputs(inputs,workflow.input_schema,partial=True)
    pool = {"inputs": deepcopy(inputs), "nodes": {}, "vars": {name: deepcopy(decl["default"]) for name, decl in workflow.variables.items() if "default" in decl}}
    if scope: pool[scope[0]] = scope[1]
    return {"pool": pool, "outputs": {}, "edges": {}, "prefix": prefix, "repeats": {}}


def enabled(workflow, node, local, message):
    if local.get("test_node_id"): return node.id == local["test_node_id"]
    if node.type in TRIGGERS: return node.type == "trigger_" + message["type"]
    if node.type in STARTS: return True
    flags = [local["edges"].get(edge_key(edge), False) for edge in workflow.edges if edge.target == node.id]
    return all(flags) if node.config.get("join", "any") == "all" else any(flags)


def edge_key(edge):
    return "|".join((edge.source, edge.target, edge.source_port))


def finish_product(execution, workflow, node, local, run, result, selected=None):
    from app.runner.service import emit
    from app.execution.storage import check_active_run
    check_active_run(execution.session, execution.run_id)
    key = local["prefix"] + node.id
    check_size(result, 1048576, "node output")
    from app.runner.fields import business,project,output_schema
    if not business(workflow,node) or node.type not in STARTS|TERMINALS|{"transform"}:check_schema(result, definition(node.type)["output_schema"], node.type+" outputs")
    protocol_result=result
    result=project(node,workflow,result)
    if business(workflow,node):check_schema(result,output_schema(node,workflow),node.id+' 输出')
    local["pool"]["nodes"][node.id] = result
    check_size(local["pool"], 4194304, "variable pool")
    if node.type in TERMINALS and (node.type != "end" or business(workflow,node)):
        duplicate=set(local["outputs"])&set(protocol_result["values"])
        if duplicate:raise AppError("OUTPUT_FIELD_CONFLICT","多个已执行结束节点返回同名字段："+", ".join(sorted(duplicate)),422)
        local["outputs"].update(protocol_result["values"])
    if local.get("test_node_id"): local["outputs"] = deepcopy(result)
    # Every branch owns its result. The short merge transaction publishes a
    # coherent pool; branches must not overwrite another branch's projection.
    local.setdefault("presentations", {})
    if node.type == "output" and not local["prefix"]:
        local["presentations"][node.id] = {"node_id": key, **protocol_result["presentation"]}
    for edge in workflow.edges:
        if edge.source == node.id: local["edges"][edge_key(edge)] = selected is None or selected == edge.source_port
    if not local["prefix"] and not execution.state.get("parallel_branch"):
        run.update(variable_pool=local["pool"], outputs=local["outputs"], updated_at=utc_now())
        run["presentations"] = [local["presentations"][name] for name in sorted(local["presentations"])]
    if not execution.state.get("parallel_branch"):
        save_resource(execution.session, "run", run["id"], run, execution.context)
    else:
        execution.state.setdefault("run_updates", {})[key] = {field: deepcopy(run[field]) for field in ("data_sources", "dataset_id", "dataset_ids", "chart_spec", "artifact_ids", "mode") if field in run}
    emit(execution.session, run["id"], execution.context, "node.completed", key, "succeeded", {"outputs": result, "selected_port": selected, "execution_id": execution.execution_id})


class ProductCompiler:
    def __init__(self, run, bridge):
        self.run = run
        self.bridge = bridge
        self.request = RunCreate.model_validate(run["request"])
        self.graph = StateGraph(ExecutionState, context_schema=ExecutionContext)

    def key(self, state, scope, node_id, suffix=""):
        local = self.bridge.read(state)["frames"][scope]
        return local["prefix"] + node_id + suffix

    def ordinary(self, workflow, node, scope, depth):
        async def execute(state, runtime: Runtime[ExecutionContext]):
            key = self.key(state, scope, node.id)
            async def model_preflight(execution):
                if node.type == 'document_extractor':
                    local = execution.state['frames'][scope]
                    run = get_resource(execution.session, 'run', execution.run_id, execution.context)
                    if enabled(workflow, node, local, run['message']):
                        from app.imports.documents import validate_extraction_request
                        from app.knowledge.execution_scope import bind_execution
                        values = resolve(node.inputs, local['pool'])
                        check_schema(values, definition(node.type)['input_schema'], 'document inputs')
                        with bind_execution(execution):
                            validate_extraction_request(file_id=values['file_id'], context=execution.context,
                                                        session=execution.session, **node.config)
                    return
                if node.type=='document_generate':
                    local=execution.state['frames'][scope]
                    run=get_resource(execution.session,'run',execution.run_id,execution.context)
                    if enabled(workflow,node,local,run['message']):
                        from app.reporting.document_nodes import validate_request
                        values=resolve(node.inputs,local['pool'])
                        check_schema(values,definition(node.type)['input_schema'],'document inputs')
                        validate_request(node.config,values,context=execution.context,session=execution.session,run_id=execution.run_id)
                    return
                if node.type not in {"llm", "parameter_extractor", "question_classifier"} or node.config.get("mode") == "demo":
                    return
                local = execution.state["frames"][scope]
                run = get_resource(execution.session, "run", execution.run_id, execution.context)
                if not enabled(workflow, node, local, run["message"]):
                    return
                expected = node.config.get("model_config_version")
                if not expected:
                    raise AppError("MODEL_VERSION_CHANGED", "历史运行未固定模型版本，请重新创建运行", 409)
                from app.model_catalog.service import resolve_runtime_model
                resolved_model=resolve_runtime_model(node.config.get("model_config_id"), context=execution.context,
                    session=execution.session, expected_version=expected)
                if node.type == "llm":
                    # Pure request rendering is known to precede every model
                    # request. Keep it inside the trusted preflight callback;
                    # post-issue AppErrors still follow the existing ledger.
                    raw_values = resolve(node.inputs, local["pool"])
                    from app.runner.fields import service_values
                    values = service_values(node,raw_values)
                    check_schema(values, definition("llm")["input_schema"], "llm inputs")
                    from app.model_catalog.preflight import validate_llm_prompt_request
                    validate_llm_prompt_request(config=node.config, inputs=values)
                from app.execution.model_reservations import admit_model_context
                from app.runner.fields import service_values
                admit_model_context(execution,config=node.config,inputs=service_values(node,resolve(node.inputs,local["pool"])),resolved=resolved_model,node_type=node.type)
            async def operation(execution):
                from app.runner.service import emit
                snapshot = execution.state
                local = snapshot["frames"][scope]
                run = get_resource(execution.session, "run", execution.run_id, execution.context)
                if not enabled(workflow, node, local, run["message"]):
                    for edge in workflow.edges:
                        if edge.source == node.id: local["edges"][edge_key(edge)] = False
                    emit(execution.session, execution.run_id, execution.context, "node.skipped", key, "skipped", {"reason": "inactive branch"})
                    return snapshot
                raw_values = resolve(node.inputs, local["pool"])
                from app.runner.fields import service_values,business,service_config
                if node.type in STARTS and business(workflow,node):raw_values=deepcopy(local["pool"]["inputs"])
                values = service_values(node,raw_values)
                if workflow.schemaVersion == "1.0":
                    carried = snapshot.setdefault("legacy", {})
                    if node.type == "plugin": values = {"arguments": {"entity": self.request.query.entity, "periods": self.request.query.periods, **node.config.get("arguments", {})}}
                    if node.type == "calculate": values = {"facts": carried.get("facts", [])}
                    if node.type in {"chart", "report"}: values = {"dataset": carried["dataset"]}
                else: check_schema(values, definition(node.type)["input_schema"], node.type+" inputs")
                emit(execution.session, execution.run_id, execution.context, "node.started", key, "running", {"node_type": node.type, "scope": local["prefix"], "inputs": raw_values, "execution_id": execution.execution_id})
                execution.session.commit()  # never hold an event writer while awaiting a node
                legacy = GraphRuntime(request=self.request, run=run, context=execution.context, session=execution.session, invoke=execution.invoke, event=lambda *args: emit(execution.session, execution.run_id, execution.context, *args), persist=lambda: None)
                # One business dispatch only. Structured control and Agent calls are
                # handled by native graph nodes below, never legacy execute().
                if node.type=='document_generate':
                    wrapped=await execution.invoke('app.reporting.document_nodes','execute_node',node_type=node.type,config=service_config(node),inputs=values,run_id=execution.run_id)
                    result,selected=wrapped['outputs'],None
                    from app.reporting.completed_documents import artifact_receipt
                    execution.state.setdefault('document_node_receipts',{})[execution.execution_id]=[
                        artifact_receipt(get_resource(execution.session,'artifact',item['id'],execution.context),execution.execution_id)
                        for item in result['artifacts']]
                    # Renderer registration is in this same session. Refresh the
                    # run before normal projection so its attachment IDs survive.
                    run=get_resource(execution.session,'run',execution.run_id,execution.context)
                elif workflow.schemaVersion == "1.0" and node.type == "query":
                    from app.contracts import FinancialFact
                    facts = await execution.invoke("app.finance.service", "query_facts", query=self.request.query)
                    result, selected = {"facts": [FinancialFact.model_validate(item).model_dump(mode="json") for item in facts], "source_refs": sorted({item.source_ref if hasattr(item, "source_ref") else item["source_ref"] for item in facts})}, None
                elif node.type in BUSINESS_TYPES:
                    from app.finance.execution_adapter import execute_business_node
                    if node.type in {"query", "calculate"}:
                        if business(workflow,node):
                            if 'query' not in values:raise AppError('BUSINESS_INPUT_REQUIRED','Finance nodes require an explicit query input',422)
                        else:values.setdefault("query", self.request.query.model_dump(mode="json"))
                    bind_sources(execution, run, node, values)
                    execution.session.commit()  # derivation is durable before network/model work
                    wrapped = await execute_business_node(execution=execution, node_type=node.type, config=service_config(node), inputs=values)
                    result, selected = wrapped["outputs"], wrapped.get("selected_port")
                    bind_sources(execution, run, node, values, result)
                    if node.type == "calculate":
                        run["dataset_id"] = result["dataset_id"]
                        if result["dataset_id"] not in run.setdefault("dataset_ids", []): run["dataset_ids"].append(result["dataset_id"])
                    if node.type == "chart": run["chart_spec"] = result["chart_spec"]
                    if node.type == "report":
                        for artifact_id in result["artifact_ids"]:
                            if artifact_id not in run.setdefault("artifact_ids", []): run["artifact_ids"].append(artifact_id)
                else:
                    model_node=node.type in {'llm','parameter_extractor','question_classifier'} and node.config.get('mode')!='demo'
                    if model_node:
                        from app.application_runtime import model_attempt
                        model_attempt(execution)
                    result, selected = await legacy.dispatch(node, values, local["pool"], workflow, key, depth)
                    if model_node:
                        from app.application_runtime import model_usage
                        model_usage(execution,result.get('usage'))
                if workflow.schemaVersion == "1.0":
                    if node.type in {"plugin", "query"}: snapshot["legacy"]["facts"] = result["facts"]
                    if node.type == "calculate": snapshot["legacy"]["dataset"] = result["dataset"]
                finish_product(execution, workflow, node, local, run, result, selected)
                return snapshot
            return await runtime.context.bridge.step(state, node_key=key, operation=operation, side_effect=node.type in EFFECT_TYPES,
                tool_call=node.type in {"plugin", "http"} and enabled(workflow, node, runtime.context.bridge.read(state)["frames"][scope], self.run["message"]),
                preflight=model_preflight if node.type in {"llm", "parameter_extractor", "question_classifier", "document_generate", "document_extractor"} else None)
        return execute

    def branch(self, operation, branch_name, scope, node_id):
        async def execute(state, config, runtime: Runtime[ExecutionContext]):
            # Marker lives in the private blob, never in LangGraph channels.
            async def mark(execution):
                execution.state["parallel_branch"] = True
                return execution.state
            marked = await runtime.context.bridge.step(state, node_key=self.key(state, scope, node_id)+":fork", operation=mark)
            if hasattr(operation, "ainvoke"):
                result = await operation.ainvoke(marked, config=config, context=runtime.context)
            else: result = await operation(marked, runtime)
            return {"branch_refs": {branch_name: result["state_ref"]}}
        return execute

    def join(self, names, join_key, scope):
        async def execute(state, runtime: Runtime[ExecutionContext]):
            async def operation(execution):
                from app.execution.storage import read_blob
                snapshot = execution.state
                branches = [read_blob(execution.session, execution.run_id, {"state_ref": state["branch_refs"][name]}) for name in sorted(names)]
                merge_snapshots(snapshot, branches)
                if snapshot.get("parallel_branch"): return snapshot  # an enclosing branch owns publication
                snapshot.pop("parallel_branch", None)
                run = get_resource(execution.session, "run", execution.run_id, execution.context)
                for _, changes in sorted(snapshot.pop("run_updates", {}).items()):
                    for field, value in changes.items():
                        if field in {"data_sources", "artifact_ids", "dataset_ids"}:
                            existing = run.setdefault(field, [])
                            for item in value:
                                if item not in existing: existing.append(item)
                        elif field == "mode":
                            if value == "model": run[field] = value
                        else: run[field] = value
                local = snapshot["frames"]["root"]
                run.update(variable_pool=local["pool"], outputs=local["outputs"], updated_at=utc_now())
                run["presentations"] = [local.get("presentations", {})[name] for name in sorted(local.get("presentations", {}))]
                save_resource(execution.session, "run", execution.run_id, run, execution.context)
                return snapshot
            prefix = self.bridge.read(state)["frames"][scope]["prefix"]
            return await runtime.context.bridge.step(state, node_key=prefix+join_key, operation=operation)
        return execute

    def repeat_enter(self, workflow, node, scope, child_scope):
        async def execute(state, runtime: Runtime[ExecutionContext]):
            key = self.key(state, scope, node.id)
            async def operation(execution):
                from app.runner.service import emit
                snapshot = execution.state
                local = snapshot["frames"][scope]
                run = get_resource(execution.session, "run", execution.run_id, execution.context)
                active = enabled(workflow, node, local, run["message"])
                record = {"active": active, "index": 0, "done": not active, "results": [], "deadline": time.time()+node.config["timeout_seconds"]}
                local["repeats"][node.id] = record
                if not active:
                    for edge in workflow.edges:
                        if edge.source == node.id: local["edges"][edge_key(edge)] = False
                    emit(execution.session, execution.run_id, execution.context, "node.skipped", key, "skipped", {})
                    return snapshot
                raw_values = resolve(node.inputs, local["pool"])
                from app.runner.fields import service_values
                values = service_values(node,raw_values)
                check_schema(values, definition(node.type)["input_schema"], "repeat inputs")
                if node.type == "iteration":
                    record["items"] = values["items"]
                    if len(values["items"]) > node.config["max_iterations"]: raise AppError("ITERATION_LIMIT", "输入超过迭代上限", 422)
                    record["done"] = not bool(values["items"])
                else: record["state"] = values.get("state", resolve(node.config.get("state", {}), local["pool"]))
                emit(execution.session, execution.run_id, execution.context, "node.started", key, "running", {"node_type": node.type, "inputs": raw_values, "execution_id": execution.execution_id})
                if not record["done"]: self.seed_child(snapshot, node, local, child_scope)
                return snapshot
            return await runtime.context.bridge.step(state, node_key=key+":enter", operation=operation)
        return execute

    def seed_child(self, snapshot, node, local, child_scope):
        record = local["repeats"][node.id]
        index = record["index"]
        item = {"item": record["items"][index], "index": index} if node.type == "iteration" else {"state": record["state"], "index": index}
        child = WorkflowDefinition.model_validate(node.config["body"])
        snapshot["frames"][child_scope] = frame(child, self.request.inputs, prefix=f"{local['prefix']}{node.id}[{index}].", scope=(node.type, item))
        snapshot["frames"][child_scope]["pool"]["user_vars"]=deepcopy(local["pool"].get("user_vars",{}))

    def repeat_advance(self, workflow, node, scope, child_scope):
        async def execute(state, runtime: Runtime[ExecutionContext]):
            snapshot = self.bridge.read(state)
            local = snapshot["frames"][scope]
            index = local["repeats"][node.id]["index"]
            key = local["prefix"]+node.id
            async def operation(execution):
                from app.runner.service import emit
                snapshot = execution.state
                local = snapshot["frames"][scope]
                record = local["repeats"][node.id]
                if time.time() > record["deadline"]: raise AppError("RUN_TIMEOUT", "循环已超过截止时间", 504)
                child_outputs = snapshot["frames"][child_scope]["outputs"]
                emit(execution.session, execution.run_id, execution.context, "iteration.completed", key, "succeeded", {"index": index, "kind": node.type})
                if node.type == "iteration": record["results"].append(child_outputs)
                else:
                    record["state"] = child_outputs
                    condition = resolve(node.config["until"], {"loop": {"state": child_outputs, "index": index}})
                    record["done"] = matches(condition["left"], condition["operator"], condition.get("right"))
                record["index"] += 1
                if node.type == "iteration": record["done"] = record["index"] >= len(record["items"])
                if not record["done"]:
                    if record["index"] >= node.config["max_iterations"]: raise AppError("ITERATION_LIMIT", "循环到达上限仍未满足结束条件", 422)
                    self.seed_child(snapshot, node, local, child_scope)
                return snapshot
            return await runtime.context.bridge.step(state, node_key=f"{key}:advance:{index}", operation=operation)
        return execute

    def repeat_finish(self, workflow, node, scope):
        async def execute(state, runtime: Runtime[ExecutionContext]):
            key = self.key(state, scope, node.id)
            async def operation(execution):
                snapshot = execution.state
                local = snapshot["frames"][scope]
                record = local["repeats"][node.id]
                if record["active"]:
                    result = {"items": record["results"], "count": record["index"]} if node.type == "iteration" else {"state": record["state"], "count": record["index"]}
                    run = get_resource(execution.session, "run", execution.run_id, execution.context)
                    finish_product(execution, workflow, node, local, run, result)
                return snapshot
            return await runtime.context.bridge.step(state, node_key=key+":finish", operation=operation)
        return execute

    def agent_seed(self, workflow, node, scope, agent_scope):
        async def execute(state, runtime: Runtime[ExecutionContext]):
            key = self.key(state, scope, node.id)
            def admitted_values(execution):
                run = get_resource(execution.session, "run", execution.run_id, execution.context)
                local = execution.state["frames"][scope]
                if not enabled(workflow, node, local, run["message"]):
                    return None
                raw_values = resolve(node.inputs, local["pool"])
                from app.runner.fields import service_values
                values = service_values(node,raw_values)
                check_schema(values, definition("agent")["input_schema"], "agent inputs")
                values.setdefault("prompt", "")
                values.setdefault("inputs", {})
                return values

            async def preflight(execution):
                values = admitted_values(execution)
                if values is None:
                    return
                agent_config = self.run.get("agent_snapshots", {}).get(agent_scope, {})
                if agent_config.get("mode") == "model":
                    expected = agent_config.get("model_config_version")
                    if not expected:
                        raise AppError("MODEL_VERSION_CHANGED", "历史智能体运行未固定模型版本，请重新创建运行", 409)
                    from app.model_catalog.service import resolve_runtime_model
                    resolve_runtime_model(agent_config.get("model_config_id"), context=execution.context,
                        session=execution.session, expected_version=expected)
                if values.get("message") is None:
                    return
                from app.messages import resolve_message
                from app.execution.message_inputs import select_message_attachments
                from pydantic import ValidationError
                raw = values["message"]
                if not isinstance(raw, dict):
                    raise AppError("MESSAGE_INVALID", "智能体消息必须是消息对象", 422)
                envelope = {k: raw[k] for k in ("type", "text", "attachment_ids", "inputs") if k in raw}
                try:
                    message = await resolve_message(message=envelope, prompt=values["prompt"], inputs=values["inputs"],
                                                    context=execution.context, session=execution.session)
                except ValidationError as exc:
                    raise AppError("MESSAGE_INVALID", "智能体消息类型或内容不符合要求", 422) from exc
                if values["prompt"] and message["text"] and values["prompt"] != message["text"]:
                    raise AppError("MESSAGE_INPUT_CONFLICT", "消息文本与智能体问题冲突", 422)
                if message["capabilities"]["requires_vision"] or message["capabilities"]["requires_audio"]:
                    raise AppError("MODEL_CAPABILITY_UNAVAILABLE", "视觉或语音适配尚未配置", 503)
                with select_message_attachments(execution, message["attachment_ids"]) as selected:
                    for attachment_id in message["attachment_ids"]:
                        selected.load(attachment_id=attachment_id, context=execution.context, session=execution.session)

            async def operation(execution):
                from app.runner.service import emit
                snapshot = execution.state
                local = snapshot["frames"][scope]
                run = get_resource(execution.session, "run", execution.run_id, execution.context)
                active = enabled(workflow, node, local, run["message"])
                snapshot.setdefault("agent_enabled", {})[agent_scope] = active
                if not active:
                    for edge in workflow.edges:
                        if edge.source == node.id: local["edges"][edge_key(edge)] = False
                    emit(execution.session, execution.run_id, execution.context, "node.skipped", key, "skipped", {})
                    return snapshot
                raw_values = resolve(node.inputs, local["pool"])
                from app.runner.fields import service_values
                values = service_values(node,raw_values)
                check_schema(values, definition("agent")["input_schema"], "agent inputs")
                from app.runner.fields import business
                if not business(workflow,node):values.setdefault("query", self.request.query.model_dump(mode="json"))
                values.setdefault("inputs", {})
                values.setdefault("prompt", "")
                if values.get("message") is not None:
                    from app.agents.messages import agent_message
                    from app.execution.message_inputs import select_message_attachments
                    # Actual adapter resolves selected authorized attachment evidence.
                    raw = values.pop("message")
                    with select_message_attachments(execution, raw.get("attachment_ids", [])):
                        message = await agent_message(message=raw, prompt=values["prompt"], inputs=values["inputs"], context=execution.context, session=execution.session)
                    values["message_context"] = message
                    values.update(prompt=message["text"], inputs=message["inputs"])
                if self.run.get('application_history'):
                    # Stored pairs were bounded and reauthorized by admission;
                    # run derivations fence their sources again before each step.
                    values['application_history']=deepcopy(self.run['application_history'])
                snapshot.setdefault("agents", {})[agent_scope] = {"input": values}
                snapshot.setdefault("dynamic_scopes", {})[agent_scope] = key+":agent"
                emit(execution.session, execution.run_id, execution.context, "node.started", key, "running", {"node_type": "agent", "inputs": raw_values, "execution_id": execution.execution_id})
                return snapshot
            return await runtime.context.bridge.step(state, node_key=key+":seed", operation=operation, side_effect=True, preflight=preflight)
        return execute

    def agent_finish(self, workflow, node, scope, agent_scope):
        async def execute(state, runtime: Runtime[ExecutionContext]):
            key = self.key(state, scope, node.id)
            async def operation(execution):
                snapshot = execution.state
                local = snapshot["frames"][scope]
                if snapshot["agent_enabled"][agent_scope]:
                    output = snapshot["agents"][agent_scope]["output"]
                    run = get_resource(execution.session, "run", execution.run_id, execution.context)
                    if output.get("mode") == "model": run["mode"] = "model"
                    finish_product(execution, workflow, node, local, run, output)
                return snapshot
            return await runtime.context.bridge.step(state, node_key=key+":collect", operation=operation)
        return execute

    def add_workflow(self, workflow, scope="root", depth=0):
        if workflow.schemaVersion == "1.0":
            from app.runner.validation import validate_legacy
            ordered = validate_legacy(workflow)
        else:
            validation_scope=scope.rsplit("/",1)[-1] if depth else None
            if scope=="root" and self.request.node_test:
                from app.execution.drafts import NodeTest,select_target
                _,_,validation_scope,_,_,_=select_target(WorkflowDefinition.model_validate(self.run["workflow_snapshot"]),NodeTest.model_validate(self.request.node_test))
            ordered = validate_graph(workflow, depth, validation_scope)
        if scope == "root" and self.request.node_test:
            ordered = [node for node in ordered if node.id == self.request.node_test["node_id"]]
        levels = {}
        for node in ordered:
            levels[node.id] = 0 if self.request.node_test and scope == "root" else max((levels[edge.source]+1 for edge in workflow.edges if edge.target == node.id), default=0)
        groups = [sorted((item for item in ordered if levels[item.id] == level), key=lambda item: item.id)
                  for level in sorted(set(levels.values()))]
        parallel_ids = {node.id for group in groups if len(group)>1 for node in group}
        entries, exits = {}, {}
        for node in ordered:
            name = scope.replace("/", "__")+"__"+node.id
            entry = exit = name
            outer_graph = None
            if node.id in parallel_ids and node.type in {"agent", "iteration", "loop"}:
                outer_graph, self.graph = self.graph, StateGraph(ExecutionState, context_schema=ExecutionContext)
            if node.type in {"iteration", "loop"}:
                child_scope = scope+"/"+node.id+"/"+node.type
                self.graph.add_node(name, self.repeat_enter(workflow, node, scope, child_scope))
                child_first, child_last = self.add_workflow(WorkflowDefinition.model_validate(node.config["body"]), child_scope, depth+1)
                advance, exit = name+"__advance", name+"__finish"
                self.graph.add_node(advance, self.repeat_advance(workflow, node, scope, child_scope))
                self.graph.add_node(exit, self.repeat_finish(workflow, node, scope))
                def route(state, scope=scope, node_id=node.id):
                    return "done" if self.bridge.read(state)["frames"][scope]["repeats"][node_id]["done"] else "body"
                self.graph.add_conditional_edges(name, route, {"done": exit, "body": child_first})
                self.graph.add_edge(child_last, advance)
                self.graph.add_conditional_edges(advance, route, {"done": exit, "body": child_first})
            elif node.type == "agent":
                agent_scope = scope+"/"+node.id
                config = self.run["agent_snapshots"][agent_scope]
                if config.get("main_workflow_id"): raise AppError("NODE_UNAVAILABLE", "嵌套主流程智能体尚未通过持久化预算适配验证", 503)
                from app.agents.langgraph_runtime import build_agent_subgraph
                self.graph.add_node(name, self.agent_seed(workflow, node, scope, agent_scope))
                child_name, exit = name+"__agent", name+"__collect"
                self.graph.add_node(child_name, build_agent_subgraph(config=config, bridge=self.bridge, scope=agent_scope).compile(checkpointer=True))
                self.graph.add_node(exit, self.agent_finish(workflow, node, scope, agent_scope))
                def route_agent(state, agent_scope=agent_scope):
                    return "child" if self.bridge.read(state)["agent_enabled"][agent_scope] else "done"
                self.graph.add_conditional_edges(name, route_agent, {"child": child_name, "done": exit})
                self.graph.add_edge(child_name, exit)
            elif node.type == "human_approval":
                from app.execution.approvals import approval_node
                operation = approval_node(self, workflow, node, scope)
                self.graph.add_node(name, self.branch(operation, name, scope, node.id) if node.id in parallel_ids else operation)
            else:
                operation = self.ordinary(workflow, node, scope, depth)
                self.graph.add_node(name, self.branch(operation, name, scope, node.id) if node.id in parallel_ids else operation)
            if outer_graph is not None:
                self.graph.add_edge(START, entry)
                self.graph.add_edge(exit, END)
                subgraph = self.graph.compile(checkpointer=True)
                self.graph = outer_graph
                self.graph.add_node(name, self.branch(subgraph, name, scope, node.id))
                entry = exit = name
            entries[node.id], exits[node.id] = entry, exit
        first = previous = None
        for index, group in enumerate(groups):
            if len(group) == 1:
                entry, exit = entries[group[0].id], exits[group[0].id]
                if previous: self.graph.add_edge(previous, entry)
                if first is None: first = entry
            else:
                # A durable fork provides one stable input to every branch.
                entry = scope.replace("/", "__")+f"__fork_{index}"
                async def passthrough(state): return {"state_ref": state["state_ref"]}
                self.graph.add_node(entry, passthrough)
                if previous: self.graph.add_edge(previous, entry)
                if first is None: first = entry
                names = [entries[node.id] for node in group]
                exit = scope.replace("/", "__")+f"__join_{index}"
                self.graph.add_node(exit, self.join(names, f"__join_{index}", scope))
                for node in group: self.graph.add_edge(entry, entries[node.id])
                self.graph.add_edge([exits[node.id] for node in group], exit)
            previous = exit
        return first, previous

    def compile(self, saver):
        workflow = WorkflowDefinition.model_validate(self.run["workflow_snapshot"])
        depth=0
        if self.request.node_test:
            from app.execution.drafts import NodeTest,select_target
            test=NodeTest.model_validate(self.request.node_test)
            workflow,_,_,_,_,_=select_target(workflow,test)
            depth=len(test.scope_path)
        first, last = self.add_workflow(workflow,depth=depth)
        async def completion(state):
            from app.execution.storage import commit_product_completion
            return commit_product_completion(self.bridge,state)
        self.graph.add_node('__product_complete',completion)
        self.graph.add_edge(START, first)
        self.graph.add_edge(last,'__product_complete')
        self.graph.add_edge('__product_complete',END)
        return self.graph.compile(checkpointer=saver)
