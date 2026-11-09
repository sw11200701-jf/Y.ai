"""Explicit public-text OpenAI-chat profiles, without opaque continuation APIs."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.errors import AppError

class ReasoningProfile(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    profile_id: Literal['aliyun_public_reasoning_v1', 'ark_public_reasoning_v1', 'ark_public_summary_v1', 'deepseek_public_reasoning_v1']
    protocol: Literal['openai_chat_completions'] = 'openai_chat_completions'
    model_id: str = Field(min_length=1, max_length=200)
    model_revision: str = Field(min_length=1, max_length=200, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$')
    transport: Literal['json', 'sse']
    request_control: Literal['none', 'enable_thinking', 'thinking_type']
    mode: Literal['provider_default', 'enabled', 'disabled', 'auto'] = 'provider_default'

    @model_validator(mode='after')
    def supported(self):
        provider = self.profile_id.split('_')[0]
        allowed = {'aliyun': {'none', 'enable_thinking'}, 'ark': {'none', 'thinking_type'}, 'deepseek': {'none', 'thinking_type'}}[provider]
        if self.request_control not in allowed or self.request_control == 'none' and self.mode != 'provider_default':
            raise ValueError('当前profile不支持此思考请求参数')
        if self.mode == 'auto' and (provider != 'ark' or self.request_control != 'thinking_type'):
            raise ValueError('auto仅适用于声明该能力的方舟thinking.type')
        if self.model_revision.lower() in {'latest','auto','default','unknown'}: raise ValueError('须明确模型版本，不能使用漂移别名')
        return self

class PublicReasoning(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    available: bool
    status: Literal['available', 'truncated', 'disabled', 'not_returned', 'redacted']
    profile_id: Literal['aliyun_public_reasoning_v1', 'ark_public_reasoning_v1', 'ark_public_summary_v1', 'deepseek_public_reasoning_v1']
    protocol: Literal['openai_chat_completions']
    model_revision: str = Field(min_length=1, max_length=200)
    profile_verification: Literal['administrator_declared']
    kind: Literal['provider_summary', 'provider_reasoning']
    source: Literal['provider.message.reasoning_content', 'provider.delta.reasoning_content']
    truncated: bool
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    unavailable_reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode='after')
    def coherent(self):
        if self.kind != ('provider_summary' if self.profile_id == 'ark_public_summary_v1' else 'provider_reasoning'):
            raise ValueError('profile内容语义不一致')
        if self.available:
            if self.status not in {'available','truncated'} or not self.text or self.unavailable_reason is not None or len(self.text.encode()) > 8192 or self.truncated != (self.status == 'truncated'):
                raise ValueError('公开文字状态或大小不一致')
        elif self.text is not None or self.status in {'available','truncated'} or not self.unavailable_reason or self.truncated:
            raise ValueError('不可读状态不应包含文字')
        return self

def public_reasoning_view(value):
    """Pure strict projection for01's trace branch, after its current-run ACL."""
    try: return PublicReasoning.model_validate(value).model_dump(mode='json', exclude_none=True)
    except (ValueError, TypeError): return {'available': False, 'status': 'not_adapted', 'unavailable_reason': '公开思考DTO无效或尚未适配'}

def selected_profile(config):
    raw = config.capabilities.get('reasoning_profile')
    if raw is None: return None
    try: profile = ReasoningProfile.model_validate(raw)
    except ValueError: raise AppError('MODEL_REASONING_PROFILE_INVALID', '公开思考profile无效或协议尚未适配', 422) from None
    if profile.model_id != config.model:
        raise AppError('MODEL_REASONING_PROFILE_MISMATCH', '公开思考profile与所选模型ID不一致', 422)
    return profile

def request_options(config, *, tools=None, parameters=None):
    profile = selected_profile(config)
    if profile is None: return {}
    if tools:
        raise AppError('MODEL_REASONING_CONTINUATION_UNSUPPORTED', '此profile尚未适配工具多轮续传，请选无工具调用或已适配模型', 422)
    if profile.profile_id == 'deepseek_public_reasoning_v1' and profile.mode != 'disabled' and set(parameters or {}) & {
            'temperature', 'frequency_penalty', 'presence_penalty', 'top_p'}:
        raise AppError('MODEL_PARAMETER_UNSUPPORTED', '此DeepSeek思考profile未放行采样惩罚或top_p参数，请显式移除，不会静默忽略', 422)
    values = {}
    if profile.mode != 'provider_default':
        if profile.request_control == 'enable_thinking': values['enable_thinking'] = profile.mode == 'enabled'
        elif profile.request_control == 'thinking_type': values['thinking'] = {'type': profile.mode}
    if profile.transport == 'sse': values.update(stream=True, stream_options={'include_usage': True})
    return values


def document_invocation(config, mode='auto', *, contract='document-model-invocation-v2'):
    """Pinned per-document policy, independent of global chat/model settings.

    Alibaba's documented DeepSeek controls:
    https://docs.bailian.console.aliyun.com/zh/model-studio/deepseek-api
    https://help.aliyun.com/zh/model-studio/deep-thinking
    Never guess provider-specific fields for another host or model.
    """
    from urllib.parse import urlsplit
    import re
    if contract not in ('document-model-invocation-v1','document-model-invocation-v2'):raise AppError('MODEL_DOCUMENT_POLICY_INVALID','切片模型执行合同无效',422)
    if mode not in ('auto','direct','model_default'):
        raise AppError('MODEL_DOCUMENT_POLICY_INVALID','切片模型执行模式无效',422)
    options={};profile=selected_profile(config);parsed=urlsplit(config.base_url)
    official=(parsed.scheme=='https' and parsed.port in (None,443)
        and (parsed.hostname in ('dashscope.aliyuncs.com','dashscope-intl.aliyuncs.com')
             or bool(re.fullmatch(r'[a-z0-9-]+\.(?:cn-beijing|ap-southeast-1|us-east-1|eu-central-1|ap-northeast-1)\.maas\.aliyuncs\.com',parsed.hostname or '')))
        and parsed.path.rstrip('/')=='/compatible-mode/v1')
    known_model=config.model in {'deepseek-v4.1-flash-1017','deepseek-v4.1-flash','deepseek-v4-pro','deepseek-v4-pro-0813','deepseek-v4-flash-0731','qwen3.8-27b'}
    if mode=='direct' and profile and profile.request_control!='none':
        # Reuse the already declared provider control, only for this invocation.
        options=({'enable_thinking':False} if profile.request_control=='enable_thinking' else {'thinking':{'type':'disabled'}})
    elif (mode=='direct' and profile is None and parsed.scheme=='https' and parsed.port in (None,443)
            and parsed.hostname=='api.deepseek.com' and parsed.path.rstrip('/') in ('','/v1')
            and config.model in {'deepseek-flash','deepseek-v4-pro'}):
        # Official documented Chat Completions toggle, scoped to this plan.
        # https://api-docs.deepseek.com/guides/thinking_mode/
        options={'thinking':{'type':'disabled'}}
    elif (contract=='document-model-invocation-v2' and mode=='auto' and profile is None
            and parsed.scheme=='https' and parsed.port in (None,443) and parsed.hostname=='api.deepseek.com'
            and parsed.path.rstrip('/') in ('','/v1') and config.model in {'deepseek-flash','deepseek-v4-pro'}):
        # Keep reasoning enabled, but bound its documented effort for document work.
        # https://api-docs.deepseek.com/guides/thinking_mode/
        options={'thinking':{'type':'enabled'},'reasoning_effort':'low'}
    elif mode in ('auto','direct') and profile is None and official and known_model:
        options={'enable_thinking':False}
    elif mode=='direct' and not (profile and profile.mode=='disabled'):
        raise AppError('MODEL_DOCUMENT_CONTROL_UNAVAILABLE','当前模型未声明可关闭思考的协议；请选择跟随模型配置或配置已支持的控制方式',422)
    # Auto respects an explicit administrator reasoning profile; model_default
    # always retains it. No vendor switch, credential access or config write.
    return {'contract':contract,'mode':mode,'model_id':config.model,
        'config_version':config.config_version,'request_options':options}


def document_request_options(config, invocation):
    if invocation is None:return {}
    if not isinstance(invocation,dict) or invocation!=document_invocation(config,invocation.get('mode'),contract=invocation.get('contract')):
        raise AppError('MODEL_DOCUMENT_POLICY_CHANGED','模型执行策略与固定任务不符，未发送请求',409)
    return invocation['request_options']

def public_reasoning(message, config, *, private_messages=(), finish_reason=None):
    profile = selected_profile(config)
    if profile is None: return None
    base = {'profile_id': profile.profile_id, 'protocol': profile.protocol, 'model_revision': profile.model_revision,
        'profile_verification': 'administrator_declared', 'kind': 'provider_summary' if profile.profile_id == 'ark_public_summary_v1' else 'provider_reasoning',
        'source': 'provider.'+('delta.' if profile.transport == 'sse' else 'message.')+'reasoning_content', 'truncated': False}
    def absent(status, reason): return {**base, 'available': False, 'status': status, 'unavailable_reason': reason}
    if profile.mode == 'disabled': return absent('disabled', '此模型配置已关闭公开思考模式')
    value = message.get('reasoning_content')
    if not isinstance(value, str) or not value.strip(): return absent('not_returned', '本次供应商回复没有已适配的可读公开思考文字')
    if '[REDACTED]' in value or any(isinstance(item, dict) and isinstance(item.get('content'), str) and item['content']
        and (item.get('role') == 'system' or len(item['content']) >= 32) and item['content'] in value for item in private_messages):
        return absent('redacted', '公开思考含脱敏标记或完整私有上下文，已隐藏')
    text = value[:4000].encode('utf-8')[:8192].decode('utf-8', errors='ignore')
    truncated = text != value or finish_reason == 'length'
    return {**base, 'available': True, 'status': 'truncated' if truncated else 'available', 'text': text, 'truncated': truncated}
