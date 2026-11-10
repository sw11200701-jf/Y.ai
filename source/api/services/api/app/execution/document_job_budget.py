"""Server-bounded document-job wall time, independent of ordinary graph limits."""
import math
import time
from app.errors import AppError

LEGACY_CONTRACT='document-job-wall-time-v1'
PREVIOUS_CONTRACT='document-job-wall-time-v2'
PREVIOUS_CURRENT_CONTRACT='document-job-wall-time-v3'
CONTRACT='document-job-wall-time-v4'
HARD_CAP_SECONDS=86400
PER_CALL_OVERHEAD_SECONDS=8
FINALIZE_ALLOWANCE_SECONDS=60


def reserve_time(*,model_calls,provider_timeout_seconds,requested_seconds=None,server_ceiling_seconds=43200,resolver_allowance_seconds=0,contract=CONTRACT,request_timeout_seconds=120):
    if (type(model_calls) is not int or not 2<=model_calls<=127 or type(provider_timeout_seconds) not in (int,float)
            or not math.isfinite(provider_timeout_seconds) or not 0<provider_timeout_seconds<=300
            or type(server_ceiling_seconds) is not int or not 30<=server_ceiling_seconds<=HARD_CAP_SECONDS
            or type(resolver_allowance_seconds) is not int or not 0<=resolver_allowance_seconds<=3):
        raise AppError('DOCUMENT_JOB_TIME_BUDGET_INVALID','文档任务时限配置无效',422)
    if contract not in (CONTRACT,PREVIOUS_CURRENT_CONTRACT,PREVIOUS_CONTRACT,LEGACY_CONTRACT):raise AppError('DOCUMENT_JOB_TIME_BUDGET_INVALID','文档任务时限合同无效',422)
    overhead,finalize=(2,30) if contract==LEGACY_CONTRACT else (PER_CALL_OVERHEAD_SECONDS,FINALIZE_ALLOWANCE_SECONDS)
    minimum=math.ceil(model_calls*(provider_timeout_seconds+overhead+resolver_allowance_seconds)+finalize)
    if type(request_timeout_seconds) not in (int,float) or not math.isfinite(request_timeout_seconds) or not 1<=request_timeout_seconds<=600:
        raise AppError('DOCUMENT_JOB_TIME_BUDGET_INVALID','单次模型等待上限无效',422)
    request_cap=max(request_timeout_seconds,provider_timeout_seconds)
    # Reserve the full extra wait for EVERY possible request. A slow early
    # window must not steal the time required by the last synthesis request.
    extra_wait=math.ceil((request_cap-provider_timeout_seconds)*model_calls) if contract in (CONTRACT,PREVIOUS_CURRENT_CONTRACT) else 0
    minimum+=extra_wait
    # A reservation is an upper bound, not provider billing. Leave a bounded
    # server-side validation margin instead of defaulting to the bare minimum.
    requested=(max(120,minimum) if contract in (LEGACY_CONTRACT,CONTRACT,PREVIOUS_CURRENT_CONTRACT) else server_ceiling_seconds) if requested_seconds is None else requested_seconds
    if type(requested) is not int or not max(30,minimum)<=requested<=server_ceiling_seconds:
        raise AppError('DOCUMENT_JOB_TIME_BUDGET_INVALID','文档任务总时限不足或超过服务端上限，尚未创建运行或调用模型',422,
            {'minimum_seconds':minimum,'maximum_seconds':server_ceiling_seconds,'requested_seconds':requested,
             'model_calls':model_calls,'provider_timeout_seconds':provider_timeout_seconds})
    result={'contract':contract,'wall_time_seconds':requested,'minimum_wall_time_seconds':minimum,
            'model_calls':model_calls,'provider_timeout_seconds':provider_timeout_seconds,
            'per_call_overhead_seconds':overhead,'finalize_allowance_seconds':finalize,
            'server_ceiling_at_admission_seconds':server_ceiling_seconds}
    if resolver_allowance_seconds:result['resolver_allowance_seconds']=resolver_allowance_seconds
    if contract in (CONTRACT,PREVIOUS_CURRENT_CONTRACT):
        result.update(shared_provider_wait_seconds=extra_wait,per_request_timeout_ceiling_seconds=request_cap)
    return result


def validated_time(plan, *, server_ceiling_seconds):
    spec=plan.get('time_budget')
    if spec is None:return None
    if not isinstance(spec,dict) or spec.get('contract') not in (CONTRACT,PREVIOUS_CURRENT_CONTRACT,PREVIOUS_CONTRACT,LEGACY_CONTRACT):
        raise AppError('DOCUMENT_JOB_TIME_BUDGET_INVALID','文档任务固定时限合同不符',409)
    expected=reserve_time(model_calls=plan['budget']['max_model_calls'],provider_timeout_seconds=spec.get('provider_timeout_seconds'),
        requested_seconds=spec.get('wall_time_seconds'),server_ceiling_seconds=server_ceiling_seconds,resolver_allowance_seconds=spec.get('resolver_allowance_seconds',0),contract=spec['contract'],request_timeout_seconds=spec.get('per_request_timeout_ceiling_seconds',120))
    keys=set(expected)-{'server_ceiling_at_admission_seconds'}
    admitted=spec.get('server_ceiling_at_admission_seconds')
    if (set(spec)!=set(expected) or any(spec[key]!=expected[key] for key in keys)
            or type(admitted) is not int or not spec['wall_time_seconds']<=admitted<=HARD_CAP_SECONDS):
        raise AppError('DOCUMENT_JOB_TIME_BUDGET_INVALID','文档任务时限或模型调用预约已变化',409)
    return spec


def runtime_timeout(*,run,session,context,ordinary_seconds,document_ceiling_seconds):
    if not run.get('semantic_job'):return ordinary_seconds,None
    # Only the canonical private server plan can opt in; HTTP graph variables
    # cannot supply this witness. Old plans retain their original time path.
    from app.execution.semantic_jobs import load_plan
    plan=load_plan(session,context,run)['plan']
    spec=validated_time(plan,server_ceiling_seconds=document_ceiling_seconds)
    return (spec['wall_time_seconds'],spec) if spec else (ordinary_seconds,None)


def require_model_time(plan,deadline_epoch,*,now=None,address_prepared=False):
    spec=plan.get('time_budget')
    if spec is None:return
    current=time.time() if now is None else now
    resolver_allowance = 0 if address_prepared else spec.get('resolver_allowance_seconds', 0)
    needed=spec['provider_timeout_seconds']+spec['per_call_overhead_seconds']+resolver_allowance+spec['finalize_allowance_seconds']
    remaining=(deadline_epoch-current) if (type(deadline_epoch) in (int,float) and math.isfinite(deadline_epoch)
        and type(current) in (int,float) and math.isfinite(current)) else -1
    if remaining<needed:
        raise AppError('DOCUMENT_JOB_TIME_BUDGET_EXHAUSTED','剩余时限不足以完成下一次模型请求并保留最终提交时间，未签发新调用',422,
                       {'minimum_remaining_seconds':needed,'remaining_seconds':max(0,remaining)})
