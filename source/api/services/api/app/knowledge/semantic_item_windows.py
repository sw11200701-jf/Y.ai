"""Source evidence windows, never a rule that rows become knowledge items."""
from collections import OrderedDict
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from openpyxl.utils.cell import coordinate_to_tuple,range_boundaries
from app.imports.documents import _segments,_locator_text,MAX_TEXT
from app.agents.builtin_chunker import ChunkWindow,digest
from app.errors import AppError
from .semantic_chunking import segment_manifest_sha256

TRANSPORT='document-semantic-items-v7'

def windows(content,kind,version_id):
    raw=[(t,l) for t,l in _segments(content,kind) if t.strip()]
    if sum(len(t) for t,l in raw)>MAX_TEXT:raise AppError('DOCUMENT_LIMIT','完整原文超限，未截断',422)
    merged={};headers={};cells={}
    if kind=='xlsx':
        # Read only merge metadata. XML does not execute workbook expressions.
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with ZipFile(BytesIO(content)) as z:
            book=ET.fromstring(z.read('xl/workbook.xml'))
            rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
            targets={r.attrib['Id']:r.attrib['Target'] for r in rels}
            for sheet in book.findall('s:sheets/s:sheet',ns):
                target=targets.get(sheet.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'),'')
                name=target.lstrip('/') if target.startswith('/') else 'xl/'+target
                if name not in z.namelist():continue
                tree=ET.fromstring(z.read(name))
                merged[sheet.attrib['name']]=[range_boundaries(m.attrib['ref']) for m in tree.findall('s:mergeCells/s:mergeCell',ns)]
        rows=OrderedDict()
        for i,(text,loc) in enumerate(raw):
            cells[(loc['sheet'],loc['cell'])]=i
            r,col=coordinate_to_tuple(loc['cell']);rows.setdefault(loc['sheet'],OrderedDict()).setdefault(r,[]).append(i)
        for sheet,rs in rows.items():
            # A short multi-column row is context, not an asserted semantic header.
            headers[sheet]=next((indices for indices in rs.values() if len(indices)>=2 and all(len(raw[i][0])<=80 for i in indices)),[])
    pieces=[];by_source={}
    for i,(text,loc) in enumerate(raw):
        start=0;by_source[i]=[]
        while start<len(text):
            end=min(start+700,len(text))
            if end<len(text):
                boundary=max(text.rfind('\n',start,end),text.rfind('。',start,end),text.rfind('；',start,end))
                if boundary>start+200:end=boundary+1
            scope=''
            if kind=='xlsx':
                from openpyxl.utils.cell import get_column_letter
                row,col=coordinate_to_tuple(loc['cell'])
                ranges=[get_column_letter(a)+str(b)+':'+get_column_letter(x)+str(y) for a,b,x,y in merged.get(loc['sheet'],[]) if (col,row)==(a,b)]
                if ranges:scope='；实际合并范围='+','.join(ranges)
            unit={'unit_id':f'src_{i}_{start}','text':'['+_locator_text(loc)+scope+']\n'+text[start:end]}
            entry={'unit':unit,'original_text':text[start:end],'locator':deepcopy(loc),'source_offset':start,'source_index':i}
            pieces.append(entry);by_source[i].append(entry);start=end
    result=[];current=[];context=[]
    def contexts(entry):
        loc=entry['locator']
        if kind!='xlsx':return []
        row,col=coordinate_to_tuple(loc['cell']);indices=list(headers.get(loc['sheet'],[]))
        for a,b,x,y in merged.get(loc['sheet'],[]):
            if b<=row<=y:
                # The merge anchor is explicit scope for the rows in its range.
                from openpyxl.utils.cell import get_column_letter
                idx=cells.get((loc['sheet'],get_column_letter(a)+str(b)))
                if idx is not None:indices.append(idx)
        return [p for i in dict.fromkeys(indices) for p in by_source[i] if i!=entry['source_index']]
    def flush():
        nonlocal current,context
        if not current:return
        ids={p['unit']['unit_id'] for p in current}
        selected=[{**p,'unit':{**p['unit'],'unit_id':'ctx_'+p['unit']['unit_id']}} for p in context if p['unit']['unit_id'] not in ids]+current
        value=ChunkWindow(document_version_id=version_id,window_id=f'window_{len(result)}',units=[p['unit'] for p in selected]).model_dump(mode='json')
        segments=[{'id':p['unit']['unit_id'],'text':p['unit']['text'],'locator':p['locator']} for p in selected]
        result.append({'input':value,'segments':segments,'evidence_map':{p['unit']['unit_id']:{'locator':p['locator'],'source_offset':p['source_offset'],'original_text':p['original_text']} for p in selected},
            'input_sha256':digest(value),'manifest_sha256':segment_manifest_sha256(segments)})
        current=[];context=[]
        if len(result)>32:raise AppError('CHUNK_JOB_LIMIT','完整语义资料超过32批容量，请分章节提交；未截断或发送模型请求',422)
    for entry in pieces:
        added=contexts(entry)
        combined={p['unit']['unit_id']:p for p in context+added}
        prospective=current+[entry];ids={p['unit']['unit_id'] for p in prospective}
        ctx=[p for k,p in combined.items() if k not in ids]
        if current and (len(prospective)+len(ctx)>16 or sum(len(p['unit']['text']) for p in prospective+ctx)>2000
                or entry['locator'].get('sheet')!=current[0]['locator'].get('sheet')):
            flush();prospective=[entry];ctx=added
        if len(prospective)+len(ctx)>16 or sum(len(p['unit']['text']) for p in prospective+ctx)>2000:
            raise AppError('CHUNK_JOB_LIMIT','来源及其完整表格上下文超过单批容量，未丢弃上下文',422)
        current=prospective;context=ctx
    flush()
    if not result:raise AppError('DOCUMENT_NO_TEXT','没有可整理的正文',422)
    return result

def plan_budget(window_ids,include_relations=False,include_content_repair=False,single_window_overview=False,**kwargs):
    from app.agents.document_overview import plan_document
    base=plan_document(window_ids,**kwargs)
    if single_window_overview:
        if len(window_ids)!=1:raise AppError('CHUNK_JOB_LIMIT','整文窗口概述仅适用于单个完整来源窗口',422)
        base.update(summary_steps=[],final_reference_id='window:'+window_ids[0],max_model_calls=1,reserved_total_tokens=kwargs['context_budget'])
    audits=[{'summary_id':'audit_'+wid,'reference_id':'audit:'+wid,'input_reference_ids':['window:'+wid],'audit_window_id':wid} for wid in window_ids]
    repairs=[]
    if include_content_repair:
        for spec in audits:spec['repair_follows']=True
        repairs=[{'summary_id':'repair_'+wid,'reference_id':'repair:'+wid,'input_reference_ids':[],'repair_window_id':wid} for wid in window_ids]
        repairs += [{'summary_id':'reaudit_'+wid,'reference_id':'reaudit:'+wid,'input_reference_ids':[],'audit_window_id':wid} for wid in window_ids]
    relations=[{'summary_id':'relations_full','reference_id':'relations:full','input_reference_ids':[],'relation_catalog':True}] if include_relations else []
    calls=base['max_model_calls']+len(audits)+len(relations)+len(repairs)
    if calls>kwargs['max_model_calls'] or calls*kwargs['context_budget']>kwargs['max_total_tokens']:
        raise AppError('CHUNK_JOB_BUDGET_EXCEEDED','完整知识生成、独立内容复核与整文归纳超过预算，未发送请求',422)
    base.update(summary_steps=audits+repairs+relations+base['summary_steps'],max_model_calls=calls,reserved_total_tokens=calls*kwargs['context_budget'])
    return base

def bind(window,spec,value):
    import hashlib
    units={u['unit_id']:u['text'] for u in window['units']}
    result=[]
    for item in value['items']:
        spans=[];originals=[]
        for uid in item['evidence_ids']:
            source=spec['evidence_map'][uid];text=source['original_text'];start=source['source_offset']
            spans.append({'locator':deepcopy(source['locator']),'offset_start':start,'offset_end':start+len(text),'text_sha256':hashlib.sha256(text.encode()).hexdigest()})
            originals.append(text)
        result.append({'topic':item['topic'],'summary':item['summary'],'text':item['content'],'unit_id':item['id'],
            **({k:item[k] for k in ('knowledge_layer','task_key','parent_task_key')} if 'knowledge_layer' in item else {}),
            'semantic_local_id':window['window_id']+':'+item['id'],'semantic_related_ids':[window['window_id']+':'+rid for rid in item['related_ids']],
            'locator':deepcopy(spans[0]['locator']),'offset_start':0,'offset_end':len(item['content']),
            'source_spans':spans,'evidence_text':''.join(originals),'transport_version':TRANSPORT})
    return {'chunks':result}
