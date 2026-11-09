"""Stable file publication under01's effect ledger. This module never retries unknown effects."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from app.auth import validate_context
from app.contracts import Artifact, ResultDataset
from app.db import get_resource, save_resource, utc_now
from app.data_access import authorize_data_resource, record_derivation, require_derivation_access
from app.errors import AppError
from app.settings import settings
from .renderers import chart_png, excel_report, word_report


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _authorize(dataset, effect, context, session):
    validate_context(context, session)
    if (effect.principal_id, effect.tenant_id, effect.workspace_id) != (context.operator_id, context.tenant_id, context.workspace_id):
        raise AppError("FORBIDDEN", "报告执行身份不一致", 403)
    run = get_resource(session, "run", effect.run_id, context)
    persisted = ResultDataset.model_validate(get_resource(session, "dataset", dataset.id, context))
    if persisted.model_dump(mode="json") != dataset.model_dump(mode="json"):
        raise AppError("CONFLICT", "报告数据与已授权持久快照不一致", 409)
    authorize_data_resource(session=session, context=context, kind="dataset", resource_id=dataset.id, action="export_detail")
    sources = [{"kind": "dataset", "resource_id": dataset.id, "action": "export_detail"}]
    for source in run.get("data_sources", []):
        current = {"kind": source["kind"], "resource_id": source["resource_id"], "action": "export_detail"}
        authorize_data_resource(session=session, context=context, **current)
        if current not in sources:
            sources.append(current)
    return sources


def _cleanup_stage(stage, root):
    # Only this invocation's resolved, generated temporary directory may be removed.
    resolved = stage.resolve()
    if resolved.parent == root and resolved.name.startswith(".report-"):
        shutil.rmtree(resolved)


def generate_stable(*, dataset, run_id, context, session, effect, expected):
    if run_id != effect.run_id:
        raise AppError("FORBIDDEN", "报告运行与服务器执行标识不一致", 403)
    sources = _authorize(dataset, effect, context, session)
    key = hashlib.sha256((effect.execution_id + ":report").encode()).hexdigest()
    result_id = "report_result_" + key
    root = Path(settings.artifact_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / ("report_" + key)
    if target.resolve().parent != root or target.is_symlink():
        raise AppError("CONFLICT", "报告目录不在受控位置", 409)
    identity = {"execution_id": effect.execution_id, "run_id": run_id, "node_key": effect.node_key,
        "principal_id": context.operator_id, "tenant_id": context.tenant_id, "workspace_id": context.workspace_id,
        "dataset_id": dataset.id, "snapshot_id": dataset.snapshot_id, "input_sha256": _hash(dataset.model_dump(mode="json"))}
    try:
        previous = get_resource(session, "report_result", result_id, context)
    except AppError as exc:
        if exc.status_code != 404:
            raise
        previous = None
    if previous is not None:
        if previous["identity"] != identity:
            raise AppError("STEP_INPUT_CHANGED", "同一报告执行标识的输入已变化", 409)
        require_derivation_access(session=session, context=context, kind="report_result", resource_id=result_id)
        result = []
        for artifact_id in previous["artifact_ids"]:
            artifact = Artifact.model_validate(get_resource(session, "artifact", artifact_id, context))
            path = Path(artifact.path).resolve()
            if (artifact.run_id, artifact.dataset_id) != (run_id, dataset.id) or path.parent != target or not path.is_file():
                raise AppError("CONFLICT", "已登记报告文件或归属校验失败", 409)
            if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256 or path.stat().st_size != artifact.size_bytes:
                raise AppError("CONFLICT", "已登记报告内容校验失败", 409)
            result.append(artifact)
        if {a.type for a in result} != {"docx", "xlsx", "png"} or len(result) != 3:
            raise AppError("CONFLICT", "已登记报告产物不完整", 409)
        return result
    intent = root / ("report_" + key + ".intent.json")
    try:
        with intent.open("x", encoding="utf-8") as handle:
            json.dump(identity, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise AppError("OUTCOME_UNKNOWN", "报告已发起但缺少完整持久结果，禁止自动重新生成", 409) from None
    if target.exists():
        raise AppError("OUTCOME_UNKNOWN", "报告目录已存在但未完整登记，禁止自动认领或覆盖", 409)
    stage = Path(tempfile.mkdtemp(prefix=".report-", dir=root))
    try:
        chart = stage / "synthetic-trend.png"
        chart_png(dataset, chart)
        excel_report(dataset, chart, stage / "synthetic-annual-analysis.xlsx")
        word_report(dataset, chart, stage / "synthetic-annual-analysis.docx")
        outputs = [("xlsx", "synthetic-annual-analysis.xlsx"), ("docx", "synthetic-annual-analysis.docx"), ("png", "synthetic-trend.png")]
        for _, filename in outputs:
            with (stage / filename).open("rb+") as handle:
                os.fsync(handle.fileno())
        # Directory rename publishes only the complete group; prior paths never get overwritten.
        os.rename(stage, target)
        artifacts = [Artifact(id="artifact_" + hashlib.sha256((effect.execution_id + ":" + kind).encode()).hexdigest(),
            run_id=run_id, dataset_id=dataset.id, type=kind, filename=filename, path=str((target / filename).resolve()),
            sha256=hashlib.sha256((target / filename).read_bytes()).hexdigest(), size_bytes=(target / filename).stat().st_size,
            created_at=utc_now(), snapshot_id=dataset.snapshot_id, metric_versions=expected["metric_versions"], source_refs=dataset.source_refs)
            for kind, filename in outputs]
        # Cancellation/revocation after rendering fences registration. Files remain unknown, never auto-retried.
        _authorize(dataset, effect, context, session)
        for artifact in artifacts:
            save_resource(session, "artifact", artifact.id, artifact.model_dump(mode="json"), context)
            record_derivation(session=session, context=context, kind="artifact", resource_id=artifact.id, sources=sources)
        value = {"id": result_id, "identity": identity, "status": "completed", "artifact_ids": [a.id for a in artifacts], "created_at": utc_now()}
        save_resource(session, "report_result", result_id, value, context)
        record_derivation(session=session, context=context, kind="report_result", resource_id=result_id, sources=sources)
        return artifacts
    except BaseException:
        if stage.exists():
            _cleanup_stage(stage, root)
        raise
