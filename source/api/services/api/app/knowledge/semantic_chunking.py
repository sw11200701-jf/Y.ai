"""Server-only semantic result validation. No model calls, grants or publication.

Transport segments are not semantic chunks. Only validated Agent span choices
can become a draft, after the caller verifies its actual execution receipt.
"""
from copy import deepcopy
import hashlib
import json
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from app.errors import AppError

LOCATORS = {"kind", "page", "paragraph", "table", "row", "column", "sheet", "cell", "start_line", "end_line", "formula_text"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Segment(Strict):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1)
    locator: dict[str, str | int | bool]

    @field_validator("locator")
    @classmethod
    def safe_locator(cls, value):
        if not value or not set(value) <= LOCATORS or any(isinstance(v, str) and len(v) > 500 for v in value.values()):
            raise ValueError("Invalid source locator")
        # The XLSX extractor records formulas as text; this flag is provenance,
        # never permission to evaluate a formula or follow an external link.
        if "formula_text" in value and (value.get("kind") != "cell" or type(value["formula_text"]) is not bool):
            raise ValueError("Invalid formula text marker")
        if any(type(item) is bool for key, item in value.items() if key != "formula_text"):
            raise ValueError("Invalid boolean source locator")
        return value


def invalid(message="模型切片结果与原文覆盖或固定输入不一致"):
    raise AppError("KNOWLEDGE_SEMANTIC_RESULT_INVALID", message, 422)


def source_segments(values):
    if not isinstance(values, list) or not values:
        invalid("本批原文段数量超限")
    try:
        segments = [Segment.model_validate(value) for value in values]
    except (ValidationError, TypeError, ValueError):
        invalid("本批原文段格式无效")
    from app.agents.builtin_chunker import ChunkWindow
    try:
        ChunkWindow.model_validate({"document_version_id": "validate", "window_id": "validate",
            "units": [{"unit_id": item.id, "text": item.text} for item in segments]})
    except (ValueError, TypeError):
        invalid("原文窗口不满足共享Agent输入合同")
    return segments


def segment_manifest_sha256(values):
    segments = source_segments(values)
    body = json.dumps([segment.model_dump() for segment in segments], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def validate_agent_window(*, window, raw_result, segments, expected_input_sha256, expected_manifest_sha256):
    """03 knowledge-chunker-v1 wire adapter; pure validation, never inference.

    Window input and locators must match the exact persisted authorized step.
    """
    from app.agents.builtin_chunker import ChunkWindow, digest, validate_result
    source = source_segments(segments)
    if segment_manifest_sha256(segments) != expected_manifest_sha256:
        invalid("原文定位manifest已变化")
    try:
        parsed = ChunkWindow.model_validate(window).model_dump(mode="json")
    except (ValidationError, ValueError, TypeError):
        invalid("切片Agent窗口输入无效")
    if digest(parsed) != expected_input_sha256 or parsed["units"] != [{"unit_id": segment.id, "text": segment.text} for segment in source]:
        invalid("切片窗口与已授权原文不一致")
    result = validate_result(parsed, raw_result)
    indexed = {segment.id: segment for segment in source}
    chunks = []
    for chunk in result["chunks"]:
        segment = indexed[chunk["unit_id"]]
        text = segment.text[chunk["start"]:chunk["end"]]
        chunks.append({"topic": chunk["topic"], "summary": chunk["summary"], "text": text,
            "unit_id": segment.id, "offset_start": chunk["start"], "offset_end": chunk["end"],
            "locator": deepcopy(segment.locator), "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
    return {"contract_version": "knowledge-chunker-v1", "window_id": parsed["window_id"],
        "document_version_id": parsed["document_version_id"], "input_sha256": expected_input_sha256,
        "segment_manifest_sha256": expected_manifest_sha256, "overview": result["overview"],
        "topics": result["topics"], "chunks": chunks, "model_execution_attested": False,
        "requires_current_authorization_and_execution_receipt": True}
