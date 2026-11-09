"""Bounded, same-actor node projections. Never expose checkpoint/provider objects."""
import json
import re
from copy import deepcopy
from typing import Any
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.db import Resource, RunEventRecord, get_resource
from app.errors import AppError, chinese_message
from app.execution.models import ExecutionRun, ExecutionStep

PRIVATE = {"password", "passwordhash", "apikey", "authorization", "cookie", "cookies", "setcookie",
    "token", "accesstoken", "refreshtoken", "sessionid", "secret", "secretref", "credentialref",
    "credential", "credentials", "privatekey", "path", "filepath", "traceback", "stack",
    "reasoningcontent", "chainofthought", "rawresponse", "rawrequest", "systemprompt", "semanticfinalizereceipt", "versionsnapshotsha256", "finalstateref", "finalstatesha256",
    "readproof", "readproofsha256"}


class NodeTrace(BaseModel):
    node_id: str
    node_type: str | None = None
    sequence: int
    status: str
    inputs: Any = None
    outputs: Any = None
    error: dict | None = None
    started_at: str | None = None
    completed_at: str | None = None
    request_id: str | None = None
    reasoning: dict
    sources: Any = Field(default_factory=list)
    technical: dict = Field(default_factory=dict)


class TracePage(BaseModel):
    run_id: str
    execution_identity: dict | None = None
    items: list[NodeTrace]
    last_sequence: int
    has_more: bool


def clean(value, depth=0):
    if depth > 32: return {"truncated": True}
    if isinstance(value, dict):
        return {key: clean(item, depth+1) for key, item in value.items()
                if isinstance(key, str) and re.sub(r"[^a-z0-9]", "", key.lower()) not in PRIVATE}
    if isinstance(value, list): return [clean(item, depth+1) for item in value[:1000]]
    if isinstance(value, (str, int, float, bool)) or value is None: return value
    return None


def bounded(value, maximum=16384):
    value = clean(value)
    body = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(body.encode()) <= maximum: return value
    return {"truncated": True, "preview": body.encode()[:maximum-256].decode("utf-8", errors="ignore"),
            "original_bytes": len(body.encode())}


def authorize_view(session, context, run_id):
    run = get_resource(session, "run", run_id, context)  # current policy + derivation
    binding = session.get(ExecutionRun, run_id, populate_existing=True)
    if binding:
        actor = (binding.principal_id, binding.tenant_id, binding.workspace_id)
    else:
        row = session.scalar(select(Resource).where(Resource.kind == "run", Resource.resource_id == run_id,
            Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id))
        actor = (row.owner_id, row.tenant_id, row.workspace_id) if row else None
    if actor != (context.operator_id, context.tenant_id, context.workspace_id):
        raise AppError("NOT_FOUND", "运行不存在或无权访问", 404)
    from app.execution.storage import authorize_dependencies
    try: authorize_dependencies(session, context, run, models=True)
    except AppError as exc:
        if exc.status_code in {403,404,409}: raise AppError("NOT_FOUND", "运行或已固定资源不可访问", 404) from None
        raise
    return run


def reasoning_view(result):
    value = result.get("reasoning") if isinstance(result, dict) else None
    if isinstance(value, dict) and "profile_id" in value:
        from app.agents.reasoning_profiles import public_reasoning_view
        return public_reasoning_view(value)
    if isinstance(value, dict) and value.get("available") is False and value.get("status") == "not_adapted":
        return {"available":False,"status":"not_adapted","unavailable_reason":"此回复尚未选择明确的公开思考profile"}
    if (isinstance(value, dict) and value.get("available") is True and
        value.get("kind") in {"provider_summary", "provider_explanation"} and
        isinstance(value.get("text"), str) and value["text"] and len(value["text"])<=4000 and len(value["text"].encode())<=8192 and
        value.get("source") in {"provider.message.reasoning_summary","provider.message.reasoning_explanation"}):
        return {"available": True, "kind": value["kind"], "text": value["text"], "source": value["source"]}
    return {"available": False, "unavailable_reason": "该模型未提供可展示的思考过程"}


def trace_view(session, context, run_id, after=0, limit=50):
    run = authorize_view(session, context, run_id)
    where = (RunEventRecord.run_id == run_id, RunEventRecord.tenant_id == context.tenant_id,
             RunEventRecord.workspace_id == context.workspace_id)
    # Execution is bounded to <=512 steps. Select metadata first and page node
    # updates; internal engine events are excluded from the product view.
    rows = list(session.scalars(select(RunEventRecord).where(*where).order_by(RunEventRecord.sequence)))
    events = [json.loads(row.payload) for row in rows]
    updates = [event for event in events if event["sequence"] > after and event.get("node_id") and
               event["type"] in {"node.started", "node.completed", "node.skipped", "node.failed", "approval.requested"}]
    page = updates[:limit]
    ids = list(dict.fromkeys(event["node_id"] for event in page))
    nodes = {node["id"]: node for node in run.get("workflow_snapshot", {}).get("nodes", [])}
    result = []
    state_cache = {}
    for key in ids:
        history = [event for event in events if event.get("node_id") == key]
        latest = next((event for event in reversed(history) if event["type"] in
            {"node.completed", "node.skipped", "node.failed", "approval.requested", "node.started"}), {})
        started = next((event for event in reversed(history) if event["type"] == "node.started" and event["sequence"]<=latest.get("sequence",0)), None)
        data = latest.get("data", {})
        execution_id = data.get("execution_id") or (started or {}).get("data", {}).get("execution_id")
        if execution_id:
            step=session.get(ExecutionStep,execution_id)
            if step is None or step.run_id!=run_id or not (step.node_key==key or step.node_key.startswith(key+":")):
                raise AppError("TRACE_BINDING_INVALID", "节点轨迹与本次持久执行编号不一致", 409)
            if latest.get("type")=="node.completed" and step.status!="succeeded":
                raise AppError("TRACE_BINDING_INVALID", "节点结果尚未完成持久提交", 409)
        output = data.get("outputs") if latest.get("type") == "node.completed" else None
        if execution_id and latest.get("type")=="node.completed":
            from app.execution.storage import read_blob
            if step.result_ref not in state_cache:
                state_cache[step.result_ref]=read_blob(session,run_id,{"state_ref":step.result_ref})
            matches=[value for local in state_cache[step.result_ref].get("frames",{}).values()
                for node_id,value in local.get("pool",{}).get("nodes",{}).items() if local["prefix"]+node_id==key]
            if len(matches)!=1 or json.dumps(matches[0],ensure_ascii=False,sort_keys=True)!=json.dumps(output,ensure_ascii=False,sort_keys=True):
                raise AppError("TRACE_BINDING_INVALID", "节点事件与本次持久结果快照不一致", 409)
            output=matches[0]
        original_output = output
        node_type = (started or {}).get("data", {}).get("node_type") or nodes.get(key, {}).get("type")
        if node_type in {"llm", "parameter_extractor", "question_classifier"} and isinstance(output, dict):
            output = {name: output[name] for name in ("text", "mode", "usage", "structured_output", "parameters", "class_id", "completion_truncated", "finish_reason") if name in output}
        retrieval_sources = None
        if node_type == "knowledge_retrieval" and isinstance(output, dict):
            from app.knowledge.workflow_trace import project_workflow_retrieval
            actual_inputs = (started or {}).get("data", {}).get("inputs")
            if not isinstance(actual_inputs, dict) or not isinstance(actual_inputs.get("query"), str):
                output = {"retrieval": None, "unavailable_reason": "历史运行未保存本次解析后的检索输入，请重新运行节点"}
            else:
                sources = []
                for field, kind in (("file_id","document"),("file_ids","document"),("knowledge_base_ids","knowledge_base"),("version_ids","knowledge_version")):
                    identifiers=actual_inputs.get(field, [])
                    for identifier in identifiers if isinstance(identifiers,list) else [identifiers]:
                        sources.append({"kind":kind,"resource_id":identifier,"action":"use"})
                projection = project_workflow_retrieval(run_id=run_id, resolved_query=actual_inputs["query"],
                    result_snapshot=output, input_sources=sources, context=context, session=session)
                output={"retrieval":projection["retrieval"]}
                retrieval_sources=projection["sources"]
        error = data.get("error")
        if not error and run["status"] in {"failed", "outcome_unknown", "paused", "cancelled"} and latest.get("type") == "node.started":
            error = run.get("error")
        request_id = (error or {}).get("details", {}).get("request_id") if isinstance((error or {}).get("details"), dict) else None
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[a-f0-9]{16,64}", request_id) else run.get("request_id")
        failed_status = run["status"] if run["status"] in {"cancelled", "paused", "outcome_unknown"} else "failed"
        item = {"node_id": key, "node_type": node_type, "sequence": latest.get("sequence", 0),
            "status": failed_status if error else latest.get("status", "pending"),
            "inputs": bounded((started or {}).get("data", {}).get("inputs")), "outputs": bounded(output),
            "error": {"code": error.get("code", "INTERNAL_ERROR"), "message": chinese_message(error.get("code", "INTERNAL_ERROR")), "request_id": request_id} if error else None,
            "started_at": (started or {}).get("created_at"),
            "completed_at": latest.get("created_at") if latest.get("type") in {"node.completed", "node.failed", "node.skipped"} else None,
            "request_id": request_id, "reasoning": bounded(reasoning_view(original_output)),
            "sources": bounded(retrieval_sources if retrieval_sources is not None else (data.get("outputs") or {}).get("source_refs", []) if isinstance(data.get("outputs"), dict) else []),
            "technical": {"selected_port": data.get("selected_port"), "execution_id": execution_id}}
        if len(json.dumps(result+[item], ensure_ascii=False).encode()) > 258048: break  # reserve envelope bytes
        result.append(item)
    # Revalidate fresh policy before serializing a previously cached result.
    session.expire_all()
    authorize_view(session, context, run_id)
    returned_ids = {item["node_id"] for item in result}
    consumed = []
    for event in page:
        if event["node_id"] not in returned_ids: break
        consumed.append(event)
    cursor = consumed[-1]["sequence"] if consumed else after
    return {"run_id": run_id, "execution_identity": run.get("execution_identity"), "items": result,
            "last_sequence": cursor, "has_more": any(event["sequence"] > cursor for event in updates)}
