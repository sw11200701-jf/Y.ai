"""Mounted by central only at /api/open/v1 after strict bearer dispatch.

Central callbacks own execution transactions, all current resource ACLs, upload
validation, and per-yield SSE reauthorization/leases. No second execution path.
"""
import inspect
from fastapi import APIRouter,Depends,Header,Request,Response,UploadFile,File
from fastapi.responses import StreamingResponse
from app.context import get_runtime_context as get_context
from app.db import get_session
from app.errors import AppError
from . import bridge
from .schemas import WorkflowInvocation,ChatInvocation,PartitionAction
from .callbacks import callback
from .validation import ApplicationRoute
from .values import canonical_payload_digest,reject_credentials
from .public_contract import route_spec
from .public_schemas import RunReceipt

router=APIRouter(tags=['application-runtime'],route_class=ApplicationRoute)


def authority(session,context,scope):
    if getattr(context,'principal_type','human')!='service':raise AppError('APP_KEY_INVALID','此接口需要有效应用凭据',401)
    from app.machine_auth import require_scope
    value=require_scope(session,context,scope)
    return value.model_dump(mode='json') if hasattr(value,'model_dump') else value


def _app(value,app_id):
    if value['application_id']!=app_id:raise AppError('NOT_FOUND','应用不存在或无权访问',404)


def _user(request,value,user):
    header=request.headers.get('x-external-user')
    if header is not None and user is not None and header!=user:raise AppError('APP_INPUT_INVALID','外部用户标识不一致',422)
    if user is not None:
        expected=canonical_payload_digest({'mode':'user','client_id':value['client_id'],'user':user})
        if value['external_user_hash']!=expected:raise AppError('APP_INPUT_INVALID','外部用户标识与请求身份分区不一致',422)


async def _invoke(name,**kwargs):
    result=callback(name)(**kwargs)
    return await result if inspect.isawaitable(result) else result


def _request_id(request):
    value=getattr(request.state,'request_id',None)
    if not isinstance(value,str) or not 1<=len(value)<=100:raise AppError('APP_API_NOT_READY','请求标识尚未绑定',503)
    return value


def _receipt(value):
    # Callback must return the projected receipt, never an internal Run object.
    try:return RunReceipt.model_validate(value).model_dump(mode='json',exclude_unset=True)
    except ValueError:raise AppError('APP_OUTPUT_INVALID','中央返回的运行回执不符合公开契约',503) from None


@router.get('/applications/{app_id}/info',**route_spec('info'))
def info(app_id:str,session=Depends(get_session),context=Depends(get_context)):
    value=authority(session,context,'app.read');_app(value,app_id);release=bridge.load_release(session,authority=value)
    # No graph, prompt, dependency or resource identity metadata.
    return {'application_id':app_id,'version_id':value['version_id'],'status':'active',
        'type':release['source_identity']['kind'],'capabilities':{'token_streaming':False,'progress_events':True,
        'trace':'trace.read' in value['scopes'] and value['runtime_policy']['expose_trace'],'resume':'runs.resume' in value['scopes'] and value['runtime_policy']['allow_resume']}}


@router.get('/applications/{app_id}/parameters',**route_spec('parameters'))
def parameters(app_id:str,session=Depends(get_session),context=Depends(get_context)):
    value=authority(session,context,'app.read');_app(value,app_id);release=bridge.load_release(session,authority=value)
    return {'application_id':app_id,'version_id':value['version_id'],'input_schema':release['input_schema'],'output_schema':release['output_schema'],
        'limits':{'input_bytes':131072,'text_characters':16000,'wait_seconds':20,'timeout_seconds':value['runtime_policy']['timeout_seconds']},
        'file_policy':{k:value['runtime_policy'][k] for k in ('max_file_bytes','allowed_file_types')},'modes':['async','bounded_wait']}


@router.post('/applications/{app_id}/runs',**route_spec('submit'))
async def submit(app_id:str,data:WorkflowInvocation,request:Request,response:Response,idempotency_key:str=Header(...,min_length=8,max_length=128,pattern=r'^[\x21-\x7e]+$'),session=Depends(get_session),context=Depends(get_context)):
    value=authority(session,context,'workflow.invoke');_app(value,app_id);_user(request,value,data.user)
    result=_receipt(await _invoke('submit',authority=value,invocation=data.model_dump(mode='json'),idempotency_key=idempotency_key,request_id=_request_id(request)))
    response.status_code=200 if result['status'] in {'succeeded','failed','cancelled','interrupted'} else 202
    return result


@router.post('/applications/{app_id}/chat/messages',**route_spec('chat'))
async def chat(app_id:str,data:ChatInvocation,request:Request,response:Response,idempotency_key:str=Header(...,min_length=8,max_length=128,pattern=r'^[\x21-\x7e]+$'),session=Depends(get_session),context=Depends(get_context)):
    value=authority(session,context,'chat.invoke');_app(value,app_id);_user(request,value,data.user)
    result=_receipt(await _invoke('submit',authority=value,invocation=data.model_dump(mode='json'),idempotency_key=idempotency_key,request_id=_request_id(request)))
    response.status_code=200 if result['status'] in {'succeeded','failed','cancelled','interrupted'} else 202
    return result


@router.get('/runs/{run_id}',**route_spec('get_run'))
async def get_run(run_id:str,request:Request,session=Depends(get_session),context=Depends(get_context)):
    value=authority(session,context,'runs.read')
    return _receipt(await _invoke('get_run',authority=value,run_id=run_id,request_id=_request_id(request)))


@router.get('/runs/{run_id}/events',**route_spec('events'))
async def events(run_id:str,request:Request,last_event_id:str|None=Header(None,min_length=1,max_length=18,pattern=r'^[0-9]+$'),session=Depends(get_session),context=Depends(get_context)):
    value=authority(session,context,'events.read')
    if last_event_id is not None and (not last_event_id.isascii() or not last_event_id.isdigit() or len(last_event_id)>18):
        raise AppError('APP_INPUT_INVALID','事件续订序号无效',422)
    result=await _invoke('events',authority=value,run_id=run_id,last_event_id=int(last_event_id or '0'),request_id=_request_id(request),request=request)
    if not isinstance(result,StreamingResponse) or result.media_type!='text/event-stream':raise AppError('APP_OUTPUT_INVALID','中央事件流未提供受控响应',503)
    return result


@router.get('/runs/{run_id}/trace',**route_spec('trace'))
async def trace(run_id:str,request:Request,session=Depends(get_session),context=Depends(get_context)):
    value=authority(session,context,'trace.read')
    if not value['runtime_policy']['expose_trace']:raise AppError('APP_SCOPE_DENIED','此发布版未开放运行摘要',403)
    return await _invoke('trace',authority=value,run_id=run_id,request_id=_request_id(request))


@router.post('/runs/{run_id}/cancel',**route_spec('cancel'))
async def cancel(run_id:str,request:Request,data:PartitionAction|None=None,session=Depends(get_session),context=Depends(get_context)):
    value=authority(session,context,'runs.cancel');_user(request,value,data.user if data else None)
    return _receipt(await _invoke('cancel',authority=value,run_id=run_id,request_id=_request_id(request)))


@router.post('/runs/{run_id}/resume',**route_spec('resume'))
async def resume(run_id:str,request:Request,data:PartitionAction|None=None,session=Depends(get_session),context=Depends(get_context)):
    value=authority(session,context,'runs.resume')
    _user(request,value,data.user if data else None)
    if not value['runtime_policy']['allow_resume']:raise AppError('APP_SCOPE_DENIED','此发布版未开放恢复',403)
    return _receipt(await _invoke('resume',authority=value,run_id=run_id,request_id=_request_id(request)))


@router.get('/conversations/{conversation_id}',**route_spec('conversation'))
async def conversation(conversation_id:str,request:Request,session=Depends(get_session),context=Depends(get_context)):
    return await _invoke('conversation',authority=authority(session,context,'conversations.read'),conversation_id=conversation_id,request_id=_request_id(request))


@router.post('/files',**route_spec('upload'))
async def upload(request:Request,file:UploadFile=File(...),session=Depends(get_session),context=Depends(get_context)):
    return await _invoke('upload',authority=authority(session,context,'files.upload'),file=file,request_id=_request_id(request))


@router.get('/files/{file_id}',**route_spec('file_metadata'))
async def file_metadata(file_id:str,request:Request,session=Depends(get_session),context=Depends(get_context)):
    return await _invoke('file_metadata',authority=authority(session,context,'files.read'),file_id=file_id,request_id=_request_id(request))


@router.get('/files/{file_id}/content',**route_spec('download'))
async def download(file_id:str,request:Request,session=Depends(get_session),context=Depends(get_context)):
    return await _invoke('download',authority=authority(session,context,'files.read'),file_id=file_id,request_id=_request_id(request))
