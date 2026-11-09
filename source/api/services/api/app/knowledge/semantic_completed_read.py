"""Read-only historical result authorization, separate from execution replay.

Only central code with a committed-finalize ledger reader may call this entry.
No HTTP flags, writes, model calls, lease acquisition, or execution admission.
"""
import base64
import hashlib
from app.errors import AppError
from app.data_access import _unrestricted, policy_version
from app.agents.builtin_chunker import digest
from . import service
from .semantic_document import _validate_source_plan, _validated_results, _draft_payload
from .semantic_finalizer import CONTRACT_VERSION as FINALIZER_CONTRACT

READ_CONTRACT = "knowledge-semantic-completed-read-v1"
READ_OPERATION = "read_committed_result"


def fail(message):
    raise AppError("KNOWLEDGE_SEMANTIC_COMPLETED_READ_INVALID", message, 409)


def completed_version_sha256(version):
    """Pin inside the real finalize ledger transaction, before later publish."""
    return digest({key: value for key, value in version.items() if key not in ("status", "published_at")})


def validate_completed_read(*, plan, read_step, read_completion, context, session):
    """Authorize a true committed result under current full-source use rights.

read_completion is SERVER ONLY. It verifies the same run's real succeeded
fenced-finalize ledger/blob and current actor, returning the proof below. The
read_step callback reads that run's committed model steps, NOT an active-worker
reader. Neither callback nor this result may be used to admit execution/retry.
"""
    try:
        return _validate(plan=plan, read_step=read_step, read_completion=read_completion, context=context, session=session)
    except (KeyError, TypeError, ValueError, IndexError):
        fail("已提交计划、结果或版本结构损坏")


def _validate(*, plan, read_step, read_completion, context, session):
    if not callable(read_completion) or not callable(read_step):
        fail("缺少服务器已提交完成凭据读取器")
    if plan["actor"] != [context.tenant_id, context.workspace_id, context.operator_id]:
        fail("结果不属于当前主体")
    # Current real session and source/version/KB/file permissions, not a cached
    # global epoch. Whole-document model output requires whole-source use.
    root = service.entry("knowledge_document", plan["document_id"], context, session)
    version = service.checked_version(plan["document_version_id"], root, context, session)
    if not _unrestricted(service.version_decision(version, root, context, session)):
        raise AppError("DATA_SCOPE_READ_DENIED", "完整处理结果需要当前全文使用范围", 403)
    file = service.raw_file({**root, "file_id": version["file_id"]}, context, session)
    if (version.get("status") not in ("validated", "published") or version.get("errors")
            or version.get("parent_version_id") != plan["parent_draft_id"]
            or version.get("file_id") != plan["file_id"] or file.get("sha256") != plan["source_sha256"]
            or version.get("source_sha256") != plan["source_sha256"]):
        fail("历史版本状态、父版本或原始来源不符")
    try:
        content = base64.b64decode(file["content_base64"], validate=True)
    except (ValueError, TypeError):
        fail("原文件编码无效")
    if hashlib.sha256(content).hexdigest() != plan["source_sha256"]:
        fail("原文件实际内容校验失败")
    _validate_source_plan(plan, file, content)
    # A changed model revision does not change historical provenance; current
    # use/revocation is still checked. No transport or secret is exposed.
    from app.agents.model_adapter import load_config
    load_config(plan["model_config_id"], context=context, session=session)
    proof = read_completion(operation=READ_OPERATION, session=session, context=context, job_id=plan["job_id"],
        document_version_id=plan["document_version_id"], expected_plan_sha256=digest(plan))
    if (not isinstance(proof, dict) or proof.get("contract_version") != READ_CONTRACT
            or proof.get("operation") != READ_OPERATION or proof.get("job_id") != plan["job_id"]
            or proof.get("document_version_id") != plan["document_version_id"]
            or proof.get("plan_sha256") != digest(plan) or not isinstance(proof.get("execution_id"), str)
            or not proof["execution_id"] or not isinstance(proof.get("result_sha256"), str)
            or len(proof["result_sha256"]) != 64
            or proof.get("version_snapshot_sha256") != completed_version_sha256(version)):
        fail("服务器完整提交证明与原计划不符")
    final, semantic, receipts = _validated_results(plan, read_step=read_step, context=context, session=session)
    if proof["execution_id"] in {r["execution_id"] for r in receipts}:
        fail("完成步骤不能复用模型步骤凭据")
    payload = _draft_payload(plan, final, semantic, receipts)
    stamp = version.get("semantic_completion", {})
    if (stamp.get("contract_version") != FINALIZER_CONTRACT or stamp.get("plan_sha256") != digest(plan)
            or stamp.get("payload_sha256") != digest(payload)):
        fail("版本完成标记与实际已提交模型结果不符")
    if (version.get("overview") != payload["overview"] or version.get("semantic_overview") != payload["semantic_overview"]
            or version.get("execution_receipts") != receipts or version.get("overview_origin") != "model"
            or version.get("changes") != {"operation": "semantic_agent", "job_id": plan["job_id"]}
            or version.get("chunking") != {"strategy": "agent"}):
        fail("实际版本概述、凭据或处理来源与固定任务不符")
    expected_chunks = []
    for number, chunk in enumerate(payload["chunks"], 1):
        original_text = chunk['evidence_text'] if chunk.get('transport_version')=='document-semantic-items-v7' else (''.join(chunk['text'][s['chunk_start']:s['chunk_end']] for s in chunk['source_spans'])
            if chunk.get('transport_version') in ('xlsx-packed-cells-v1','source-packed-segments-v1') else chunk['text'])
        expected_chunks.append({**chunk, "id": f"{version['id']}_chunk_{number}", "version_id": version["id"],
            "file_id": file["id"], "knowledge_base_id": root["knowledge_base_id"], "sequence": number,
            "source_ref": f"{chunk['original_source_ref']}:version:{version['id']}:chunk:{number}",
            "enabled": True, "status": "enabled", "text_sha256": service.text_hash(chunk["text"]),
            "original_text_sha256": service.text_hash(original_text), "source_label": service.source_label(chunk["locator"]),
            **service.counts(chunk["text"])})
    if plan.get('transport_version')=='document-semantic-items-v7':
        ids={c['semantic_local_id']:c['id'] for c in expected_chunks}
        for chunk in expected_chunks:
            chunk['related_chunk_ids']=[ids[r] for r in chunk['semantic_related_ids']]
            chunk.pop('evidence_text',None)
    if version["chunks"] != expected_chunks or version.get("chunk_count") != len(expected_chunks):
        fail("实际全部切片内容、定位或版本绑定与模型结果不符")
    # Intentionally do not compare stamp.policy_revision to the global epoch or
    # require either current head to be this version. F permits readable history.
    return {"contract_version": READ_CONTRACT, "operation": READ_OPERATION, "job_id": plan["job_id"],
        "document_id": root["id"], "document_version_id": version["id"], "status": version["status"],
        "content_sha256": version["content_sha256"], "policy_revision": policy_version(session, context),
        "historical": version["id"] not in (root.get("draft_version_id"), root.get("published_version_id"))}
