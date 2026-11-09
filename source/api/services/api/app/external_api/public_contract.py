"""Single runtime contract: actual route DTO/status metadata + live OpenAPI.

No separately maintained export response templates. Specialization binds only
the already authorized application's fixed version IO/scopes/server address.
"""
from copy import deepcopy
from . import public_schemas as dto
from .schemas import WorkflowInvocation,ChatInvocation,PartitionAction

ERRORS={401:'凭据无效或失效',403:'scope或当前资源授权不足',404:'资源不存在或无权访问',409:'状态/幂等冲突',413:'请求或文件超限',
        422:'请求参数无效',426:'必须HTTPS',429:'持久限额超限',500:'服务内部错误',503:'服务尚未就绪'}
COMMON_HEADERS={'X-Request-ID':{'description':'服务端请求标识','schema':{'type':'string'}},
    'Cache-Control':{'description':'禁止缓存','schema':{'type':'string','const':'no-store'}},
    'Vary':{'schema':{'type':'string','const':'Authorization, X-External-User'}},
    'Referrer-Policy':{'schema':{'type':'string','const':'no-referrer'}},
    'Cross-Origin-Resource-Policy':{'schema':{'type':'string','const':'same-origin'}},
    'X-Content-Type-Options':{'schema':{'type':'string','const':'nosniff'}}}
DOWNLOAD_MEDIA=('application/octet-stream','text/plain','text/markdown','text/csv','application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','image/png','image/jpeg','image/webp','audio/wav','audio/mpeg','audio/mp4')
OPERATIONS={
    'info':('get','/applications/{app_id}/info','app.read',dto.ApplicationInfo,200),
    'parameters':('get','/applications/{app_id}/parameters','app.read',dto.ApplicationParameters,200),
    'submit':('post','/applications/{app_id}/runs','workflow.invoke',dto.RunReceipt,202),
    'chat':('post','/applications/{app_id}/chat/messages','chat.invoke',dto.RunReceipt,202),
    'get_run':('get','/runs/{run_id}','runs.read',dto.RunReceipt,200),
    'events':('get','/runs/{run_id}/events','events.read',None,200),
    'trace':('get','/runs/{run_id}/trace','trace.read',dto.TraceView,200),
    'cancel':('post','/runs/{run_id}/cancel','runs.cancel',dto.RunReceipt,200),
    'resume':('post','/runs/{run_id}/resume','runs.resume',dto.RunReceipt,202),
    'conversation':('get','/conversations/{conversation_id}','conversations.read',dto.ConversationView,200),
    'upload':('post','/files','files.upload',dto.FileMetadata,201),
    'file_metadata':('get','/files/{file_id}','files.read',dto.FileMetadata,200),
    'download':('get','/files/{file_id}/content','files.read',None,200),
}


def route_spec(name):
    method,path,scope,model,status=OPERATIONS[name]
    responses={code:{'description':message,'model':dto.ErrorEnvelope,'headers':deepcopy(COMMON_HEADERS)} for code,message in ERRORS.items()}
    responses[429]['headers']['Retry-After']={'description':'至少等待的秒数','schema':{'type':'string','pattern':r'^[1-9][0-9]*$'}}
    responses[status]={'description':'已受理同一run，继续查询' if status==202 else '创建文件' if status==201 else '成功','headers':deepcopy(COMMON_HEADERS)}
    if name in {'submit','chat'}:responses[200]={'description':'有限等待内同一run已结束','model':dto.RunReceipt,'headers':deepcopy(COMMON_HEADERS)}
    if name=='events':
        responses[200]['content']={'text/event-stream':{'schema':{'type':'string','description':'id是真实sequence；event=progress/result/error；心跳为注释，无伪token流'}}}
        responses[200]['headers']['X-Accel-Buffering']={'schema':{'type':'string','const':'no'}}
    if name=='download':
        responses[200]['content']={mime:{'schema':{'type':'string','format':'binary'}} for mime in DOWNLOAD_MEDIA}
        responses[200]['headers']['Content-Disposition']={'schema':{'type':'string'},'description':'受控附件文件名，UTF-8编码'}
    parameters=[{'in':'header','name':'X-Application-Version','required':False,'schema':{'type':'string','maxLength':100},'description':'仅Key明确允许的固定发布版本；不接受latest'},
        {'in':'header','name':'X-External-User','required':False,'schema':{'type':'string','minLength':1,'maxLength':128},'description':'客户分区；JSON POST可改用body.user，同时提供必须相同。required-client至少一处提供'}]
    result={'status_code':status,'responses':responses,'response_model_exclude_unset':True,
        'openapi_extra':{'security':[{'ApplicationKey':[]}],'x-required-scope':scope,'parameters':parameters,
            'x-external-user-policy':{'json_body_alias':name in {'submit','chat','cancel','resume'},'sources':['header','body.user'] if name in {'submit','chat','cancel','resume'} else ['header'],
                'both_must_equal':True,'required_when':'client.external_user_mode=required'}}}
    if model is not None:result['response_model']=model
    else:
        from fastapi.responses import StreamingResponse,Response
        result['response_class']=StreamingResponse if name=='events' else Response
    return result


def _register_model(components,model):
    schema=model.model_json_schema(ref_template='#/components/schemas/{model}')
    definitions=schema.pop('$defs',{})
    components.update(definitions);components[model.__name__]=schema


def runtime_openapi(*,application_id=None,version_id=None,kind=None,input_schema=None,output_schema=None,public_base_url=None,scopes=None,external_user_mode='client',runtime_policy=None):
    from fastapi import FastAPI
    from .runtime_router import router
    app=FastAPI(title='Application Runtime API',version='1.2.0')
    app.include_router(router,prefix='/api/open/v1')
    spec=app.openapi();schemas=spec.setdefault('components',{}).setdefault('schemas',{})
    spec['components'].setdefault('securitySchemes',{})['ApplicationKey']={'type':'http','scheme':'bearer','bearerFormat':'ApplicationKey'}
    for model in (dto.ProgressEvent,dto.ResultEvent,dto.StreamError):_register_model(schemas,model)
    if output_schema is not None:
        schemas['RunReceipt']['properties']['outputs']=deepcopy(output_schema)
        schemas['ResultEvent']['properties']['outputs']=deepcopy(output_schema)
        schemas['AssistantHistoryItem']['properties']['content']=deepcopy(output_schema)
    if input_schema is not None:
        for name in ('WorkflowInvocation','ChatInvocation'):
            if name in schemas:schemas[name]['properties']['inputs']=deepcopy(input_schema)
    for name,(method,path,scope,model,status) in OPERATIONS.items():
        path='/api/open/v1'+path;operation=spec['paths'][path][method]
        denied=scopes is not None and scope not in scopes
        denied=denied or kind=='agent' and name=='submit' or kind=='workflow' and name in {'chat','conversation'}
        denied=denied or runtime_policy is not None and (name=='trace' and not runtime_policy.get('expose_trace',False) or name=='resume' and not runtime_policy.get('allow_resume',False))
        if denied:
            del spec['paths'][path][method]
            if not spec['paths'][path]:del spec['paths'][path]
            continue
        alias=name in {'submit','chat','cancel','resume'}
        operation['x-external-user-policy'].update(required=external_user_mode=='required')
        for parameter in operation.get('parameters',[]):
            if parameter.get('name')=='X-External-User':parameter['required']=external_user_mode=='required' and not alias
            if parameter.get('name')=='X-Application-Version' and version_id is not None:parameter['schema']['const']=version_id
        if name=='events':
            operation['responses']['200']['content']['text/event-stream']['schema']['x-event-payloads']={
                'progress':{'$ref':'#/components/schemas/ProgressEvent'},'result':{'$ref':'#/components/schemas/ResultEvent'},'error':{'$ref':'#/components/schemas/StreamError'}}
    if application_id is not None:
        for path in list(spec['paths']):
            if '{app_id}' not in path:continue
            operations=spec['paths'].pop(path)
            for operation in operations.values():operation['parameters']=[p for p in operation.get('parameters',[]) if p.get('name')!='app_id']
            spec['paths'][path.replace('{app_id}',application_id)]=operations
    if public_base_url is not None:
        # The trusted setting includes /api/open/v1; paths already include it.
        spec['servers']=[{'url':public_base_url.removesuffix('/api/open/v1')}]
    return spec
