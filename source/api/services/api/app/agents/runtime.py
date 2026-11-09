"""Bounded Agent execution. Finance calculation/report scheduling stay in the runner."""

import json
from copy import deepcopy

from jsonschema import Draft202012Validator

from app.contracts import FactQuery, ToolBinding
from app.errors import AppError
from app.plugins.errors import PluginError
from app.plugins.service import resolve_binding
from app.plugins.specs import validate_arguments
from .specs import binding_id


def check_inputs(config, inputs):
    try:
        validate_arguments(config.get("input_schema", {"type": "object", "properties": {}, "additionalProperties": False}), inputs)
    except PluginError as exc:
        raise AppError(exc.code, "智能体输入变量无效：" + exc.message, exc.status_code) from exc


def prepare_tools(config, context, session):
    settings = {s["binding_id"]: s for s in config.get("tool_settings", [])}
    tools = []
    for raw in config.get("bindings", []):
        binding = ToolBinding.model_validate(raw)
        key = binding_id(binding)
        setting = settings.get(key, {"parameter_mode": "automatic", "fixed_arguments": {}, "enabled": True})
        if not setting.get("enabled", True):
            continue
        resolved = resolve_binding(binding, context, session)
        schema = resolved["endpoint"]["input_schema"]
        if setting.get("parameter_mode") == "fixed":
            try:
                validate_arguments(schema, setting["fixed_arguments"])
            except PluginError as exc:
                raise AppError(exc.code, "固定工具参数无效", exc.status_code) from exc
        tools.append({"binding": binding, "binding_id": key, "tool_id": binding.tool_id, "setting": setting,
                      "description": resolved["tool_description"], "input_schema": schema})
    return tools


def has_financial_query(query):
    return bool(query.entity or query.periods or query.metric_codes or query.batch_id)


def financial_tool(tool):
    # These fields already bind a plugin to the immutable financial query below.
    return bool({'entity','periods'} & set(tool['input_schema'].get('properties',{})))


def authorize_query(query, context, *, required=False):
    if required or has_financial_query(query):
        if not query.entity or query.entity not in context.allowed_entities:
            raise AppError('CONNECTION_BLOCKED','财务查询需要明确且已授权的主体',403)


def actual_arguments(tool, proposed, query, *, context=None):
    if context is not None:authorize_query(query,context,required=financial_tool(tool))
    if tool["setting"].get("parameter_mode") == "fixed":
        if proposed:
            raise AppError("CONNECTION_BLOCKED", "模型不能覆盖固定工具参数", 403)
        arguments = deepcopy(tool["setting"]["fixed_arguments"])
    else:
        arguments = deepcopy(proposed)
    properties = tool["input_schema"].get("properties", {})
    for key in ("entity", "periods"):
        if key in properties:
            authorized = getattr(query, key)
            if key in arguments and arguments[key] != authorized:
                raise AppError("CONNECTION_BLOCKED", "工具参数不能更改已授权主体与期间", 403)
            arguments[key] = authorized
    try:
        validate_arguments(tool["input_schema"], arguments)
    except PluginError as exc:
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    return arguments


def bounded_output(output, config):
    schema = config.get("output_schema", {"type": "object"})
    structured = output.get("structured_output")
    if not Draft202012Validator(schema).is_valid(structured):
        raise AppError("OUTPUT_SCHEMA_MISMATCH", "智能体最终输出不符合配置的结构Schema", 422)
    if len(json.dumps(output, ensure_ascii=False, allow_nan=False).encode()) > 1048576:
        raise AppError("RESOURCE_LIMIT", "智能体调试结果超过1MiB输出上限", 422)
    return output


async def run_agent_config(*, config, prompt, query: FactQuery, inputs, context, session, run_id):
    """Compatibility import only; execution is owned by the persistent parent graph."""
    raise AppError("NODE_UNAVAILABLE", "智能体须通过统一持久运行入口执行，不能建立旁路循环", 503)
