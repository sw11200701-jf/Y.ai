"""Lossless row records with field labels and exact original-cell evidence.

This is a table parser, not an LLM output fallback. Every nonblank source cell
is accounted for before inference. Formulas and links remain inert text.
"""
from collections import OrderedDict
from copy import deepcopy
import hashlib
from openpyxl.utils.cell import coordinate_to_tuple,get_column_letter
from app.errors import AppError
from app.imports.documents import _segments,MAX_TEXT
from app.agents.builtin_chunker import ChunkWindow,digest
from .semantic_chunking import segment_manifest_sha256

CONTRACT='xlsx-records-v6'

def is_tabular(content):
    rows={}
    for text,locator in _segments(content,'xlsx'):
        if text.strip():
            row,col=coordinate_to_tuple(locator['cell']);key=(locator['sheet'],row)
            rows[key]=rows.get(key,0)+1
            if rows[key]>=2:return True
    return False

def windows(content,version_id):
    sheets=OrderedDict();source_cells={};total=0
    for text,locator in _segments(content,'xlsx'):
        total+=len(text)
        if total>MAX_TEXT:raise AppError('DOCUMENT_LIMIT','正文超过提取硬限，未截断',422)
        if not text.strip():continue
        row,col=coordinate_to_tuple(locator['cell']);item={'text':text,'locator':locator,'row':row,'col':col}
        sheets.setdefault(locator['sheet'],OrderedDict()).setdefault(row,[]).append(item)
        source_cells[(locator['sheet'],locator['cell'])]=text
    records=[];covered=set()
    for sheet,rows in sheets.items():
        # Only a complete, short, unique textual header row is inferred. If
        # ambiguous, use original column letters rather than invented names.
        header=None
        hints=('名称','编号','序号','编码','中文','英文','描述','简介','结果','状态','链接','说明','金额','日期','类型','字段','规则','审核','标准')
        english={'name','id','code','description','status','url','link','amount','date','type','field','rule','title'}
        for number,cells in list(rows.items())[:25]:
            if (len(cells)>=2 and sum(any(k in x['text'] for k in hints) or x['text'].strip().lower() in english for x in cells)>=2
                    and all(not x['locator']['formula_text'] and len(x['text'])<=80 and not x['text'].strip().replace('.','',1).isdigit() for x in cells)
                    and len({x['text'] for x in cells})==len(cells)
                    and any(n>number and len(v)>=2 for n,v in rows.items())):
                header=(number,{x['col']:x for x in cells});break
        for row,cells in rows.items():
            if header and row==header[0]:continue
            is_data=bool(header and row>header[0] and len(cells)>=2)
            text='';parts=[]
            def literal(value):
                nonlocal text
                text+=value
            def original(cell,start=0,end=None):
                nonlocal text
                value=cell['text'][start:end];begin=len(text);text+=value
                parts.append({'source_id':f"cell_{cell['row']}_{cell['col']}_{start}",'source_offset':start,'start':begin,'end':len(text),
                    'locator':deepcopy(cell['locator']),'text_sha256':hashlib.sha256(value.encode()).hexdigest()})
                covered.add((sheet,cell['locator']['cell']))
            literal(f'工作表：{sheet}\n记录行：{row}\n')
            for cell in cells:
                label=header[1].get(cell['col']) if is_data else None
                if label:original(label)
                else:literal(('列'+get_column_letter(cell['col'])) if is_data or len(cells)>1 else cell['locator']['cell'])
                literal('：');original(cell);literal('\n')
            # Include header columns with blank row values explicitly. Empty
            # values are structural, not claims copied from another record.
            if is_data:
                present={x['col'] for x in cells}
                for col,label in header[1].items():
                    if col not in present:original(label);literal('：（空）\n')
            locator=deepcopy(cells[0]['locator']);locator['cell']=cells[0]['locator']['cell']+(':'+cells[-1]['locator']['cell'] if len(cells)>1 else '')
            locator['formula_text']=any(c['locator']['formula_text'] for c in cells)
            # Long rows are split only into explicit continuations, keeping
            # all evidence offsets. Normal product rows stay atomic.
            for start in range(0,len(text),1100):
                value=text[start:start+1100];mapped=[]
                for part in parts:
                    a,b=max(start,part['start']),min(start+len(value),part['end'])
                    if a<b:mapped.append({**part,'source_offset':part['source_offset']+a-part['start'],'start':a-start,'end':b-start,'text_sha256':hashlib.sha256(text[a:b].encode()).hexdigest()})
                if not mapped:raise AppError('KNOWLEDGE_SEMANTIC_JOB_INVALID','表格记录缺少单元格依据',409)
                records.append({'id':f'record_{len(records)}_{start}','text':value,'locator':locator,'parts':mapped,'sheet':sheet,'row':row,'continuation':start>0,'record_kind':'metadata' if len(cells)==1 else 'record'})
        if header:
            # A header not referenced by any data row must remain an explicit
            # source record. Never drop an unused column heading silently.
            missing=[x for x in header[1].values() if (sheet,x['locator']['cell']) not in covered]
            for cell in missing:
                records.append({'id':f'record_{len(records)}_0','text':cell['text'],'locator':deepcopy(cell['locator']),
                    'parts':[{'source_id':f"cell_{cell['row']}_{cell['col']}_0",'source_offset':0,'start':0,'end':len(cell['text']),'locator':deepcopy(cell['locator']),'text_sha256':hashlib.sha256(cell['text'].encode()).hexdigest()}], 'sheet':sheet,'row':header[0],'continuation':False,'record_kind':'metadata'});covered.add((sheet,cell['locator']['cell']))
    if covered!=set(source_cells):raise AppError('KNOWLEDGE_SEMANTIC_JOB_INVALID','表格原始单元格覆盖不完整',409)
    combined=[]
    for record in records:
        if (combined and record['record_kind']=='metadata' and combined[-1]['record_kind']=='metadata'
                and not record['continuation'] and combined[-1]['sheet']==record['sheet'] and len(combined[-1]['text'])+1+len(record['text'])<=1100):
            previous=combined[-1];shift=len(previous['text'])+1;previous['text']+='\n'+record['text']
            previous['parts'] += [{**p,'start':p['start']+shift,'end':p['end']+shift} for p in record['parts']]
            previous['locator']['cell']=previous['locator']['cell'].split(':')[0]+':'+record['locator']['cell'].split(':')[-1]
        else:combined.append(record)
    records=combined
    result=[];current=[];size=0
    def flush():
        nonlocal current,size
        if not current:return
        value=ChunkWindow(document_version_id=version_id,window_id=f'window_{len(result)}',units=[{'unit_id':r['id'],'text':r['text']} for r in current]).model_dump(mode='json')
        segments=[{'id':r['id'],'text':r['text'],'locator':r['locator']} for r in current]
        result.append({'input':value,'segments':segments,'source_map':{r['id']:r['parts'] for r in current},'record_contract':CONTRACT,
            'input_sha256':digest(value),'manifest_sha256':segment_manifest_sha256(segments)})
        current=[];size=0
        if len(result)>32:raise AppError('CHUNK_JOB_LIMIT','完整表格记录超过当前任务容量；未截断或发送模型请求',422)
    for record in records:
        if current and (len(current)>=8 or size+len(record['text'])>2000 or current[0]['sheet']!=record['sheet']):flush()
        current.append(record);size+=len(record['text'])
    flush()
    if not result:raise AppError('DOCUMENT_NO_TEXT','没有可处理的表格记录',422)
    return result
