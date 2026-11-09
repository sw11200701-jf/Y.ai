"""Trusted, deterministic Office rendering. Arguments are data, never code.

No filesystem/URL input or output path. Byte results must be registered by the
central Artifact/effect bridge before they become downloadable user resources.
"""
from dataclasses import dataclass
from datetime import date
import hashlib
from io import BytesIO
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
from app.errors import AppError
from .document_specs import validate_content, width, lines

MIME = {'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
ASSET_SHA256 = '50cd6368657f10d1bfc5e2bcc260679f0d8c9c8f75067793cc9645fce8664d0a'
for prefix, uri in [('p', P), ('a', A), ('r', R), ('dc', 'http://purl.org/dc/elements/1.1/'),
                    ('dcterms', 'http://purl.org/dc/terms/'), ('xsi', 'http://www.w3.org/2001/XMLSchema-instance'),
                    ('cp', 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties')]:
    ET.register_namespace(prefix, uri)

@dataclass(frozen=True)
class RenderedDocument:
    content: bytes
    extension: str
    mime_type: str
    sha256: str
    size_bytes: int
    tool_id: str
    input_sha256: str
    renderer_version: str = 'general-office-v1'

def _package(files):
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
        for name, value in sorted(files.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, value)
    body = output.getvalue()
    if len(body) > 2097152:
        raise AppError('DOCUMENT_OUTPUT_LIMIT', '生成文件超过2MiB限制', 422)
    return body

def _normalize(body):
    with ZipFile(BytesIO(body)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    for name, content in files.items():
        if 'vbaproject' in name.lower() or name.lower().endswith(('.exe', '.bin', '.js')):
            raise AppError('DOCUMENT_PACKAGE_INVALID', '生成文件包含不允许的执行内容', 500)
        if name.endswith('.rels'):
            root = ET.fromstring(content)
            if any(item.get('TargetMode') == 'External' for item in root):
                raise AppError('DOCUMENT_PACKAGE_INVALID', '生成文件包含不允许的外部连接', 500)
    if 'docProps/core.xml' in files:
        core = ET.fromstring(files['docProps/core.xml'])
        for tag in ('created', 'modified'):
            node = core.find('{http://purl.org/dc/terms/}'+tag)
            if node is not None:
                node.text = '2000-01-01T00:00:00Z'  # Stable content; actual issued time belongs to server receipt.
        files['docProps/core.xml'] = _xml(core)
    return _package(files)

def _word(data):
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Inches(8.27), Inches(11.69)
    section.top_margin = section.bottom_margin = Inches(.8)
    section.left_margin = section.right_margin = Inches(.85)
    for name, size in [('Normal', 11), ('Title', 24), ('Subtitle', 11), ('Heading 1', 16)]:
        style = document.styles[name]
        style.font.name, style.font.size, style.font.color.rgb = 'Arial', Pt(size), RGBColor(0, 0, 0)
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), 'Microsoft YaHei')
        style.paragraph_format.space_after = Pt(8)
    document.add_paragraph(data.title, 'Title')
    if data.subtitle:
        document.add_paragraph(data.subtitle, 'Subtitle')
    for source in data.sections:
        if source.heading:
            document.add_paragraph(source.heading, 'Heading 1')
        for value in source.paragraphs:
            paragraph = document.add_paragraph(value)
            paragraph.paragraph_format.line_spacing = 1.2
        for value in source.bullets:
            document.add_paragraph(value, 'List Bullet')
        if source.table:
            table = document.add_table(rows=1, cols=len(source.table.headers))
            table.style = 'Table Grid'
            table.autofit = False
            for cell, value in zip(table.rows[0].cells, source.table.headers):
                cell.text = value
                for run in cell.paragraphs[0].runs:
                    run.bold = True
            repeat = OxmlElement('w:tblHeader')
            repeat.set(qn('w:val'), 'true')
            table.rows[0]._tr.get_or_add_trPr().append(repeat)
            borders = OxmlElement('w:tblBorders')
            for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
                element = OxmlElement('w:'+edge)
                for key, value in {'val': 'single', 'sz': '4', 'color': 'D9D9D9'}.items():
                    element.set(qn('w:'+key), value)
                borders.append(element)
            table._tbl.tblPr.append(borders)
            for row in source.table.rows:
                for cell, value in zip(table.add_row().cells, row):
                    cell.text = value
            document.add_paragraph()
    document.core_properties.title = data.title
    document.core_properties.author = 'Finance Agent Platform'
    output = BytesIO()
    document.save(output)
    return _normalize(output.getvalue())

def _excel(data):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.title = data.title
    workbook.properties.creator = 'Finance Agent Platform'
    from datetime import datetime
    workbook.properties.created = workbook.properties.modified = datetime(2000, 1, 1)
    for source in data.sheets:
        sheet = workbook.create_sheet(source.name)
        for cell, value in [('A1', data.title), ('A2', data.description)]:
            sheet[cell] = value
            sheet[cell].data_type = 's'
        sheet['A1'].font = Font(name='Microsoft YaHei', size=16, bold=True, color='000000')
        sheet['A1'].alignment = Alignment(wrap_text=True, vertical='top')
        sheet['A2'].font = Font(name='Microsoft YaHei', size=11, color='000000')
        sheet['A2'].alignment = Alignment(wrap_text=True, vertical='top')
        sheet.freeze_panes = 'A5'
        widths = []
        for index, column in enumerate(source.columns, 1):
            cell = sheet.cell(4, index, column.name)
            cell.data_type = 's'
            cell.fill = PatternFill('solid', fgColor='17365D')
            cell.font = Font(name='Microsoft YaHei', bold=True, color='FFFFFF', size=11)
            cell.alignment = Alignment(vertical='center', wrap_text=True)
            col_width = min(48, max(16, width(column.name)+4, max((width(str(row[index-1]))+4 for row in source.rows), default=0)))
            widths.append(col_width)
            sheet.column_dimensions[get_column_letter(index)].width = col_width
        if len(source.columns) > 1:
            sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(source.columns))
            sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(source.columns))
        sheet.row_dimensions[1].height = max(28, lines(data.title, max(8, sum(widths)*.6))*24+6)
        sheet.row_dimensions[2].height = max(24, lines(data.description, max(8, sum(widths)*.9))*16+6)
        sheet.row_dimensions[4].height = max(30, max(lines(col.name, max(8, size-2)) for col, size in zip(source.columns, widths))*16+8)
        edge = Side(style='thin', color='D9D9D9')
        for row_index, values in enumerate(source.rows, 5):
            row_lines = max(lines(str(value) if value is not None else '', max(8, size-2)) for value, size in zip(values, widths))
            if row_lines > 24:
                raise AppError('DOCUMENT_INPUT_INVALID', 'Excel单行文字超过可读行高，请拆为更多数据行', 422)
            sheet.row_dimensions[row_index].height = max(24, row_lines*16+8)
            for column_index, (column, value) in enumerate(zip(source.columns, values), 1):
                cell = sheet.cell(row_index, column_index)
                cell.value = date.fromisoformat(value) if value is not None and column.type == 'date' else value
                if column.type == 'text' and value is not None:
                    cell.data_type = 's'  # = + - @ stay literal text, never formulas/links.
                cell.font = Font(name='Microsoft YaHei', size=11, color='000000')
                cell.alignment = Alignment(wrap_text=True, vertical='top', horizontal='left' if column.type == 'text' else 'right')
                cell.border = Border(left=edge, right=edge, top=edge, bottom=edge)
                if row_index % 2:
                    cell.fill = PatternFill('solid', fgColor='F3F6FA')
                cell.number_format = {'integer': '#,##0', 'number': '#,##0.00', 'date': 'yyyy-mm-dd'}.get(column.type, 'General')
        sheet.auto_filter.ref = f'A4:{get_column_letter(len(source.columns))}{len(source.rows)+4}'
    output = BytesIO()
    workbook.save(output)
    return _normalize(output.getvalue())

def _sub(parent, namespace, tag, attributes=None, text=None):
    node = ET.SubElement(parent, '{'+namespace+'}'+tag, attributes or {})
    if text is not None:
        node.text = text
    return node

def _textbox(tree, *, identifier, name, x, y, w, h, values, size, bold=False, color='172B4D', bullets=False):
    shape = _sub(tree, P, 'sp')
    nv = _sub(shape, P, 'nvSpPr')
    _sub(nv, P, 'cNvPr', {'id': str(identifier), 'name': name})
    _sub(nv, P, 'cNvSpPr', {'txBox': '1'})
    _sub(nv, P, 'nvPr')
    properties = _sub(shape, P, 'spPr')
    transform = _sub(properties, A, 'xfrm')
    _sub(transform, A, 'off', {'x': str(x*9525), 'y': str(y*9525)})
    _sub(transform, A, 'ext', {'cx': str(w*9525), 'cy': str(h*9525)})
    _sub(properties, A, 'prstGeom', {'prst': 'rect'})
    _sub(properties, A, 'noFill')
    _sub(_sub(properties, A, 'ln'), A, 'noFill')
    body = _sub(shape, P, 'txBody')
    _sub(_sub(body, A, 'bodyPr', {'wrap': 'square', 'lIns': '0', 'rIns': '0', 'tIns': '0', 'bIns': '0'}), A, 'noAutofit')
    _sub(body, A, 'lstStyle')
    for value in values:
        paragraph = _sub(body, A, 'p')
        prop = _sub(paragraph, A, 'pPr', {'marL': '285750', 'indent': '-238125'} if bullets else {})
        _sub(_sub(prop, A, 'lnSpc'), A, 'spcPct', {'val': '115000'})
        _sub(_sub(prop, A, 'spcAft'), A, 'spcPts', {'val': '900' if bullets else '0'})
        if bullets:
            _sub(prop, A, 'buFont', {'typeface': 'Arial'})
            _sub(prop, A, 'buChar', {'char': '•'})
        else:
            _sub(prop, A, 'buNone')
        run = _sub(paragraph, A, 'r')
        style = _sub(run, A, 'rPr', {'lang': 'zh-CN', 'sz': str(size*75), 'b': '1' if bold else '0'})
        _sub(_sub(style, A, 'solidFill'), A, 'srgbClr', {'val': color})
        _sub(style, A, 'latin', {'typeface': 'Arial'})
        _sub(style, A, 'ea', {'typeface': 'Microsoft YaHei'})
        _sub(run, A, 't', text=value)
        _sub(paragraph, A, 'endParaRPr', {'lang': 'zh-CN', 'sz': str(size*75)})

def _slide_table(tree, table, y):
    frame = _sub(tree, P, 'graphicFrame')
    nv = _sub(frame, P, 'nvGraphicFramePr')
    _sub(nv, P, 'cNvPr', {'id': '4', 'name': '内容表格'})
    _sub(nv, P, 'cNvGraphicFramePr')
    _sub(nv, P, 'nvPr')
    transform = _sub(frame, P, 'xfrm')
    _sub(transform, A, 'off', {'x': str(72*9525), 'y': str(y*9525)})
    _sub(transform, A, 'ext', {'cx': str(1136*9525), 'cy': str((len(table.rows)+1)*64*9525)})
    graphic = _sub(_sub(frame, A, 'graphic'), A, 'graphicData', {'uri': A+'/table'})
    native = _sub(graphic, A, 'tbl')
    _sub(native, A, 'tblPr', {'firstRow': '1', 'bandRow': '1'})
    grid = _sub(native, A, 'tblGrid')
    for _ in table.headers:
        _sub(grid, A, 'gridCol', {'w': str(int(1136*9525/len(table.headers)))})
    for index, values in enumerate([table.headers, *table.rows]):
        row = _sub(native, A, 'tr', {'h': str(64*9525)})
        for value in values:
            cell = _sub(row, A, 'tc')
            body = _sub(cell, A, 'txBody')
            _sub(body, A, 'bodyPr')
            _sub(body, A, 'lstStyle')
            run = _sub(_sub(body, A, 'p'), A, 'r')
            style = _sub(run, A, 'rPr', {'lang': 'zh-CN', 'sz': '1650', 'b': '1' if index == 0 else '0'})
            _sub(_sub(style, A, 'solidFill'), A, 'srgbClr', {'val': 'FFFFFF' if index == 0 else '172B4D'})
            _sub(style, A, 'latin', {'typeface': 'Arial'})
            _sub(style, A, 'ea', {'typeface': 'Microsoft YaHei'})
            _sub(run, A, 't', text=value)
            properties = _sub(cell, A, 'tcPr', {'marL': '114300', 'marR': '114300', 'marT': '76200', 'marB': '76200', 'anchor': 'ctr'})
            _sub(_sub(properties, A, 'solidFill'), A, 'srgbClr', {'val': '17365D' if index == 0 else 'F3F6FA' if index%2 else 'FFFFFF'})
            for edge in ('lnL', 'lnR', 'lnT', 'lnB'):
                _sub(_sub(_sub(properties, A, edge, {'w': '9525'}), A, 'solidFill'), A, 'srgbClr', {'val': 'D9D9D9'})

def _xml(value):
    if value.tag == '{'+CT+'}Types':
        # OPC content-types uses a default namespace and unprefixed children.
        import copy
        value = copy.deepcopy(value)
        for node in value.iter():
            if not node.tag.startswith('{'+CT+'}'):
                raise ValueError('Unexpected content-types element')
            node.tag = node.tag[len(CT)+2:]
        value.set('xmlns', CT)
    return ET.tostring(value, encoding='utf-8', xml_declaration=True)

def _powerpoint(data):
    asset = Path(__file__).with_name('assets')/'document-base-v1.pptx'
    if not asset.is_file():
        raise AppError('DOCUMENT_RENDERER_UNAVAILABLE', 'PPT固定模板缺失，未生成替代文件', 503)
    if hashlib.sha256(asset.read_bytes()).hexdigest() != ASSET_SHA256:
        raise AppError('DOCUMENT_RENDERER_UNAVAILABLE', 'PPT固定模板校验失败，未装载替代模板', 503)
    with ZipFile(asset) as source:
        files = {name: source.read(name) for name in source.namelist() if not name.startswith(('ppt/slides/', 'ppt/notesSlides/'))}
        blank = ET.fromstring(source.read('ppt/slides/slide1.xml'))
    presentation = ET.fromstring(files['ppt/presentation.xml'])
    slide_ids = presentation.find('{'+P+'}sldIdLst')
    slide_ids.clear()
    rels = ET.fromstring(files['ppt/_rels/presentation.xml.rels'])
    for node in list(rels):
        if node.get('Type', '').endswith('/slide'):
            rels.remove(node)
    types = ET.fromstring(files['[Content_Types].xml'])
    for node in list(types):
        if node.get('PartName', '').startswith(('/ppt/slides/', '/ppt/notesSlides/')):
            types.remove(node)
    import copy
    for index, item in enumerate(data.slides, 1):
        slide = copy.deepcopy(blank)
        tree = slide.find('.//{'+P+'}spTree')
        _textbox(tree, identifier=2, name='页标题', x=72, y=44, w=1136, h=110, values=[item.title], size=40, bold=True)
        if item.subtitle:
            _textbox(tree, identifier=3, name='副标题', x=72, y=160, w=1136, h=64, values=[item.subtitle], size=22, color='526174')
        y = 250 if item.subtitle else 190
        if item.table:
            _slide_table(tree, item.table, y)
        else:
            _textbox(tree, identifier=4, name='正文', x=72, y=y, w=1136, h=400, values=item.bullets, size=26, bullets=True)
        _textbox(tree, identifier=5, name='页码', x=1100, y=676, w=108, h=24, values=[f'{index} / {len(data.slides)}'], size=16, color='526174')
        files[f'ppt/slides/slide{index}.xml'] = _xml(slide)
        relationships = ET.Element('{'+PKG+'}Relationships')
        _sub(relationships, PKG, 'Relationship', {'Id': 'rLayout', 'Type': R+'/slideLayout', 'Target': '../slideLayouts/slideLayout1.xml'})
        files[f'ppt/slides/_rels/slide{index}.xml.rels'] = _xml(relationships)
        _sub(slide_ids, P, 'sldId', {'id': str(255+index), '{'+R+'}id': f'rDocumentSlide{index}'})
        _sub(rels, PKG, 'Relationship', {'Id': f'rDocumentSlide{index}', 'Type': R+'/slide', 'Target': f'slides/slide{index}.xml'})
        _sub(types, CT, 'Override', {'PartName': f'/ppt/slides/slide{index}.xml', 'ContentType': 'application/vnd.openxmlformats-officedocument.presentationml.slide+xml'})
    files['ppt/presentation.xml'] = _xml(presentation)
    files['ppt/_rels/presentation.xml.rels'] = _xml(rels)
    files['[Content_Types].xml'] = _xml(types)
    core = ET.fromstring(files['docProps/core.xml'])
    title = core.find('{http://purl.org/dc/elements/1.1/}title')
    if title is None:
        title = ET.SubElement(core, '{http://purl.org/dc/elements/1.1/}title')
    title.text = data.title
    files['docProps/core.xml'] = _xml(core)
    app = ET.fromstring(files['docProps/app.xml'])
    count = app.find('{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Slides')
    if count is not None:
        count.text = str(len(data.slides))
    files['docProps/app.xml'] = _xml(app)
    return _normalize(_package(files))

def render_document(*, tool_id, arguments):
    data = validate_content(tool_id, arguments)
    renderer, extension = {'document.word.v1': (_word, 'docx'), 'document.excel.v1': (_excel, 'xlsx'),
                           'document.powerpoint.v1': (_powerpoint, 'pptx')}[tool_id]
    try:
        body = renderer(data)
    except AppError:
        raise
    except Exception:
        raise AppError('DOCUMENT_RENDER_FAILED', '文档生成失败，未登记为成功文件', 500) from None
    import json
    input_hash = hashlib.sha256(json.dumps(data.model_dump(mode='json'), ensure_ascii=False,
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    return RenderedDocument(body, extension, MIME[extension], hashlib.sha256(body).hexdigest(), len(body), tool_id, input_hash)
