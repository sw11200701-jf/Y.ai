"""Frozen v2 node handlers. Missing model configuration is an explicit failure."""

import json
import re

from jsonschema import Draft202012Validator
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.connections.security import redact
from app.contracts import BusinessContext, FactQuery, ToolBinding
from app.errors import AppError
from app.plugins.service import execute_tool
from app.plugins.specs import check_schema
from .model_adapter import OpenAICompatibleAdapter, load_config
from .langchain_adapter import LangChainAdapter
from .service import execute_agent
from .specs import ModelParameters
from .prompts import render_messages


def parse_json(text):
    try:
        return json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (TypeError, ValueError, RecursionError) as exc:
        raise AppError("VALIDATION_ERROR", "输出不是有效的有限JSON", 422) from exc


def adapter(config, *, context, session):
    if not config.get("model_config_id"):
        raise AppError("MODEL_NOT_CONFIGURED", "该节点未选择管理员模型配置ID", 503)
    resolved = load_config(config["model_config_id"], context=context, session=session, expected_version=config.get("model_config_version"))
    return LangChainAdapter(config["model_config_id"], expected_version=resolved.config_version)


async def json_model(config, instruction, text, *, context, session):
    result = await adapter(config, context=context, session=session).chat([
        {"role": "system", "content": instruction + " Return a JSON object only, without hidden reasoning."},
        {"role": "user", "content": text},
    ], context=context, session=session, json_output=True, parameters=config.get("model_parameters"), context_budget=config.get("context_budget"))
    if result["tool_calls"]:
        raise AppError("MODEL_ERROR", "此节点不允许模型调用工具", 502)
    from .reasoning import unavailable
    metadata={"reasoning":result.get("reasoning",unavailable())}
    metadata.update({k:result[k] for k in ("finish_reason","completion_truncated") if k in result})
    return parse_json(result["text"]),metadata


async def execute_node(*, node_type: str, config: dict, inputs: dict, context: BusinessContext, session: Session, run_id: str) -> dict:
    warnings, refs, selected = [], [], None
    model_metadata={}
    if node_type in {"http", "plugin"}:
        try:
            binding = ToolBinding.model_validate(config.get("binding"))
        except ValidationError as exc:
            raise AppError("INVALID_BINDING", "HTTP/插件节点需要已发布绑定，不能指定任意URL", 422) from exc
        if set(config) - {"binding", "arguments"} or set(inputs) - {"arguments", "message"}:
            raise AppError("CONNECTION_BLOCKED", "HTTP节点只接受绑定和业务参数", 403)
        arguments = inputs.get("arguments", config.get("arguments", {}))
        if inputs.get("message") is not None:
            from .messages import agent_message
            resolved = await agent_message(message=inputs["message"], prompt="", inputs={}, context=context, session=session)
            merged = dict(resolved["inputs"])
            if any(k in merged and merged[k] != v for k, v in arguments.items()):
                raise AppError("MESSAGE_INPUT_CONFLICT", "消息参数与工具参数冲突", 422)
            arguments = {**merged, **arguments}
        result = await execute_tool(binding=binding, arguments=arguments, context=context, session=session)
        outputs = result.model_dump(mode="json")
        refs = result.source_refs
    elif node_type == "agent":
        if not config.get("agent_id"):
            raise AppError("INVALID_BINDING", "Agent节点需要显式agent_id", 422)
        outputs = await execute_agent(agent_id=config["agent_id"], agent_version_id=config.get("agent_version_id"),
            prompt=inputs.get("prompt", ""), query=FactQuery.model_validate(inputs.get("query", {})), inputs=inputs.get("inputs", {}),
            context=context, session=session, run_id=run_id, message=inputs.get("message"))
        refs, warnings = outputs.get("source_refs", []), outputs.get("warnings", [])
    elif node_type == "llm":
        node_inputs = dict(inputs)
        enabled = config.get('vision_enabled', False)
        if type(enabled) is not bool:
            raise AppError('VISION_INPUT_INVALID', '视觉开关须为布尔值', 422)
        image_ids = inputs.get('image_attachment_ids', [])
        if inputs.get("message") is not None:
            from .messages import agent_message
            resolved = await agent_message(message=inputs["message"], prompt=node_inputs.get("prompt") or "", inputs={}, allow_images=enabled, context=context, session=session)
            if 'image_attachment_ids' not in inputs: image_ids = resolved['image_attachment_ids']
            if "prompt_messages" not in config:
                node_inputs["prompt"] = resolved["text"]
            node_inputs["context"] = {"context": inputs.get("context"), "documents": resolved.get("documents", [])}
            if "prompt_messages" in config:
                node_inputs["context"]["message_text"] = resolved["text"]
            refs.extend(ref for doc in resolved.get("documents", []) for ref in doc.get("source_refs", []))
        messages, output_format = render_messages(config, node_inputs)
        if image_ids and not enabled:
            raise AppError('MODEL_VISION_DISABLED', '此LLM节点未开启视觉输入', 422)
        if enabled and not image_ids:
            raise AppError('VISION_INPUT_INVALID', '已开启视觉，请选择本次消息中的图片', 422)
        from .vision import bind_images, selected_source_refs
        from .model_access import bind_model_access
        with bind_images(image_ids, context=context, session=session), bind_model_access(context=context, session=session):
            result = await adapter(config, context=context, session=session).chat(messages, context=context, session=session,
                parameters=config.get("model_parameters"), context_budget=config.get("context_budget"), response_format=output_format)
            refs.extend(selected_source_refs())
        if result["tool_calls"]:
            raise AppError("MODEL_ERROR", "LLM节点没有工具授权", 502)
        outputs = {"text": result["text"], "mode": "model", "usage": result["usage"]}
        from .reasoning import unavailable
        outputs["reasoning"] = result.get("reasoning", unavailable())
        for key in ("finish_reason", "completion_truncated"):
            if key in result:outputs[key]=result[key]
        if output_format and output_format["type"] != "text":
            # The trusted adapter already validates this exact response while it
            # still owns the transient HTTP receipt. Never persist that receipt.
            outputs["structured_output"] = result["structured_output"]
        def collect_sources(value):
            if isinstance(value, dict):
                refs.extend(ref for ref in value.get("source_refs", []) if isinstance(ref, str))
                for key, child in value.items():
                    if key != "source_refs":
                        collect_sources(child)
            elif isinstance(value, list):
                for item in value:
                    collect_sources(item)
        collect_sources(node_inputs.get("context"))
        refs = list(dict.fromkeys(refs))
    elif node_type == "parameter_extractor":
        schema = config.get("schema")
        try:
            check_schema(schema, business_input=True)
        except (ValueError, TypeError, AttributeError) as exc:
            raise AppError("VALIDATION_ERROR", "参数提取节点需要有效业务输入Schema", 422) from exc
        text, mode = inputs.get("text"), config.get("mode", "model")
        if not isinstance(text, str) or len(text) > 16000:
            raise AppError("VALIDATION_ERROR", "参数提取text须为至多16000字符文本", 422)
        if mode == "demo":
            value = parse_json(text)
            warnings.append("演示参数提取仅解析JSON对象并校验Schema，不是自然语言模型提取")
        elif mode == "model":
            value, model_metadata = await json_model(config, "Extract only the parameters matching this JSON Schema: " + json.dumps(schema, ensure_ascii=False), text, context=context, session=session)
        else:
            raise AppError("VALIDATION_ERROR", "未知参数提取模式", 422)
        if not Draft202012Validator(schema).is_valid(value):
            raise AppError("VALIDATION_ERROR", "提取参数不符合配置Schema", 422)
        outputs = {"parameters": redact(value), "mode": mode, **model_metadata}
    elif node_type == "question_classifier":
        classes = config.get("classes", [])
        if not isinstance(classes, list) or not 1 <= len(classes) <= 16:
            raise AppError("VALIDATION_ERROR", "问题分类需要1至16个类别", 422)
        ids = []
        for item in classes:
            if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", item["id"]) or item["id"] == "else"
                    or not isinstance(item.get("label"), str) or not isinstance(item.get("keywords"), list)
                    or any(not isinstance(k, str) or not k.strip() or len(k) > 100 for k in item["keywords"])):
                raise AppError("VALIDATION_ERROR", "分类ID、标签或关键词配置无效", 422)
            ids.append(item["id"])
        if len(set(ids)) != len(ids):
            raise AppError("VALIDATION_ERROR", "分类ID不能重复", 422)
        question, mode = inputs.get("question"), config.get("mode", "model")
        if not isinstance(question, str) or len(question) > 4000:
            raise AppError("VALIDATION_ERROR", "分类question须为至多4000字符文本", 422)
        if mode == "demo":
            matches = [c["id"] for c in classes if any(k.casefold() in question.casefold() for k in c["keywords"])]
            if len(matches) > 1:
                raise AppError("CLASSIFICATION_AMBIGUOUS", "演示关键词同时匹配多个类别，请调整规则", 422)
            selected = matches[0] if matches else "else"
            warnings.append("演示分类仅使用显式关键词，不是模型语义分类")
        elif mode == "model":
            value, model_metadata = await json_model(config, "Classify into exactly one category, returning {\"class_id\":...}. Allowed categories: " + json.dumps(classes, ensure_ascii=False) + "; else when unsupported.", question, context=context, session=session)
            selected = value.get("class_id") if isinstance(value, dict) else None
            if selected not in ids + ["else"]:
                raise AppError("MODEL_ERROR", "模型分类不在配置枚举内", 502)
        else:
            raise AppError("VALIDATION_ERROR", "未知分类模式", 422)
        outputs = {"class_id": selected, "mode": mode, **model_metadata}
    else:
        raise AppError("NODE_UNAVAILABLE", "该节点不属于Agent模块支持范围", 503)
    if len(json.dumps(outputs, ensure_ascii=False, allow_nan=False).encode()) > 1048576:
        raise AppError("RESOURCE_LIMIT", "节点输出超过1MiB上限", 422)
    result = {"outputs": outputs, "source_refs": refs, "warnings": warnings}
    if selected is not None:
        result["selected_port"] = selected
    return result
