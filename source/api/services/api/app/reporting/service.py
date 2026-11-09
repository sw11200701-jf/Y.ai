import hashlib
import shutil
from pathlib import Path
from sqlalchemy.orm import Session
from app.contracts import ResultDataset, Artifact, BusinessContext
from app.db import new_id, utc_now
from app.errors import AppError
from app.settings import settings
from app.finance.domain import build_result, FinanceValidationError
from .renderers import chart_png, excel_report, word_report

def validate_dataset(*, dataset: ResultDataset, context: BusinessContext) -> dict:
    if dataset.query.entity not in context.allowed_entities:
        raise AppError("CONNECTION_BLOCKED", "无权导出该主体", status_code=403)
    # Detect accidental mutations or hand-edited result rows before publication.
    try:
        expected = build_result([f.model_dump(mode="json") for f in dataset.facts], dataset.query.model_dump(mode="json"))
    except FinanceValidationError as exc:
        raise AppError("VALIDATION_ERROR", str(exc)) from exc
    if (expected["snapshot_id"] != dataset.snapshot_id or expected["rows"] != dataset.rows
            or expected["source_refs"] != dataset.source_refs or expected["metric_version"] != dataset.metric_version
            or expected["metric_versions"] != getattr(dataset, "metric_versions", expected["metric_versions"])
            or expected["synthetic"] != dataset.synthetic):
        raise AppError("CONFLICT", "数据集快照或计算结果与底表不一致，拒绝导出", status_code=409)
    return expected

async def generate_artifacts(*, dataset: ResultDataset, run_id: str, context: BusinessContext, session: Session) -> list[Artifact]:
    expected = validate_dataset(dataset=dataset, context=context)
    if len(dataset.query.periods) > 3:
        raise AppError("VALIDATION_ERROR", "固定年度报告模板最多支持三个年度")
    from app.execution.effects import get_effect_context
    effect = get_effect_context()
    if effect is not None:
        from .durable import generate_stable
        return generate_stable(dataset=dataset, run_id=run_id, context=context, session=session, effect=effect, expected=expected)
    root = Path(settings.artifact_dir).resolve()
    target = root / new_id("report")
    target.mkdir(parents=True, exist_ok=False)
    try:
        chart = target / "synthetic-trend.png"
        chart_png(dataset, chart)
        outputs = [("xlsx", target / "synthetic-annual-analysis.xlsx"), ("docx", target / "synthetic-annual-analysis.docx")]
        excel_report(dataset, chart, outputs[0][1])
        word_report(dataset, chart, outputs[1][1])
        outputs.append(("png", chart))
        return [Artifact(id=new_id("artifact"), run_id=run_id, dataset_id=dataset.id, type=kind,
                         filename=path.name, path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                         size_bytes=path.stat().st_size, created_at=utc_now(), snapshot_id=dataset.snapshot_id,
                         metric_versions=expected["metric_versions"], source_refs=dataset.source_refs) for kind, path in outputs]
    except Exception:
        # All paths are generated children of the configured root, never user-supplied.
        if target.resolve().parent == root:
            shutil.rmtree(target)
        raise
