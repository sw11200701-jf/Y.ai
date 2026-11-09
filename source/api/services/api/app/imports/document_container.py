"""Validate Office container identity before parsing or reserving model work.

Read package metadata only; never rename, decrypt, convert, or execute content.
"""
from io import BytesIO
import re
from zipfile import ZipFile, BadZipFile, LargeZipFile
from app.errors import AppError

OLE = bytes.fromhex('d0cf11e0a1b11ae1')
PARTS = {'docx': 'word/document.xml', 'xlsx': 'xl/workbook.xml', 'pptx': 'ppt/presentation.xml'}
RULE_FORMATS = {".txt": "text", ".md": "markdown", ".markdown": "markdown",
                ".docx": "docx", ".xlsx": "xlsx", ".pdf": "pdf"}
OFFICE_ALIASES = {"docm": "docx", "xlsm": "xlsx", "pptm": "pptx"}
OFFICE_CONVERSIONS = {
    "xls": "xlsx", "xlsb": "xlsx", "ods": "xlsx",
    "ppt": "pptx", "odp": "pptx",
    "rtf": "docx", "odt": "docx",
}
SEMANTIC_FORMATS = {**RULE_FORMATS, ".doc": "doc", ".pptx": "pptx", ".csv": "csv", ".tsv": "tsv",
                    **{"." + name: name for name in (*OFFICE_ALIASES, *OFFICE_CONVERSIONS)}}
ODF_MIME = {
    "odt": b"application/vnd.oasis.opendocument.text",
    "ods": b"application/vnd.oasis.opendocument.spreadsheet",
    "odp": b"application/vnd.oasis.opendocument.presentation",
}
CONVERSION_HINTS = {
    "." + name: "本机 Office 转换组件暂未启用。组件可用后即可上传原件。"
    for name in OFFICE_CONVERSIONS
}


def canonical_format(kind):
    value = SEMANTIC_FORMATS.get(kind.lower(), kind.lower().lstrip("."))
    return OFFICE_ALIASES.get(value, OFFICE_CONVERSIONS.get(value, value))


def validate_container(content, kind):
    if kind in ("doc", "xls", "ppt") and not content.startswith(OLE):
        raise AppError("DOCUMENT_FORMAT_MISMATCH", f"文件内容与旧版 Office .{kind} 不符，请检查原文件。", 422)
    if kind == "rtf" and not re.match(rb"\{\\rtf[0-9]+", content[:32].lstrip()):
        raise AppError("DOCUMENT_FORMAT_MISMATCH", "文件内容与 RTF 不符，请检查原文件。", 422)
    if kind == "pdf" and not re.search(rb"%PDF-[0-9]+\.[0-9]+", content[:1024]):
        raise AppError("DOCUMENT_FORMAT_MISMATCH", "文件内容与 PDF 不符，请使用原应用重新导出为 PDF。", 422)
    expected_part = PARTS.get(OFFICE_ALIASES.get(kind, kind))
    if kind == "xlsb":
        expected_part = "xl/workbook.bin"
    if not expected_part and kind not in ODF_MIME:
        return
    if content.startswith(OLE):
        raise AppError('DOCUMENT_FORMAT_MISMATCH',
            f'文件扩展名为 .{kind}，内容是旧版或受保护的 Office 文件。请核对文件是否加密及其原始后缀。', 422,
            {'expected_format': kind, 'detected_container': 'ole_compound', 'model_request_sent': False})
    try:
        with ZipFile(BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > 10000 or sum(i.file_size for i in infos) > 80 * 1024 * 1024:
                raise AppError('DOCUMENT_LIMIT', 'Office文件解压大小或成员数超限，未发送模型请求', 422)
            names = {i.filename for i in infos}
            if len(names) != len(infos) or any(i.flag_bits & 1 or i.filename.startswith('/') or '..' in i.filename.split('/') for i in infos):
                raise AppError('DOCUMENT_INVALID', 'Office文件包含加密、重名或非法路径，未发送模型请求', 422)
            if kind in ODF_MIME:
                if ("mimetype" not in names or "content.xml" not in names
                        or archive.getinfo("mimetype").file_size > 128
                        or archive.read("mimetype") != ODF_MIME[kind]):
                    raise AppError("DOCUMENT_FORMAT_MISMATCH", f"文件内容与 .{kind} 不符，请检查原文件。", 422)
                return
            actual = next((k for k, part in PARTS.items() if part in names), None)
            if '[Content_Types].xml' not in names or expected_part not in names:
                message = (f'文件扩展名为.{kind}，内容实际为.{actual}。请上传正确格式的文件。' if actual
                           else f'文件不是有效的 .{kind} 文档，请检查原文件。')
                raise AppError('DOCUMENT_FORMAT_MISMATCH', message, 422,
                    {'expected_format': kind, 'detected_format': actual, 'model_request_sent': False})
    except (BadZipFile, LargeZipFile, EOFError, ValueError) as exc:
        raise AppError('DOCUMENT_INVALID', f'.{kind} 文件已损坏或不是有效的 Office 文档，请检查原文件。', 422) from exc
