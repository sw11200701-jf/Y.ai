"""Explicit owner/grant authorization, immutable revisions, published-only retrieval."""
import base64
import hashlib
import json
from dataclasses import replace
from copy import deepcopy
from decimal import Decimal
from app.db import new_id, utc_now
from .data_store import get_resource, list_resources, save_resource, decision, link
from app.data_access import filter_rows, _unrestricted, _column_policy
from app.errors import AppError
from app.imports.documents import import_document, extract_document, _terms
from .schemas import DatasetWrite, BatchRevision, DocumentOverview
from .governance import parse_table, assess, corrected_rows, FINANCE_FIELDS
from app.imports.chunking import extract_chunks, counts, text_hash, source_label
from .execution_scope import scope_layers, require_sources_in_scope

def require_knowledge(context, session, write=False):
    try:
        from app.rbac import require_permission, require_any_permission
    except ImportError as exc:
        raise AppError("SERVICE_UNAVAILABLE", "页面能力检查尚未接入，请等待服务集成", 503) from exc
    if write: require_permission(context, session, "page.knowledge")
    else: require_any_permission(context, session, ["page.knowledge", "page.ai", "page.workbench"])

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()

def missing():
    raise AppError("NOT_FOUND", "Resource not found", 404)

def library(kb_id, context, session, write=False, data_compat=False):
    if data_compat:
        from app.rbac import require_any_permission
        require_any_permission(context, session, ["page.knowledge", "page.database"] if write else ["page.knowledge", "page.database", "page.ai", "page.workbench"])
    else:
        require_knowledge(context, session, write)
    value = get_resource(session, "knowledge_base", kb_id, context, access="write" if write else "read")
    if value.get('status') == 'deleted': missing()
    return value

def entry(kind, resource_id, context, session, write=False, allow_deleted=False):
    value = get_resource(session, kind, resource_id, context, access="write" if write else "read")
    library(value["knowledge_base_id"], context, session, write=write, data_compat=kind == "knowledge_dataset")
    if value.get("status") == "deleted" and not allow_deleted: missing()
    return value

def raw_file(root, context, session, write=False):
    kind = "document" if root["file_id"].startswith("document_") else "knowledge_file"
    file = get_resource(session, kind, root["file_id"], context, access="write" if write else "read")
    if file.get("knowledge_base_id") is not None and file["knowledge_base_id"] != root["knowledge_base_id"]: missing()
    if kind == "document":
        if file.get("knowledge_document_id") and file["knowledge_document_id"] != root["id"]: missing()
        if not file.get("knowledge_base_id") and not root.get("legacy_import"): missing()
    elif file.get("resource_kind") != "knowledge_dataset" or file.get("resource_id") != root["id"]:
        missing()
    return file

def public(value):
    return {k: v for k, v in value.items() if k not in ("content_base64", "chunks", "source_rows", "working_rows", "rows")}

def public_entry(kind, value, context, session):
    authorized = decision(session, kind, value["id"], context)
    if _unrestricted(authorized):
        return public(value)
    return {k: value[k] for k in ("id", "name", "status", "knowledge_base_id", "published_version_id", "draft_version_id", "created_at") if k in value}

async def create_library(*, name, description="", context, session):
    require_knowledge(context, session, True)
    value = {"id": new_id("knowledge_base"), "name": name, "description": description, "status": "active", "created_at": utc_now(),
             "access_policy": "central_data_policy", "retrieval_method": "keyword_overlap_v1"}
    save_resource(session, "knowledge_base", value["id"], value, context)
    return value

async def edit_library(*, kb_id, name, description="", context, session):
    value = library(kb_id, context, session, write=True)
    require_full_resource_maintenance("knowledge_base", kb_id, context, session)
    value.update(name=name, description=description, updated_at=utc_now())
    save_resource(session, "knowledge_base", kb_id, value, context)
    return value

def list_children(kind, kb_id, context, session):
    library(kb_id, context, session)
    return [public_entry(kind, v, context, session) for v in list_resources(session, kind, context) if v.get("knowledge_base_id") == kb_id and v.get("status") != "deleted"]

def source_root(file_id, context, session):
    kind = "document" if isinstance(file_id, str) and file_id.startswith("document_") else "knowledge_file"
    file = get_resource(session, kind, file_id, context)
    for root_kind in ("knowledge_document", "knowledge_dataset"):
        for root in list_resources(session, root_kind, context):
            if root.get("file_id") == file_id or (root_kind == "knowledge_dataset" and file_id in root.get("file_ids", [])):
                root = entry(root_kind, root["id"], context, session)
                raw_file({**root, "file_id": file_id}, context, session)
                return file, root_kind, root
    raise AppError("DOCUMENT_NOT_PUBLISHED", "资料尚未纳入已授权知识库，请先纳库治理并发布")

def authorize_document_file(*, file_id, context, session):
    file = get_resource(session, "document", file_id, context)
    if file.get("knowledge_base_id"):
        library(file["knowledge_base_id"], context, session)
        if not file.get("knowledge_document_id"): return file  # same-transaction upload before root binding
        root = get_resource(session, "knowledge_document", file["knowledge_document_id"], context)
        if root["knowledge_base_id"] != file["knowledge_base_id"] or root["file_id"] != file_id: missing()
        if root.get("status") == "deleted": missing()
    else:
        matches = [r for r in list_resources(session, "knowledge_document", context) if r["file_id"] == file_id]
        if matches:
            entry("knowledge_document", matches[0]["id"], context, session)
        else:
            # Legacy standalone previews are owner-only, never an implicit grant path.
            from app.access import owned
            owned(session, "document", file_id, context)
    return file

async def upload_document(*, kb_id, filename, content, context, session, profile="legacy"):
    library(kb_id, context, session, write=True)
    original = await import_document(filename=filename, content=content, knowledge_base_id=kb_id, profile=profile, context=context, session=session)
    for old in list_resources(session, "knowledge_document", context):
        if old.get("knowledge_base_id") == kb_id and old["file_id"] == original["id"]:
            if old["status"] == "deleted":
                raise AppError("CONFLICT", "相同文件已有删除历史，请修订现有资料或使用新文件", 409)
            return public(old) | {"duplicate": True}
    root = {"id": new_id("knowledge_document"), "knowledge_base_id": kb_id, "file_id": original["id"], "name": original["filename"],
            "status": "uploaded", "created_at": utc_now(), "published_version_id": None, "draft_version_id": None,
            "data_policy_generation": 2, "overview": {"topic": "", "subject": "", "period": "", "summary": ""}}
    file = get_resource(session, "document", original["id"], context, access="write")
    file.update(knowledge_base_id=kb_id, knowledge_document_id=root["id"])
    save_resource(session, "document", file["id"], file, context)
    save_resource(session, "knowledge_document", root["id"], root, context)
    link(session, context, "knowledge_document", root["id"], "knowledge_base", kb_id)
    link(session, context, "document", file["id"], "knowledge_document", root["id"])
    return root

def make_document_version(root, chunks, parent, context, session, changes, warnings=()):
    version_id = new_id("knowledge_version")
    bound = []
    for number, chunk in enumerate(chunks, 1):
        original = chunk.get("original_source_ref", chunk["source_ref"])
        enabled = chunk.get("enabled", True)
        bound.append({**chunk, "id": f"{version_id}_chunk_{number}", "version_id": version_id, "file_id": root["file_id"],
                      "knowledge_base_id": root["knowledge_base_id"], "original_source_ref": original,
                      "source_ref": f"{original}:version:{version_id}:chunk:{number}", "sequence": number, "enabled": enabled,
                      "status": "enabled" if enabled else "disabled", "text_sha256": text_hash(chunk["text"]),
                      "original_text_sha256": chunk.get("original_text_sha256", text_hash(chunk["text"])),
                      "source_label": source_label(chunk["locator"]), **counts(chunk["text"])})
    invalid = [{"code": "EMPTY_CHUNK", "chunk_id": c["id"], "message": "切片不可为空"} for c in bound if not c["text"].strip()]
    value = {"created_by": context.operator_id, "id": version_id, "knowledge_base_id": root["knowledge_base_id"], "resource_kind": "knowledge_document", "resource_id": root["id"],
             "file_id": root["file_id"], "status": "invalid" if invalid else "validated", "parent_version_id": parent, "chunks": bound,
             "changes": changes, "errors": invalid, "warnings": list(warnings), "created_at": utc_now(), "content_sha256": digest(bound),
             "source_refs": [c["source_ref"] for c in bound], "chunk_count": len(bound), "source_sha256": raw_file(root, context, session)["sha256"],
             "overview": deepcopy(root.get("overview", {}))}
    value["overview_sha256"] = digest(value["overview"])
    previous = get_resource(session, "knowledge_version", parent, context) if parent else {}
    value.update(chunking=changes.get("chunking", previous.get("chunking", {"strategy": "legacy"})),
                 source_text_length=changes.get("source_text_length", previous.get("source_text_length")),
                 enabled_chunk_count=sum(c["enabled"] for c in bound), disabled_chunk_count=sum(not c["enabled"] for c in bound),
                 char_count=sum(c["char_count"] for c in bound), retrieval_method="keyword_overlap_v1", embedding_status="not_configured")
    save_resource(session, "knowledge_version", version_id, value, context)
    if root.get("data_policy_generation") == 2:
        link(session, context, "knowledge_version", version_id, "knowledge_document", root["id"])
    root.update(draft_version_id=version_id, updated_at=utc_now())
    if root["status"] != "published": root["status"] = "validated" if not invalid else "invalid"
    save_resource(session, "knowledge_document", root["id"], root, context)
    return public(value) | {"preview": bound[:20], "requires_publish": True, "message": "已生成切片修订，发布后对当前库检索生效"}

async def parse_document(*, document_id, context, session, chunk_size=1000, chunk_overlap=100, strategy="length"):
    root = entry("knowledge_document", document_id, context, session, write=True)
    require_full_document_maintenance(root, context, session)
    file = raw_file(root, context, session)
    try:
        content = base64.b64decode(file["content_base64"], validate=True)
    except (ValueError, TypeError):
        raise AppError("DOCUMENT_INVALID", "原始文件编码校验失败，未生成新版本", 422) from None
    if hashlib.sha256(content).hexdigest() != file["sha256"]: raise AppError("CONFLICT", "原始文件校验失败，未生成新版本", 409)
    started = utc_now()
    extracted = extract_chunks(content=content, kind=file["format"], file_id=file["id"], strategy=strategy, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    result = make_document_version(root, extracted["chunks"], root.get("draft_version_id"), context, session,
                                   {"operation": "rechunk" if root.get("draft_version_id") else "parse", "chunking": extracted["chunking"],
                                    "source_text_length": extracted["text_length"]}, extracted["warnings"])
    root["last_processing"] = {"id": new_id("parse_attempt"), "status": "completed", "started_at": started, "completed_at": utc_now(),
                               "version_id": result["id"], "chunking": extracted["chunking"], "text_length": extracted["text_length"], "chunk_count": result["chunk_count"]}
    save_resource(session, "knowledge_document", root["id"], root, context)
    return result | {"processing": root["last_processing"]}

async def revise_document(*, document_id, corrections, context, session, base_version_id=None, additions=(), deletions=()):
    from .curation import revise
    return await revise(document_id=document_id, corrections=corrections, additions=additions, deletions=deletions,
        context=context, session=session, base_version_id=base_version_id)


def checked_version(version_id, root, context, session, write=False):
    version = get_resource(session, "knowledge_version", version_id, context, access="write" if write else "read")
    if version["resource_id"] != root["id"] or version["knowledge_base_id"] != root["knowledge_base_id"]: missing()
    expected_kind = "knowledge_document" if root["id"].startswith("knowledge_document_") else "knowledge_dataset"
    if version["resource_kind"] != expected_kind: missing()
    original = raw_file({**root, "file_id": version["file_id"]}, context, session, write=write)
    if version.get("source_sha256") and version["source_sha256"] != original["sha256"]:
        raise AppError("CONFLICT", "版本与原始文件来源校验不一致", 409)
    content = version["chunks"] if expected_kind == "knowledge_document" else version["rows"]
    if digest(content) != version["content_sha256"]:
        raise AppError("CONFLICT", "版本内容与快照校验不一致", 409)
    if version.get("overview_sha256") and digest(version.get("overview", {})) != version["overview_sha256"]:
        raise AppError("CONFLICT", "版本概述与快照校验不一致", 409)
    for chunk in version["chunks"]:
        if (chunk.get("file_id"), chunk.get("version_id"), chunk.get("knowledge_base_id")) != (version["file_id"], version_id, root["knowledge_base_id"]): missing()
    if expected_kind == "knowledge_dataset":
        if len(version["chunks"]) != len(version["rows"]): raise AppError("CONFLICT", "数据集与切片行数不一致", 409)
        for row, chunk in zip(version["rows"], version["chunks"]):
            source = f"file:{version['file_id']}:sha256:{version['source_sha256']}:{row['source_locator']}:batch:{version['batch_id']}:version:{version_id}"
            if (chunk["text"] != "; ".join(f"{k}: {v}" for k, v in row["values"].items()) or chunk["source_ref"] != source
                    or chunk["locator"] != {"kind": "row", "row": row["row"], "source": row["source_locator"]}):
                raise AppError("CONFLICT", "治理数据集切片与已发布行/来源不一致", 409)
    if version.get('human_revision'):
        from .curation import verify
        verify(version, context=context, session=session)
    return version

async def publish_document(*, document_id, version_id, context, session):
    root = entry("knowledge_document", document_id, context, session, write=True)
    require_full_document_maintenance(root, context, session)
    from .curation import lock_document
    lock_document(session, context, document_id)
    root = entry('knowledge_document', document_id, context, session, write=True)
    if version_id != (root.get('draft_version_id') or root.get('published_version_id')):
        raise AppError('KNOWLEDGE_REVISION_CONFLICT', '请发布最新保存的知识版本', 409)
    version = checked_version(version_id, root, context, session, write=True)
    if version["status"] not in ("validated", "published") or version["errors"]:
        raise AppError("QUALITY_FAILED", "错误版本不得发布")
    version.update(status="published", published_at=utc_now())
    save_resource(session, "knowledge_version", version_id, version, context)
    root.update(status="published", published_version_id=version_id, updated_at=utc_now())
    save_resource(session, "knowledge_document", root["id"], root, context)
    return public(version)

async def create_dataset(*, kb_id, definition, context, session):
    library(kb_id, context, session, write=True)
    require_full_resource_maintenance("knowledge_base", kb_id, context, session)
    definition = DatasetWrite.model_validate(definition).model_dump()
    if definition["dataset_type"] == "finance" and not definition["schema_fields"]:
        definition["schema_fields"] = deepcopy(FINANCE_FIELDS)
    root = {"id": new_id("knowledge_dataset"), "knowledge_base_id": kb_id, **definition, "status": "draft", "file_ids": [],
            "file_id": None, "created_at": utc_now(), "published_version_id": None, "draft_batch_id": None}
    save_resource(session, "knowledge_dataset", root["id"], root, context)
    return root

async def upload_batch(*, dataset_id, filename, content, context, session, sheet_name=None):
    root = entry("knowledge_dataset", dataset_id, context, session, write=True)
    require_full_resource_maintenance("knowledge_dataset", dataset_id, context, session)
    parsed = parse_table(filename, content, sheet_name)
    file_id = new_id("knowledge_file")
    file = {"id": file_id, "knowledge_base_id": root["knowledge_base_id"], "resource_id": root["id"], "resource_kind": "knowledge_dataset",
            "filename": filename.replace("\\", "/").split("/")[-1][:150], "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content), "content_base64": base64.b64encode(content).decode("ascii"), "created_at": utc_now()}
    save_resource(session, "knowledge_file", file_id, file, context)
    root["file_id"] = file_id
    root["file_ids"].append(file_id)
    return make_batch(root, file, parsed["source_rows"], parsed["source_rows"], root["schema_fields"], root["mapping"], root["quality_rules"],
                      None, [], [], context, session, parsed["columns"], parsed["sheet_name"])

def make_batch(root, file, original, working, fields, mapping, rules, parent, corrections, cleaning, context, session, columns, sheet_name):
    if root["dataset_type"] == "finance":
        expected = {f["name"]: f["type"] for f in FINANCE_FIELDS}
        if {f["name"]: f["type"] for f in fields} != expected or any(not f.get("required") for f in fields):
            raise AppError("VALIDATION_ERROR", "财务专门类型须使用标准七字段及其类型，可手动映射源列；其他Schema请用generic")
    result = assess(source_rows=working, fields=fields, mapping=mapping, quality_rules=rules, cleaning=cleaning, dataset_type=root["dataset_type"])
    batch = {"id": new_id("knowledge_batch"), "knowledge_base_id": root["knowledge_base_id"], "dataset_id": root["id"], "file_id": file["id"],
             "source_sha256": file["sha256"], "source_rows": deepcopy(original), "working_rows": deepcopy(working), "columns": columns, "sheet_name": sheet_name,
             "schema_fields": fields, "mapping": mapping, "quality_rules": rules, "corrections": corrections, "cleaning": cleaning,
             "parent_batch_id": parent, "created_at": utc_now(), "published_version_id": None, **result}
    save_resource(session, "knowledge_batch", batch["id"], batch, context)
    root.update(draft_batch_id=batch["id"], schema_fields=deepcopy(fields), mapping=deepcopy(mapping), quality_rules=deepcopy(rules), updated_at=utc_now())
    if root["status"] != "published": root["status"] = "invalid" if batch["status"] == "invalid" else "validated"
    save_resource(session, "knowledge_dataset", root["id"], root, context)
    return batch_preview(batch)

def batch_preview(batch):
    return public(batch) | {"original_preview": batch["source_rows"][:20], "working_preview": batch["working_rows"][:20], "preview": batch["rows"][:20]}

def get_batch(batch_id, context, session, write=False):
    batch = get_resource(session, "knowledge_batch", batch_id, context, access="write" if write else "read")
    root = entry("knowledge_dataset", batch["dataset_id"], context, session, write=write)
    if batch["knowledge_base_id"] != root["knowledge_base_id"]: missing()
    raw_file({**root, "file_id": batch["file_id"]}, context, session)
    return batch, root

async def revise_batch(*, batch_id, revision, context, session):
    previous, root = get_batch(batch_id, context, session, write=True)
    require_full_resource_maintenance("knowledge_dataset", root["id"], context, session)
    require_full_resource_maintenance("knowledge_batch", batch_id, context, session)
    change = BatchRevision.model_validate(revision).model_dump(exclude_none=True)
    working = corrected_rows(previous["working_rows"], change["corrections"])
    file = raw_file({**root, "file_id": previous["file_id"]}, context, session)
    return make_batch(root, file, previous["source_rows"], working, change.get("schema_fields", previous["schema_fields"]),
                      change.get("mapping", previous["mapping"]), change.get("quality_rules", previous["quality_rules"]), previous["id"],
                      change["corrections"], change["cleaning"] or previous["cleaning"], context, session, previous["columns"], previous["sheet_name"])

async def publish_batch(*, batch_id, context, session):
    batch, root = get_batch(batch_id, context, session, write=True)
    require_full_resource_maintenance("knowledge_dataset", root["id"], context, session)
    require_full_resource_maintenance("knowledge_batch", batch_id, context, session)
    if batch["published_version_id"]:
        existing = checked_version(batch["published_version_id"], root, context, session)
        root.update(status="published", published_version_id=existing["id"], last_published_batch_id=batch_id, updated_at=utc_now())
        save_resource(session, "knowledge_dataset", root["id"], root, context)
        return public(existing) | {"dataset_id": root["id"], "dataset_status": root["status"]}
    if batch["status"] != "validated" or batch["errors"]:
        raise AppError("QUALITY_FAILED", "错误批次不得发布或进入正式查询", details={"errors": batch["errors"]})
    version_id = new_id("knowledge_version")
    chunks = []
    for number, row in enumerate(batch["rows"], 1):
        source = f"file:{batch['file_id']}:sha256:{batch['source_sha256']}:{row['source_locator']}:batch:{batch_id}:version:{version_id}"
        chunks.append({"id": f"{version_id}_chunk_{number}", "file_id": batch["file_id"], "version_id": version_id,
                       "knowledge_base_id": root["knowledge_base_id"], "text": "; ".join(f"{k}: {v}" for k, v in row["values"].items()),
                       "locator": {"kind": "row", "row": row["row"], "source": row["source_locator"]}, "source_ref": source})
    version = {"id": version_id, "resource_kind": "knowledge_dataset", "resource_id": root["id"], "knowledge_base_id": root["knowledge_base_id"],
               "file_id": batch["file_id"], "batch_id": batch_id, "parent_version_id": root["published_version_id"], "status": "published",
               "rows": deepcopy(batch["rows"]), "chunks": chunks, "content_sha256": digest(batch["rows"]), "source_sha256": batch["source_sha256"],
               "schema_fields": batch["schema_fields"], "mapping": batch["mapping"], "quality": batch["quality"], "errors": [],
               "source_refs": [c["source_ref"] for c in chunks], "published_at": utc_now(), "chunk_count": len(chunks)}
    save_resource(session, "knowledge_version", version_id, version, context)
    batch.update(status="published", published_version_id=version_id)
    save_resource(session, "knowledge_batch", batch_id, batch, context)
    root.update(status="published", published_version_id=version_id, last_published_batch_id=batch_id, updated_at=utc_now())
    save_resource(session, "knowledge_dataset", root["id"], root, context)
    from .legacy_tables import refresh_bound_versions
    refresh_bound_versions(root["id"], context, session)
    return public(version) | {"dataset_id": root["id"], "dataset_status": root["status"]}

def published_version(root, context, session, version_id=None):
    if root["status"] != "published" or not root["published_version_id"]:
        raise AppError("NOT_PUBLISHED", "资源尚未发布或已停用，不能正式查询/检索")
    version = checked_version(version_id or root["published_version_id"], root, context, session)
    if version["status"] != "published" or version.get("errors"): raise AppError("NOT_PUBLISHED", "版本未发布或质量检查未通过")
    return version

async def query_dataset(*, dataset_id, context, session, version_id=None, agent_scope=None):
    root = entry("knowledge_dataset", dataset_id, context, session)
    version = published_version(root, context, session, version_id)
    require_sources_in_scope(sources=[("knowledge_dataset", dataset_id), ("data_table", dataset_id), ("knowledge_version", version["id"])],
        layers=scope_layers(agent_scope), context=context, session=session, version_id=version["id"])
    fields = [f["name"] for f in version["schema_fields"]]
    authorized = version_decision(version, root, context, session)
    visible = []
    for offset in range(0, len(version["rows"]), 1000):
        result = filter_rows(decision=authorized, rows=[r["values"] for r in version["rows"]], fields=fields,
            query={"offset": offset, "limit": 1000})
        visible.extend({"values": row} for row in result["rows"])
    return {"dataset_id": dataset_id, "knowledge_base_id": root["knowledge_base_id"], "version_id": version["id"],
            "snapshot_id": version["content_sha256"], "rows": visible,
            "source_refs": [f"dataset:{dataset_id}:version:{version['id']}:sha256:{version['source_sha256']}"],
            "policy_version": authorized.policy_revision}

async def change_status(*, kind, resource_id, status, context, session):
    if status == 'deleted':
        from .lifecycle import remove
        return await remove(kind, resource_id, context=context, session=session)
    root = entry(kind, resource_id, context, session, write=True)
    require_full_resource_maintenance(kind, resource_id, context, session)
    if status not in ("disabled", "deleted"): raise AppError("VALIDATION_ERROR", "不支持该状态")
    root.update(status=status, updated_at=utc_now())
    save_resource(session, kind, resource_id, root, context)
    return public(root)

def versions(*, kind, resource_id, context, session):
    root = entry(kind, resource_id, context, session)
    visible = []
    for value in list_resources(session, "knowledge_version", context):
        if value["resource_id"] != resource_id: continue
        try: checked_version(value["id"], root, context, session)
        except AppError as exc:
            if exc.code == "NOT_FOUND": continue
            raise
        visible.append(public(value))
    return visible

def version_preview(*, version_id, context, session):
    version = get_resource(session, "knowledge_version", version_id, context)
    root = entry(version["resource_kind"], version["resource_id"], context, session)
    checked_version(version_id, root, context, session)
    visible = visible_chunks(version, root, context, session)
    return {k: version[k] for k in ("id", "resource_kind", "resource_id", "knowledge_base_id", "status", "content_sha256")} | {
        "chunk_count": len(visible), "preview": visible[:20]}

def require_full_document_maintenance(root, context, session):
    require_full_resource_maintenance("knowledge_document", root["id"], context, session)

def require_full_resource_maintenance(kind, resource_id, context, session):
    if not _unrestricted(decision(session, kind, resource_id, context, "maintain")):
        raise AppError("DATA_SCOPE_WRITE_DENIED", "全文解析和切片修订需要该文档完整维护范围", 403)

def chunk_fields(version, root):
    return list(dict.fromkeys(key for chunk in presented_chunks(version, root) for key in chunk))

def version_decision(version, root, context, session):
    authorized = decision(session, "knowledge_version", version["id"], context)
    root_kind = version["resource_kind"]
    file_kind = "document" if version["file_id"].startswith("document_") else "knowledge_file"
    layers = list(authorized.layers)
    for kind, resource_id in (("knowledge_base", root["knowledge_base_id"]), (root_kind, root["id"]), (file_kind, version["file_id"])):
        layers.extend(decision(session, kind, resource_id, context).layers)
    return replace(authorized, layers=layers)

def visible_chunks(version, root, context, session):
    authorized = version_decision(version, root, context, session)
    fields = chunk_fields(version, root)
    allowed, masks = _column_policy(authorized, fields)
    required = {"text", "id", "enabled", "source_ref"}
    if not required <= allowed or required & set(masks):
        return []
    return filter_rows(decision=authorized, rows=presented_chunks(version, root), fields=fields,
        query={"limit": 1000})["rows"]

def presented_chunks(version, root):
    result = []
    for number, chunk in enumerate(version["chunks"], 1):
        enabled = chunk.get("enabled", True)
        result.append({**chunk, "chunk_id": chunk["id"], "sequence": chunk.get("sequence", number), "enabled": enabled,
                       "status": "enabled" if enabled else "disabled", "version_status": version["status"],
                       "document_id": root["id"], "document_name": root["name"],
                       "source_label": chunk.get("source_label", source_label(chunk["locator"])),
                       "text_sha256": chunk.get("text_sha256", text_hash(chunk["text"])), **counts(chunk["text"])})
    return result

def version_chunks(*, version_id, offset, limit, context, session, q="", enabled=None):
    value = get_resource(session, "knowledge_version", version_id, context)
    root = entry(value["resource_kind"], value["resource_id"], context, session)
    checked_version(version_id, root, context, session)
    chunks = visible_chunks(value, root, context, session)
    from .customer_chunks import text as customer_text
    filtered = [c for c in chunks if (not q or q.casefold() in c["text"].casefold()) and (enabled is None or c.get("enabled", True) == enabled)]
    return {"version_id": version_id, "snapshot_id": value["content_sha256"], "source_sha256": value.get("source_sha256"),
            "total": len(filtered), "chunk_count": len(chunks), "enabled_chunk_count": sum(c.get("enabled", True) for c in chunks),
            "disabled_chunk_count": sum(not c.get("enabled", True) for c in chunks), "chunking": value.get("chunking", {"strategy": "legacy"}),
            "version_status": value["status"], "retrieval_method": "keyword_overlap_v1", "embedding_status": "not_configured",
            "offset": offset, "limit": limit, "items": [{**c,"display_text":customer_text(c)} for c in filtered[offset:offset+limit]]}

def document_chunks(*, document_id, version_id, context, session, **options):
    root = entry("knowledge_document", document_id, context, session)
    selected = version_id or root.get("draft_version_id") or root.get("published_version_id")
    if not selected:
        return {"document_id": document_id, "version_id": None, "total": 0, "chunk_count": 0, "items": [], "message": "文档尚未解析"}
    checked_version(selected, root, context, session)
    return version_chunks(version_id=selected, context=context, session=session, **options)

def record_parse_failure(*, document_id, options, error, context, session):
    root = entry("knowledge_document", document_id, context, session, write=True)
    root["last_processing"] = {"id": new_id("parse_attempt"), "status": "failed", "completed_at": utc_now(), "chunking": options,
                               "error": {"code": error.code, "message": error.message}, "version_created": False}
    save_resource(session, "knowledge_document", document_id, root, context)
    return root["last_processing"]

def library_batches(*, kb_id, context, session):
    library(kb_id, context, session)
    results = []
    for root in list_resources(session, "knowledge_dataset", context):
        if root.get("knowledge_base_id") != kb_id or root.get("status") == "deleted": continue
        results.extend(batches(dataset_id=root["id"], context=context, session=session))
    return results

def capabilities():
    from app.imports.documents import upload_contract
    from app.imports.document_container import CONVERSION_HINTS
    from app.agents.slicing_specialists import SPECIALISTS, select
    from .slicing_parser import capabilities as parser_capabilities
    portable = upload_contract("portable-slicing-1.0.0")
    guidance = {suffix: hint for suffix, hint in CONVERSION_HINTS.items()
                if suffix not in portable["formats"]}
    if ".doc" not in portable["formats"]:
        guidance[".doc"] = "本机旧版 Word 转换组件暂未启用。组件可用后即可上传原件。"
    routes = {suffix: SPECIALISTS[select(kind)]["name"] for suffix, kind in portable["formats"].items()}
    return {"chunking": {"strategies": ["length", "paragraph"], "chunk_size_min": 100, "chunk_size_max": 2000,
                         "overlap_max": 200, "overlap_max_fraction": "0.5", "max_chunks": 1000, "max_source_characters": 500000},
            "retrieval": {"method": "keyword_overlap_v1", "status": "available", "published_enabled_only": True,
                          "embedding_status": "not_configured", "ocr": False}, "versioned_edits": True, "versioned_rechunking": True,
            "upload_profiles": [upload_contract("legacy"), portable],
            "format_guidance": guidance, "format_routes": routes,
            "portable_slicing": parser_capabilities()}

def batch_rows(*, batch_id, view, offset, limit, context, session):
    value, _ = get_batch(batch_id, context, session)
    if not _unrestricted(decision(session, "knowledge_batch", batch_id, context, "maintain")):
        raise AppError("DATA_SCOPE_WRITE_DENIED", "治理原始行需要完整维护范围，请使用受控查询预览已授权数据", 403)
    rows = value[{"original": "source_rows", "working": "working_rows", "governed": "rows"}[view]]
    return {"batch_id": batch_id, "view": view, "total": len(rows), "offset": offset, "limit": limit, "items": rows[offset:offset+limit]}

def batches(*, dataset_id, context, session):
    entry("knowledge_dataset", dataset_id, context, session)
    visible = []
    for batch in list_resources(session, "knowledge_batch", context):
        if batch["dataset_id"] != dataset_id: continue
        try: get_batch(batch["id"], context, session)
        except AppError as exc:
            if exc.code == "NOT_FOUND": continue
            raise
        visible.append(public(batch))
    return visible

def download_file(*, file_id, context, session):
    file, kind, root = source_root(file_id, context, session)
    file_kind = "document" if file_id.startswith("document_") else "knowledge_file"
    if not _unrestricted(decision(session, file_kind, file_id, context, "download_original")) or not _unrestricted(decision(session, kind, root["id"], context)):
        raise AppError("DATA_SCOPE_DOWNLOAD_DENIED", "原件包含范围外内容，无法下载完整原件", 403)
    content = base64.b64decode(file["content_base64"], validate=True)
    if hashlib.sha256(content).hexdigest() != file["sha256"]:
        raise AppError("CONFLICT", "原始文件校验失败", 409)
    return file["filename"], content

async def search_knowledge(*, query, knowledge_base_ids, context, session, file_ids=None, version_ids=None, top_k=5, agent_scope=None):
    layers = scope_layers(agent_scope, knowledge=True)
    if not isinstance(query, str) or not query.strip() or len(query) > 500 or not _terms(query):
        raise AppError("VALIDATION_ERROR", "请输入1..500字有效检索词")
    for values, label in ((knowledge_base_ids, "knowledge_base_ids"), (file_ids, "file_ids"), (version_ids, "version_ids")):
        if values is None and label != "knowledge_base_ids": continue
        if not isinstance(values, list) or not 1 <= len(values) <= 20 or any(not isinstance(v, str) for v in values) or len(set(values)) != len(values):
            raise AppError("VALIDATION_ERROR", f"{label}须为1..20个不重复ID")
    if type(top_k) is not int or not 1 <= top_k <= 20: raise AppError("VALIDATION_ERROR", "top_k须为1..20")
    for kb_id in knowledge_base_ids: library(kb_id, context, session)
    for file_id in file_ids or []:
        _, _, root = source_root(file_id, context, session)
        if root["knowledge_base_id"] not in knowledge_base_ids: missing()
    selected = []
    if version_ids:
        for vid in version_ids:
            version = get_resource(session, "knowledge_version", vid, context)
            root = entry(version["resource_kind"], version["resource_id"], context, session)
            if root["knowledge_base_id"] not in knowledge_base_ids or file_ids and version["file_id"] not in file_ids: missing()
            selected.append(published_version(root, context, session, vid))
    else:
        for kind in ("knowledge_document", "knowledge_dataset"):
            for root in list_resources(session, kind, context):
                if root["knowledge_base_id"] not in knowledge_base_ids or root["status"] != "published": continue
                if file_ids and root.get("file_id") not in file_ids and not set(root.get("file_ids", [])) & set(file_ids): continue
                try:
                    selected_version = published_version(root, context, session)
                    if file_ids and selected_version["file_id"] not in file_ids:
                        matching = [v for v in list_resources(session, "knowledge_version", context)
                                    if v["resource_id"] == root["id"] and v["file_id"] in file_ids and v["status"] == "published"]
                        if not matching: continue
                        selected_version = published_version(root, context, session, sorted(matching, key=lambda v: v.get("published_at", ""))[-1]["id"])
                    selected.append(selected_version)
                except AppError as exc:
                    if exc.code == "NOT_FOUND" and not file_ids: continue
                    raise
    scoped = []
    for version in selected:
        file_kind = "document" if version["file_id"].startswith("document_") else "knowledge_file"
        try:
            require_sources_in_scope(sources=[("knowledge_version", version["id"]), (file_kind, version["file_id"])],
                layers=layers, context=context, session=session, version_id=version["id"])
            scoped.append(version)
        except AppError as exc:
            if exc.status_code != 404 or version_ids or file_ids:
                raise
    if selected and not scoped and layers:
        missing()
    selected = scoped
    if len(selected) > 100 or sum(len(c["text"]) for v in selected for c in v["chunks"]) > 2000000:
        raise AppError("KNOWLEDGE_LIMIT", "检索范围过大，请显式选择文件/版本；未截断范围")
    verified_cache,provenance_warnings={},[]
    current=[]
    for version in selected:
        if version.get('portable_result'):
            from .slicing_retrieval import verified_published_version
            verified_cache[version['id']]=verified_published_version(version,context=context,session=session)
            if not verified_cache[version['id']]:
                if version_ids or file_ids:raise AppError('SLICING_PROVENANCE_UNAVAILABLE','所选版本当前无法核对来源与最终审核，未用于检索',409)
                provenance_warnings.append('部分已发布切片当前无法核对来源或原图核查，已从本次检索排除')
                continue
        current.append(version)
    selected=current
    ranked, terms = [], _terms(query)
    for version in selected:
        root = entry(version["resource_kind"], version["resource_id"], context, session)
        visible = visible_chunks(version, root, context, session)
        for chunk in visible:
            if not chunk.get("enabled", True): continue
            if not chunk.get("text") or chunk["text"] == "***": continue
            tokens = _terms(chunk["text"])
            matched = sorted(set(terms) & set(tokens))
            if not matched: continue
            score = Decimal(len(matched))*3/Decimal(len(terms)) + Decimal(sum(min(tokens[t], 3) for t in matched))/100
            if query.strip().lower() in chunk["text"].lower(): score += 1
            ranked.append({**chunk, "knowledge_base_id": version["knowledge_base_id"], "resource_id": version["resource_id"],
                           "version_id": version["id"], "snapshot_id": version["content_sha256"], "source_sha256": version.get("source_sha256"), "matched_terms": matched,
                           "score": format(score.quantize(Decimal("0.000001")), "f")})
    ranked.sort(key=lambda h: (-Decimal(h["score"]), h["version_id"], h["id"]))
    hits = ranked[:top_k]
    from .slicing_retrieval import expand_hits
    def visible_for_expansion(version):
        root=entry(version['resource_kind'],version['resource_id'],context,session)
        require_sources_in_scope(sources=[('knowledge_version',version['id']),('document' if version['file_id'].startswith('document_') else 'knowledge_file',version['file_id'])],layers=layers,context=context,session=session,version_id=version['id'])
        return visible_chunks(version,root,context,session)
    def verified_for_expansion(version):
        return verified_cache.get(version['id'],False)
    hits,expanded_ids,expansion_warnings=expand_hits(hits,{v['id']:v for v in selected},visible=visible_for_expansion,verify=verified_for_expansion)
    sources = []
    for version in selected:
        sources.extend([{"kind": "knowledge_base", "resource_id": version["knowledge_base_id"], "action": "use"},
            {"kind": version["resource_kind"], "resource_id": version["resource_id"], "action": "use"},
            {"kind": "knowledge_version", "resource_id": version["id"], "action": "use"},
            {"kind": "document" if version["file_id"].startswith("document_") else "knowledge_file", "resource_id": version["file_id"], "action": "use"}])
    return {"method": "keyword_overlap_v1", "hits": hits, "query": query, "knowledge_base_ids": knowledge_base_ids,
            "data_sources": [dict(items) for items in dict.fromkeys(tuple(s.items()) for s in sources)],
            "source_refs": list(dict.fromkeys([h["source_ref"] for h in hits]+[e["source_ref"] for h in hits for e in h.get("expanded_context",[])])), "used_versions": [{"version_id": v["id"], "snapshot_id": v["content_sha256"],
               "knowledge_base_id": v["knowledge_base_id"], "file_id": v["file_id"], "source_sha256": v.get("source_sha256")} for v in selected],
            "used_chunks": list(dict.fromkeys([h["id"] for h in hits]+expanded_ids)), "warnings": list(dict.fromkeys(provenance_warnings+expansion_warnings)), "expansion_method":"verified_parent_one_hop_v1", "vector": False, "ocr": False}

async def search_files(*, query, file_ids, context, session, top_k=5, agent_scope=None):
    if not isinstance(file_ids, list) or not 1 <= len(file_ids) <= 20 or any(not isinstance(f, str) for f in file_ids):
        raise AppError("VALIDATION_ERROR", "file_ids须显式提供1..20个受控ID")
    kb_ids = []
    for fid in file_ids:
        _, _, root = source_root(fid, context, session)
        if root["status"] != "published": raise AppError("NOT_PUBLISHED", "未发布资料不能检索")
        if root["knowledge_base_id"] not in kb_ids: kb_ids.append(root["knowledge_base_id"])
    return await search_knowledge(query=query, knowledge_base_ids=kb_ids, file_ids=file_ids, top_k=top_k, context=context, session=session, agent_scope=agent_scope)

async def adopt_legacy_documents(*, context, session):
    from app.auth import validate_context
    from app.auth_models import Installation
    from app.access import owned
    validate_context(context, session)
    installation = session.get(Installation, 1)
    if context.role not in ("admin", "super_admin") or installation is None or installation.admin_user_id != context.operator_id:
        raise AppError("FORBIDDEN", "仅安装管理员可显式纳入历史资料", 403)
    # Repository returns only owned/explicitly granted resources; no global legacy scan or bypass.
    existing = [b for b in list_resources(session, "knowledge_base", context) if b.get("legacy_import")]
    kb = existing[0] if existing else await create_library(name="历史演示资料", description="安装管理员私有历史资料；治理发布后检索", context=context, session=session)
    kb["legacy_import"] = True
    save_resource(session, "knowledge_base", kb["id"], kb, context)
    linked = {d["file_id"] for d in list_resources(session, "knowledge_document", context)}
    for file in list_resources(session, "document", context):
        if file["id"] in linked: continue
        try: owned(session, "document", file["id"], context)
        except AppError: continue
        root = {"id": new_id("knowledge_document"), "knowledge_base_id": kb["id"], "file_id": file["id"], "name": file["filename"],
                "status": "uploaded", "published_version_id": None, "draft_version_id": None, "created_at": utc_now(), "legacy_import": True}
        save_resource(session, "knowledge_document", root["id"], root, context)
    return kb

async def financial_facts(*, batch_id, query, context, session):
    from app.contracts import FinancialFact
    batch, root = get_batch(batch_id, context, session)
    if root["dataset_type"] != "finance": raise AppError("VALIDATION_ERROR", "通用数据集不能作为财务事实自动解释")
    if batch["status"] != "published" or not batch["published_version_id"]: raise AppError("NOT_PUBLISHED", "治理批次未发布")
    version = published_version(root, context, session, batch["published_version_id"])
    require_sources_in_scope(sources=[("knowledge_dataset", root["id"]), ("data_table", root["id"]), ("knowledge_version", version["id"])],
        layers=scope_layers(), context=context, session=session, version_id=version["id"])
    by_row = {c["locator"]["row"]: c["source_ref"] for c in version["chunks"]}
    authorized = version_decision(version, root, context, session)
    fields = [f["name"] for f in version["schema_fields"]]
    allowed, masks = _column_policy(authorized, fields)
    if not set(fields) <= allowed or set(fields) & set(masks):
        raise AppError("QUERY_FIELD_DENIED", "财务计算所需字段被隐藏或脱敏，无法执行此计算", 422)
    filters = [{"field": "entity", "value": query.entity}, {"field": "period", "op": "in", "value": query.periods},
        {"field": "metric_code", "op": "in", "value": query.metric_codes}]
    result = filter_rows(decision=authorized, rows=[r["values"] for r in version["rows"]], fields=fields,
        query={"filters": filters, "limit": 1000})
    visible = list(result["rows"])
    for offset in range(1000, result["total"], 1000):
        visible.extend(filter_rows(decision=authorized, rows=[r["values"] for r in version["rows"]], fields=fields,
            query={"filters": filters, "offset": offset, "limit": 1000})["rows"])
    source_by_values = {digest(r["values"]): by_row[r["row"]] for r in version["rows"]}
    selected = []
    for values in visible:
        if any(values[k] != getattr(query, k) for k in ("currency", "unit", "scope")):
            raise AppError("FINANCIAL_CONTEXT_MISMATCH", "治理财务批次与查询币种/单位/口径不一致")
        selected.append(FinancialFact(**values, source_ref=source_by_values[digest(values)], synthetic=True))
    return selected

def access_metadata(*, kb_id, context, session):
    from app.data_access import data_metadata
    return data_metadata(session, decision(session, "knowledge_base", kb_id, context, "manage").resource, context)

async def set_access(*, kb_id, grants, cascade, context, session):
    from app.data_access import replace_data_grants
    decision(session, "knowledge_base", kb_id, context, "manage")
    targets = [("knowledge_base", kb_id)]
    if cascade:
        for kind in ("knowledge_document", "knowledge_dataset", "knowledge_file", "knowledge_batch", "knowledge_version", "document"):
            for value in list_resources(session, kind, context):
                if value.get("knowledge_base_id") == kb_id:
                    decision(session, kind, value["id"], context, "manage")
                    targets.append((kind, value["id"]))
        # Legacy raw files stay untouched; their IDs come only from owned historical manifests.
        for root in list_resources(session, "knowledge_document", context):
            if root["knowledge_base_id"] == kb_id and ("document", root["file_id"]) not in targets:
                decision(session, "document", root["file_id"], context, "manage")
                targets.append(("document", root["file_id"]))
    for kind, resource_id in targets:
        replace_data_grants(session=session, kind=kind, resource_id=resource_id, context=context,
            grants=[{"subject_type": "user", "subject_id": g["user_id"], "effect": "allow",
                "actions": ["use", "maintain"] if g["permission"] == "write" else ["use"]} for g in grants])
    return access_metadata(kb_id=kb_id, context=context, session=session) | {"explicitly_updated": [{"kind": k, "id": i} for k, i in targets],
                                                                         "automatic_inheritance": False}


async def edit_document_overview(*, document_id, overview, context, session):
    root = entry("knowledge_document", document_id, context, session, write=True)
    require_full_document_maintenance(root, context, session)
    from .curation import lock_document
    lock_document(session, context, document_id)
    root = entry('knowledge_document', document_id, context, session, write=True)
    root["overview"] = DocumentOverview.model_validate(overview).model_dump()
    save_resource(session, "knowledge_document", document_id, root, context)
    selected = root.get("draft_version_id") or root.get("published_version_id")
    if not selected:
        return public(root) | {"requires_publish": True}
    # Keep overview and chunk versions in one atomic, witnessed revision.
    return await revise_document(document_id=document_id, corrections=[], base_version_id=selected, context=context, session=session)


def library_overview(*, kb_id, context, session):
    library(kb_id, context, session)
    items = []
    for root in list_resources(session, "knowledge_document", context):
        if root.get("knowledge_base_id") != kb_id or root.get("status") != "published":
            continue
        try:
            version = published_version(root, context, session)
            # A partial source policy cannot safely use a whole-document prose summary.
            if not _unrestricted(version_decision(version, root, context, session)):
                continue
            items.append({"document_id": root["id"], "document_name": root["name"], "version_id": version["id"],
                "snapshot_id": version["content_sha256"], "source_sha256": version["source_sha256"],
                "overview": version.get("overview", {}), "chunk_count": len(visible_chunks(version, root, context, session))})
        except AppError as exc:
            if exc.status_code != 404:
                raise
    return {"knowledge_base_id": kb_id, "items": items, "document_count": len(items), "overview_source": "authorized_published_documents"}
