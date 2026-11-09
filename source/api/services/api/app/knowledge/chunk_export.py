"""Complete immutable document-version XLSX download; no model or writes."""
import base64
import hashlib
import json
import re
import math
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from app.errors import AppError
from app.data_access import _unrestricted
from app.imports.documents import _segments, _locator_text, MAX_TEXT, MAX_CHUNKS
from . import service as s

MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
LOCATOR_KEYS = {'kind','page','paragraph','table','row','column','sheet','cell','start_line','end_line','paragraph_in_source','formula_text'}
COORDINATES = '0-based Unicode code points; start inclusive, end exclusive; relative to one _segments original text segment (UTF-8-sig decoded text/Markdown, DOCX paragraph or table cell, PDF extracted page, XLSX cell str value); not byte/UTF-16/document-global offsets'
OFFSET_KIND = 'segment Unicode codepoints [start,end); 见说明'


def invalid(message):
    raise AppError('KNOWLEDGE_EXPORT_INVALID', message, 422)


def _string(value):
    try: length = len(value.encode('utf-16-le')) // 2
    except UnicodeEncodeError: invalid('文本含无效Unicode字符，未导出损坏文件')
    if length > 32767:
        invalid('单元格正文超过Excel 32767 UTF-16长度上限，请拆分修订；未截断或导出部分文件')
    if any(not (ch in '\t\n\r' or 0x20 <= ord(ch) <= 0xD7FF or 0xE000 <= ord(ch) <= 0xFFFD or 0x10000 <= ord(ch) <= 0x10FFFF) for ch in value):
        invalid('文本含Excel XML不支持的字符，未静默删除')
    return value


def _row(ws, values):
    row = ws.max_row + 1 if ws.max_row > 1 or ws.cell(1, 1).value is not None else 1
    for col, value in enumerate(values, 1):
        cell = ws.cell(row, col)
        if isinstance(value, str):
            cell.value = _string(value)
            cell.data_type = 's'  # = + - @ remain literal strings, never formulas.
        else:
            cell.value = value


def _authorized(document_id, version_id, context, session):
    root = s.entry('knowledge_document', document_id, context, session)
    version = s.checked_version(version_id, root, context, session)
    if version.get('status') not in ('validated', 'published') or version.get('errors'):
        invalid('仅能导出明确选定的有效草稿或发布版本')
    file = s.raw_file({**root, 'file_id':version['file_id']}, context, session)
    kb = s.library(root['knowledge_base_id'], context, session)
    if not _unrestricted(s.version_decision(version, root, context, session)):
        raise AppError('DATA_SCOPE_EXPORT_DENIED', '完整切片导出需要当前全文范围', 403)
    for kind, identifier in [('knowledge_base', kb['id']), ('knowledge_document', root['id']),
            ('knowledge_version', version_id), ('document' if file['id'].startswith('document_') else 'knowledge_file', file['id'])]:
        if not _unrestricted(s.decision(session, kind, identifier, context, 'export_detail')):
            raise AppError('DATA_SCOPE_EXPORT_DENIED', '完整导出需要来源、知识库、版本的完整明细导出权限', 403)
    try: content = base64.b64decode(file['content_base64'], validate=True)
    except (ValueError, TypeError): invalid('原始文件编码无效')
    if hashlib.sha256(content).hexdigest() != file['sha256'] or version.get('source_sha256') != file['sha256']:
        raise AppError('SOURCE_HASH_MISMATCH', '原文件实际内容与版本来源hash不一致', 409)
    return root, version, file, kb, content


def _original(span, segments, file_id):
    locator = span.get('locator')
    if not isinstance(locator, dict) or set(locator) - LOCATOR_KEYS:
        invalid('来源定位缺失或含不支持字段，不能猜测原文位置')
    start, end = span.get('offset_start'), span.get('offset_end')
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        invalid('来源offset缺失或无效，未猜测重复文字位置')
    kind = locator.get('kind')
    fields = {'page':['page'], 'paragraph':['paragraph'], 'table_cell':['table','row','column'],
              'cell':['sheet','cell'], 'lines':[]}.get(kind)
    if fields is None or any(key not in locator for key in fields): invalid('来源定位类型不受支持')
    matches = [(i,text,loc) for i,(text,loc) in enumerate(segments)
        if loc.get('kind') == kind and all(loc.get(key) == locator[key] for key in fields)]
    if len(matches) != 1: invalid('来源segment不唯一或已变化，不能猜测匹配')
    index, text, original_locator = matches[0]
    if end > len(text): invalid('来源offset超出原始segment')
    if kind == 'lines':
        precise = (text[:start].count('\n')+1, text[:end].count('\n')+(0 if text[end-1:end]=='\n' else 1))
        if (locator.get('start_line'),locator.get('end_line')) not in (precise,(original_locator['start_line'],original_locator['end_line'])):
            invalid('持久化行定位与原始segment或准确span不一致')
    ref = span.get('original_source_ref', span.get('source_ref'))
    expected = f'document:{file_id}:{_locator_text(locator)}:chars:{start}-{end}'
    if ref != expected: invalid('持久化source_ref与locator/offset不一致')
    return text[start:end], index, json.dumps(original_locator, ensure_ascii=False, sort_keys=True)


def export_chunks(*, document_id, version_id, context, session):
    try: return _export_chunks(document_id=document_id,version_id=version_id,context=context,session=session)
    except (KeyError,TypeError,ValueError,IndexError): invalid('持久化版本或来源结构无效，未导出部分内容')


def _export_chunks(*, document_id, version_id, context, session):
    root, version, file, kb, content = _authorized(document_id, version_id, context, session)
    segments = list(_segments(content, file['format']))  # extraction only; no rechunk or search
    if sum(len(text) for text,_ in segments) > MAX_TEXT: invalid('原文超过提取硬限，未截断')
    chunks = version.get('chunks')
    if not isinstance(chunks, list) or not 1 <= len(chunks) <= MAX_CHUNKS: invalid('切片数量超限或为空')
    wb = Workbook(); ws = wb.active; ws.title = '切片'
    origins = wb.create_sheet('来源明细'); notes = wb.create_sheet('说明')
    headers = ['序号','切片ID','主题','概述','切片正文','原文片段（按span顺序无分隔拼接）','启用','策略',
        '知识库ID','知识库名称','文档ID','文档名称','版本ID','版本状态','原文件名','原文件ID','原文件SHA256',
        '原始source_ref JSON','locator_json','来源span数','offset_start','offset_end','offset坐标定义','切片文本SHA256','原始文本SHA256']
    _row(ws, headers)
    _row(origins, ['切片序号','切片ID','span序号','原始source_ref','locator_json','类型','页码','段落','表格','行','列','工作表','单元格','起始行','结束行',
        'offset_start','offset_end','offset坐标定义','原始segment序号（0-based）','原始segment locator JSON','原文片段','片段SHA256'])
    span_count = 0
    for number, chunk in enumerate(chunks, 1):
        spans = chunk.get('source_spans', [chunk])
        if not isinstance(spans, list) or not 1 <= len(spans) <= 1000: invalid('来源spans为空或超限')
        originals, refs, locators = [], [], []
        for span_no, span in enumerate(spans, 1):
            original, segment_index, segment_locator = _original(span, segments, file['id'])
            originals.append(original); loc = span['locator']; locators.append(loc)
            ref = span.get('original_source_ref', span.get('source_ref')); refs.append(ref)
            _row(origins, [number,chunk['id'],span_no,ref,json.dumps(loc,ensure_ascii=False,sort_keys=True),loc['kind'],
                *[loc.get(k) for k in ('page','paragraph','table','row','column','sheet','cell','start_line','end_line')],
                span['offset_start'],span['offset_end'],OFFSET_KIND,segment_index,segment_locator,original,s.text_hash(original)])
            span_count += 1
            if span_count > 10000: invalid('来源明细超10000条，未截断')
        original = ''.join(originals)
        if s.text_hash(chunk['text']) != chunk.get('text_sha256') or s.text_hash(original) != chunk.get('original_text_sha256'):
            raise AppError('SOURCE_HASH_MISMATCH', '切片或原始文本SHA不一致，未以当前人工正文冒充原文', 409)
        _row(ws,[number,chunk['id'],chunk.get('topic',''),chunk.get('summary',''),chunk['text'],original,chunk.get('enabled',True),
            chunk.get('strategy',version.get('chunking',{}).get('strategy','')),kb['id'],kb.get('name',''),root['id'],root.get('name',''),
            version_id,version['status'],file['filename'],file['id'],file['sha256'],json.dumps(refs,ensure_ascii=False),
            json.dumps(locators,ensure_ascii=False,sort_keys=True),len(spans),spans[0]['offset_start'] if len(spans)==1 else None,
            spans[0]['offset_end'] if len(spans)==1 else None,OFFSET_KIND,chunk['text_sha256'],chunk['original_text_sha256']])
    for row in [('项目','值'),('导出范围','complete-version：当前明确版本全部切片，含停用片，不受页面分页、搜索或筛选影响'),
            ('版本ID',version_id),('文档ID',root['id']),('版本状态',version['status']),('切片行数',len(chunks)),('来源明细行数',span_count),
            ('版本内容SHA256',version['content_sha256']),('原文件SHA256',file['sha256']),('offset坐标',COORDINATES),
            ('原文定位','用原文件名/ID/SHA核对文件；按来源明细页/段落/表格行列/工作表单元格定位。页码等为提取器提供的1-based值；不存在真实页码留空。'),
            ('文本提取定义','TXT/Markdown去UTF-8 BOM但保留换行；DOCX正文段落/表格cell.text；PDF page.extract_text；XLSX str(cell.value)，公式为原始文字，不执行。不是渲染页的视觉字符坐标。'),
            ('人工修改','切片正文为该版本当前持久正文；原文片段由原文件固定segment的持久offset切取，人工修订不改变原文。两份SHA分别校验。'),
            ('多来源','来源明细每span独立定位；主表多span的offset留空，原文仅按原span顺序无分隔拼接校验，不代表连续位置。'),
            ('查看长正文','数据完整保存在单元格；Excel行高有上限，超长多行正文请选中单元格在公式栏查看完整内容。'),
            ('限制','超过Excel单元格32767 UTF-16长度/不支持XML字符明确拒绝；不静默截断。全文与明细导出权逐来源检查。'),
            ('处理','0模型请求；不重新切片；不自动发布；不写业务库；不包含原文件下载URL、凭据或宿主路径。')]: _row(notes,row)
    for sheet in wb:
        sheet.freeze_panes = 'A2'; sheet.auto_filter.ref = sheet.dimensions
        sheet.row_dimensions[1].height = 64
        for index in range(2,sheet.max_row+1): sheet.row_dimensions[index].height = 100
        for cell in sheet[1]: cell.font = Font(bold=True,color='FFFFFF'); cell.fill = PatternFill('solid',fgColor='17365D')
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width = 24
            for cell in col: cell.alignment = Alignment(vertical='top',wrap_text=True)
    for col in ('E','F'): ws.column_dimensions[col].width = 56
    origins.column_dimensions['U'].width = 56
    notes.column_dimensions['A'].width = 28; notes.column_dimensions['B'].width = 110
    for sheet in wb:
        for row in sheet:
            lines = 1
            for cell in row:
                width = max(8,sheet.column_dimensions[cell.column_letter].width-2)
                wrapped = sum(max(1,math.ceil(sum(2 if ord(ch)>255 else 1 for ch in part)/width)) for part in str(cell.value or '').split('\n'))
                lines = max(lines,wrapped)
            sheet.row_dimensions[row[0].row].height = min(409,max(26,lines*15+8))
    # Reauthorize immediately before returning download bytes, including revocation.
    _authorized(document_id,version_id,context,session)
    output = BytesIO(); wb.save(output); wb.close()
    return output.getvalue(), {'version_id':version_id,'chunk_count':len(chunks),'span_count':span_count,'scope':'complete-version'}
