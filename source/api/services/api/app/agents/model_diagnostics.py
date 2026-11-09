"""Bounded, allowlisted transport diagnostics; provider text is never public."""
import asyncio
import json
import re
from app.errors import AppError

_CODES=frozenset({'InvalidApiKey','InvalidParameter','ModelNotFound','AccessDenied','Throttling',
    'InternalError','ServiceUnavailable','QuotaExhausted','InvalidParameter.Model','InvalidParameterValue',
    'invalid_api_key','invalid_request_error','model_not_found','insufficient_quota','rate_limit_exceeded',
    'PermissionDenied','Arrearage','DataInspectionFailed','UnsupportedOperation'})
_REQUEST_ID=re.compile(r'(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|req_[A-Za-z0-9]{16,64})\Z')


def _request_id(value,secrets):
    if isinstance(value,str) and _REQUEST_ID.fullmatch(value) and not any(s and s in value for s in secrets):return value
    return None


async def provider_http_error(response,*,secrets=()):
    """Read at most8KiB of an already-open response, without trusting its message."""
    status=response.status_code
    reason,message={400:('request_invalid','模型服务未接受请求参数，请核对模型ID、能力和参数'),
        401:('authentication','模型服务认证未通过，请核对平台Key及其所属地域'),
        403:('provider_access','模型服务拒绝本次调用，请核对模型开通状态、业务空间和地域权限'),
        404:('model_or_endpoint','模型服务未找到模型或接口，请核对模型ID、服务地址、地域和业务空间'),
        429:('rate_or_quota','模型服务触发限流或额度限制，请核对调用频率、配额和账单')}.get(status,
            ('provider_unavailable','模型服务暂时异常，请保留请求编号后检查服务状态') if status>=500 else
            ('redirect','模型服务返回重定向，请配置最终的兼容API基址') if 300<=status<400 else
            ('provider_rejected','模型服务未完成本次请求，请核对平台配置'))
    detail={'failure_stage':'provider_http','upstream_status':status,'reason':reason}
    identifier=next((v for k in ('x-request-id','x-dashscope-request-id','x-acs-request-id')
        if (v:=_request_id(response.headers.get(k),secrets))),None)
    body=bytearray();complete=False
    try:
        async with asyncio.timeout(2):
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if len(body)+len(chunk)>8192:break
                body.extend(chunk)
            else:complete=True
        value=json.loads(body) if complete else None
        if isinstance(value,dict):
            error=value.get('error');error=error if isinstance(error,dict) else {}
            code=error.get('code') or value.get('code') or value.get('Code')
            if isinstance(code,str) and code in _CODES and not any(s and s in code for s in secrets):detail['provider_error_code']=code
            if identifier is None:
                identifier=next((v for k in ('request_id','requestId','RequestId') if (v:=_request_id(value.get(k),secrets))),None)
    except Exception:pass # A partial/oversized/invalid error body cannot hide the observed HTTP status.
    if detail.get('provider_error_code')=='Arrearage':
        detail['reason']='account_arrearage';message='模型服务账户欠费，需恢复账户可用状态；本次未返回可验收的模型输出'
    elif detail.get('provider_error_code') in ('QuotaExhausted','insufficient_quota'):
        detail['reason']='account_quota';message='模型服务账户额度不足，本次未返回可验收的模型输出'
    elif status==402:
        detail['reason']='payment_required';message='模型服务要求检查付费状态或账户额度，本次未返回可验收的模型输出'
    if identifier:detail['provider_request_id']=identifier
    # Keep gateway status/code distinct from this application's RBAC401/403/404.
    # This is NOT a completed-model receipt and must not alter effect replay rules.
    return AppError('MODEL_ERROR',f'{message}（上游HTTP {status}）',502,detail)

