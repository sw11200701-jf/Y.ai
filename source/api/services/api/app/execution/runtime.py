"""File-backed official saver; execution recovery is independent of queue lifetime."""
import asyncio
import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command
from langsmith import tracing_context
from sqlalchemy import select, func
from app.db import SessionLocal, Resource, RunEventRecord, utc_now
from app.errors import AppError
from app.settings import settings
from app.contracts import WorkflowDefinition, RunCreate
from app.execution.models import ExecutionRun, ExecutionApproval, ExecutionStep
from app.execution.storage import authorize, fresh_context, read_blob, put_blob, claim, heartbeat, release, engine_key, DurableBridge
from app.execution.contracts import ExecutionContext, capabilities
from app.execution.compiler import ProductCompiler, frame


@asynccontextmanager
async def checkpoint_store():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(settings.data_dir/"execution-checkpoints.sqlite3")) as connection:
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA busy_timeout=30000")
        saver = AsyncSqliteSaver(connection, serde=JsonPlusSerializer(pickle_fallback=False, allowed_json_modules=None, allowed_msgpack_modules=None))
        await saver.setup()
        yield saver


def graph_config(binding):
    isolation = ":".join((binding.tenant_id, binding.workspace_id, binding.principal_id, binding.run_id, binding.engine_key, binding.dsl_hash))
    return {"configurable": {"thread_id": hashlib.sha256(isolation.encode()).hexdigest()}, "recursion_limit": 4096, "callbacks": []}


def update_status(session, context, run, status, event_type, error=None, data=None):
    from app.runner.service import emit
    from app.db import save_resource
    run.update(status=status, updated_at=utc_now())
    if error is not None: run["error"] = error
    else: run.pop("error", None)
    save_resource(session, "run", run["id"], run, context)
    emit(session, run["id"], context, event_type, status=status, data=data or ({"error": error} if error else {}))
    if settings.application_api_enabled:
        from app.application_runtime import settle_run
        settle_run(session,run['id'],run)
    session.commit()


async def execute(run_id):
    from app.execution.retained_intents import deny_execution
    deny_execution(run_id)
    owner = secrets.token_hex(24)
    if not claim(run_id, owner): return
    from app.execution.lease_pulse import LeasePulse
    pulse = LeasePulse(run_id, owner, asyncio.current_task())
    pulse.start()
    try:
        with SessionLocal() as session:
            context, binding, run = authorize(session, run_id)
            if run["status"] not in {"queued", "running", "resuming"}: return
            recovering = binding.state_ref is not None
            update_status(session, context, run, "running", "run.recovered" if recovering else "run.started")
            workflow = WorkflowDefinition.model_validate(run["workflow_snapshot"])
            if binding.state_ref is None:
                limits=run.get("test_limits",workflow.limits)
                timeout = min(limits.get("timeout_seconds", 120), settings.run_timeout_seconds)
                from app.execution.document_job_budget import runtime_timeout
                timeout,document_time=runtime_timeout(run=run,session=session,context=context,ordinary_seconds=timeout,
                    document_ceiling_seconds=settings.document_job_timeout_max_seconds)
                deadline = time.time()+timeout
                root_frame = frame(workflow, RunCreate.model_validate(run["request"]).inputs)
                from app.execution.drafts import readonly_pool
                root_frame["pool"].update(readonly_pool(context, run_id))
                from app.user_variables import pool as user_variable_pool
                if run.get("test_pool"):
                    root_frame["pool"] = run["test_pool"]
                    root_frame["test_node_id"] = run["request"]["node_test"]["node_id"]
                root_frame["pool"]["user_vars"]=user_variable_pool(context,session,workflow)
                initial = {"frames": {"root": root_frame}, "agents": {}, "budget": {"steps": 0, "max_steps": limits.get("max_steps", 256), "deadline_epoch": deadline, "deadline": datetime.fromtimestamp(deadline, timezone.utc).isoformat()}}
                if document_time is not None:initial['budget']['document_job_time_budget']=document_time
                if run.get('semantic_job'):
                    initial['semantic_plan_sha256']=run['semantic_job']['plan_sha256']
                initial_ref = put_blob(session, run_id, initial)
                session.commit()
            else:
                if binding.initial_ref is None: raise AppError("CHECKPOINT_INCOMPATIBLE", "缺少原始执行状态，禁止推测恢复", 409)
                initial_ref = {"state_ref": binding.initial_ref}
            config = graph_config(binding)
            execution_context = ExecutionContext(run_id=run_id, engine_key=binding.engine_key, principal_id=binding.principal_id, tenant_id=binding.tenant_id, workspace_id=binding.workspace_id, bridge=DurableBridge(run_id, owner, run.get("test_limits",workflow.limits).get("max_concurrency", 4)))
        async with checkpoint_store() as saver:
            from app.execution.semantic_jobs import compiler_for_run
            graph = compiler_for_run(run, execution_context.bridge).compile(saver)
            with tracing_context(enabled=False):
                checkpoint = await graph.aget_state(config)
                graph_input = None if checkpoint.values else initial_ref
                if run.get("resume_approval_id"):
                    graph_input = Command(resume={"approval_id": run["resume_approval_id"]})
                if run.get("resume_approval_ids"):
                    requested = set(run["resume_approval_ids"])
                    decisions = {item.id: {"approval_id": item.value["approval_id"]}
                        for task in checkpoint.tasks for item in task.interrupts
                        if isinstance(item.value, dict) and item.value.get("approval_id") in requested}
                    if len(decisions) != len(requested):
                        raise AppError("APPROVAL_MISMATCH", "审批与当前暂停节点不一致", 409)
                    graph_input = Command(resume=decisions)
                with SessionLocal() as session:
                    current_context, _, latest = authorize(session, run_id)
                    deadline = read_blob(session, run_id, initial_ref)["budget"]["deadline_epoch"]
                    completion_ref=None
                    if latest.get('semantic_job'):
                        from app.execution.semantic_jobs import completed_state_ref
                        completion_ref=completed_state_ref(session,current_context,latest)
                    else:
                        from app.execution.storage import product_completed_state_ref
                        completion_ref=product_completed_state_ref(session,current_context,latest)
                    if not checkpoint.next and completion_ref is None:
                        # Hard crash may leave saved state without a next
                        # task. Original input reuses committed exact steps.
                        graph_input=initial_ref
                remaining = deadline-time.time()
                if completion_ref is None:
                    if remaining <= 0: raise AppError("RUN_TIMEOUT", "运行已超过原始截止时间", 504)
                    async with asyncio.timeout(remaining):
                        result = await graph.ainvoke(graph_input, config=config, context=execution_context)
                else:result=completion_ref
                checkpoint = await graph.aget_state(config)
        with SessionLocal() as session:
            context, binding, run = authorize(session, run_id)
            if run["status"] in {"cancelled", "failed"}: return
            result_ref=None
            if run.get('semantic_job'):
                from app.execution.semantic_jobs import completed_state_ref
                result_ref=completed_state_ref(session,context,run)
            else:
                from app.execution.storage import product_completed_state_ref
                result_ref=product_completed_state_ref(session,context,run)
            if result_ref is None:
                if checkpoint.next:
                    update_status(session, context, run, "waiting_approval", "run.paused", data={"reason": "approval_required", "next_nodes": list(checkpoint.next)})
                    return
                raise AppError('EXECUTION_INCOMPLETE','流程尚未完成最终提交，不能标记成功',409)
            snapshot = read_blob(session, run_id, result_ref)
            root = snapshot["frames"]["root"]
            run.update(outputs=root["outputs"], variable_pool=root["pool"])
            run.pop("resume_approval_id", None)
            run.pop("resume_approval_ids", None)
            update_status(session, context, run, "succeeded", "run.completed")
    except asyncio.CancelledError:
        # Explicit cancellation is already durable. A graceful process shutdown
        # leaves a checkpoint eligible for actor-authorized continuation.
        with SessionLocal() as session:
            row = session.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==run_id))
            binding = session.get(ExecutionRun, run_id, populate_existing=True)
            if row and binding and binding.lease_owner==owner:
                run = json.loads(row.payload)
                if run["status"] in {"running", "resuming"}:
                    issued = session.scalar(select(ExecutionStep).where(ExecutionStep.run_id==run_id, ExecutionStep.side_effect==True, ExecutionStep.status=="issued").limit(1))
                    diagnostic = pulse.failure or {'cause': 'EXECUTION_INTERRUPTED', 'phase': 'task_cancellation'}
                    run.update(status="outcome_unknown" if issued else "paused", updated_at=utc_now(), error={"code": "OUTCOME_UNKNOWN" if issued else "EXECUTION_PAUSED", "message": "副作用结果未知，禁止自动重试" if issued else "执行已暂停，可重新授权后继续", "details": diagnostic})
                    row.payload = json.dumps(run, ensure_ascii=False)
                    from app.runner.service import emit
                    emit(session, run_id, context, 'effect.unknown' if issued else 'run.paused', status=run['status'], data={'error': run['error']})
                    if settings.application_api_enabled:
                        from app.application_runtime import settle_run
                        settle_run(session,run_id,run)
                    session.commit()
        raise
    except Exception as exc:
        error = exc if isinstance(exc, AppError) else AppError("RUN_TIMEOUT" if isinstance(exc, TimeoutError) else "INTERNAL_ERROR", "运行超过截止时间" if isinstance(exc, TimeoutError) else "执行节点失败", 504 if isinstance(exc, TimeoutError) else 500)
        with SessionLocal() as session:
            # Any outstanding issued effect has an uncertain completion window.
            # Preserve that fact even if the immediate exception was a timeout or
            # revoked session after the call; never advertise it as retryable.
            issued = session.scalar(select(ExecutionStep).where(ExecutionStep.run_id==run_id, ExecutionStep.side_effect==True, ExecutionStep.status=="issued").limit(1))
            if issued is not None and error.code not in {"STEP_INPUT_CHANGED", "ENGINE_MISMATCH"}:
                error = AppError("OUTCOME_UNKNOWN", "副作用执行结果尚未确认，禁止自动重试", 409, {"execution_id": issued.execution_id, "node_key": issued.node_key, "cause": error.code})
            row = session.scalar(select(Resource).where(Resource.kind=="run", Resource.resource_id==run_id))
            binding = session.get(ExecutionRun, run_id, populate_existing=True)
            if row and binding and binding.lease_owner==owner:
                run = json.loads(row.payload)
                if run["status"] not in {"cancelled", "failed", "succeeded"}:
                    from types import SimpleNamespace
                    context = SimpleNamespace(tenant_id=row.tenant_id, workspace_id=row.workspace_id)
                    from app.runner.service import emit
                    status = "outcome_unknown" if error.code == "OUTCOME_UNKNOWN" else "paused" if error.code in {"SESSION_REVOKED", "AUTH_REQUIRED", "LEASE_LOST"} else "failed"
                    run.update(status=status, updated_at=utc_now(), error=error.as_dict())
                    row.payload = json.dumps(run, ensure_ascii=False)
                    emit(session, run_id, context, "effect.unknown" if status=="outcome_unknown" else "run.paused" if status=="paused" else "run.failed", status=status, data={"error": error.as_dict()})
                    if settings.application_api_enabled:
                        from app.application_runtime import settle_run
                        settle_run(session,run_id,run)
                    session.commit()
    finally:
        await pulse.close()
        release(run_id, owner)


async def state_view(session, context, run_id):
    _, binding, run = authorize(session, run_id, context,execute=False)
    last_sequence = run.get("last_sequence", 0)
    budget = read_blob(session, run_id, {"state_ref": binding.state_ref})["budget"] if binding.state_ref else {}
    budget["steps"] = binding.steps
    budget.update(model_calls=binding.model_calls, tool_calls=binding.tool_calls)
    if run.get('semantic_job') and run['status']=='succeeded':
        # authorize() already proved the real complete ledger and current domain
        # READ contract. Do not rebuild an execution graph or demand a live lease.
        from types import SimpleNamespace
        checkpoint=SimpleNamespace(config=None,next=[])
    else:
        async with checkpoint_store() as saver:
            from app.execution.semantic_jobs import compiler_for_run
            graph = compiler_for_run(run, DurableBridge(run_id, "read-only")).compile(saver)
            checkpoint = await graph.aget_state(graph_config(binding))
    from app.execution.approvals import public_approval
    rows = list(session.scalars(select(ExecutionApproval).where(ExecutionApproval.run_id==run_id)))
    from app.execution.api import project_run
    projection = project_run(session, context, run)
    return {"run_id": run_id, "status": run["status"], "engine": run["engine"], "capabilities": projection["capabilities"], "last_sequence": last_sequence, "checkpoint_id": (checkpoint.config or {}).get("configurable", {}).get("checkpoint_id"), "next_nodes": [name for name in checkpoint.next if name!='__product_complete'], "budget": budget, "approvals": [public_approval(row) for row in rows]}
