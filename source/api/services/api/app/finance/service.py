from sqlalchemy.orm import Session
from app.contracts import BusinessContext, FactQuery, FinancialFact, ResultDataset
from app.db import get_resource, list_resources, save_resource, new_id, utc_now
from app.errors import AppError
from .domain import build_result, FinanceValidationError
import hashlib

async def query_facts(*, query: FactQuery, context: BusinessContext, session: Session) -> list[FinancialFact]:
    if query.entity not in context.allowed_entities:
        raise AppError("CONNECTION_BLOCKED", "无权查询该主体", status_code=403)
    if query.batch_id and query.batch_id.startswith("knowledge_batch_"):
        from app.knowledge.service import financial_facts
        return await financial_facts(batch_id=query.batch_id, query=query, context=context, session=session)
    if query.batch_id:
        batch = get_resource(session, "import_batch", query.batch_id, context)
        if batch["status"] != "committed":
            raise AppError("IMPORT_INVALID", "批次尚未确认或含校验错误")
        candidates = batch["facts"]
    else:
        candidates = list_resources(session, "fact", context)
    # Reject ambiguous context for the selected entity/period before applying context filters.
    selected = [f for f in candidates if f["entity"] == query.entity and f["period"] in query.periods
                and f["metric_code"] in query.metric_codes]
    if any(any(f[k] != getattr(query, k) for k in ("currency", "unit", "scope")) for f in selected):
        raise AppError("FINANCIAL_CONTEXT_MISMATCH", "查询包含混合币种、单位或口径，请指定匹配批次")
    return [FinancialFact.model_validate(f) for f in selected]

async def calculate_dataset(*, facts: list[FinancialFact], query: FactQuery, context: BusinessContext,
                            session: Session) -> ResultDataset:
    from app.execution.effects import get_effect_context
    effect = get_effect_context()
    if effect is not None:
        from app.auth import validate_context
        validate_context(context, session)
        if (effect.principal_id, effect.tenant_id, effect.workspace_id) != (context.operator_id, context.tenant_id, context.workspace_id):
            raise AppError("FORBIDDEN", "计算执行身份不一致", 403)
        run = get_resource(session, "run", effect.run_id, context)
    if query.entity not in context.allowed_entities:
        raise AppError("CONNECTION_BLOCKED", "无权计算该主体", status_code=403)
    try:
        result = build_result([f.model_dump(mode="json") for f in facts], query.model_dump(mode="json"))
    except FinanceValidationError as exc:
        message = str(exc)
        code = "FINANCIAL_CONTEXT_MISMATCH" if "FINANCIAL_CONTEXT_MISMATCH" in message else "CONFLICT" if "CONFLICT" in message else "VALIDATION_ERROR"
        raise AppError(code, message) from exc
    dataset_id = "dataset_" + hashlib.sha256((effect.execution_id + ":dataset").encode()).hexdigest() if effect else new_id("dataset")
    if effect:
        try:
            previous = ResultDataset.model_validate(get_resource(session, "dataset", dataset_id, context))
        except AppError as exc:
            if exc.status_code != 404:
                raise
        else:
            if previous.snapshot_id != result["snapshot_id"]:
                raise AppError("STEP_INPUT_CHANGED", "同一执行标识的计算输入已变化", 409)
            return previous
    dataset = ResultDataset(id=dataset_id, created_at=utc_now(), query=query, **result)
    save_resource(session, "dataset", dataset.id, dataset.model_dump(mode="json"), context)
    if effect and run.get("data_sources"):
        from app.data_access import record_derivation
        record_derivation(session=session, context=context, kind="dataset", resource_id=dataset.id, sources=run["data_sources"])
    return dataset
