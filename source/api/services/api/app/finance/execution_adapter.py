"""Business-node bridge for canonical NodeExecution; no graph/compiler/checkpointer here."""
from app.auth import validate_context
from app.db import get_resource
from app.errors import AppError
from app.execution.contracts import NodeExecution
from app.execution.effects import get_effect_context
from app.knowledge.execution_scope import bind_execution

EFFECT_NODES = frozenset({"document_extractor", "calculate", "report"})


def has_side_effect(node_type, inputs):
    return node_type in EFFECT_NODES or node_type == "query" and any(
        source.get("type") == "plugin" for source in inputs.get("sources", []) if isinstance(source, dict))


async def execute_business_node(*, execution: NodeExecution, node_type: str, config: dict, inputs: dict):
    validate_context(execution.context, execution.session)
    run = get_resource(execution.session, "run", execution.run_id, execution.context)
    if run["id"] != execution.run_id:
        raise AppError("NOT_FOUND", "运行不可访问", 404)
    effect = get_effect_context()
    if effect is not None and (effect.execution_id, effect.run_id, effect.principal_id, effect.tenant_id, effect.workspace_id) != (
        execution.execution_id, execution.run_id, execution.context.operator_id, execution.context.tenant_id, execution.context.workspace_id):
        raise AppError("FORBIDDEN", "业务节点执行身份与服务器边界不一致", 403)
    if node_type in ("calculate", "report") and effect is None:
        raise AppError("EXECUTION_CONTEXT_REQUIRED", "持久业务副作用缺少服务器执行标识", 409)
    if node_type in ("chart", "report"):
        from app.contracts import ResultDataset
        requested = ResultDataset.model_validate(inputs["dataset"])
        stored = ResultDataset.model_validate(get_resource(execution.session, "dataset", requested.id, execution.context))
        if requested.model_dump(mode="json") != stored.model_dump(mode="json"):
            raise AppError("CONFLICT", "数据引用与已授权持久快照不一致", 409)
    with bind_execution(execution):
        module = "app.database.nodes" if node_type == "database_query" else "app.finance.nodes"
        # Canonical invoke rechecks the fresh server caller, never an Agent creator.
        return await execution.invoke(module, "execute_node", node_type=node_type, config=config,
            inputs=inputs, run_id=execution.run_id)
