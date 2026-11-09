"""One bounded HTTP execution path for console tests and agent calls."""

import asyncio
import inspect
import json
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.connections.security import approved_address, origin, redact, validate_base_url
from .errors import PluginError
from .specs import EndpointDraft, validate_arguments
from .fields import map_output, normalize_arguments, request_parts


async def execute_tool(binding: dict, arguments: dict, context: dict) -> dict:
    """Execute a server-resolved immutable tool. Never accept these fields from a model.

    context contains trusted allowed_origins, allowed_entities, secret_resolver and
    optional test transport. The public service resolves all three binding IDs first.
    """
    started = time.monotonic()
    endpoint = EndpointDraft.model_validate(binding["endpoint"])
    connection = binding["connection"]
    if not connection.get("enabled", True) or binding.get("version_status", "published") != "published":
        raise PluginError("INVALID_BINDING", "工具版本或连接已停用", 422)
    if endpoint.input_fields is not None:
        arguments = normalize_arguments(endpoint.input_fields, arguments)
    validate_arguments(endpoint.input_schema, arguments)
    entities = context.get("allowed_entities", ["SYNTHETIC-DEMO"])
    if "entity" in arguments and arguments["entity"] not in entities:
        raise PluginError("CONNECTION_BLOCKED", "无权查询该主体", 403)
    try:
        serialized = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise PluginError("VALIDATION_ERROR", "业务参数不是有效的有限 JSON", 422) from exc
    if len(serialized.encode("utf-8")) > 32768:
        raise PluginError("VALIDATION_ERROR", "业务参数超过 32 KiB", 422)
    base_url = validate_base_url(connection["base_url"])
    p = urlsplit(base_url)
    kwargs: dict[str, Any] = {}
    custom_headers = {}
    request_path = endpoint.path
    if endpoint.input_fields is not None:
        request_path, query, body_values, custom_headers = request_parts(endpoint.input_fields, arguments, endpoint.path, endpoint.method, connection)
        kwargs["params"] = query
        if endpoint.method == "POST":
            if endpoint.request_encoding == "json":
                kwargs["json"] = body_values
            else:
                if any(not isinstance(v,(str,int,float,bool,list)) or (isinstance(v,list) and any(not isinstance(x,(str,int,float,bool)) for x in v)) for v in body_values.values()):
                    raise PluginError("VALIDATION_ERROR", "表单仅支持标量或标量数组，不读取本地文件", 422)
                def scalar(value):return str(value).lower() if type(value) is bool else str(value)
                if endpoint.request_encoding == "form_urlencoded":
                    kwargs["data"] = {k:[scalar(x) for x in v] if isinstance(v,list) else scalar(v) for k,v in body_values.items()}
                else:
                    kwargs["files"] = [(k,(None,scalar(x))) for k,v in body_values.items() for x in (v if isinstance(v,list) else [v])]
    elif endpoint.method == "GET":
        if any(isinstance(v, dict) or (isinstance(v, list) and any(isinstance(x, (dict, list)) for x in v)) for v in arguments.values()):
            raise PluginError("VALIDATION_ERROR", "GET 参数只支持标量和标量数组", 422)
        kwargs["params"] = arguments
    else:
        if endpoint.request_encoding != "json":
            raise PluginError("VALIDATION_ERROR", "表单编码需要显式typed输入字段", 422)
        kwargs["json"] = arguments
    if p.hostname not in connection.get("allowed_hosts", []):
        raise PluginError("CONNECTION_BLOCKED", "连接主机未获管理员允许", 403)
    address = await approved_address(base_url, context["allowed_origins"])
    host = f"[{address}]" if ":" in address else address
    port = p.port or (443 if p.scheme == "https" else 80)
    # Connect to the vetted literal IP, avoiding a second DNS lookup/rebinding.
    target = f"{p.scheme}://{host}:{port}{p.path.rstrip('/')}{request_path}"
    headers = {"Accept": "application/json", "Host": p.netloc, **custom_headers}
    secret = ""
    auth = connection.get("auth_type", "none")
    if auth != "none":
        resolver = context.get("secret_resolver")
        if not resolver or not connection.get("secret_ref"):
            raise PluginError("SERVICE_UNAVAILABLE", "连接凭据尚未配置", 503)
        try:
            value = resolver(connection["secret_ref"])
            secret = await value if inspect.isawaitable(value) else value
        except Exception as exc:
            raise PluginError("SERVICE_UNAVAILABLE", "无法读取本机连接凭据", 503) from exc
        if not isinstance(secret, str) or not secret or "\r" in secret or "\n" in secret:
            raise PluginError("SERVICE_UNAVAILABLE", "连接凭据不可用", 503)
        if auth == "bearer":
            headers["Authorization"] = f"Bearer {secret}"
        elif auth == "api_key":
            headers[connection.get("api_key_header", "X-API-Key")] = secret
        else:
            raise PluginError("INVALID_BINDING", "不支持的鉴权方式", 422)
    maximum = min(int(context.get("max_output_bytes", 1048576)), 1048576)
    timeout = min(float(context.get("tool_timeout_seconds", 10)), 30)
    try:
        async with asyncio.timeout(timeout), httpx.AsyncClient(
            timeout=httpx.Timeout(timeout), follow_redirects=False, trust_env=False,
            transport=context.get("http_transport"), limits=httpx.Limits(max_connections=4),
        ) as client:
            async with client.stream(endpoint.method, target, headers=headers,
                                     extensions={"sni_hostname": p.hostname}, **kwargs) as response:
                if response.status_code >= 300:
                    raise PluginError("TOOL_ERROR", f"接口返回 HTTP {response.status_code}", 502)
                if "application/json" not in response.headers.get("content-type", "").lower():
                    raise PluginError("TOOL_ERROR", "接口未返回 JSON", 502)
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > maximum:
                        raise PluginError("TOOL_ERROR", "接口输出超过允许大小，未使用截断财务数据", 502)
                    chunks.extend(chunk)
                try:
                    body = json.loads(chunks, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, RecursionError) as exc:
                    raise PluginError("TOOL_ERROR", "接口 JSON 无效", 502) from exc
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise PluginError("TOOL_TIMEOUT", "接口请求超时", 504) from exc
    except httpx.HTTPError as exc:
        raise PluginError("TOOL_ERROR", "接口网络请求失败", 502) from exc
    from jsonschema import Draft202012Validator, FormatChecker
    preview_body = body
    if isinstance(body, dict) and (body.get("success") is False or body.get("error")):
        raise PluginError("TOOL_ERROR", "接口报告业务失败", 502)
    if endpoint.output_fields is not None:
        body = map_output(endpoint.output_fields, body)
    if not Draft202012Validator(endpoint.output_schema, format_checker=FormatChecker()).is_valid(body):
        raise PluginError("TOOL_ERROR", "接口响应不符合已发布工具的 Schema", 502)
    clean = redact(body, (secret,))
    preview = redact(preview_body, (secret,)) if not endpoint.output_fields else clean
    preview_json = json.dumps(preview, ensure_ascii=False)
    if len(preview_json.encode()) > 32768:
        preview = {"preview": preview_json[:8000], "truncated": True, "response_bytes": len(preview_json.encode())}
    execution_id = "toolrun_" + uuid.uuid4().hex
    source_ref = f"http:{binding['plugin_version_id']}:{binding['tool_id']}:{binding['connection_id']}:{execution_id}"
    return {"data": clean, "response_preview": preview, "output_values": clean if endpoint.output_fields is not None else None, "source_ref": source_ref, "audit": {
        "execution_id": execution_id, "status": "succeeded", "plugin_version_id": binding["plugin_version_id"],
        "tool_id": binding["tool_id"], "connection_id": binding["connection_id"],
        "method": endpoint.method, "response_bytes": len(chunks),
        "duration_ms": round((time.monotonic() - started) * 1000),
    }}
