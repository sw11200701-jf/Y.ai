"""Read literal CSV/TSV records and retain original row and column positions."""
import codecs
import csv
from io import StringIO

from app.errors import AppError

DELIMITERS = {"csv": ",", "tsv": "\t"}


def cells(content, kind):
    if content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings = ("utf-16",)
    else:
        encodings = ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise AppError("DOCUMENT_ENCODING", "CSV/TSV 请保存为 UTF-8、带 BOM 的 UTF-16 或 GB18030。", 422)
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        raise AppError("DOCUMENT_FORMAT_MISMATCH", "CSV/TSV 应为纯文本表格，请用原应用重新导出。", 422)
    count = 0
    positions = 0
    characters = 0
    try:
        reader = csv.reader(StringIO(text, newline=""), delimiter=DELIMITERS[kind], strict=True)
        for row_number, row in enumerate(reader, 1):
            positions += len(row)
            if row_number > 100_000 or len(row) > 16_384 or positions > 1_000_000:
                raise AppError("DOCUMENT_LIMIT", "CSV/TSV 行列数超过解析上限。", 422)
            for column, value in enumerate(row, 1):
                if value.strip():
                    count += 1
                    characters += len(value)
                    if count > 10_000:
                        raise AppError("DOCUMENT_LIMIT", "CSV/TSV 的非空单元格超过 10000 个。", 422)
                    if characters > 500_000:
                        raise AppError("DOCUMENT_LIMIT", "CSV/TSV 的正文超过 500000 字符。", 422)
                    yield row_number, column, value
    except csv.Error as exc:
        raise AppError("DOCUMENT_INVALID", "CSV/TSV 的引号、字段长度或分隔结构无效，请重新导出。", 422) from exc
    if count == 0:
        raise AppError("DOCUMENT_INVALID", "CSV/TSV 中没有可处理的非空单元格。", 422)
