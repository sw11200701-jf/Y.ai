"""LangChain model/messages over the existing trusted, bounded local adapter.

Only a configuration identifier lives on the model. The endpoint/credential are
resolved at the trusted request boundary, never in graph state or tool schemas.
"""

import json
from typing import Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field
from langsmith import tracing_context

from app.errors import AppError
from .model_adapter import OpenAICompatibleAdapter, load_config
from .model_access import current_model_access, bind_model_access


def wire_messages(messages: Sequence[BaseMessage]) -> list[dict]:
    result = []
    for message in messages:
        if not isinstance(message.content, str):
            raise AppError("MODEL_CAPABILITY_UNAVAILABLE", "当前模型消息适配仅支持授权后的文本内容", 503)
        if isinstance(message, AIMessage):
            if message.invalid_tool_calls or len(message.tool_calls) > 1:
                raise AppError("MODEL_ERROR", "模型每轮只允许一个有效受控工具调用", 502)
            value = {"role": "assistant", "content": message.content or None}
            if message.tool_calls:
                value["tool_calls"] = [{"id": call["id"], "type": "function", "function": {
                    "name": call["name"], "arguments": json.dumps(call["args"], ensure_ascii=False, allow_nan=False)}} for call in message.tool_calls]
        elif isinstance(message, ToolMessage):
            value = {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
        elif isinstance(message, SystemMessage):
            value = {"role": "system", "content": message.content}
        elif isinstance(message, HumanMessage):
            value = {"role": "user", "content": message.content}
        else:
            raise AppError("MODEL_ERROR", "消息角色不在当前模型协议允许范围内", 422)
        result.append(value)
    return result


class BoundedChatModel(BaseChatModel):
    configuration_id: str = Field(min_length=1, max_length=100)
    configuration_version: str | None = None
    parameters: dict | None = None
    context_budget: int | None = Field(default=None, ge=512, le=1048576)
    json_output: bool = False
    response_format: dict | None = None

    @property
    def _llm_type(self) -> str:
        return "zhangpu-trusted-compatible"

    @property
    def _identifying_params(self) -> dict:
        return {"configuration_id": self.configuration_id}

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):
        if kwargs:
            raise AppError("VALIDATION_ERROR", "工具绑定仅接受服务器生成的工具定义", 422)
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise AppError("NODE_UNAVAILABLE", "模型使用异步执行入口", 503)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if stop or set(kwargs) - {"tools"}:
            raise AppError("VALIDATION_ERROR", "模型请求包含不支持的控制参数", 422)
        context, session = current_model_access()
        adapter = OpenAICompatibleAdapter(load_config(self.configuration_id, context=context, session=session,
                                                      expected_version=self.configuration_version))
        result = await adapter.chat(wire_messages(messages), tools=kwargs.get("tools"),
                                    parameters=self.parameters, context_budget=self.context_budget,
                                    json_output=self.json_output, response_format=self.response_format)
        calls = [{"id": call["id"], "name": call["name"], "args": call["arguments"], "type": "tool_call"}
                 for call in result["tool_calls"]]
        message = AIMessage(content=result["text"], tool_calls=calls,
                            response_metadata={key: result[key] for key in ("usage", "estimated_input_tokens", "structured_output", "reasoning", "finish_reason", "completion_truncated") if key in result})
        return ChatResult(generations=[ChatGeneration(message=message)])


class LangChainAdapter:
    """Existing node chat result shape, driven by LC's real async model protocol."""
    def __init__(self, configuration_id, *, expected_version=None):
        self.configuration_id = configuration_id
        self.expected_version = expected_version

    async def chat(self, messages, *, context, session, tools=None, parameters=None, context_budget=None, json_output=False, response_format=None):
        from .prompts import field_error
        if parameters is not None and type(parameters) is not dict:
            field_error("模型参数须为对象", ("config", "model_parameters"))
        if context_budget is not None and (type(context_budget) is not int or not 512 <= context_budget <= 1048576):
            field_error("上下文预算须为512至1048576的整数", ("config", "context_budget"))
        standard = []
        for message in messages:
            role, content = message.get("role"), message.get("content")
            if content is None: content = ""
            if not isinstance(content, str):
                raise AppError("MODEL_ERROR", "模型消息内容须为文本，不能隐式丢弃数值或布尔值", 422)
            if role == "system":
                standard.append(SystemMessage(content=content))
            elif role == "user":
                standard.append(HumanMessage(content=content))
            elif role == "tool":
                standard.append(ToolMessage(content=content, tool_call_id=message["tool_call_id"]))
            elif role == "assistant":
                calls = [{"name": call["function"]["name"], "args": json.loads(call["function"]["arguments"]), "id": call["id"]}
                         for call in message.get("tool_calls", [])]
                standard.append(AIMessage(content=content, tool_calls=calls))
            else:
                raise AppError("MODEL_ERROR", "消息角色不在当前模型协议允许范围内", 422)
        model = BoundedChatModel(configuration_id=self.configuration_id, configuration_version=self.expected_version, cache=False,
                                 parameters=parameters, context_budget=context_budget, json_output=json_output, response_format=response_format)
        if tools:
            model = model.bind_tools(tools)
        with tracing_context(enabled=False), bind_model_access(context=context, session=session):
            response = await model.ainvoke(standard, config={"callbacks": []})
        result = {"text": response.content,
                "tool_calls": [{"id": call["id"], "name": call["name"], "arguments": call["args"]} for call in response.tool_calls],
                "usage": response.response_metadata.get("usage", {}),
                "estimated_input_tokens": response.response_metadata.get("estimated_input_tokens", 0)}
        if "structured_output" in response.response_metadata:
            result["structured_output"] = response.response_metadata["structured_output"]
        for key in ("reasoning", "finish_reason", "completion_truncated"):
            if key in response.response_metadata:result[key]=response.response_metadata[key]
        return result
