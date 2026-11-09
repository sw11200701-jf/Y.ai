"""Explicitly configured model adapter. No fallback from model failure to Demo."""

import asyncio
import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from app.connections.security import approved_address, redact, validate_base_url
from app.errors import AppError
from app.secrets import read_secret


@dataclass(frozen=True)
class ModelConfig:
    # Internal transport DTO only. Public callers always resolve a catalog ID.
    base_url: str
    model: str
    secret_ref: str | None = field(default=None, repr=False)
    timeout_seconds: float = 20
    allow_private_network: bool = True
    model_config_id: str | None = None
    config_version: str | None = None
    capabilities: dict = field(default_factory=lambda: {"parameters": ["temperature", "max_tokens"], "tools": True, "json_object": True, "json_schema": False})
    context_window: int = 32768
    max_output_tokens: int = 4096


def load_config(config_id: str, *, context, session, expected_version=None):
    from app.model_catalog.service import resolve_runtime_model
    if context is None or session is None:
        raise AppError("AUTH_REQUIRED", "模型解析需要当前调用者会话", 401)
    return resolve_runtime_model(config_id, context=context, session=session, expected_version=expected_version)


def model_status(*, context, session, config_id=None) -> dict:
    from app.model_catalog.service import usable_catalog
    items = usable_catalog(session, context)["items"]
    ids = [item["id"] for item in items if config_id is None or item["id"] == config_id]
    return {"configured": bool(ids), "configuration_ids": ids, "verified": False,
            "reason": None if ids else "MODEL_NOT_CONFIGURED", "default_mode": "model",
            "message": "已配置模型接口，尚未验证真实推理" if ids else "尚未配置获准使用的真实模型"}


def effective_parameters(parameters, config):
    from pydantic import ValidationError
    from .specs import ModelParameters
    from .prompts import field_error
    raw = parameters if parameters is not None else {}
    if type(raw) is not dict:
        field_error("模型参数须为对象", ("config", "model_parameters"))
    allowed = set(config.capabilities.get("parameters", []))
    for key in raw:
        if key not in allowed:
            field_error("所选模型不支持此参数", ("config", "model_parameters", key), "MODEL_PARAMETER_UNSUPPORTED")
    try:
        values = ModelParameters.model_validate(raw).model_dump(exclude_unset=True)
    except ValidationError as exc:
        from app.plugins.validation import details
        errors = details(exc.errors())
        for error in errors:
            error["loc"] = ["config", "model_parameters", *error["loc"]]
        raise AppError("VALIDATION_ERROR", "模型参数类型、范围或空值无效", 422, errors) from None
    if parameters is None and "temperature" in allowed:
        values.setdefault("temperature", 0)
    if "max_tokens" not in allowed:
        field_error("所选模型未声明输出Token上限参数，无法进行有界调用", ("config", "model_parameters", "max_tokens"), "MODEL_PARAMETER_UNSUPPORTED")
    values.setdefault("max_tokens", min(4096, config.max_output_tokens))
    if values["max_tokens"] > config.max_output_tokens:
        field_error("输出Token上限超过所选模型允许范围", ("config", "model_parameters", "max_tokens"))
    return values


def chat_payload(config, messages, *, parameters=None, response_format=None, tools=None, document_policy=None):
    """One pure payload builder shared by durable preflight and the actual send."""
    from .prompts import normalize_response_format
    from .reasoning_profiles import request_options
    values=effective_parameters(parameters,config)
    output=normalize_response_format(response_format)
    payload={'model':config.model,**values,'messages':messages}
    payload.update(request_options(config,tools=tools,parameters=values))
    from .reasoning_profiles import document_request_options
    payload.update(document_request_options(config,document_policy))
    if tools:payload.update(tools=tools,tool_choice='auto',parallel_tool_calls=False)
    if output:
        payload['response_format']={'type':output['type']}
        if output['type']=='json_schema':
            payload['response_format']['json_schema']={'name':output.get('name','structured_output'),'schema':output['schema'],'strict':output.get('strict',False)}
    return payload


class _ReceivedModelResponse(dict):
    """Local transport carrier. Its receipt is never a JSON field or SDK state."""

    def __init__(self, payload, receipt):
        super().__init__(payload)
        self._receipt = receipt


def _response_validation_failure(response, error):
    # JSON/provider fields cannot manufacture this local carrier or receipt.
    if type(response) is _ReceivedModelResponse:
        from app.execution.effects import observed_model_validation_failure
        return observed_model_validation_failure(response._receipt, error)
    return error  # Protocol mocks / pure callers have no observed HTTP proof.


def parse_json_reply(text):
    """Accept JSON or one enclosing JSON fence, never prose/partial JSON repair."""
    import re
    if not isinstance(text,str):raise ValueError('JSON text required')
    value=text.strip().removeprefix('\ufeff').strip()
    fence=re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```',value,re.IGNORECASE)
    if fence:value=fence.group(1)
    def unique(pairs):
        result={}
        for key,item in pairs:
            if key in result:raise ValueError('Duplicate JSON key')
            result[key]=item
        return result
    return json.loads(value,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))


class OpenAICompatibleAdapter:
    """A configurable protocol adapter for an administrator's model endpoint."""

    def __init__(self, config: ModelConfig):
        self.config = config

    def fresh_config(self):
        if getattr(self.config, "model_config_id", None):
            from .model_access import current_model_access
            context, session = current_model_access()
            return load_config(self.config.model_config_id, context=context, session=session, expected_version=self.config.config_version)
        return self.config  # Internal raw transport fixtures; never an API config input.

    async def _request(self, payload: dict) -> dict:
        config = self.fresh_config()
        from .vision import validate_payload, revalidate_images
        image_secrets = validate_payload(payload)
        parsed = urlsplit(config.base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        from app.model_catalog.security import approved_model_address
        address = await approved_model_address(config)
        host = f"[{address}]" if ":" in address else address
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        target = f"{parsed.scheme}://{host}:{port}{parsed.path.rstrip('/')}/chat/completions"
        headers = {"Host": parsed.netloc, "Accept": "text/event-stream" if payload.get('stream') else "application/json"}
        token = ""
        if config.secret_ref:
            try:
                token = read_secret(config.secret_ref)
            except Exception as exc:
                raise AppError("MODEL_CREDENTIAL_MISSING", "模型凭据不可用，请管理员重新核对", 409) from None
            if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
                raise AppError("MODEL_CREDENTIAL_MISSING", "模型凭据不可用，请管理员重新核对", 409)
            headers["Authorization"] = f"Bearer {token}"
        try:
            self.fresh_config()
            revalidate_images()
            from app.execution.document_request_wait import transport_wait
            with transport_wait(config) as timeout_seconds:
                async with asyncio.timeout(timeout_seconds), httpx.AsyncClient(
                    trust_env=False, follow_redirects=False, timeout=timeout_seconds,
                ) as client:
                    async with client.stream("POST", target, headers=headers, json=payload,
                                             extensions={"sni_hostname": parsed.hostname}) as response:
                        if response.status_code != 200:
                            from .model_diagnostics import provider_http_error
                            raise await provider_http_error(response, secrets=(token, *image_secrets))
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > 262144:
                                raise AppError("MODEL_ERROR", "真实模型响应超过限制", 502)
                            body.extend(chunk)
            revalidate_images()
            # Only a normally exhausted, bounded HTTP stream can mint a receipt.
            # Headers, partial bodies, disconnects, timeouts and size aborts cannot.
            from app.execution.effects import observe_complete_model_response, observed_model_validation_failure
            receipt = observe_complete_model_response()
            try:
                if payload.get('stream'):
                    from .model_sse import decode_chat_sse
                    output = decode_chat_sse(body)
                else:
                    output = json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(output, dict):
                    raise ValueError()
                return _ReceivedModelResponse(redact(output, (token, *image_secrets)), receipt)
            except (ValueError, TypeError, RecursionError):
                raise observed_model_validation_failure(receipt,
                    AppError("MODEL_ERROR", "真实模型JSON响应无效", 502)) from None
        except AppError:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise AppError("MODEL_TIMEOUT", "真实模型请求超过时限", 504,
                {"failure_stage": "transport", "reason": "timeout"}) from None
        except Exception as exc:
            raise AppError("MODEL_ERROR", "真实模型网络或JSON响应无效", 502,
                {"failure_stage": "transport", "reason": "network_or_response"}) from None

    async def chat(self, messages: list[dict], *, tools: list[dict] | None = None,
                   parameters: dict | None = None, context_budget: int | None = None, json_output: bool = False,
                   response_format: dict | None = None, context=None, session=None, document_policy=None,
                   _document_stage_validation: bool = False) -> dict:
        if context is not None or session is not None:
            from .model_access import bind_model_access
            with bind_model_access(context=context, session=session):
                return await self.chat(messages, tools=tools, parameters=parameters, context_budget=context_budget,
                                       json_output=json_output, response_format=response_format, document_policy=document_policy,
                                       _document_stage_validation=_document_stage_validation)
        from .prompts import normalize_response_format, validate_output, field_error
        config = self.fresh_config()
        parameters = effective_parameters(parameters, config)
        max_tokens = parameters["max_tokens"]
        budget = min(8192, config.context_window) if context_budget is None else context_budget
        if type(budget) is not int or not 512 <= budget <= config.context_window:
            field_error("上下文预算超过模型允许范围或类型无效", ("config", "context_budget"))
        output_format = normalize_response_format(response_format if response_format is not None else ({"type": "json_object"} if json_output else None))
        if _document_stage_validation and (tools or not document_policy or document_policy.get('contract') not in ('document-model-invocation-v1','document-model-invocation-v2')):
            raise AppError('MODEL_DOCUMENT_POLICY_INVALID','阶段结构校验仅允许固定的无工具文档任务',422)
        if tools and not config.capabilities.get("tools"):
            raise AppError("MODEL_CAPABILITY_UNAVAILABLE", "所选模型未声明支持工具调用", 422)
        if output_format and output_format["type"] != "text" and not config.capabilities.get(output_format["type"]):
            field_error("所选模型不支持该结构化输出模式", ("config", "response_format"), "MODEL_CAPABILITY_UNAVAILABLE")
        from .vision import prepare_images
        wire_messages, image_reserve = prepare_images(config, messages)
        estimate = (len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False).encode()) + 2) // 3 + image_reserve
        if estimate + max_tokens > budget:
            raise AppError("CONTEXT_BUDGET_EXCEEDED", "消息/工具Schema与输出预算超过上下文预算（保守估算）", 422,
                           details={"estimated_input_tokens": estimate, "context_budget": budget})
        payload = chat_payload(config, wire_messages, parameters=parameters, response_format=output_format, tools=tools, document_policy=document_policy)
        output = await self._request(payload)
        try:
            message = output["choices"][0]["message"]
            text = message.get("content")
            if text is None: text = ""
            calls = message.get("tool_calls")
            if calls is None: calls = []
            if not isinstance(text, str) or not isinstance(calls, list) or len(calls) > 1:
                raise ValueError()
            safe_calls = []
            for call in calls:
                function = call["function"]
                arguments = json.loads(function["arguments"], parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(arguments, dict) or not isinstance(function["name"], str):
                    raise ValueError()
                safe_calls.append({"id": str(call.get("id", "call_0"))[:100], "name": function["name"], "arguments": arguments})
            usage = {key: value for key, value in output.get("usage", {}).items()
                     if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(value) is int and value >= 0}
            if usage.get("total_tokens", 0) > budget or usage.get("completion_tokens", 0) > max_tokens:
                raise AppError("CONTEXT_BUDGET_EXCEEDED", "模型报告的用量超过上下文预算", 422)
            # Only explicitly selected public-text profiles may expose reasoning.
            result = {"text": text, "tool_calls": safe_calls, "usage": usage, "estimated_input_tokens": estimate}
            details=output.get('usage',{}).get('completion_tokens_details')
            reasoning_tokens=details.get('reasoning_tokens') if isinstance(details,dict) else None
            result['response_usage_metadata']={'output_limit':max_tokens,**usage,
                **({'reasoning_tokens':reasoning_tokens} if type(reasoning_tokens) is int and reasoning_tokens>=0 else {})}
            from .reasoning import provider_reasoning
            result["reasoning"] = provider_reasoning(message, config.capabilities, private_messages=messages)
            finish_reason = output["choices"][0].get("finish_reason")
            from .reasoning_profiles import public_reasoning
            projected = public_reasoning(message, config, private_messages=messages, finish_reason=finish_reason)
            if projected is not None:
                result['reasoning'] = projected
            if isinstance(finish_reason, str) and finish_reason in {"stop", "length", "tool_calls", "content_filter", "function_call"}:
                result["finish_reason"] = finish_reason
                result["completion_truncated"] = finish_reason == "length"
            if not _document_stage_validation and not safe_calls and output_format and output_format["type"] != "text":
                result["structured_output"] = validate_output(text, output_format)
            # Preserve the private completed-HTTP capability through domain
            # validation. It is an attribute, never a JSON field/checkpoint.
            return _ReceivedModelResponse(result, output._receipt) if type(output) is _ReceivedModelResponse else result
        except AppError as exc:
            raise _response_validation_failure(output, exc) from None
        except Exception as exc:
            raise _response_validation_failure(output,
                AppError("MODEL_ERROR", "真实模型消息或工具参数格式无效", 502)) from None

    async def select(self, question: str, tools: list[dict], query: dict, *, context=None, session=None) -> dict:
        if context is not None or session is not None:
            from .model_access import bind_model_access
            with bind_model_access(context=context, session=session):
                return await self.select(question, tools, query)
        config = self.fresh_config()
        if not config.capabilities.get("tools"):
            raise AppError("MODEL_CAPABILITY_UNAVAILABLE", "所选模型未声明支持工具调用", 422)
        descriptions = [{"type": "function", "function": {
            "name": f"bound_tool_{index}", "description": tool["description"][:2000],
            "parameters": tool["input_schema"],
        }} for index, tool in enumerate(tools)]
        descriptions.append({"type": "function", "function": {
            "name": "reject_intent", "description": "Reject requests outside annual financial comparison/reporting.",
            "parameters": {"type": "object", "properties": {"reason": {"type": "string", "enum": ["unsupported_intent"]}},
                           "required": ["reason"], "additionalProperties": False}}})
        from .reasoning_profiles import request_options
        request_options(config, tools=descriptions)  # This legacy selector also requires a tool round.
        payload = {"model": config.model, **effective_parameters({}, config),
                   "messages": [{"role": "system", "content": (
                       "You are a bounded financial query planner. For annual financial comparison/reporting, "
                       "select exactly one provided read-only tool. For other intents call reject_intent. "
                       "Do not compute, execute, change the authorized query, "
                       "or provide addresses/credentials. Authoritative query: " + json.dumps(query, ensure_ascii=False))},
                       {"role": "user", "content": question}],
                   "tools": descriptions, "tool_choice": "required", "parallel_tool_calls": False}
        try:
            output = await self._request(payload)
            calls = output["choices"][0]["message"]["tool_calls"]
            if len(calls) != 1:
                raise ValueError()
            call = calls[0]["function"]
            if call["name"] == "reject_intent":
                raise AppError("VALIDATION_ERROR", "真实模型识别的问题不在年度财务对比范围内", 422)
            aliases = {f"bound_tool_{i}": tool["binding_id"] for i, tool in enumerate(tools)}
            return {"intent": "annual_comparison", "tool_calls": [{"binding_id": aliases[call["name"]],
                                                                       "arguments": json.loads(call["arguments"])}]}
        except AppError:
            raise
        except Exception as exc:
            raise AppError("SERVICE_UNAVAILABLE", "真实模型未返回有效的单工具计划", 503) from exc
