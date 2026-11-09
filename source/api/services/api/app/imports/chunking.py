"""Version-oriented chunking over authorized original bytes, independent of legacy extraction cache."""
import hashlib
import re
from app.errors import AppError
from .documents import _segments, _locator_text, MAX_TEXT, MAX_CHUNKS

def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def counts(text):
    return {"char_count": len(text), "word_count": len(re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]", text))}

def source_label(locator):
    kind = locator.get("kind")
    if kind == "page": return f"第{locator['page']}页"
    if kind == "paragraph": return f"第{locator['paragraph']}段"
    if kind == "table_cell": return f"表{locator['table']} 第{locator['row']}行第{locator['column']}列"
    if kind == "cell": return f"工作表「{locator['sheet']}」单元格 {locator['cell']}"
    if kind == "row": return f"第{locator['row']}行"
    return f"第{locator.get('start_line', 1)}至{locator.get('end_line', 1)}行"

def validate_options(strategy, chunk_size, chunk_overlap):
    if (strategy not in ("length", "paragraph") or type(chunk_size) is not int or not 100 <= chunk_size <= 2000
            or type(chunk_overlap) is not int or not 0 <= chunk_overlap <= min(200, chunk_size // 2)):
        raise AppError("VALIDATION_ERROR", "切片策略须为按长度或按段落；长度100至2000字，重叠0至200字且不超过长度一半")

def extract_chunks(*, content, kind, file_id, strategy="length", chunk_size=1000, chunk_overlap=100):
    validate_options(strategy, chunk_size, chunk_overlap)
    chunks, warnings, total = [], [], 0
    if kind == "docx": warnings.append({"code": "DOCX_BODY_TEXT_ONLY", "message": "仅提取正文段落和表格，不包含页眉页脚、文本框与图片"})
    try:
        for text, original_locator in _segments(content, kind):
            total += len(text)
            if total > MAX_TEXT: raise AppError("DOCUMENT_LIMIT", "原文超过50万字符，请拆分文件；未截断文本")
            if not text.strip():
                if original_locator["kind"] == "page": warnings.append({"code": "PDF_PAGE_NO_TEXT", "page": original_locator["page"], "message": "该页没有可提取文本，当前不支持OCR"})
                continue
            spans = [(0, len(text))]
            if strategy == "paragraph":
                # Blank lines are paragraph boundaries; existing DOCX paragraphs/pages/cells are never merged.
                spans = []
                begin = 0
                for boundary in re.finditer(r"\r?\n[\t ]*\r?\n+", text):
                    if text[begin:boundary.start()].strip(): spans.append((begin, boundary.start()))
                    begin = boundary.end()
                if text[begin:].strip(): spans.append((begin, len(text)))
            for paragraph_index, (begin, stop) in enumerate(spans, 1):
                start = begin
                while start < stop:
                    end = min(start + chunk_size, stop)
                    body = text[start:end]
                    if body.strip():
                        locator = dict(original_locator)
                        if locator["kind"] == "lines":
                            locator.update(start_line=text[:start].count("\n") + 1,
                                           end_line=text[:end].count("\n") + (0 if text[end-1:end] == "\n" else 1))
                        if strategy == "paragraph": locator["paragraph_in_source"] = paragraph_index
                        original_ref = f"document:{file_id}:{_locator_text(locator)}:chars:{start}-{end}"
                        chunks.append({"id": f"{file_id}_source_{len(chunks)+1}", "file_id": file_id, "text": body, "locator": locator,
                                       "offset_start": start, "offset_end": end, "source_ref": original_ref, "source_label": source_label(locator),
                                       "sequence": len(chunks)+1, "enabled": True, "status": "enabled", "strategy": strategy,
                                       "text_sha256": text_hash(body), "original_text_sha256": text_hash(body), **counts(body)})
                        if len(chunks) > MAX_CHUNKS: raise AppError("DOCUMENT_LIMIT", "切片超过1000片，请增大长度或拆分原文；未返回截断结果")
                    if end == stop: break
                    start = end - chunk_overlap
    except AppError:
        raise
    except Exception as exc:
        raise AppError("DOCUMENT_INVALID", "无法解析文件正文，请检查文件格式或是否损坏") from exc
    if not chunks: raise AppError("DOCUMENT_NO_TEXT", "没有可提取的正文；扫描PDF或图片尚未配置OCR")
    return {"chunks": chunks, "warnings": warnings, "text_length": total,
            "chunking": {"strategy": strategy, "chunk_size": chunk_size, "chunk_overlap": chunk_overlap,
                         "overlap_scope": "within_source_paragraph" if strategy == "paragraph" else "within_source_segment"}}
