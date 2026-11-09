"""Legacy-shaped Agent debug uses the same durable parent, ledger and child graph."""
import asyncio
from copy import deepcopy
from app.contracts import WorkflowDefinition, RunCreate
from app.db import SessionLocal, get_resource
from app.errors import AppError


async def execute_agent_debug(*, config: dict, prompt: str, query, inputs: dict,
                              context, session, message=None) -> dict:
    """Trusted03 adapter only. Config must already pass03 AgentWrite/dependencies.

    Creates a normal private run, returns the old Agent output plus run_id. No
    MemorySaver, hidden model loop or separate queue is used by this entry point.
    """
    from app.runner.service import scheduler
    from app.agents.specs import AgentWrite
    from app.agents.data_tools import authorize_scope
    from app.plugins.permissions import require_ai_access
    require_ai_access(context, session)
    public = {key: value for key, value in config.items() if key in AgentWrite.model_fields}
    AgentWrite.model_validate(public)
    authorize_scope(config, context, session)
    definition = WorkflowDefinition.model_validate({"id": "debug", "name": "智能体调试", "schemaVersion": "2.0", "nodes": [
        {"id": "start", "type": "start"},
        {"id": "debug_agent", "type": "agent", "config": {"agent_id": "debug_frozen"}, "inputs": {"prompt": prompt, "query": query.model_dump(mode="json") if hasattr(query, "model_dump") else query, "inputs": inputs, **({"message": message} if message is not None else {})}},
        {"id": "output", "type": "output", "inputs": {"result": {"$ref": "nodes.debug_agent"}}}], "edges": [{"source": "start", "target": "debug_agent"}, {"source": "debug_agent", "target": "output"}]})
    request = RunCreate(workflow=definition, prompt=prompt, query=query, inputs=inputs, message=message)
    async with scheduler.admission_lock:
        admitted = await scheduler._submit(request, context, session, frozen_agents={"root/debug_agent": deepcopy(config)})
    try:
        async with asyncio.timeout(125):
            while True:
                with SessionLocal() as current:
                    run = get_resource(current, "run", admitted["id"], context)
                if run["status"] == "succeeded": return {**run["outputs"]["result"], "run_id": run["id"]}
                if run["status"] in {"failed", "cancelled", "outcome_unknown", "paused", "waiting_approval"}:
                    error = run.get("error") or {"code": "RUN_NOT_COMPLETE", "message": "调试运行尚未完成"}
                    details = {"run_id": run["id"], "status": run["status"]}
                    preflight = error.get("details") or {}
                    status = 409
                    if run["status"] == "failed" and isinstance(preflight, dict) and preflight.get("phase") == "preflight" and preflight.get("effect_issued") is False:
                        admitted_status = preflight.get("http_status")
                        if type(admitted_status) is int and 400 <= admitted_status <= 599:
                            status = admitted_status
                        details.update(phase="preflight", effect_issued=False)
                    raise AppError(error["code"], error["message"], status, details)
                await asyncio.sleep(.05)
    except TimeoutError as exc:
        raise AppError("RUN_TIMEOUT", "调试运行超过响应时限，请查看运行记录", 504, {"run_id": admitted["id"]}) from exc
