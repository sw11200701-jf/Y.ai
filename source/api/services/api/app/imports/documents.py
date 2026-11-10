"""Controlled file IDs, text extraction with locators, and transparent keyword retrieval."""
import base64
import hashlib
import importlib.util
import re
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import PurePath
from zipfile import ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn
from openpyxl import load_workbook
from sqlalchemy.orm import Session
from app.contracts import BusinessContext
from app.db import new_id, utc_now
from app.knowledge.data_store import get_resource, list_resources, save_resource, decision
from app.errors import AppError
from .document_container import CONVERSION_HINTS, RULE_FORMATS, SEMANTIC_FORMATS

MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT = 500000
MAX_CHUNKS = 1000
FORMATS = RULE_FORMATS

def upload_contract(profile="legacy"):
    if profile == "legacy":
        # PPTX uses the provenance-aware model recipe; the classic rule parser
        # continues to reject it explicitly instead of losing layout/images.
        rule_formats = {suffix: kind for suffix, kind in FORMATS.items()
                        if kind != "pdf" or importlib.util.find_spec("pypdf")}
        return {"profile": profile, "formats": {**rule_formats, ".pptx": "pptx"},
                "rule_formats": rule_formats, "max_bytes": MAX_BYTES}
    if profile != "portable-slicing-1.0.0":
        raise AppError("VALIDATION_ERROR", "未知文档处理方式")
    from app.knowledge.slicing_parser import capabilities as parser_capabilities
    available = parser_capabilities()
    return {"profile": profile, "formats": {suffix: SEMANTIC_FORMATS[suffix] for suffix in available["formats"]},
            "max_bytes": available["max_bytes"]}

def capabilities():
    pdf = importlib.util.find_spec("pypdf") is not None
    result = {"formats": {kind: {"supported": kind != "pdf" or pdf, "method": "text_layer" if kind == "pdf" else "text",
                                "reason": None if kind != "pdf" or pdf else "pypdf dependency not installed"} for kind in FORMATS.values()},
            "ocr": {"supported": False, "reason": "扫描PDF及图片OCR尚未实现"},
            "retrieval": {"method": "keyword_overlap_v1", "vector": False, "reranker": False},
            "limits": {"file_bytes": MAX_BYTES, "text_chars": MAX_TEXT, "chunks": MAX_CHUNKS}}
    result["formats"]["docx"]["scope"] = "body paragraphs and body tables; excludes headers/footers/textboxes/images"
    result["formats"]["xlsx"]["scope"] = "nonempty cells; formula text only, no execution or linked-file access"
    return result

def public_document(doc):
    return {k: v for k, v in doc.items() if k not in ("content_base64", "chunks")}

def document_metadata(*, file_id, context, session):
    from app.knowledge.service import authorize_document_file
    from app.data_access import _unrestricted
    value = authorize_document_file(file_id=file_id, context=context, session=session)
    if _unrestricted(decision(session, "document", file_id, context)):
        return public_document(value)
    return {key: value[key] for key in ("id", "filename", "format", "status")}

def validate_file_id(file_id):
    if not isinstance(file_id, str) or not re.fullmatch(r"document_[a-f0-9]{32}", file_id):
        raise AppError("VALIDATION_ERROR", "请使用资料导入返回的受控file_id，不能提供路径或URL")

def _zip_limit(content):
    with ZipFile(BytesIO(content)) as archive:
        infos = archive.infolist()
        if len(infos) > 2000 or sum(i.file_size for i in infos) > 40 * 1024 * 1024:
            raise AppError("DOCUMENT_LIMIT", "文档解压大小或成员数超限")

def _decode_plain_text(content):
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError("DOCUMENT_ENCODING", "TXT/Markdown 请保存为 UTF-8 编码。", 422) from exc
    if "\x00" in text:
        raise AppError("DOCUMENT_ENCODING", "文件包含 NUL 字节，请重新导出为 UTF-8 纯文本。", 422)
    return text

async def import_document(*, filename: str, content: bytes, context: BusinessContext, session: Session, knowledge_base_id=None, profile="legacy", deduplicate=True) -> dict:
    safe_name = filename.replace("\\", "/").split("/")[-1][:150]
    suffix = PurePath(safe_name).suffix.lower()
    contract = upload_contract(profile)
    formats, limit = contract["formats"], contract["max_bytes"]
    if suffix not in formats:
        message = CONVERSION_HINTS.get(suffix)
        if suffix in SEMANTIC_FORMATS and profile == "legacy":
            message = "该文件请使用知识切片助手上传和处理。"
        elif suffix == ".doc":
            message = "本机旧版 Word 转换组件暂未启用。组件可用后即可上传原件。"
        raise AppError("DOCUMENT_UNSUPPORTED", message or "当前入口不支持该文件格式，请按上传区列出的格式重新导出。", 422,
                       details={"formats": list(formats), "profile": profile})
    if not content or len(content) > limit:
        raise AppError("VALIDATION_ERROR", "请选择非空受支持文档，最大" + str(limit // (1024 * 1024)) + "MB",
                       details={"formats": list(formats), "max_bytes": limit, "profile": profile})
    from .document_container import validate_container
    validate_container(content, formats[suffix])
    if formats[suffix] in ("text", "markdown"):
        _decode_plain_text(content)
    if formats[suffix] in ("csv", "tsv"):
        from .delimited_text import cells
        for _ in cells(content, formats[suffix]):
            pass
    digest = hashlib.sha256(content).hexdigest()
    if deduplicate:
        for existing in list_resources(session, "document", context):
            if existing["sha256"] == digest and existing["format"] == formats[suffix] and existing.get("knowledge_base_id") == knowledge_base_id:
                return public_document(existing) | {"duplicate": True}
    doc = {"id": new_id("document"), "filename": safe_name, "format": formats[suffix], "size_bytes": len(content),
           "sha256": digest, "content_base64": base64.b64encode(content).decode("ascii"), "status": "uploaded",
           "created_at": utc_now(), "synthetic": True, "source_refs": [], "chunk_count": 0}
    if profile != "legacy": doc["upload_profile"] = profile
    if knowledge_base_id:
        from app.knowledge.service import library
        library(knowledge_base_id, context, session, write=True)
        doc["knowledge_base_id"] = knowledge_base_id
    save_resource(session, "document", doc["id"], doc, context)
    return public_document(doc) | {"duplicate": False}

def _segments(content, kind):
    """Yield original text and a stable locator. Never resolve links or execute formulas."""
    from .document_container import validate_container
    validate_container(content, kind)
    if kind not in FORMATS.values():
        raise AppError("DOCUMENT_UNSUPPORTED", "此格式请使用知识切片助手处理，普通文本规则解析不支持该格式", 422)
    if kind in ("text", "markdown"):
        text = _decode_plain_text(content)
        yield text, {"kind": "lines", "start_line": 1, "end_line": text.count("\n") + 1}
    elif kind == "docx":
        _zip_limit(content)
        doc = Document(BytesIO(content))
        paragraph, table_no = 0, 0
        for element in doc.element.body:
            if element.tag == qn("w:p"):
                paragraph += 1
                yield Paragraph(element, doc).text, {"kind": "paragraph", "paragraph": paragraph}
            elif element.tag == qn("w:tbl"):
                table_no += 1
                for r, row in enumerate(Table(element, doc).rows, 1):
                    for c, cell in enumerate(row.cells, 1):
                        yield cell.text, {"kind": "table_cell", "table": table_no, "row": r, "column": c}
    elif kind == "xlsx":
        _zip_limit(content)
        wb = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
        count = 0
        try:
            for ws in wb:
                ws.reset_dimensions()
                for row in ws:
                    for cell in row:
                        if cell.value is not None:
                            count += 1
                            if count > 10000:
                                raise AppError("DOCUMENT_LIMIT", "XLSX资料非空单元格超过10000")
                            yield str(cell.value), {"kind": "cell", "sheet": ws.title, "cell": cell.coordinate,
                                                   "formula_text": cell.data_type == "f"}
        finally:
            wb.close()
    elif kind == "pdf":
        if not capabilities()["formats"]["pdf"]["supported"]:
            raise AppError("SERVICE_UNAVAILABLE", "文本PDF提取需要pypdf依赖；OCR未实现", status_code=503)
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise AppError("DOCUMENT_UNSUPPORTED", "不支持加密PDF")
        if len(reader.pages) > 200:
            raise AppError("DOCUMENT_LIMIT", "PDF最多200页")
        for number, page in enumerate(reader.pages, 1):
            yield page.extract_text() or "", {"kind": "page", "page": number}

def _locator_text(locator):
    kind = locator["kind"]
    if kind == "cell": return f"sheet:{locator['sheet']}!{locator['cell']}"
    if kind == "page": return f"page:{locator['page']}"
    if kind == "paragraph": return f"paragraph:{locator['paragraph']}"
    if kind == "table_cell": return f"table:{locator['table']}:row:{locator['row']}:column:{locator['column']}"
    return f"lines:{locator['start_line']}-{locator['end_line']}"

def validate_extraction_request(*, file_id: str, context: BusinessContext, session: Session,
                                chunk_size: int = 1000, chunk_overlap: int = 100, agent_scope=None):
    """Pure authorization/configuration preflight; repeated at actual extraction."""
    validate_file_id(file_id)
    from app.knowledge.service import authorize_document_file
    authorize_document_file(file_id=file_id, context=context, session=session)
    from app.knowledge.execution_scope import scope_layers, require_sources_in_scope
    require_sources_in_scope(sources=[("document", file_id)], layers=scope_layers(agent_scope, knowledge=True), context=context, session=session)
    from app.data_access import _unrestricted
    if not _unrestricted(decision(session, "document", file_id, context, "maintain")):
        raise AppError("DATA_SCOPE_WRITE_DENIED", "原文提取需要该资料的完整维护范围", 403)
    if (type(chunk_size) is not int or not 200 <= chunk_size <= 2000 or type(chunk_overlap) is not int
            or not 0 <= chunk_overlap <= 200 or chunk_overlap >= chunk_size):
        raise AppError("VALIDATION_ERROR", "chunk_size须为200..2000，overlap须为0..200且小于size")
    doc = get_resource(session, "document", file_id, context)
    if doc.get("status") == "extracted" and (doc["chunk_size"] != chunk_size or doc["chunk_overlap"] != chunk_overlap):
        raise AppError("CONFLICT", "资料已按固定切分参数解析，拒绝静默改变文件ID", status_code=409)
    return doc


async def extract_document(*, file_id: str, context: BusinessContext, session: Session,
                           chunk_size: int = 1000, chunk_overlap: int = 100, agent_scope=None) -> dict:
    doc = validate_extraction_request(file_id=file_id, context=context, session=session,
                                      chunk_size=chunk_size, chunk_overlap=chunk_overlap, agent_scope=agent_scope)
    if doc.get("status") == "extracted":
        return _extraction_result(doc)
    content = base64.b64decode(doc["content_base64"], validate=True)
    chunks, warnings, total = [], [], 0
    if doc["format"] == "docx":
        warnings.append({"code": "DOCX_BODY_TEXT_ONLY", "message": capabilities()["formats"]["docx"]["scope"]})
    try:
        for text, original_locator in _segments(content, doc["format"]):
            total += len(text)
            if total > MAX_TEXT:
                raise AppError("DOCUMENT_LIMIT", "提取文本超过500000字符，未返回截断文本")
            if not text.strip():
                if original_locator["kind"] == "page":
                    warnings.append({"code": "PDF_PAGE_NO_TEXT", "page": original_locator["page"], "ocr_supported": False})
                continue
            step = chunk_size - chunk_overlap
            for start in range(0, len(text), step):
                end = min(start + chunk_size, len(text))
                locator = dict(original_locator)
                if locator["kind"] == "lines":
                    locator.update(start_line=text[:start].count("\n") + 1,
                                   end_line=text[:end].count("\n") + (0 if text[end-1:end] == "\n" else 1))
                source = f"SYNTHETIC:document:{file_id}:{_locator_text(locator)}:chars:{start}-{end}"
                chunks.append({"id": f"{file_id}_chunk_{len(chunks)+1}", "file_id": file_id, "text": text[start:end],
                               "locator": locator, "offset_start": start, "offset_end": end, "source_ref": source, "synthetic": True})
                if len(chunks) > MAX_CHUNKS:
                    raise AppError("DOCUMENT_LIMIT", "资料超过1000块，未返回截断结果")
                if end == len(text):
                    break
    except AppError:
        raise
    except Exception as exc:
        raise AppError("DOCUMENT_INVALID", "无法读取有效的资料文件") from exc
    if not chunks:
        raise AppError("DOCUMENT_NO_TEXT", "未提取到文本；扫描PDF或图片OCR尚未支持", details={"ocr_supported": False, "warnings": warnings})
    doc.update(status="extracted", chunks=chunks, chunk_count=len(chunks), text_length=total, chunk_size=chunk_size,
               chunk_overlap=chunk_overlap, extracted_at=utc_now(), source_refs=[c["source_ref"] for c in chunks],
               extraction_method="text_layer" if doc["format"] == "pdf" else "text", warnings=warnings)
    save_resource(session, "document", file_id, doc, context)
    return _extraction_result(doc)

def _extraction_result(doc):
    return {"file_id": doc["id"], "filename": doc["filename"], "sha256": doc["sha256"], "chunks": doc["chunks"],
            "chunk_count": doc["chunk_count"], "text_length": doc["text_length"], "source_refs": doc["source_refs"],
            "extraction_method": doc["extraction_method"], "warnings": doc["warnings"], "synthetic": doc["synthetic"]}

def _terms(text):
    # Latin words plus Chinese overlapping bigrams; exact substring is separately scored.
    terms = re.findall(r"[a-z0-9_]+", text.lower())
    for phrase in re.findall(r"[\u3400-\u9fff]+", text):
        terms.extend(phrase[i:i+2] for i in range(len(phrase)-1))
        if len(phrase) == 1: terms.append(phrase)
    return Counter(terms)

async def search_documents(*, query: str, file_ids: list[str], context: BusinessContext, session: Session, top_k: int = 5, agent_scope=None) -> dict:
    # Official retrieval is governed/published-only. Raw extraction remains a preview.
    from app.knowledge.service import search_files
    return await search_files(query=query, file_ids=file_ids, top_k=top_k, context=context, session=session, agent_scope=agent_scope)

async def _legacy_keyword_search(*, query: str, file_ids: list[str], context: BusinessContext, session: Session, top_k: int = 5) -> dict:
    if not isinstance(query, str) or not query.strip() or len(query) > 500 or not _terms(query):
        raise AppError("VALIDATION_ERROR", "检索query须为1..500字的有效关键词")
    if (not isinstance(file_ids, list) or not 1 <= len(file_ids) <= 20 or any(not isinstance(f, str) for f in file_ids)
            or len(set(file_ids)) != len(file_ids)):
        raise AppError("VALIDATION_ERROR", "file_ids须显式提供1..20个不重复受控文件ID")
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise AppError("VALIDATION_ERROR", "top_k须为1..20")
    documents = []
    for file_id in file_ids:
        validate_file_id(file_id)
        doc = get_resource(session, "document", file_id, context)
        if doc["status"] != "extracted":
            raise AppError("DOCUMENT_NOT_INDEXED", "资料尚未提取，请先执行文档提取节点", details={"file_id": file_id})
        documents.append(doc)
    query_terms, ranked, warnings = _terms(query), [], []
    for doc in documents:
        warnings.extend(doc.get("warnings", []))
        for chunk in doc["chunks"]:
            tokens = _terms(chunk["text"])
            matched = sorted(set(query_terms) & set(tokens))
            if not matched:
                continue
            coverage = Decimal(len(matched)) / Decimal(len(query_terms))
            frequency = sum(min(tokens[t], 3) for t in matched)
            exact = Decimal(1) if query.strip().lower() in chunk["text"].lower() else Decimal(0)
            score = (coverage * 3 + Decimal(frequency) / 100 + exact).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            ranked.append({**chunk, "filename": doc["filename"], "score": format(score, "f"), "matched_terms": matched})
    ranked.sort(key=lambda hit: (-Decimal(hit["score"]), hit["file_id"], hit["id"]))
    hits = ranked[:top_k]
    return {"method": "keyword_overlap_v1", "query": query, "file_ids": file_ids, "hits": hits,
            "source_refs": [h["source_ref"] for h in hits], "warnings": warnings, "synthetic": True,
            "scoring": "3*unique_keyword_coverage + capped_term_frequency/100 + exact_phrase_bonus; not vector/OCR"}
