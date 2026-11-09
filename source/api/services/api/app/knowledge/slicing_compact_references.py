"""Compact model IDs become exact native-source quotations, not inferred facts.

The body is one claim against all selected full sources. Its correctness is
decided by the actual independent audit, never by this mechanical projection.
"""
from copy import deepcopy
from .slicing_contracts import digest,fail

CONTRACT='full-native-source-references-v1'
DERIVED=('evidence','claims','occurrence_ids')

def compact_input(plan,value):
    if plan.get('native_reference_contract')!=CONTRACT:return value
    def visit(v):
        if isinstance(v,list):return [visit(x) for x in v]
        if not isinstance(v,dict):return deepcopy(v)
        item=all(k in v for k in ('knowledge_id','content','source_ids','knowledge_layer'))
        return {k:visit(x) for k,x in v.items() if not item or k not in DERIVED}
    return visit(value)

def inflate_output(plan,stage,data,value):
    if plan.get('native_reference_contract')!=CONTRACT or not isinstance(value,dict):return value
    if stage=='relation' and data.get('relation_batch_contract')=='complete-pair-judgments-in-batches-v1':
        single_data={k:v for k,v in data.items() if k!='relation_batch_contract'}
        return {'relations':[inflate_output(plan,stage,single_data,row) for row in value['relations']]}
    if stage=='relation':
        result=deepcopy(value);relation=result['relation'];ids=relation.pop('source_ids')
        visible={source['source_id'] for source in data.get('sources',[])};evidence=[]
        for sid in ids:
            if sid not in visible or sid not in plan['registry']['sources']:fail('关系依据不属于当前完整来源','SLICING_RESULT_INVALID')
            text=plan['registry']['sources'][sid]['text']
            if not text.strip():fail('空文字图片不能代作关系文字依据','SLICING_RESULT_INVALID')
            evidence.append({'source_id':sid,'quote':text,'role':'完整原生来源；关系结论由实际逐对模型审核'})
        relation['evidence']=evidence
        return result
    if (stage=='audit' and plan.get('audit_verdict_contract')=='independent-unit-verdicts-v1') or (stage=='finalaudit' and data.get('audit_facet')=='units'):
        verdicts=value['verdicts'];expected=set(data['audit_item_ids'] if stage=='finalaudit' else [i['knowledge_id'] for i in data['candidate']['items']])
        if len(verdicts)!=len(expected) or {v['knowledge_id'] for v in verdicts}!=expected:fail('最终逐条审核须完整列出本批知识ID，不得省略或增加','SLICING_RESULT_INVALID')
        by_id={v['knowledge_id']:v for v in verdicts};items=deepcopy(data['candidate']['items'])
        for item in items:item.update(status=by_id[item['knowledge_id']]['status'],semantic_checks=deepcopy(by_id[item['knowledge_id']]['semantic_checks']))
        issues=deepcopy(value['issues'])
        if plan.get('audit_issue_retention_contract')=='blocking-issues-require-repair-v1':
            # An audit cannot erase an unresolved problem merely by omitting
            # it or labelling its copy resolved. Only repair owns that change.
            seen={digest(issue) for issue in issues}
            for issue in data['candidate']['issues']:
                if issue['severity']=='blocker' and issue['status']=='open' and digest(issue) not in seen:
                    issues.append(deepcopy(issue));seen.add(digest(issue))
        return {'complete':value['complete'],'items':items,'coverage':deepcopy(data['candidate']['coverage']),'issues':issues}
    if not isinstance(value.get('items'),list):return value
    result=deepcopy(value);registry=plan['registry'];visible={s['source_id'] for s in data.get('sources',[])}
    invalid=[]
    for item in value['items']:
        for sid in item['source_ids']:
            if sid not in visible or sid not in registry['sources']:
                invalid.append({'knowledge_id':item['knowledge_id'],'source_id':sid,'reason':'本次未装载的来源，不可作为正文依据；必要时先请求完整上下文。'})
            elif not registry['sources'][sid]['text'].strip():
                invalid.append({'knowledge_id':item['knowledge_id'],'source_id':sid,'reason':'这是无文字原图的位置对象，不是正文来源。原图只能通过attachment_occurrence_ids挂到具有真实原生文字依据的业务知识；禁止生成“本页有图/未OCR”占位条目。'})
    if invalid:fail('正文source_ids包含未装载或无文字的图片对象，请逐项纠正',details={'invalid_text_source_references':invalid})
    old_items=list((data.get('candidate') or {}).get('items',[])) if isinstance(data.get('candidate'),dict) else []
    for group in data.get('groups',[]):old_items.extend(group.get('items',[]))
    if stage in ('slice','repair') and plan.get('group_identity_contract')=='scoped-generated-knowledge-identities-v1':
        existing={item['knowledge_id'] for item in old_items}
        for item in result['items']:
            if item['knowledge_id'] not in existing:
                item['knowledge_id']='candidate_g'+str(data['group_index'])+'_'+digest(item['knowledge_id'])[:20]
    by_id={i['knowledge_id']:i for i in old_items};mapping={row['old_id']:row['new_id'] for row in result.get('id_map',[])}
    for item in result['items']:
        from .slicing_native_layout import with_complete_tables
        complete_item=with_complete_tables(plan,item);item['content']=complete_item['content']
        old=by_id.get(item['knowledge_id'])
        unchanged=old is not None and compact_input(plan,{k:v for k,v in old.items() if k not in ('status','semantic_checks')})=={k:v for k,v in item.items() if k not in ('status','semantic_checks')}
        if unchanged:
            for field in DERIVED:item[field]=deepcopy(old[field])
            continue
        evidence=[]
        for sid in item['source_ids']:
            if sid not in visible or sid not in registry['sources']:fail('知识来源不属于本次完整装载的原件','SLICING_RESULT_INVALID')
            text=registry['sources'][sid]['text']
            if not text.strip():fail('无文字原图只能作为附件，不能代作正文文字证据','SLICING_RESULT_INVALID')
            evidence.append({'source_id':sid,'quote':text,'role':'完整原生文字来源；语义支持须独立审核'})
        item['evidence']=evidence;item['occurrence_ids']=[]
        indices=list(range(len(evidence)));texts=[]
        if stage=='merge':
            members=[i for i in old_items if mapping.get(i['knowledge_id'])==item['knowledge_id']]
            # Retain every actual original claim through a real model-proposed
            # consolidation. Indexes refer to the complete native-source union.
            for member in members:
                for claim in member['claims']:
                    if claim['text'] not in texts:texts.append(claim['text'])
        if item['content'] not in texts:texts.append(item['content'])
        item['claims']=[{'text':text,'evidence_indices':indices} for text in texts]
    return result
