import hashlib
import base64
from sqlalchemy.orm import Session
from app.contracts import BusinessContext
from app.db import get_resource, list_resources, save_resource, new_id, utc_now
from app.errors import AppError
from app.finance.domain import fact_key
from .workbook import parse_workbook, MAX_BYTES

def public_batch(batch: dict) -> dict:
    return {k: v for k, v in batch.items() if k not in ("facts", "content_base64")} | {"preview": batch.get("facts", [])[:20]}

async def import_file(*, filename: str, content: bytes, context: BusinessContext, session: Session) -> dict:
    if not filename.lower().endswith(".xlsx") or len(content) > MAX_BYTES:
        raise AppError("IMPORT_INVALID", "仅支持最大5MB的标准.xlsx文件")
    digest = hashlib.sha256(content).hexdigest()
    for existing in list_resources(session, "import_batch", context):
        if existing["content_sha256"] == digest:
            return public_batch(existing) | {"duplicate": True}
    facts, errors, count = parse_workbook(content, digest)
    for fact in facts:
        if fact["entity"] not in context.allowed_entities:
            row = int(fact["source_ref"].rsplit("D", 1)[1])
            errors.append({"code": "ENTITY_NOT_ALLOWED", "message": "主体超出本地合成演示授权范围", "row": row,
                           "field": "entity", "column": "entity", "cell": f"A{row}", "sheet": "FinancialFacts"})
    batch_id = new_id("batch")
    batch = {"id": batch_id, "filename": filename.replace("\\", "/").split("/")[-1][:150],
             "content_sha256": digest, "content_base64": base64.b64encode(content).decode("ascii"),
             "status": "invalid" if errors else "validated",
             "facts": facts, "errors": errors, "row_count": count, "valid_row_count": len(facts),
             "error_count": len(errors), "synthetic": True, "created_at": utc_now(), "committed_at": None,
             "source_refs": sorted({f["source_ref"] for f in facts})}
    save_resource(session, "import_batch", batch_id, batch, context)
    return public_batch(batch) | {"duplicate": False}

async def commit_batch(*, batch_id: str, context: BusinessContext, session: Session) -> dict:
    batch = get_resource(session, "import_batch", batch_id, context)
    if batch["status"] == "committed":
        return public_batch(batch) | {"duplicate": True}
    if batch["status"] != "validated" or batch["errors"]:
        raise AppError("IMPORT_INVALID", "错误批次不能确认或查询", details={"errors": batch["errors"]})
    existing = {fact_key(f) for f in list_resources(session, "fact", context)}
    collisions = [f["source_ref"] for f in batch["facts"] if fact_key(f) in existing]
    if collisions:
        raise AppError("CONFLICT", "已有已确认的相同主体、年度、指标及口径，请勿重复发布", status_code=409,
                       details={"source_refs": collisions})
    for f in batch["facts"]:
        f["id"] = new_id("fact")
        save_resource(session, "fact", f["id"], {**f, "batch_id": batch_id}, context)
    batch.update(status="committed", committed_at=utc_now())
    save_resource(session, "import_batch", batch_id, batch, context)
    return public_batch(batch)
