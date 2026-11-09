"""User-triggered full answer trial, separate from the 16-token diagnostic."""
import time
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field
from app.errors import AppError


class ModelTrialInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    text:str=Field(min_length=1,max_length=8000)
    max_output_tokens:int=Field(default=512,ge=1,le=4096)


class ModelTrialResponse(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    test_kind:Literal['user_prompt']='user_prompt'
    model_id:str
    request_sent:Literal[True]
    response_received:Literal[True]
    response_valid:Literal[True]
    answer:str=Field(max_length=262144)
    answer_available:bool
    elapsed_ms:int=Field(ge=0)
    finish_reason:Literal['stop','length','tool_calls','content_filter','function_call']|None=None
    completion_truncated:bool
    response_state:Literal['answer','empty','tool_request']
    usage:dict[Literal['prompt_tokens','completion_tokens','total_tokens'],Annotated[int,Field(ge=0)]]=Field(default_factory=dict)
    quality_evaluated:Literal[False]=False


async def trial_model(*,model_id,data,context,session):
    from app.model_catalog import service as catalog
    from app.model_catalog.router import test_config
    from .model_adapter import OpenAICompatibleAdapter,load_config
    started=time.monotonic()
    phase='preflight'
    try:
        if not data.text.strip():raise AppError('MODEL_TRIAL_INPUT_INVALID','请输入需要发送给模型的测试内容',422)
        _,model=catalog.managed_row(session,context,catalog.MODEL,model_id,write=True)
        _,provider=catalog.managed_row(session,context,catalog.PROVIDER,model['provider_id'],write=True)
        config=test_config(session,context,provider,model)
        if 'max_tokens' not in config.capabilities.get('parameters',[]):raise AppError('MODEL_PARAMETER_UNSUPPORTED','当前模型未声明有界输出参数，无法执行试答',422)
        if data.max_output_tokens>config.max_output_tokens:raise AppError('MODEL_PARAMETER_UNSUPPORTED','试答输出上限超过所选模型配置，请调低',422)
        with catalog.bind_model_test_access(context=context,session=session,config_id=model_id):
            phase='calling'
            result=await OpenAICompatibleAdapter(config).chat([{'role':'user','content':data.text}],
                parameters={'max_tokens':data.max_output_tokens},context_budget=config.context_window,context=context,session=session)
            phase='returned'
            load_config(model_id,context=context,session=session,expected_version=config.config_version)
    except AppError as exc:
        safe_keys={'failure_stage','upstream_status','reason','provider_error_code','provider_request_id'}
        previous=exc.details if isinstance(exc.details,dict) else {}
        observed=phase=='returned' or previous.get('failure_stage')=='provider_http' or previous.get('response_received') is True
        sent=True if observed else False if phase=='preflight' else None
        received=True if observed else False if phase=='preflight' else None
        valid=True if phase=='returned' else False if observed or phase=='preflight' else None
        details={**{key:previous[key] for key in safe_keys if key in previous},
            'elapsed_ms':int((time.monotonic()-started)*1000),'test_kind':'user_prompt',
            'request_sent':sent,'response_received':received,'response_valid':valid}
        raise AppError(exc.code,exc.message,exc.status_code,details) from None
    answer=result.get('text','')
    return ModelTrialResponse.model_validate({'test_kind':'user_prompt','model_id':model_id,'request_sent':True,'response_received':True,'response_valid':True,
        'answer':answer,'answer_available':bool(answer.strip()),'elapsed_ms':int((time.monotonic()-started)*1000),
        'finish_reason':result.get('finish_reason'),'completion_truncated':bool(result.get('completion_truncated')),
        'response_state':'tool_request' if result.get('tool_calls') else 'answer' if answer.strip() else 'empty',
        'usage':result.get('usage',{}),'quality_evaluated':False}).model_dump(mode='json')
