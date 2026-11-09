import json
from fastapi import APIRouter, Depends, Query, Request
from app.auth import identity
from app.db import get_session, list_resources, get_resource
from app.errors import AppError
from .schemas import ProviderWrite, ProviderPatch, ModelWrite, ModelPatch, Empty, ProviderView, ModelView, ProviderList, ProviderPresetList, ModelList, UsableList
from . import service as s

router = APIRouter(tags=['model-management'])


@router.get('/model-provider-presets', response_model=ProviderPresetList)
def provider_presets(request: Request, session=Depends(get_session)):
    from .presets import provider_presets as suggestions
    s.manage(identity(request), session)
    return suggestions()


@router.get('/model-providers', response_model=ProviderList)
def providers(request: Request, session=Depends(get_session)):
    context = identity(request);s.manage(context, session)
    return {'items': [s.provider_view(value) for value in list_resources(session, s.PROVIDER, context) if not value.get('archived')]}


@router.post('/model-providers', status_code=201, response_model=ProviderView)
def create_provider(body: ProviderWrite, request: Request, session=Depends(get_session)):
    return s.create_provider(session, identity(request), body)


@router.get('/model-providers/{provider_id}', response_model=ProviderView)
def provider(provider_id: str, request: Request, session=Depends(get_session)):
    _, value = s.managed_row(session, identity(request), s.PROVIDER, provider_id)
    return s.provider_view(value)


@router.patch('/model-providers/{provider_id}', response_model=ProviderView)
def patch_provider(provider_id: str, body: ProviderPatch, request: Request, session=Depends(get_session)):
    return s.update_provider(session, identity(request), provider_id, body)


@router.delete('/model-providers/{provider_id}')
def delete_provider(provider_id: str, request: Request, expected_revision: int = Query(ge=1), session=Depends(get_session)):
    return s.archive_provider(session, identity(request), provider_id, expected_revision)


@router.get('/model-providers/{provider_id}/models', response_model=ModelList)
def provider_models(provider_id: str, request: Request, session=Depends(get_session)):
    context = identity(request);_, parent = s.managed_row(session, context, s.PROVIDER, provider_id)
    return {'items': [s.model_view(value, parent) for value in list_resources(session, s.MODEL, context)
                      if value['provider_id'] == provider_id and not value.get('archived')]}


@router.post('/model-providers/{provider_id}/models', status_code=201, response_model=ModelView)
def create_model(provider_id: str, body: ModelWrite, request: Request, session=Depends(get_session)):
    return s.create_model(session, identity(request), provider_id, body)


@router.get('/models/{model_id}', response_model=ModelView)
def model(model_id: str, request: Request, session=Depends(get_session)):
    context = identity(request);_, value = s.managed_row(session, context, s.MODEL, model_id)
    _, parent = s.managed_row(session, context, s.PROVIDER, value['provider_id'])
    return s.model_view(value, parent)


@router.patch('/models/{model_id}', response_model=ModelView)
def patch_model(model_id: str, body: ModelPatch, request: Request, session=Depends(get_session)):
    return s.update_model(session, identity(request), model_id, body)


@router.delete('/models/{model_id}')
def delete_model(model_id: str, request: Request, expected_revision: int = Query(ge=1), session=Depends(get_session)):
    return s.archive_model(session, identity(request), model_id, expected_revision)


@router.get('/models/{model_id}/references')
def model_references(model_id: str, request: Request, session=Depends(get_session)):
    context = identity(request);s.managed_row(session, context, s.MODEL, model_id)
    references = s.references(session, context, model_id);items = []
    for row in references:
        try:
            value = get_resource(session, row.kind, row.resource_id, context)
        except AppError as exc:
            if exc.status_code == 404:
                continue
            raise
        items.append({'kind': row.kind, 'id': row.resource_id, 'name': value.get('name', row.resource_id)})
    return {'items': items, 'blocked_by_references': bool(references)}


@router.get('/model-catalog', response_model=UsableList)
def usable_models(request: Request, session=Depends(get_session)):
    return s.usable_catalog(session, identity(request))


def test_config(session, context, provider, model=None):
    if not provider['enabled'] or model and not model['enabled']:
        raise AppError('MODEL_DISABLED', '请先启用待测试的平台和模型', 409)
    if provider['auth_type'] == 'api_key' and not provider.get('secret_ref'):
        raise AppError('MODEL_CREDENTIAL_MISSING', '平台认证密钥不可用', 409)
    return s.ResolvedModel(provider['base_url'], model['model_name'] if model else '', provider.get('secret_ref'),
        provider['timeout_seconds'], provider['allow_private_network'], model['id'] if model else provider['id'],
        s.version(model, provider) if model else f'p{provider["revision"]}',
        model['capabilities'] if model else {}, model['context_window'] if model else 8192,
        model['max_output_tokens'] if model else 16)


@router.post('/model-providers/{provider_id}/test-connection')
async def connection_test(provider_id: str, body: Empty, request: Request, session=Depends(get_session)):
    from .security import test_connection
    context = identity(request);_, parent = s.managed_row(session, context, s.PROVIDER, provider_id, write=True)
    return await test_connection(test_config(session, context, parent), context=context, session=session)


@router.post('/models/{model_id}/test-inference')
async def inference_test(model_id: str, body: Empty, request: Request, session=Depends(get_session)):
    from app.agents.model_adapter import OpenAICompatibleAdapter
    from .security import approved_model_address
    context = identity(request);_, value = s.managed_row(session, context, s.MODEL, model_id, write=True)
    _, parent = s.managed_row(session, context, s.PROVIDER, value['provider_id'], write=True)
    config = test_config(session, context, parent, value)
    await approved_model_address(config)
    parameters = {'max_tokens': min(16, config.max_output_tokens)}
    if 'max_tokens' not in config.capabilities['parameters']:
        raise AppError('MODEL_PARAMETER_UNSUPPORTED', '推理测试需要模型支持输出上限参数', 422)
    with s.bind_model_test_access(context=context, session=session, config_id=model_id):
        result = await OpenAICompatibleAdapter(config).chat([{'role': 'user', 'content': 'Reply with OK.'}],
            context=context, session=session, parameters=parameters, context_budget=min(config.context_window, 8192))
    finish_reason = result.get('finish_reason')
    reason = ('output_limit' if result.get('completion_truncated') or finish_reason == 'length' else
              'unexpected_tool_call' if result.get('tool_calls') or finish_reason in ('tool_calls', 'function_call') else
              'content_filtered' if finish_reason == 'content_filter' else
              'empty_visible_output' if not isinstance(result.get('text'), str) or not result['text'].strip() else None)
    if reason is not None:
        message = (f'模型服务已响应，但本次测试达到{parameters["max_tokens"]} token输出上限，尚未得到完整回复；配置已保留'
                   if reason == 'output_limit' else
                   '模型服务已响应，但没有返回可见回复，本次推理尚未验证通过；配置已保留'
                   if reason == 'empty_visible_output' else
                   '模型服务已响应，但回复未正常完成，本次推理尚未验证通过；配置已保留')
        raise AppError('MODEL_TEST_INCOMPLETE', message, 502, {
            'failure_stage': 'model_output', 'reason': reason, 'service_responded': True,
            'inference_verified': False, 'output_token_limit': parameters['max_tokens'],
        })
    return {'inference_verified': True, 'test_kind': 'synthetic_prompt', 'customer_material_sent': False,
            'text': result['text'][:500], 'usage': result['usage'], 'output_token_limit': parameters['max_tokens']}


from app.agents.model_trial import ModelTrialInput,ModelTrialResponse


@router.post('/models/{model_id}/test-prompt',response_model=ModelTrialResponse)
async def prompt_trial(model_id:str,body:ModelTrialInput,request:Request,session=Depends(get_session)):
    from app.agents.model_trial import trial_model
    return await trial_model(model_id=model_id,data=body,context=identity(request),session=session)
