"""Bounded local layout and asset registry. No network, macros or document tools."""
from copy import deepcopy
from io import BytesIO
import hashlib
import posixpath
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET
from PIL import Image
from app.errors import AppError
from .slicing_contracts import digest, fail, check_registry

MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_EXPANDED_BYTES = 80 * 1024 * 1024
MAX_TEXT = 500000
MAX_PIXELS = 40000000
# Original-byte registry/verification does not rasterize the image. Large
# originals may be registered for manual review, while OCR keeps its own cap.
MAX_METADATA_PIXELS = 100000000
IMAGE_LAYOUT_V2 = 'office-image-occurrences-v2'
IMAGE_LAYOUT_V3 = 'office-native-tables-and-image-occurrences-v3'
IMAGE_LAYOUT_V4 = 'office-native-attachment-context-v4'
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
NS.update(v='urn:schemas-microsoft-com:vml',mc='http://schemas.openxmlformats.org/markup-compatibility/2006')
R = '{' + NS['r'] + '}'
_doc_converter = None
_office_converter = None
_office_formats = frozenset()
_ocr = None


def register_local_adapters(*, doc_converter=None, office_converter=None, office_formats=(), ocr=None):
    """Trusted startup only. Adapters must be qualified independently of this parser."""
    global _doc_converter, _office_converter, _office_formats, _ocr
    if doc_converter is not None:
        if _doc_converter is not None or not callable(doc_converter): raise RuntimeError('DOC adapter registration invalid')
        _doc_converter = doc_converter
    if office_converter is not None:
        from app.imports.document_container import OFFICE_CONVERSIONS
        if _office_converter is not None or not callable(office_converter) or not office_formats or set(office_formats) - OFFICE_CONVERSIONS.keys():
            raise RuntimeError("Office adapter registration invalid")
        _office_converter = office_converter
        _office_formats = frozenset(office_formats)
    if ocr is not None:
        if _ocr is not None or not callable(ocr): raise RuntimeError('OCR adapter registration invalid')
        _ocr = ocr


def capabilities():
    from app.imports.document_container import OFFICE_CONVERSIONS, SEMANTIC_FORMATS
    from importlib.util import find_spec
    formats = [suffix for suffix in SEMANTIC_FORMATS
               if (suffix != '.doc' or _doc_converter) and (suffix != '.pdf' or find_spec('pypdf'))
               and (SEMANTIC_FORMATS[suffix] not in OFFICE_CONVERSIONS or SEMANTIC_FORMATS[suffix] in _office_formats)]
    return {'recipe_version': 'portable-slicing-1.0.0', 'formats': formats,
            'max_bytes': MAX_FILE_BYTES, 'doc_conversion': bool(_doc_converter), 'ocr': bool(_ocr),
            'layout': {'docx': 'body/header/footer paragraphs, tables and embedded image occurrences',
                       'xlsx': 'cells, hidden state and merged ranges', 'pptx': 'ordered slide text, shape geometry and image occurrences',
                       'csv': 'comma-delimited literal cells in record order',
                       'tsv': 'tab-delimited literal cells in record order',
                       'pdf': 'text lines and actual image placements/nested forms; OCR requires qualified adapter'}}


def _archive(content):
    try:
        archive = ZipFile(BytesIO(content))
        infos = archive.infolist()
        if len(infos) > 10000 or sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES or len({i.filename for i in infos}) != len(infos): fail('Office展开大小、成员数或重名超过限制', 'SLICING_PARSE_LIMIT')
        if any(i.flag_bits & 1 or i.filename.startswith('/') or '..' in i.filename.split('/') for i in infos): fail('Office压缩包包含加密或非法路径', 'SLICING_PARSE_INVALID')
        return archive
    except BadZipFile: fail('Office文件结构无效', 'SLICING_PARSE_INVALID')


def _xml(archive, path):
    try:
        raw=archive.read(path)
        if len(raw)>16*1024*1024 or b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:fail('Office XML展开或实体声明超过允许范围','SLICING_PARSE_LIMIT')
        root=None;count=0;depth=0
        for event,node in ET.iterparse(BytesIO(raw),events=('start','end')):
            if event=='start':
                count+=1;depth+=1
                if root is None:root=node
                if count>100000 or depth>64:fail('Office XML对象数或层次超过限额','SLICING_PARSE_LIMIT')
            else:depth-=1
        return root
    except (KeyError,ET.ParseError):fail('Office XML结构缺失或无效','SLICING_PARSE_INVALID')


def _relationships(archive, part):
    folder, name = posixpath.split(part)
    relpath = posixpath.join(folder, '_rels', name + '.rels')
    if relpath not in archive.namelist(): return {}
    result = {}
    for item in _xml(archive, relpath):
        if item.get('TargetMode') == 'External': continue
        raw=item.get('Target','')
        if not raw or raw.startswith('//') or ':' in raw.split('/')[0]:continue
        target=posixpath.normpath(raw.lstrip('/') if raw.startswith('/') else posixpath.join(folder,raw))
        if target=='..' or target.startswith('../'):continue
        result[item.get('Id')] = target
    return result


def _image_nodes(paragraph):
    """Choose one OOXML compatibility branch; retain actual VML occurrences."""
    result=[]
    def visit(node):
        if node is not paragraph and node.tag=='{'+NS['w']+'}p':return
        if node.tag=='{'+NS['mc']+'}AlternateContent':
            choices=list(node.findall('mc:Choice',NS))
            selected=next((c for c in choices if c.findall('.//a:blip',NS) or c.findall('.//v:imagedata',NS)),None)
            if selected is None:selected=node.find('mc:Fallback',NS)
            if selected is not None:visit(selected)
            return
        if node.tag=='{'+NS['a']+'}blip':result.append(node.get(R+'embed'))
        elif node.tag=='{'+NS['v']+'}imagedata':result.append(node.get(R+'id'))
        for child in node:visit(child)
    visit(paragraph)
    return result

def _paragraph_text(paragraph):
    parts=[]
    def visit(node):
        if node is not paragraph and node.tag=='{'+NS['w']+'}p':return
        if node.tag in ('{'+NS['w']+'}t','{'+NS['w']+'}delText'):parts.append(node.text or '')
        for child in node:visit(child)
    visit(paragraph)
    return ''.join(parts)

def parse_document(content, kind, *, file_id, version_sha256, perform_ocr=True, asset_sink=None,image_layout_version=None,collect_task_outline=False):
    if image_layout_version not in (None,IMAGE_LAYOUT_V2,IMAGE_LAYOUT_V3,IMAGE_LAYOUT_V4):fail('未知图片出现解析协议','SLICING_PARSE_INVALID')
    modern_images=image_layout_version in (IMAGE_LAYOUT_V2,IMAGE_LAYOUT_V3,IMAGE_LAYOUT_V4)
    from app.imports.document_container import OFFICE_ALIASES, OFFICE_CONVERSIONS, SEMANTIC_FORMATS, validate_container
    format_kind = SEMANTIC_FORMATS.get(kind.lower(), kind.lower())
    kind = next((suffix for suffix, name in SEMANTIC_FORMATS.items() if name == format_kind), kind)
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_FILE_BYTES: fail('资料大小超过40MiB或为空', 'SLICING_PARSE_LIMIT')
    if hashlib.sha256(content).hexdigest() != version_sha256: fail('资料实际hash与来源版本不符', 'SLICING_SOURCE_CHANGED')
    validate_container(content, format_kind)
    if format_kind in OFFICE_CONVERSIONS:
        if _office_converter is None or format_kind not in _office_formats:
            fail("本机 Office 转换组件暂未启用，待组件恢复后可处理原件。", "SLICING_FORMAT_UNAVAILABLE")
        target_kind = OFFICE_CONVERSIONS[format_kind]
        converted = _office_converter(content=content, kind=format_kind)
        converted_sha = hashlib.sha256(converted).hexdigest()
        result = parse_document(
            converted, target_kind, file_id=file_id, version_sha256=converted_sha,
            perform_ocr=perform_ocr, asset_sink=asset_sink, image_layout_version=image_layout_version,
            collect_task_outline=collect_task_outline,
        )
        result["conversion"] = {"source_format": format_kind, "target_format": target_kind, "sha256": converted_sha}
        result["conversion_sha256"] = converted_sha
        result["source_sha256"] = version_sha256
        for item in result["sources"].values():
            item["version_sha256"] = version_sha256
        result["registry_sha256"] = digest({k: result[k] for k in ("sources", "assets", "occurrences")})
        return result
    if format_kind in OFFICE_ALIASES:
        kind = "." + OFFICE_ALIASES[format_kind]
    sources, assets, occurrences, warnings = {}, {}, {}, []
    layout_exclusions=[]
    task_outline={}
    total = 0

    def source(text, locator, *, state='current', metadata=None, allow_empty=False):
        nonlocal total
        if not text.strip() and not allow_empty:
            layout_exclusions.append({'locator':locator,'reason':'empty formatting object; no text or image evidence'})
            return None
        total += len(text)
        if total > MAX_TEXT or len(sources) >= 10000: fail('正文或来源对象超过限额，未截断', 'SLICING_PARSE_LIMIT')
        identifier = 'source_' + digest({'file_id': file_id, 'version': version_sha256, 'locator': locator})[:32]
        if identifier in sources: fail('原始定位重复', 'SLICING_PARSE_INVALID')
        sources[identifier] = {'source_id': identifier, 'file_id': file_id, 'version_sha256': version_sha256,
            'text': text, 'locator': locator, 'source_state': state, 'metadata': metadata or {}}
        return identifier

    def image(content, source_id, locator):
        if len(occurrences) >= 2000: fail('图片出现超过限额', 'SLICING_PARSE_LIMIT')
        try:
            with Image.open(BytesIO(content)) as opened:
                width, height = opened.size
                if width * height > (MAX_METADATA_PIXELS if modern_images else MAX_PIXELS) or width <= 0 or height <= 0: fail('图片原始尺寸超过本地元数据核验限额', 'SLICING_PARSE_LIMIT')
                opened.verify()
        except AppError:raise
        except Exception: fail('图片格式或尺寸未通过本地检查', 'SLICING_PARSE_INVALID')
        sha = hashlib.sha256(content).hexdigest()
        asset_id = 'asset_' + sha
        assets.setdefault(asset_id, {'asset_id': asset_id, 'sha256': sha, 'width': width, 'height': height, 'size_bytes': len(content)})
        if asset_sink is not None:asset_sink(asset_id,content)
        oid = 'occurrence_' + digest({'source_id': source_id, 'locator': locator, 'asset_id': asset_id})[:32]
        if oid in occurrences: fail('图片出现定位重复', 'SLICING_PARSE_INVALID')
        occurrence = {'occurrence_id': oid, 'asset_id': asset_id, 'source_id': source_id, 'locator': locator,
                      'ocr_reviewed': False, 'ocr_lines': []}
        occurrences[oid] = occurrence
        if _ocr and perform_ocr and width*height>MAX_PIXELS:
            warnings.append({'rule':'IMAGE_EXCEEDS_OCR_PIXEL_BUDGET_REQUIRES_REVIEW','severity':'blocker','occurrence_id':oid})
        elif _ocr and perform_ocr:
            # Adapter cannot set reviewed status or model-facing URLs.
            lines = _ocr(content=content, width=width, height=height)
            if not isinstance(lines, list) or len(lines) > 10000: fail('OCR返回结构无效', 'SLICING_PARSE_INVALID')
            for line in lines:
                if type(line) is not dict or set(line) != {'text', 'region'} or not isinstance(line['text'], str): fail('OCR文字行结构无效')
                region = line['region']
                if (not isinstance(region, list) or len(region) != 4 or any(type(n) not in (int, float) for n in region)
                        or not 0 <= region[0] < region[2] <= width or not 0 <= region[1] < region[3] <= height): fail('OCR行框超出原图')
                text = sources[source_id]['text']
                prefix = '\n' if text else ''
                start = len(text) + len(prefix)
                sources[source_id]['text'] += prefix + line['text']
                occurrence['ocr_lines'].append({**deepcopy(line), 'start': start, 'end': start + len(line['text'])})
        elif perform_ocr: warnings.append({'rule': 'OCR_ADAPTER_UNAVAILABLE', 'occurrence_id': oid})

    if kind == '.doc':
        if _doc_converter is None: fail('尚未注册合格的本地DOC转换器；未调用模型', 'SLICING_DOC_ADAPTER_UNAVAILABLE')
        converted = _doc_converter(content=content)
        if not isinstance(converted, bytes) or len(converted) > MAX_FILE_BYTES: fail('DOC转换结果无效', 'SLICING_PARSE_INVALID')
        # Keep the original source identity; conversion bytes are separately pinned.
        result = parse_document(converted, '.docx', file_id=file_id, version_sha256=hashlib.sha256(converted).hexdigest(),perform_ocr=perform_ocr,asset_sink=asset_sink,image_layout_version=image_layout_version,collect_task_outline=collect_task_outline)
        result['conversion_sha256'] = result['source_sha256']
        result['source_sha256'] = version_sha256
        for item in result['sources'].values(): item['version_sha256'] = version_sha256
        if image_layout_version==IMAGE_LAYOUT_V4:result['registry_sha256']=digest({k:result[k] for k in ('sources','assets','occurrences')})
        return result
    if kind in ('.docx', '.pptx'):
        with _archive(content) as archive:
            if kind == '.docx':
                parts = ['word/document.xml'] + sorted(name for name in archive.namelist() if name.startswith(('word/header', 'word/footer')) and name.endswith('.xml'))
                for part in parts:
                    root = _xml(archive, part)
                    relationships = _relationships(archive, part)
                    task_labels={}
                    if collect_task_outline:
                        from .slicing_native_tasks import paragraph_tasks
                        task_labels=paragraph_tasks(archive,part,root,_xml)
                    parents={child:parent for parent in root.iter() for child in parent}
                    table_ids={table:index for index,table in enumerate(root.findall('.//w:tbl',NS),1)}
                    for index, paragraph in enumerate(root.findall('.//w:p', NS), 1):
                        text=_paragraph_text(paragraph) if modern_images else ''.join(t.text or '' for t in paragraph.iter() if t.tag in ('{'+NS['w']+'}t','{'+NS['w']+'}delText'))
                        locator={'kind':'paragraph','part':part,'paragraph':index}
                        ancestry=[];parent=parents.get(paragraph)
                        while parent is not None:ancestry.append(parent);parent=parents.get(parent)
                        cell=next((n for n in ancestry if n.tag=='{'+NS['w']+'}tc'),None)
                        if cell is not None:
                            row=parents[cell];table=parents[row]
                            locator={'kind':'table_cell','part':part,'table':table_ids[table],'row':list(table.findall('w:tr',NS)).index(row)+1,'column':1+sum(int(c.find('w:tcPr/w:gridSpan',NS).get('{'+NS['w']+'}val','1')) if c.find('w:tcPr/w:gridSpan',NS) is not None else 1 for c in list(row.findall('w:tc',NS))[:list(row.findall('w:tc',NS)).index(cell)]),'paragraph_in_source':list(cell.findall('.//w:p',NS)).index(paragraph)+1}
                        deleted=any(n.tag=='{'+NS['w']+'}del' for n in ancestry)
                        inserted=any(n.tag=='{'+NS['w']+'}ins' for n in ancestry)
                        hidden=paragraph.find('.//w:vanish',NS) is not None
                        image_ids=_image_nodes(paragraph) if modern_images else [n.get(R+'embed') for n in paragraph.findall('.//a:blip',NS)]
                        sid=source(text,locator,state='revoked' if deleted else 'draft' if inserted else 'current',metadata={'presentation_hidden':hidden,'revision_state':'deleted' if deleted else 'inserted' if inserted else 'normal',**({'native_reading_order':index} if image_layout_version==IMAGE_LAYOUT_V4 else {})},allow_empty=bool(image_ids))
                        if collect_task_outline and sid is not None:task_outline[sid]=task_labels[index]
                        for number, rid in enumerate(image_ids, 1):
                            path = relationships.get(rid)
                            if path and path in archive.namelist(): image(archive.read(path), sid, {**locator, 'drawing': number})
                            elif modern_images:fail('文档图片为外链或缺失内部关系，未忽略图像证据','SLICING_IMAGE_SOURCE_UNAVAILABLE')
            else:
                presentation = _xml(archive, 'ppt/presentation.xml')
                relationships = _relationships(archive, 'ppt/presentation.xml')
                slide_ids = presentation.findall('.//p:sldId', NS)
                if len(slide_ids) > 300: fail('幻灯片超过300页', 'SLICING_PARSE_LIMIT')
                for number, item in enumerate(slide_ids, 1):
                    part = relationships.get(item.get(R + 'id'))
                    if not part: fail('幻灯片顺序关系缺失', 'SLICING_PARSE_INVALID')
                    slide = _xml(archive, part)
                    rels = _relationships(archive, part)
                    tree=slide.find('p:cSld/p:spTree',NS)
                    seen_shape_ids=set()
                    def shapes(parent,groups=()):
                        if len(groups)>16:fail('幻灯片组合层次超过16层','SLICING_PARSE_LIMIT')
                        if parent is None:return
                        for shape in parent:
                            kind=shape.tag.rsplit('}',1)[-1]
                            if kind not in ('sp','pic','graphicFrame','grpSp','cxnSp'):continue
                            own=next((n for n in list(shape) if n.tag.rsplit('}',1)[-1].startswith('nv')),None)
                            identity=own.find('p:cNvPr',NS) if own is not None else None
                            native_id=identity.get('id') if identity is not None else None
                            if not native_id or native_id in seen_shape_ids:fail('原生幻灯片对象ID缺失或重复','SLICING_PARSE_INVALID')
                            seen_shape_ids.add(native_id)
                            if len(seen_shape_ids)>10000:fail('幻灯片对象超过限额','SLICING_PARSE_LIMIT')
                            if kind=='grpSp':yield from shapes(shape,(*groups,native_id))
                            else:yield shape,native_id,groups,kind
                    for shape,native_id,groups,shape_kind in shapes(tree):
                        text='\n'.join(''.join(t.text or '' for t in p.findall('.//a:t',NS)) for p in shape.findall('.//a:p',NS))
                        table_rows=None
                        if image_layout_version in (IMAGE_LAYOUT_V3,IMAGE_LAYOUT_V4):
                            table=shape.find('.//a:tbl',NS)
                            if table is not None:
                                # Keep empty cells: dropping them changes a signed amount's
                                # debit/credit column. This is native XML, never OCR.
                                table_rows=[['\n'.join(''.join(t.text or '' for t in p.findall('.//a:t',NS)) for p in cell.findall('a:txBody/a:p',NS)) for cell in row.findall('a:tc',NS)] for row in table.findall('a:tr',NS)]
                                import json
                                text='原生表格；数组逐行、单元格逐列，空字符串保留空单元格：\n'+json.dumps(table_rows,ensure_ascii=False)
                        transform=shape.find('.//a:xfrm',NS)
                        geometry=ET.tostring(transform,encoding='unicode') if transform is not None else None
                        locator={'kind':'slide_shape','slide':number,'native_shape_id':native_id,'group_ids':list(groups),'part':part}
                        sid=source(text,locator,metadata={'geometry':geometry,'object_kind':shape_kind,'presentation_hidden':slide.get('show')=='0',**({'native_table_rows':table_rows} if table_rows is not None else {})},allow_empty=bool(shape.findall('.//a:blip',NS)))
                        for image_index,blip in enumerate(shape.findall('.//a:blip',NS),1):
                            path=rels.get(blip.get(R+'embed'))
                            if path and path in archive.namelist():image(archive.read(path),sid,{**locator,'drawing':image_index})
    elif kind == '.xlsx':
        from openpyxl import load_workbook
        with _archive(content): pass
        workbook = load_workbook(BytesIO(content), data_only=False, read_only=False, keep_links=False)
        try:
            for sheet in workbook:
                if sheet.max_row * sheet.max_column > 1000000: fail('工作表范围超过限额', 'SLICING_PARSE_LIMIT')
                merged = [str(r) for r in sheet.merged_cells.ranges]
                for row in sheet:
                    for cell in row:
                        if cell.value is None: continue
                        hidden = sheet.sheet_state != 'visible' or bool(sheet.row_dimensions[cell.row].hidden) or bool(sheet.column_dimensions[cell.column_letter].hidden)
                        source(str(cell.value), {'kind': 'cell', 'sheet': sheet.title, 'cell': cell.coordinate}, state='current',
                               metadata={'presentation_hidden':hidden,'sheet_state': sheet.sheet_state, 'merged_ranges': merged, 'formula': cell.data_type == 'f'})
        finally: workbook.close()
    elif kind in ('.csv', '.tsv'):
        from openpyxl.utils.cell import get_column_letter
        from app.imports.delimited_text import cells
        for row, column, value in cells(content, format_kind):
            source(value, {'kind': 'cell', 'sheet': format_kind.upper(), 'cell': f'{get_column_letter(column)}{row}'},
                   metadata={'formula': False, 'merged_ranges': [], 'sheet_state': 'visible', 'presentation_hidden': False})
    elif kind=='.pdf':
        from .slicing_pdf import objects
        for object_kind,payload,locator,metadata in objects(content):
            if object_kind=='text':source(payload,locator,metadata=metadata)
            elif object_kind=='image':
                sid=source('',locator,metadata=metadata,allow_empty=True)
                image(payload,sid,locator)
            else:
                source('',locator,state='draft',metadata=metadata,allow_empty=True)
                warnings.append({'rule':metadata['reason'],'severity':'blocker','locator':locator})
    elif kind in ('.txt','.md'):
        from app.imports.documents import _segments
        original_kind={'.txt':'text','.md':'markdown'}[kind]
        for text,locator in _segments(content,original_kind):
            for number,line in enumerate(text.splitlines(keepends=True),1):source(line,{'kind':'lines','start_line':number,'end_line':number})
    else: fail('当前切片解析器不支持该格式', 'SLICING_FORMAT_UNAVAILABLE')
    if not sources: fail('没有可处理来源', 'SLICING_PARSE_EMPTY')
    if sum(len(s['text']) for s in sources.values()) > MAX_TEXT: fail('OCR后正文超过限额，未截断', 'SLICING_PARSE_LIMIT')
    if image_layout_version==IMAGE_LAYOUT_V4 and kind=='.pptx':
        from .slicing_native_layout import augment_ppt
        augment_ppt({'sources':sources,'assets':assets,'occurrences':occurrences},content)
    if image_layout_version==IMAGE_LAYOUT_V4 and kind=='.docx':
        for occurrence in occurrences.values():
            owner=sources[occurrence['source_id']];order=owner['metadata']['native_reading_order'];part=owner['locator']['part']
            neighbors=[(s['metadata']['native_reading_order'],sid) for sid,s in sources.items() if s['locator'].get('part')==part and s['text'].strip()]
            before=[sid for n,sid in sorted(neighbors,reverse=True) if n<order][:2]
            after=[sid for n,sid in sorted(neighbors) if n>order][:2]
            occurrence['native_context']={'part':part,'native_reading_order':order,'nearest_source_ids':list(reversed(before))+after}
    check_registry(sources, assets, occurrences)
    return {'source_sha256': version_sha256,**({'native_task_outline_contract':'native-task-role-context-v1','native_task_outline':task_outline,'native_task_outline_sha256':digest(task_outline)} if collect_task_outline else {}),**({'image_layout_version':image_layout_version} if modern_images else {}), 'sources': sources, 'assets': assets, 'occurrences': occurrences,
            'warnings': warnings, 'layout_exclusions':layout_exclusions, 'registry_sha256': digest({'sources': sources, 'assets': assets, 'occurrences': occurrences})}


def text_projection(registry, source_ids):
    """No image bytes, base64, storage paths or generated URLs enter a text model."""
    from .slicing_reading_order import ordered_identifiers
    if not set(source_ids) <= registry['sources'].keys(): fail('上下文引用越出固定来源范围', 'SLICING_SCOPE_DENIED')
    sources = [{key: source[key] for key in ('source_id', 'version_sha256', 'text', 'locator', 'source_state', 'metadata')}
               for sid, source in ((sid,registry['sources'][sid]) for sid in ordered_identifiers(registry,'sources')) if sid in source_ids]
    occurrences = [{**{key: item[key] for key in ('occurrence_id', 'asset_id', 'source_id', 'locator', 'ocr_reviewed', 'ocr_lines')},
                    **({'native_context':deepcopy(item['native_context'])} if 'native_context' in item else {}),
                    **{key:item[key] for key in ('reviewed_disposition','review_reason','human_review_sha256') if key in item}}
                   for item in (registry['occurrences'][oid] for oid in ordered_identifiers(registry,'occurrences')) if item['source_id'] in source_ids]
    return {'sources': sources, 'occurrences': occurrences}
