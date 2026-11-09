"""Finite Demo intents and constrained model tool selection through LangGraph."""

import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.errors import AppError
from app.plugins.errors import PluginError
from app.plugins.specs import validate_arguments

DEMO_QUESTIONS = (
    "比较两年营业收入和净利润，生成报告",
    "比较2024和2025年营业收入和净利润，生成报告",
    "生成年度财务分析报告",
    "compare 2024 and 2025 revenue and net profit and generate a report",
)


def normalized(value: str) -> str:
    return re.sub(r"[\s，。,.!！?？、]", "", value).lower()


class PlanningState(TypedDict):
    question: str
    selected: dict


async def plan(question: str, allowed_tools: list[dict], context: dict) -> dict:
    """Pure planner boundary. Tools contain safe schemas and opaque binding IDs only."""
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise AppError("VALIDATION_ERROR", "问题不能为空且不得超过 4000 字符", 422)
    if not allowed_tools:
        raise AppError("INVALID_BINDING", "智能体尚未绑定可用工具", 422)
    mode = context.get("mode", "demo")
    query = {key: value for key, value in context["query"].items()
             if key in {"entity", "periods", "metric_codes", "currency", "unit", "scope"}}
    if query["entity"] not in context.get("allowed_entities", ["SYNTHETIC-DEMO"]):
        raise AppError("CONNECTION_BLOCKED", "无权查询该主体", 403)
    if len(query["periods"]) != 2 or len(set(query["periods"])) != 2:
        raise AppError("VALIDATION_ERROR", "年度对比需要两个不同期间", 422)
    tools_by_id = {tool["binding_id"]: tool for tool in allowed_tools}
    if len(tools_by_id) != len(allowed_tools):
        raise AppError("INVALID_BINDING", "工具绑定 ID 不得重复", 422)

    async def select(state: PlanningState):
        if mode == "demo":
            if normalized(state["question"]) not in {normalized(x) for x in DEMO_QUESTIONS}:
                raise AppError("VALIDATION_ERROR", "演示规划器仅支持约定年度对比问题，请使用示例问题", 422,
                               details={"mode": "demo", "examples": list(DEMO_QUESTIONS)})
            if set(query["periods"]) != {"2024", "2025"}:
                raise AppError("VALIDATION_ERROR", "演示规划器仅支持合成样例 2024/2025", 422)
            arguments = {"entity": query["entity"], "periods": query["periods"]}
            # A deterministic first compatible binding, explicitly labelled Demo.
            for tool in allowed_tools:
                try:
                    validate_arguments(tool["input_schema"], arguments)
                    return {"selected": {"intent": "annual_comparison", "tool_calls": [
                        {"binding_id": tool["binding_id"], "arguments": arguments}]}}
                except PluginError:
                    continue
            raise AppError("INVALID_BINDING", "没有接受 entity/periods 的年度财务工具", 422)
        if mode != "model" or context.get("adapter") is None:
            raise AppError("SERVICE_UNAVAILABLE", "真实模型未配置", 503)
        return {"selected": await context["adapter"].select(state["question"], allowed_tools, query)}

    def validate(state: PlanningState):
        selected = state["selected"]
        if not isinstance(selected, dict) or selected.get("intent") != "annual_comparison":
            raise AppError("VALIDATION_ERROR", "模型意图不在允许范围内", 422)
        calls = selected.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1:
            raise AppError("INVALID_BINDING", "计划必须只选择一个已绑定的只读工具", 422)
        for call in calls:
            if not isinstance(call, dict) or call.get("binding_id") not in tools_by_id:
                raise AppError("CONNECTION_BLOCKED", "拒绝未经绑定的工具", 403)
            arguments = call.get("arguments")
            try:
                validate_arguments(tools_by_id[call["binding_id"]]["input_schema"], arguments)
            except PluginError as exc:
                raise AppError(exc.code, exc.message, exc.status_code) from exc
            if arguments.get("entity") != query["entity"] or arguments.get("periods") != query["periods"]:
                raise AppError("CONNECTION_BLOCKED", "模型参数不能更改已授权主体与期间", 403)
        return {"selected": {**selected, "mode": mode, "warnings": [
            "演示规划模式：有限规则选择，HTTP、财务计算及报告真实执行"] if mode == "demo" else []}}

    graph = StateGraph(PlanningState)
    graph.add_node("select", select)
    graph.add_node("validate", validate)
    graph.add_edge(START, "select")
    graph.add_edge("select", "validate")
    graph.add_edge("validate", END)
    result = await graph.compile().ainvoke({"question": question, "selected": {}}, config={"recursion_limit": 4})
    return result["selected"]
