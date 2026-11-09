"""Private bridge admission, not a provider-usage or completed-response receipt."""
import hashlib,json,time
from sqlalchemy import select,update,func
from app.db import utc_now
from app.errors import AppError
from app.execution.models import ExecutionRun,ExecutionStep,ExecutionBlob

MAX_RUN_CONTEXT_TOKENS=67108864
MAX_DOCUMENT_CONTEXT_TOKENS=134217728
KIND='model_context_reservation_v1'


def context_ceiling(session,execution):
    """Only the canonical signed document plan may reserve above ordinary limits."""
    if not execution.node_key.startswith('semantic:'):return MAX_RUN_CONTEXT_TOKENS
    from app.db import get_resource
    run=get_resource(session,'run',execution.run_id,execution.context)
    if not run.get('semantic_job'):return MAX_RUN_CONTEXT_TOKENS
    from .semantic_jobs import load_plan
    wrapper=load_plan(session,execution.context,run);plan=wrapper['plan']
    if plan.get('document_context_budget_contract')!='signed-document-context-reservation-v1':return MAX_RUN_CONTEXT_TOKENS
    budget=plan['budget'];maximum=budget['reserved_total_tokens']
    if (not execution.node_key.startswith('semantic:'+plan['job_id']+':model:')
            or type(maximum) is not int or not 1024<=maximum<=MAX_DOCUMENT_CONTEXT_TOKENS
            or maximum!=budget['max_model_calls']*budget['per_call_context_budget']
            or wrapper['reservation']['total_tokens']!=maximum
            or execution.model_context_reservation['context_tokens']!=budget['per_call_context_budget']):
        raise AppError('EXECUTION_PREFLIGHT_ONLY','文档上下文预约不属于固定计划',409)
    return max(MAX_RUN_CONTEXT_TOKENS,maximum)

def reserve_context(session,execution,*,lease_owner,lease_seconds):
    plan=execution.model_context_reservation
    if plan is None:return
    if (type(plan) is not dict or set(plan)!={'context_tokens','image_tokens'}
            or type(plan['context_tokens']) is not int or not 512<=plan['context_tokens']<=1048576
            or type(plan['image_tokens']) is not int or not 0<=plan['image_tokens']<=plan['context_tokens']):
        raise AppError('EXECUTION_PREFLIGHT_ONLY','模型预约必须由受信预检生成',409)
    now=time.time()
    # Acquire the same run lease's writer fence before summing/inserting, so
    # parallel branches cannot each spend the same remaining context budget.
    fenced=session.execute(update(ExecutionRun).where(ExecutionRun.run_id==execution.run_id,
        ExecutionRun.lease_owner==lease_owner,ExecutionRun.lease_until>=now).values(lease_until=now+lease_seconds))
    if fenced.rowcount!=1:raise AppError('LEASE_LOST','模型预约租约已失效',409)
    identifier=hashlib.sha256((execution.run_id+':model-context:'+execution.execution_id).encode()).hexdigest()
    if session.get(ExecutionBlob,identifier) is not None:
        raise AppError('EFFECT_NOT_REPLAYABLE','此模型步骤已预约，禁止再次发出请求',409)
    existing=session.scalar(select(func.coalesce(func.sum(func.json_extract(ExecutionBlob.payload,'$.reserved_context_tokens')),0)).where(
        ExecutionBlob.run_id==execution.run_id,func.json_extract(ExecutionBlob.payload,'$.kind')==KIND))
    total=int(existing)+plan['context_tokens']
    maximum=context_ceiling(session,execution)
    if total>maximum:
        raise AppError('CONTEXT_BUDGET_EXCEEDED','本次运行的持久化模型上下文预约超过总限额',422,
            {'reserved_context_tokens':int(existing),'requested_context_tokens':plan['context_tokens'],'max_reserved_context_tokens':maximum})
    record={'kind':KIND,'execution_id':execution.execution_id,'reserved_context_tokens':plan['context_tokens'],
        'image_input_tokens':plan['image_tokens'],'max_run_context_tokens':maximum,
        'policy':'full_per_call_context_including_images_no_automatic_refund','provider_usage_known':False}
    session.add(ExecutionBlob(id=identifier,run_id=execution.run_id,payload=json.dumps(record,sort_keys=True),created_at=utc_now()))
    # This insertion and ExecutionStep(issued) commit together in the bridge.
    # Success, failure, cancellation and UNKNOWN never refund/rewrite it.

def admit_model_context(execution,*,config,inputs,resolved,node_type):
    from app.agents.model_requirements import preflight_model_requirements
    # These nodes call json_model with json_output=True, regardless of config.
    response_format=({'type':'json_object'} if node_type in {'parameter_extractor','question_classifier'}
        else config.get('response_format'))
    checked=preflight_model_requirements(model_config_id=config.get('model_config_id'),expected_version=config.get('model_config_version'),
        parameters=config.get('model_parameters'),context_budget=config.get('context_budget'),response_format=response_format,
        context=execution.context,session=execution.session)
    image_tokens=0
    if node_type=='llm':
        enabled=config.get('vision_enabled',False)
        if type(enabled) is not bool:raise AppError('VISION_INPUT_INVALID','视觉开关须为布尔值',422)
        ids=inputs.get('image_attachment_ids')
        message=inputs.get('message')
        if ids is None and isinstance(message,dict):
            from app.db import get_resource
            ids=[]
            for identifier in message.get('attachment_ids',[]):
                metadata=get_resource(execution.session,'attachment',identifier,execution.context)
                if metadata.get('media_kind')=='image':ids.append(identifier)
        ids=[] if ids is None else ids
        if ids and not enabled:raise AppError('MODEL_VISION_DISABLED','此LLM节点未开启视觉输入',422)
        if enabled:
            if not ids:raise AppError('VISION_INPUT_INVALID','已开启视觉，请选择本次消息中的图片',422)
            if (type(ids) is not list or not 1<=len(ids)<=4 or any(type(i) is not str for i in ids) or len(set(ids))!=len(ids)):
                raise AppError('VISION_INPUT_INVALID','请选择本次消息的至多4张不同图片',422)
            import math
            from app.agents.vision import profile,_load_in_scope
            profile(resolved)
            images=_load_in_scope(execution.message_attachments,tuple(ids),execution.context,execution.session)
            image_tokens=sum(1024+2048*math.ceil(w/512)*math.ceil(h/512) for _,_,w,h in images)
            if image_tokens+checked['max_output_tokens']>checked['context_budget']:
                raise AppError('CONTEXT_BUDGET_EXCEEDED','图片预约与输出预算超过上下文预算',422)
    execution.model_context_reservation={'context_tokens':checked['context_budget'],'image_tokens':image_tokens}
