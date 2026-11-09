"""A synchronous receipt-validation batch, fenced before AND after traversal.

Only immutable receipts share the gate inside this stack frame. Current user,
ACL, source bytes, model, plan, active status and lease are rechecked at both
boundaries. No proof survives the batch, task, session or an awaited effect.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import time
from app.errors import AppError


def install(read_step, *, run_id, lease_owner, load_plan):
    active = ContextVar('semantic_receipt_batch', default=None)

    def gate(session, context):
        from app.execution.storage import authorize, check_active_run
        _, binding, run = authorize(session, run_id, context)
        check_active_run(session, run_id)
        if binding.lease_owner != lease_owner or binding.lease_until < time.time():
            raise AppError('LEASE_LOST', '文档任务租约失效', 409)
        wrapper = load_plan(session, context, run)
        return binding, run, wrapper

    def stamp(value):
        b, r, w = value
        return (b.generation, b.engine_key, b.dsl_hash, b.model_calls,
            r['semantic_job']['plan_sha256'], w['run_id'])

    def state(session, context):
        scope = active.get()
        if scope is not None and scope[0] is session and scope[1] is context:
            return scope[2]
        return gate(session, context)

    @contextmanager
    def batch(*, session, context):
        before = gate(session, context)
        original_stamp = stamp(before)
        token = active.set((session, context, before))
        try:
            yield
            if stamp(gate(session, context)) != original_stamp:
                raise AppError('SEMANTIC_RECEIPT_INVALID', '核验期间任务绑定已变化，未签发新调用', 409)
        finally:
            active.reset(token)

    read_step.batch = batch
    return state
