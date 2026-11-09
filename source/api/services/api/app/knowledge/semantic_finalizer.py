"""F core1..6 semantic draft storage. Server callback, never an HTTP input.

Uses the existing Resource draft and explicit publish contract; no new tables,
rule parser, automatic publish, or implicit approval system.
"""
from copy import deepcopy
import sqlite3
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from app.db import Resource, utc_now
from app.errors import AppError
from app.data_access import policy_version
from app.agents.builtin_chunker import digest
from . import service

CONTRACT_VERSION = "knowledge-semantic-finalizer-v2"


def fail(message):
    raise AppError("KNOWLEDGE_SEMANTIC_DRAFT_CONFLICT", message, 409)


def _policy_guard(value):
    """F has no approval backend: never discard a pre-existing requirement."""
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if ((key in ("review", "approval", "requires_review", "requires_approval", "knowledge_unit_id")
             or key.startswith(("review_", "approval_"))) and item):
            raise AppError("KNOWLEDGE_REVIEW_POLICY_UNSUPPORTED", "来源携带当前F不支持的审核策略，未降级或生成草稿", 409)
        if key in ("metadata", "policy") and isinstance(item, dict):
            _policy_guard(item)
    if value.get("status") in ("pending_review", "in_review", "pending_approval"):
        raise AppError("KNOWLEDGE_REVIEW_POLICY_UNSUPPORTED", "来源审核状态不能降级为普通草稿", 409)


def _source(plan, context, session):
    from .semantic_document import _source as load_source
    root, file, _ = load_source(plan["document_id"], context, session)
    _policy_guard(root)
    _policy_guard(file)
    _policy_guard(service.library(root["knowledge_base_id"], context, session, write=True))
    for version_id in {root.get("draft_version_id"), root.get("published_version_id")} - {None}:
        _policy_guard(service.checked_version(version_id, root, context, session))
    return root, file


def _version_row(version_id, context, session):
    return session.scalar(select(Resource).where(Resource.kind == "knowledge_version",
        Resource.resource_id == version_id, Resource.tenant_id == context.tenant_id,
        Resource.workspace_id == context.workspace_id))


def validate_completed(*, plan, root, context, session):
    """Current authorization is checked by caller; attest an exact draft replay."""
    if root.get("draft_version_id") != plan["document_version_id"]:
        fail("当前草稿已变化，请新建任务")
    version = service.checked_version(plan["document_version_id"], root, context, session)
    _policy_guard(version)
    stamp = version.get("semantic_completion", {})
    if (stamp.get("contract_version") != CONTRACT_VERSION or stamp.get("plan_sha256") != digest(plan)
            or stamp.get("policy_revision") != policy_version(session, context)
            or version.get("parent_version_id") != plan["parent_draft_id"]
            or version.get("status") != "validated"):
        fail("固定任务草稿、策略世代或终态不符")
    return version


def _response(value):
    return service.public(value) | {"preview": value["chunks"][:20], "requires_publish": True,
        "manual_publish_required": True, "finalizer_contract_version": CONTRACT_VERSION,
        "message": "模型切片草稿已生成，须显式发布后检索生效"}


def finalize_draft(*, operation, context, session, plan=None, payload=None):
    if operation == "check_ready":
        if plan is not None:
            _source(plan, context, session)
        return {"contract_version": CONTRACT_VERSION, "ready": True}
    if operation != "commit_draft" or not isinstance(plan, dict) or not isinstance(payload, dict):
        fail("不支持的草稿提交操作")
    # Caller owns the surrounding transaction/savepoint. Lock with a payload CAS
    # before checking the parent. The no-op UPDATE also serializes SQLite writers.
    root, file = _source(plan, context, session)
    row = session.scalar(select(Resource).where(Resource.kind == "knowledge_document",
        Resource.resource_id == root["id"], Resource.tenant_id == context.tenant_id,
        Resource.workspace_id == context.workspace_id))
    if row is None:
        fail("文档已不存在")
    old_payload = row.payload
    try:
        changed = session.execute(update(Resource).where(Resource.id == row.id, Resource.payload == old_payload)
            .values(payload=old_payload).execution_options(synchronize_session=False))
    except OperationalError as error:
        # A concurrent SQLite read-snapshot upgrade can fail immediately even
        # with busy_timeout. Do not replay a paid job or expose a generic 500.
        if (session.get_bind().dialect.name == "sqlite" and isinstance(error.orig, sqlite3.OperationalError)
                and (getattr(error.orig, "sqlite_errorcode", 0) & 255) in (5, 6)):
            fail("文档并发修改，请刷新草稿状态")
        raise
    if changed.rowcount != 1:
        fail("文档并发修改，请新建任务")
    from .semantic_document import revalidate_document
    root = revalidate_document(plan, context, session)
    root, file = _source(plan, context, session)
    existing = _version_row(plan["document_version_id"], context, session)
    payload_hash = digest(payload)
    if existing is not None:
        version = validate_completed(plan=plan, root=root, context=context, session=session)
        if version["semantic_completion"].get("payload_sha256") != payload_hash:
            fail("相同固定版本不能提交不同模型结果")
        return _response(version)
    if root.get("draft_version_id") != plan["parent_draft_id"]:
        fail("父草稿已变化，请新建任务")
    chunks = payload["chunks"]
    if not chunks or any(not c["text"].strip() for c in chunks):
        fail("完整语义切片不能为空")
    bound = []
    version_id = plan["document_version_id"]
    for number, chunk in enumerate(chunks, 1):
        original = chunk["original_source_ref"]
        original_text = chunk['evidence_text'] if chunk.get('transport_version')=='document-semantic-items-v7' else (''.join(chunk['text'][s['chunk_start']:s['chunk_end']] for s in chunk['source_spans'])
            if chunk.get('transport_version') in ('xlsx-packed-cells-v1','source-packed-segments-v1') else chunk['text'])
        bound.append({**deepcopy(chunk), "id": f"{version_id}_chunk_{number}", "version_id": version_id,
            "file_id": file["id"], "knowledge_base_id": root["knowledge_base_id"], "sequence": number,
            "source_ref": f"{original}:version:{version_id}:chunk:{number}", "enabled": True, "status": "enabled",
            "text_sha256": service.text_hash(chunk["text"]), "original_text_sha256": service.text_hash(original_text),
            "source_label": service.source_label(chunk["locator"]), **service.counts(chunk["text"])})
    if plan.get('transport_version')=='document-semantic-items-v7':
        ids={c['semantic_local_id']:c['id'] for c in bound}
        for chunk in bound:
            chunk['related_chunk_ids']=[ids[r] for r in chunk['semantic_related_ids']]
            chunk.pop('evidence_text',None)
    value = {"id": version_id, "knowledge_base_id": root["knowledge_base_id"], "resource_kind": "knowledge_document",
        "resource_id": root["id"], "file_id": file["id"], "status": "validated", "parent_version_id": plan["parent_draft_id"],
        "chunks": bound, "chunk_count": len(bound), "errors": [], "warnings": [], "created_at": utc_now(),
        "changes": {"operation": "semantic_agent", "job_id": plan["job_id"]}, "chunking": {"strategy": "agent"},
        "content_sha256": service.digest(bound), "source_refs": [c["source_ref"] for c in bound],
        "source_sha256": file["sha256"], "source_text_length": sum(len(u["text"]) for w in plan["windows"] for u in w["input"]["units"]),
        "overview": deepcopy(payload["overview"]), "overview_origin": "model", "semantic_overview": deepcopy(payload["semantic_overview"]),
        "execution_receipts": deepcopy(payload["execution_receipts"]), "enabled_chunk_count": len(bound), "disabled_chunk_count": 0,
        "char_count": sum(c["char_count"] for c in bound), "retrieval_method": "keyword_overlap_v1", "embedding_status": "not_configured"}
    value["overview_sha256"] = service.digest(value["overview"])
    value["semantic_completion"] = {"contract_version": CONTRACT_VERSION, "plan_sha256": digest(plan), "payload_sha256": payload_hash}
    service.save_resource(session, "knowledge_version", version_id, value, context)
    if root.get("data_policy_generation") == 2:
        service.link(session, context, "knowledge_version", version_id, "knowledge_document", root["id"])
    root.update(draft_version_id=version_id, updated_at=utc_now())
    if root["status"] != "published":
        root["status"] = "validated"
    service.save_resource(session, "knowledge_document", root["id"], root, context)
    value["semantic_completion"]["policy_revision"] = policy_version(session, context)
    service.save_resource(session, "knowledge_version", version_id, value, context)
    return _response(value)
