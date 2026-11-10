"""One-shot document transport wait, backed by a shared reserved time pool.

Does not change the provider configuration, model call count or old plans.
Only a live server effect may use the permit. Timeout/partial response never
creates a success receipt, retry permit, or a release of an unknown request.
"""
from contextvars import ContextVar
from contextlib import contextmanager
import math,time
from app.errors import AppError
from app.execution.effects import get_effect_context

_active=ContextVar('document_request_wait',default=None)


async def prepare_document_address(plan, execution, *, config=None):
    from app.execution.document_job_budget import require_model_time
    from app.execution.model_network import prepare_model_address
    deadline = execution.budget['deadline_epoch']
    require_model_time(plan, deadline)
    await prepare_model_address(execution, config=config, model_config_id=plan['model_config_id'],
                                model_config_version=plan['model_version'])
    require_model_time(plan, deadline, address_prepared=True)


@contextmanager
def document_wait(plan, execution):
    from app.execution.document_job_budget import CONTRACT
    spec=plan.get('time_budget') or {}
    if spec.get('contract')!=CONTRACT:
        yield
        return
    effect=get_effect_context()
    if effect is None or effect.execution_id!=execution.execution_id:
        raise AppError('SEMANTIC_RECEIPT_INVALID','请求等待缺少真实执行凭据',409)
    budget=execution.state['budget'];used=budget.get('document_provider_wait_used_seconds',0)
    pool=spec['shared_provider_wait_seconds'];base=spec['provider_timeout_seconds']
    if type(used) is not int or not 0<=used<=pool:
        raise AppError('DOCUMENT_JOB_TIME_BUDGET_INVALID','已用模型等待预算无效',409)
    remaining=budget['deadline_epoch']-time.time()-spec['finalize_allowance_seconds']-spec['per_call_overhead_seconds']-(0 if execution.model_address is not None else spec.get('resolver_allowance_seconds',0))
    timeout=min(spec['per_request_timeout_ceiling_seconds'],base+pool-used,remaining)
    if timeout<base:
        raise AppError('DOCUMENT_JOB_TIME_BUDGET_EXHAUSTED','剩余时间不足，未发送模型请求',422)
    permit={'effect':effect,'model_id':plan['model_config_id'],'version':plan['model_version'],
        'base':base,'timeout':timeout,'started':False,'elapsed':None}
    token=_active.set(permit)
    try:
        yield
        if not permit['started'] or permit['elapsed'] is None:
            raise AppError('SEMANTIC_RECEIPT_INVALID','请求等待缺少完整传输记录',409)
        # Scheduler/clock jitter belongs to the existing local-overhead reserve.
        consumed=min(pool-used,max(0,math.ceil(permit['elapsed']-base)))
        budget['document_provider_wait_used_seconds']=used+consumed
        execution.state.setdefault('document_request_waits',{})[execution.execution_id]={
            'contract':'document-request-wait-v1','timeout_seconds':timeout,
            'elapsed_ms':round(permit['elapsed']*1000),'shared_wait_used_seconds':consumed}
    finally:
        _active.reset(token)

@contextmanager
def transport_wait(config):
    permit=_active.get()
    if permit is None:
        yield config.timeout_seconds
        return
    if (permit['effect']!=get_effect_context() or permit['started']
            or permit['model_id']!=config.model_config_id or permit['version']!=config.config_version
            or permit['base']!=config.timeout_seconds):
        raise AppError('SEMANTIC_RECEIPT_INVALID','模型等待凭据或当前模型绑定不符',409)
    permit['started']=True;start=time.monotonic()
    try:
        yield permit['timeout']
    finally:
        permit['elapsed']=time.monotonic()-start
