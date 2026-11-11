"""Three-column semantic customer workbook; reuse every proven provenance export gate."""
from io import BytesIO
import math,re,json
from openpyxl import Workbook
from openpyxl.styles import Alignment,Font,PatternFill
from .chunk_export import export_chunks,_authorized,_row,invalid
from .customer_chunks import knowledge_content,knowledge_id,source_paragraphs,semantic
from . import service as s

CONTRACT='customer-semantic-three-columns-v1'
HEADERS=['知识ID','知识内容']

def _semantic_location(chunk,filename):
    # Multi-source knowledge can start with a table header. Showing only the
    # first locator hides the actual criteria and merged conditions. Every
    # span has already passed export_chunks' exact source/hash/ACL checks.
    locations=[s.source_label(span['locator']) for span in chunk.get('source_spans') or []]
    if not locations:locations=[s.source_label(chunk['locator'])]
    return filename+'｜'+'\n'.join(dict.fromkeys(locations))

def _portable_location(value,filename):
    locations=[]
    for line in value.splitlines():
        try:entry=json.loads(line);loc=entry['locator']
        except (ValueError,TypeError,KeyError):invalid('已校验知识附件的来源定位结构无效')
        kind=loc.get('kind');parts=[filename]
        if kind=='slide_shape':parts += ['第'+str(loc['slide'])+'页（幻灯片）','对象 cNvPr ID='+str(loc['native_shape_id'])]
        elif kind=='cell':parts += ['工作表“'+str(loc['sheet'])+'”','单元格 '+str(loc['cell'])]
        elif kind=='paragraph':parts += ['原文段落 '+str(loc['paragraph'])]
        elif kind=='table_cell':parts += ['表格 '+str(loc['table']),'第'+str(loc['row'])+'行、第'+str(loc['column'])+'列']
        elif 'page' in loc:parts += ['第'+str(loc['page'])+'页',json.dumps({k:v for k,v in loc.items() if k not in ('kind','page')},ensure_ascii=False)]
        else:parts += [json.dumps(loc,ensure_ascii=False)]
        if loc.get('part') and kind in ('paragraph','table_cell'):parts.append('部件 '+loc['part'])
        if 'url_slot' in entry:parts.append('附件顺序 '+str(entry['url_slot']))
        if loc.get('drawing'):parts.append('图片出现 '+str(loc['drawing']))
        locations.append('｜'.join(parts))
    return '\n'.join(dict.fromkeys(locations))

def _portable_rows(version,*,context,session,filename=None):
    """Use the already committed, hash-checked artifact; never model URLs."""
    from app.db import get_resource
    from .slicing_finalizer import checked_csv
    from .slicing_csv import decode_csv
    run_id=version.get('portable_execution_run_id')
    if not run_id:invalid('完整知识版本缺少原始运行凭据')
    run=get_resource(session,'run',run_id,context)
    ids=run.get('knowledge_csv_artifact_ids') or []
    if not 1<=len(ids)<=8:invalid('完整知识版本缺少唯一可核对附件')
    matches=[]
    for aid in ids:
        meta=get_resource(session,'artifact',aid,context)
        if meta.get('version_id')==version['id']:matches.append(aid)
    if len(matches)!=1:invalid('完整知识版本附件不唯一')
    meta,content=checked_csv(matches[0],context=context,session=session)
    rows=decode_csv(content)
    for item in meta.get('escaped_cells',[]):
        number,key,original=item['row'],item['column'],item['original']
        if not 1<=number<=len(rows) or key not in rows[number-1] or rows[number-1][key]!="'"+original:invalid('安全CSV副本的转义凭据不一致')
        rows[number-1][key]=original
    if [(r[HEADERS[0]],r[HEADERS[1]]) for r in rows]!=[(c['id'],c['text']) for c in version['chunks']]:invalid('知识附件与完整版本正文不一致')
    # Keep each approved semantic knowledge body and its exact attachment URLs.
    return [[r[k] for k in HEADERS] for r in rows],{'version_id':version['id'],'chunk_count':len(rows),'span_count':sum(len(c['evidence']) for c in version['chunks']),'scope':'complete-version'}

def export_customer(*,document_id,version_id,context,session,columns=None):
    from .customer_export_columns import select_columns,COLUMNS,metadata_values,SELECTION_CONTRACT
    custom=columns is not None;selected=select_columns(columns)
    root,version,file,_,_=_authorized(document_id,version_id,context,session)
    pinned=s.digest(version)
    # Retain source hashes, exact span locators, all-row coverage, current ACL,
    # formula safety and Excel limits; no parallel weaker export path.
    portable=version.get('chunking',{}).get('recipe_version')=='portable-slicing-1.0.0'
    portable_rows=None
    if version.get('human_revision'):
        from .curation import verify
        verify(version,context=context,session=session)
        metadata={'version_id':version_id,'chunk_count':len(version['chunks']),'span_count':sum(len(c.get('evidence') or c.get('source_spans') or [c]) for c in version['chunks']),'scope':'complete-version'}
    elif portable:portable_rows,metadata=_portable_rows(version,context=context,session=session,filename=file['filename'])
    else:_,metadata=export_chunks(document_id=document_id,version_id=version_id,context=context,session=session)
    fresh=_authorized(document_id,version_id,context,session)
    if s.digest(fresh[1])!=pinned:invalid('导出期间版本已变化')
    chunks=version['chunks']
    wb=Workbook();ws=wb.active;ws.title='知识切片';_row(ws,[COLUMNS[key][0] for key in selected])
    base_rows=portable_rows if portable else [[knowledge_id(chunk,version_id,i),knowledge_content(chunk)]
                                             for i,chunk in enumerate(chunks,1)]
    if len(base_rows)!=len(chunks):invalid('完整版本的切片与导出行数不一致')
    originals=source_paragraphs(chunks,lambda:file)
    for number,(chunk,values,original) in enumerate(zip(chunks,base_rows,originals),1):
        record=dict(zip(('knowledge_id','knowledge_content'),values))
        record.update(sequence=number,semantic=semantic({**chunk,'original_text':original}),original_paragraph=original)
        if set(selected)-record.keys():record.update(metadata_values(chunk,version,file))
        _row(ws,[record[key] for key in selected])
    ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions;ws.sheet_view.showGridLines=False
    from openpyxl.utils import get_column_letter
    widths={get_column_letter(i):COLUMNS[key][1] for i,key in enumerate(selected,1)}
    for col,width in widths.items():ws.column_dimensions[col].width=width
    for cell in ws[1]:cell.font=Font(name='Microsoft YaHei',size=11,bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='25476A');cell.alignment=Alignment(horizontal='center',vertical='center')
    ws.row_dimensions[1].height=28
    for row in ws.iter_rows(min_row=2):
        lines=1
        for cell in row:
            cell.font=Font(name='Microsoft YaHei',size=11);cell.alignment=Alignment(vertical='top',wrap_text=True)
            width=max(8,widths[cell.column_letter]-2)
            lines=max(lines,sum(max(1,math.ceil(sum(2 if ord(ch)>255 else 1 for ch in part)/width)) for part in str(cell.value or '').split('\n')))
        ws.row_dimensions[row[0].row].height=min(409,max(36,lines*16+10))
    fresh=_authorized(document_id,version_id,context,session)
    if s.digest(fresh[1])!=pinned:invalid('导出期间版本已变化')
    output=BytesIO();wb.save(output);wb.close()
    name=re.sub(r'[\\/\x00-\x1f\x7f]','_',root.get('name') or file['filename'])
    name=re.sub(r'\.[A-Za-z0-9]{1,8}$','',name)[:160]+'_知识切片.xlsx'
    return output.getvalue(),{**metadata,'format':SELECTION_CONTRACT if custom else CONTRACT,'filename':name,'columns':list(selected)}
