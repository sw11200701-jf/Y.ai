"""Read-time whitelist for a server-bound retrieval result, never a new search.

The central trace reader owns node/attempt/snapshot binding and all other run
surfaces. This helper additionally enforces the current private run principal
and current exact source access before emitting any query or content.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
from app.errors import AppError
from app.db import get_resource
from app.auth_models import RunAuthorization
from app.data_models import DataDerivation
from app.data_access import policy_version, _unrestricted
from .data_store import decision

FIELD_BYTES = 16 * 1024
PROJECTION_BYTES = 128 * 1024
SOURCE_KINDS = {"knowledge_base", "knowledge_document", "knowledge_dataset", "knowledge_version", "document", "knowledge_file",
                "knowledge_source", "knowledge_source_version", "knowledge_unit", "knowledge_unit_revision"}
METHODS = {"keyword_overlap_v1", "semantic_rrf_zh_v1", "hybrid_rrf_zh_v1", "typed_legacy_rank_fusion_v1", "partitioned_retrieval", "fixed_empty_scope"}


def denied():
    raise AppError("NOT_FOUND", "运行或知识来源不可访问", 404)


def invalid():
    raise AppError("KNOWLEDGE_TRACE_INVALID", "本次检索证据不完整，请重新运行节点", 422)


def _text(value):
    if not isinstance(value, str):
        invalid()
    raw = value.encode("utf-8")
    return raw[:FIELD_BYTES].decode("utf-8", errors="ignore"), len(raw) > FIELD_BYTES


def _locator(value):
    allowed = {"page", "paragraph", "start_line", "end_line", "sheet", "row", "table", "kind"}
    return {key: val for key, val in value.items() if key in allowed and type(val) in (str, int) and
            (not isinstance(val, str) or len(val.encode("utf-8")) <= 500)} if isinstance(value, dict) else {}


def _score(hit, method):
    value = hit.get("score")
    try:
        if isinstance(value, bool) or value is None or len(str(value)) > 100 or not Decimal(str(value)).is_finite():
            raise ValueError()
    except (InvalidOperation, ValueError):
        return {"available": False, "value": None, "kind": "not_provided"}
    kind = {"keyword_overlap_v1": "keyword_overlap", "semantic_rrf_zh_v1": "semantic_rrf", "hybrid_rrf_zh_v1": "hybrid_rrf",
            "typed_legacy_rank_fusion_v1": "partition_rank_fusion"}.get(method, "service_score")
    return {"available": True, "value": str(value), "kind": kind}


def _legacy(hit, context, session):
    from . import service as legacy
    version_id = hit.get("version_id")
    version = get_resource(session, "knowledge_version", version_id, context)
    root = legacy.entry(version["resource_kind"], version["resource_id"], context, session)
    version = legacy.published_version(root, context, session, version_id)
    identity = hit.get("chunk_id", hit.get("id"))
    current = next((chunk for chunk in legacy.visible_chunks(version, root, context, session)
                    if chunk["id"] == identity and chunk.get("enabled", True)), None)
    if current is None or hit.get("text") != current.get("text") or hit.get("source_ref") != current.get("source_ref"):
        denied()
    if hit.get("snapshot_id") not in (None, version.get("content_sha256")):
        denied()
    file_kind = "document" if version["file_id"].startswith("document_") else "knowledge_file"
    refs = [("knowledge_base", root["knowledge_base_id"]), (version["resource_kind"], root["id"]),
            ("knowledge_version", version_id), (file_kind, version["file_id"])]
    full_metadata = all(_unrestricted(decision(session, kind, rid, context)) for kind, rid in refs)
    source = {"document_id": root["id"], "version_id": version_id, "chunk_id": identity, "source_ref": current["source_ref"]}
    if full_metadata:
        source["display_name"] = legacy.raw_file({**root, "file_id": version["file_id"]}, context, session)["filename"]
    if isinstance(current.get("source_label"), str) and current["source_label"] != "***":
        source["label"] = current["source_label"]
    source["locator"] = _locator(current.get("locator"))
    text, cut = _text(hit["text"])
    return {"kind": "document", "citation_id": identity, "content": {"text": text}, "source": source, "truncated": cut}, refs


def _typed(hit, context, session):
    # Deliberately lazy: adding this file to the Schema6 baseline does not import
    # or migrate the unshipped typed knowledge/Schema8 subsystem.
    try:
        from . import units_service as typed
    except ImportError as exc:
        raise AppError("KNOWLEDGE_TRACE_TYPE_UNAVAILABLE", "当前候选未包含此知识单元类型", 503) from exc
    uid, revision_id, version_id = hit.get("unit_id"), hit.get("revision_id"), hit.get("source_version_id")
    current = typed.store_for(session, context).get_unit(uid, revision_id, action="use")
    if not revision_id or not version_id or current["revision_id"] != revision_id or current["source_version_id"] != version_id:
        denied()
    root = typed._root(current["source_id"], context, session)
    version = typed._version(version_id, root, context, session, "use")
    current, _, _ = typed._present(current, root, version, context, session, "use")
    if current is None or hit.get("source_ref") != current["source_ref"] or hit.get("kind") != current["kind"]:
        denied()
    kind = current["kind"]
    source = {key: current[key] for key in ("source_id", "source_version_id", "unit_id", "revision_id", "source_ref")}
    source["locator"] = _locator(current.get("locator"))
    if current.get("source_label"):
        source["label"] = current["source_label"]
    refs = [("knowledge_base", root["knowledge_base_id"]), ("knowledge_source", root["id"]), ("knowledge_source_version", version_id),
            ("knowledge_unit", uid), ("knowledge_unit_revision", revision_id)]
    file_id = version.get("metadata", {}).get("file_id")
    if file_id:
        refs.append((typed._file_kind(root, file_id), file_id))
        if all(_unrestricted(decision(session, resource_kind, rid, context)) for resource_kind, rid in refs):
            source["display_name"] = typed._file_metadata(root, context, session, file_id=file_id)["filename"]
    payload, original = current["payload"], hit.get("payload", {})
    cut = False
    if kind == "record":
        fields = []
        original_fields = {field["name"]: field for field in original.get("fields", [])}
        for field in payload["fields"]:
            name = field["name"]
            if name not in original_fields or name not in original.get("values", {}):
                continue
            value = payload["values"][name]
            if type(value) is not type(original["values"][name]) or value != original["values"][name]:
                denied()
            if isinstance(value, str):
                value, shortened = _text(value); cut |= shortened
            entry = {key: field[key] for key in ("name", "label", "type", "unit", "currency") if key in field}
            entry["value"] = value
            fields.append(entry)
        content = {"fields": fields, "schema_version": payload.get("schema_version")}
    else:
        keys = ("question", "answer") if kind == "qa_pair" else ("body",)
        content = {}
        for key in keys:
            if original.get(key) != payload.get(key):
                denied()
            content[key], shortened = _text(original[key]); cut |= shortened
        if kind == "qa_pair" and hit.get("matched_purpose") in ("primary", "alias", "child"):
            content["matched_purpose"] = hit["matched_purpose"]
    return {"kind": kind, "citation_id": uid, "content": content, "source": source, "truncated": cut}, refs


def project_workflow_retrieval(*, run_id, resolved_query, result_snapshot, input_sources, context, session):
    """Central must supply the persisted result for this exact run/node/attempt.

    This is a server-only read projection, not a capture API or a reretrieval.
    The returned source list must be covered by the central run derivation.
    No source/body is returned when any current source check fails.
    """
    from app.auth import validate_context
    validate_context(context, session)
    auth = session.get(RunAuthorization, run_id, populate_existing=True)
    if auth is None or auth.user_id != context.operator_id:
        denied()
    get_resource(session, "run", run_id, context)
    policy = policy_version(session, context)
    from sqlalchemy import select
    from app.db import Resource
    run_pk = session.scalar(select(Resource.id).where(Resource.kind == "run", Resource.resource_id == run_id,
        Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id))
    covered = set(session.scalars(select(DataDerivation.source_pk).where(DataDerivation.resource_pk == run_pk,
        DataDerivation.actor_id == context.operator_id, DataDerivation.policy_revision == policy)))
    if not isinstance(result_snapshot, dict) or not isinstance(resolved_query, str) or not resolved_query or len(resolved_query) > 500:
        invalid()
    if result_snapshot.get("query", resolved_query) != resolved_query:
        invalid()
    hits = result_snapshot.get("hits")
    if not isinstance(hits, list) or len(hits) > 100 or not isinstance(input_sources, list):
        invalid()
    result_sources = result_snapshot.get("data_sources", [])
    if not isinstance(result_sources, list):
        invalid()
    references = deepcopy(input_sources) + deepcopy(result_sources)
    if not references or len(references) > 512:
        invalid()
    canonical = {}
    def authorize_refs(refs):
        for kind, identity in refs:
            if not isinstance(kind, str) or kind not in SOURCE_KINDS or not isinstance(identity, str) or not identity:
                invalid()
            current = decision(session, kind, identity, context, "use")
            if current.resource.id not in covered:
                denied()
            canonical[(kind, identity)] = {"kind": kind, "resource_id": identity, "action": "use"}
    authorize_refs([(ref.get("kind"), ref.get("resource_id")) for ref in references if isinstance(ref, dict)])
    if any(not isinstance(ref, dict) or ref.get("action", "use") != "use" for ref in references):
        invalid()
    method = result_snapshot.get("method")
    if not isinstance(method, str) or method not in METHODS:
        invalid()
    items, trimmed = [], False
    for hit in hits:
        if not isinstance(hit, dict):
            invalid()
        item, refs = _typed(hit, context, session) if hit.get("unit_id") else _legacy(hit, context, session)
        authorize_refs(refs)
        item["score"] = _score(hit, method)
        if hit.get("parent_context"):
            parent, refs = _typed(hit["parent_context"], context, session)
            authorize_refs(refs)
            item["parent_context"] = parent
        if len(json.dumps(items + [item], ensure_ascii=False).encode("utf-8")) <= PROJECTION_BYTES:
            items.append(item)
        else:
            trimmed = True
    # Repeat the principal/run/derivation and source checks after all resolution.
    validate_context(context, session)
    get_resource(session, "run", run_id, context)
    authorize_refs(list(canonical))
    if policy_version(session, context) != policy:
        denied()
    retrieval = {"schema_version": "knowledge-retrieval-trace-v1", "query": resolved_query, "method": method,
                 "keyword": result_snapshot.get("keyword") if isinstance(result_snapshot.get("keyword"), bool) else None,
                 "vector": result_snapshot.get("vector") is True, "returned_count": len(hits), "displayed_count": len(items),
                 "items": items, "truncated": trimmed or any(item["truncated"] or item.get("parent_context", {}).get("truncated", False) for item in items)}
    return {"retrieval": retrieval, "sources": list(canonical.values())}
