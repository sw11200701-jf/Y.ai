"""Explicit-input node handlers; no latest-file/dataset lookup or global mutable execution state."""
import json
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from sqlalchemy.orm import Session
from app.contracts import BusinessContext, FactQuery, FinancialFact, ResultDataset, ToolBinding
from app.db import save_resource
from app.errors import AppError
from app.imports.documents import extract_document, search_documents
from app.reporting.service import generate_artifacts, validate_dataset
from .domain import build_result, FinanceValidationError
from .service import query_facts, calculate_dataset
from .node_schemas import NODE_SCHEMAS

async def query_sources(*, query: FactQuery, sources: list | None, context: BusinessContext, session: Session):
    if sources is None:
        if not query.batch_id:
            raise AppError("VALIDATION_ERROR", "v2查询必须指定batch_id或sources，不允许全局最新数据回退")
        facts = await query_facts(query=query, context=context, session=session)
    else:
        if not isinstance(sources, list) or not 1 <= len(sources) <= 16 or query.batch_id:
            raise AppError("VALIDATION_ERROR", "sources须为1..16个显式来源，不能同时指定query.batch_id")
        facts = []
        for source in sources:
            if not isinstance(source, dict):
                raise AppError("VALIDATION_ERROR", "来源须为batch或plugin对象")
            if source.get("type") == "batch" and set(source) == {"type", "batch_id"} and isinstance(source["batch_id"], str):
                facts.extend(await query_facts(query=query.model_copy(update={"batch_id": source["batch_id"]}), context=context, session=session))
            elif source.get("type") == "plugin" and set(source) == {"type", "binding", "arguments"} and isinstance(source["arguments"], dict):
                from app.plugins.service import execute_tool
                result = await execute_tool(binding=ToolBinding.model_validate(source["binding"]), arguments=source["arguments"], context=context, session=session)
                facts.extend(result.facts)
            else:
                raise AppError("VALIDATION_ERROR", "来源只允许受控batch_id或已发布插件binding/arguments")
    try:
        build_result([f.model_dump(mode="json") for f in facts], query.model_dump(mode="json"))
    except FinanceValidationError as exc:
        code = "CONFLICT" if "CONFLICT" in str(exc) else "FINANCIAL_CONTEXT_MISMATCH"
        raise AppError(code, str(exc), status_code=409 if code == "CONFLICT" else 422) from exc
    return facts

async def execute_node(*, node_type: str, config: dict, inputs: dict, context: BusinessContext,
                       session: Session, run_id: str) -> dict:
    schema = NODE_SCHEMAS.get(node_type)
    if schema is None:
        raise AppError("VALIDATION_ERROR", "本模块不支持该节点类型")
    for value, key in ((config, "config_schema"), (inputs, "input_schema")):
        if not Draft202012Validator(schema[key]).is_valid(value):
            raise AppError("VALIDATION_ERROR", f"{node_type}输入或配置不符合声明Schema")
    try:
        if node_type == "document_extractor":
            outputs = await extract_document(file_id=inputs["file_id"], context=context, session=session, **config)
        elif node_type == "knowledge_retrieval":
            if "knowledge_base_ids" in inputs:
                from app.knowledge.service import search_knowledge
                outputs = await search_knowledge(context=context, session=session, **inputs, **config)
            else:
                outputs = await search_documents(query=inputs["query"], file_ids=inputs["file_ids"], context=context, session=session, **config)
        elif node_type == "query":
            query = FactQuery.model_validate(inputs["query"])
            facts = await query_sources(query=query, sources=inputs.get("sources"), context=context, session=session)
            outputs = {"facts": [f.model_dump(mode="json") for f in facts], "source_refs": sorted({f.source_ref for f in facts})}
        elif node_type == "calculate":
            dataset = await calculate_dataset(facts=[FinancialFact.model_validate(f) for f in inputs["facts"]],
                                              query=FactQuery.model_validate(inputs["query"]), context=context, session=session)
            outputs = {"dataset": dataset.model_dump(mode="json"), "dataset_id": dataset.id, "snapshot_id": dataset.snapshot_id,
                       "source_refs": dataset.source_refs, "warnings": dataset.warnings}
        elif node_type == "chart":
            dataset = ResultDataset.model_validate(inputs["dataset"])
            validate_dataset(dataset=dataset, context=context)
            series = config.get("series", [r["metric_code"] for r in dataset.rows if r["unit"] == dataset.query.unit][:2])
            if any(code not in {r["metric_code"] for r in dataset.rows} for code in series):
                raise AppError("VALIDATION_ERROR", "图表series必须引用当前显式数据集中的指标")
            units = {r["unit"] for r in dataset.rows if r["metric_code"] in series}
            if len(units) != 1:
                raise AppError("FINANCIAL_CONTEXT_MISMATCH", "单一图表不能混用金额和比率或使用空series")
            spec = {"type": "bar", "dataset_id": dataset.id, "snapshot_id": dataset.snapshot_id, "series": series,
                    "periods": sorted(dataset.query.periods), "currency": dataset.query.currency, "unit": next(iter(units)),
                    "metric_versions": getattr(dataset, "metric_versions", {}), "source_refs": dataset.source_refs,
                    "rows": [r for r in dataset.rows if r["metric_code"] in series]}
            outputs = {"chart_spec": spec, "source_refs": dataset.source_refs}
        else:
            dataset = ResultDataset.model_validate(inputs["dataset"])
            artifacts = await generate_artifacts(dataset=dataset, run_id=run_id, context=context, session=session)
            for a in artifacts:
                save_resource(session, "artifact", a.id, a.model_dump(mode="json"), context)
            outputs = {"artifacts": [{k: v for k, v in a.model_dump(mode="json").items() if k != "path"} for a in artifacts],
                       "artifact_ids": [a.id for a in artifacts], "dataset_id": dataset.id, "snapshot_id": dataset.snapshot_id,
                       "source_refs": dataset.source_refs}
    except ValidationError as exc:
        raise AppError("VALIDATION_ERROR", "节点财务数据类型或字段无效") from exc
    if node_type == "knowledge_retrieval":
        explicit_sources = set()
        for key, resource_kind in (("file_ids", "document"), ("knowledge_base_ids", "knowledge_base"), ("version_ids", "knowledge_version")):
            explicit_sources.update((resource_kind, resource_id) for resource_id in inputs.get(key, []))
        outputs["data_sources"] = [source for source in outputs.get("data_sources", [])
            if (source["kind"], source["resource_id"]) not in explicit_sources]
    if len(json.dumps(outputs, ensure_ascii=False).encode("utf-8")) > 1048576:
        raise AppError("NODE_OUTPUT_LIMIT", "节点结果超过1MiB，未截断或替换结果")
    return {"outputs": outputs, "source_refs": outputs.get("source_refs", []), "warnings": outputs.get("warnings", [])}
