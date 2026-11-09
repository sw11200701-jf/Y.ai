"""Bounded semantic terminal protocol, derived exclusively from step receipts.

No source or item is summarized or clipped to obtain a fit. Every current item
pair is assessed for consolidation and candidate relations. Final item judgments
and source-coverage judgments are separate model calls with a common candidate
hash. Their coverage manifest is persisted with the ordinary completion proof.
"""
from copy import deepcopy
from itertools import combinations
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry
from .slicing_contracts import SCHEMAS,DEFAULTS,digest,encoded,fail,validate,check_items,check_result,check_evidence,stable_ids,unique,item_occurrences

CONTRACT='bounded-terminal-v2'
PROMPTS={
 'merge':'分片协议 bounded-terminal-v2：本批是完整候选与其完整来源。评估本批所有无序知识对，包括跨原始组的对。只有同一task_key、knowledge_layer、parent_task_key且检索目的相同才可归并。未归并的条目逐字段原样保留且ID不变；真正归并保留所有原证据、claims文本、分支及来源/出现的并集。每个输入ID恰好一条id_map。不能因分页或本批暂未见其他条目而宣称全库完成。',
 'candidates':'分片协议 bounded-terminal-v2：本批是完整知识与完整相关来源；评估本批所有无序知识对。assessed_ids必须恰为本批全体ID，pairs只引用本批ID。跨批组合由软件遍历覆盖，不能据本批没有关系宣称全库没有关系；不要为凑数提出关系。',
 'units':'分片协议 bounded-terminal-v2 最终知识审核：全部归并与逐对关系审核已经完成。global_candidate_sha256绑定整个不可变候选，本批仅审核audit_item_ids指定的完整知识；context_items仅为完整父片上下文。每条逐结论核对所有完整来源，不信候选自报true。只允许改变status和semantic_checks，事实、ID、claims、证据和分支不得修改，发现事实问题输出blocker。coverage仅描述本分片，全文覆盖由后续来源审核单独负责。原先未解决的blocker不会因在本分片省略而关闭。',
 'coverage':'分片协议 bounded-terminal-v2 最终来源覆盖审核：全部归并、关系审核、知识结论审核已经完成。当前sources/occurrences是本批完整原始对象，items是所有引用本批对象的完整最终知识，不能省略任何引用。本次只判断这些来源/出现是否确被知识完整表达，是否遗漏条件、分支、流程总览、图片或当前有效规则；知识的其他来源不在本批，不得假称看过。planning_obligations为原始任务要求。只输出complete、coverage、issues，不输出或修改items。每个本批source/occurrence恰一条coverage；represented须列全部实际引用ID；有疑问pending_review并给blocker，排除须具体理由。',
}


def enabled(plan):
    marker=plan.get('terminal_contract')
    if marker and marker!=CONTRACT:fail('未知末端分片协议','SLICING_TERMINAL_CONTRACT_INVALID')
    return marker==CONTRACT


def coverage_schema():
    fields=SCHEMAS['result']['properties']
    return {'type':'object','properties':{k:deepcopy(fields[k]) for k in ('complete','coverage','issues')},
            'required':['complete','coverage','issues'],'additionalProperties':False}


def response_schema(plan,stage,data,*,model_reply=False):
    if stage=='relation' and data.get('relation_batch_contract')=='complete-pair-judgments-in-batches-v1':
        single=response_schema(plan,stage,{k:v for k,v in data.items() if k!='relation_batch_contract'},model_reply=model_reply)
        return {'type':'object','properties':{'relations':{'type':'array','minItems':1,'maxItems':16,'items':single}},'required':['relations'],'additionalProperties':False}
    if model_reply and ((stage=='audit' and plan.get('audit_verdict_contract')=='independent-unit-verdicts-v1') or (plan.get('native_reference_contract')=='full-native-source-references-v1' and stage=='finalaudit' and data.get('audit_facet')=='units')):
        item=SCHEMAS['result']['properties']['items']['items']['properties']
        verdict={'type':'object','properties':{key:deepcopy(item[key]) for key in ('knowledge_id','status','semantic_checks')},
            'required':['knowledge_id','status','semantic_checks'],'additionalProperties':False}
        return {'type':'object','properties':{'complete':{'type':'boolean'},'verdicts':{'type':'array','items':verdict},
            'issues':deepcopy(SCHEMAS['result']['properties']['issues'])},'required':['complete','verdicts','issues'],'additionalProperties':False}
    schema=coverage_schema() if stage=='finalaudit' and data.get('audit_facet')=='coverage' else deepcopy(SCHEMAS[DEFAULTS['stages'][stage]['schema_key']])
    if plan.get('source_alias_contract')=='source-and-knowledge-identity-aliases-v2' and stage=='candidates':
        schema['properties']['pairs']['items']['properties']['candidate_type']['enum']=['prerequisite','next_step','alternative','exception','related']
    if plan.get('source_alias_contract')=='source-and-knowledge-identity-aliases-v2' and stage=='relation':
        schema['properties']['relation']['properties']['type']['enum']=['prerequisite','next_step','alternative','exception','related']
        if model_reply and plan.get('native_reference_contract')=='full-native-source-references-v1':
            relation=schema['properties']['relation']
            relation['properties'].pop('evidence');relation['required'].remove('evidence')
            relation['properties']['source_ids']={'type':'array','items':{'type':'string'},'uniqueItems':True}
            relation['required'].append('source_ids')
    if plan.get('attachment_contract')=='original-image-attachments-v1' and 'items' in schema.get('properties',{}):
        item=schema['properties']['items']['items']
        if plan.get('native_knowledge_kind_contract')=='bounded-document-kind-v1':
            item['properties']['kind']={'type':'string','minLength':1,'maxLength':100}
        item['properties']['attachment_occurrence_ids']={'type':'array','items':{'type':'string'},'uniqueItems':True}
        item['required'].append('attachment_occurrence_ids')
        if model_reply and plan.get('native_reference_contract')=='full-native-source-references-v1':
            for name in ('evidence','claims','occurrence_ids'):
                item['properties'].pop(name,None)
                if name in item['required']:item['required'].remove(name)
    return schema


def request_contract(plan,stage,data,spec):
    """Only a pinned private-plan marker selects this trusted prompt/schema."""
    result=deepcopy(spec);schema=None
    if not enabled(plan):return result,schema
    facet=data.get('audit_facet')
    key=facet if stage=='finalaudit' else stage
    if key in PROMPTS:result['system_prompt']+='\n\n'+PROMPTS[key]
    if stage=='relation' and data.get('relation_batch_contract')=='complete-pair-judgments-in-batches-v1':
        result['system_prompt']+='\n本次pairs为有限批次，必须对每个pair分别完整审核并在relations中恰返回一项decision、reason、relation。逐项保持原端点、方向、类型，逐项选择真正支持该对关系的source_ids；不能用其他对的来源。每对独立判断，禁止漏对、合并决定、以批次整体通过代替逐对判断。'
    if stage=='finalaudit' and facet=='coverage':schema=coverage_schema()
    if plan.get('attachment_contract')=='original-image-attachments-v1':schema=response_schema(plan,stage,data,model_reply=True)
    return result,schema


def validate_response(plan,stage,data,value,*,model_reply=False):
    enabled(plan)
    if plan.get('attachment_contract')=='original-image-attachments-v1':
        try:
            encoded(value);Draft202012Validator(response_schema(plan,stage,data,model_reply=model_reply),registry=Registry()).validate(value)
        except ValidationError:
            if plan.get('schema_diagnostic_contract')=='schema-path-feedback-v1':
                errors=list(Draft202012Validator(response_schema(plan,stage,data,model_reply=model_reply),registry=Registry()).iter_errors(value))
                safe=[]
                for error in errors[:64]:
                    row={'path':list(error.absolute_path),'rule':error.validator}
                    if error.validator in ('enum','type','required','additionalProperties','minItems','maxItems','minLength','maxLength'):row['expected']=deepcopy(error.validator_value)
                    safe.append(row)
                fail('阶段字段不符合固定Schema；按具体path及expected修正，不要重写无关业务事实',details={'schema_issues':safe,'total_schema_issues':len(errors)})
            fail('阶段回复不符合已固定的原图附件协议')
        except Exception:fail('阶段回复不符合已固定的原图附件协议')
        return deepcopy(value)
    if stage=='finalaudit' and data.get('audit_facet')=='coverage':
        try:
            encoded(value);Draft202012Validator(coverage_schema(),registry=Registry()).validate(value)
        except Exception:fail('来源覆盖审核回复不符合固定分片协议')
        return deepcopy(value)
    return validate(stage,value)


def identity(item):return {key:value for key,value in item.items() if key not in ('status','semantic_checks')}
def pair_key(left,right):return tuple(sorted((digest(left),digest(right))))
def item_pairs(items):return {pair_key(a,b) for a,b in combinations(items,2)}

MERGE_PREFILTER='same-task-identity-prefilter-v1'
def merge_identity(item):return tuple(item.get(key) for key in ('task_key','knowledge_layer','parent_task_key'))
def incompatible_pairs(items):
    return {pair_key(a,b) for a,b in combinations(items,2) if merge_identity(a)!=merge_identity(b)}


def item_witness(item):return {'sha256':digest(item),'identity_sha256':digest(identity(item))}


def validate_merge_lineage(result,manifest):
    """Replay every identity transition, not only comparisons of final items."""
    initial=manifest.get('merge_initial_items');final_ids=manifest.get('merge_final_working_ids')
    ids={item['knowledge_id'] for item in result['items']}
    def invalid():fail('原始知识到最终知识的归并身份转换凭据不完整','SLICING_FINALAUDIT_REQUIRED')
    def witnesses(value):
        return isinstance(value,dict) and value and all(isinstance(key,str) and isinstance(row,dict) and set(row)=={'sha256','identity_sha256'}
            and all(isinstance(h,str) and len(h)==64 and all(c in '0123456789abcdef' for c in h) for h in row.values()) for key,row in value.items())
    if not witnesses(initial) or set(initial)!=set(manifest['original_ids']) or not isinstance(final_ids,dict) or set(final_ids)!=ids:invalid()
    active=deepcopy(initial);origins={key:key for key in initial}
    for proof in manifest['merge']:
        before,after=proof.get('before_items'),proof.get('after_items')
        if not witnesses(before) or not witnesses(after) or not set(before)<=set(active) or any(active[key]!=value for key,value in before.items()):invalid()
        if len(proof['item_hashes'])!=len(before) or set(proof['item_hashes'])!={value['sha256'] for value in before.values()}:invalid()
        mappings=proof['id_map']
        if unique([row['old_id'] for row in mappings],'归并转换输入ID')!=set(before) or {row['new_id'] for row in mappings}!=set(after) or any(not row['reason'].strip() for row in mappings):invalid()
        if set(after)&(set(active)-set(before)):invalid()
        mapping={row['old_id']:row['new_id'] for row in mappings}
        for new in after:
            members=[old for old,target in mapping.items() if target==new]
            if len(members)==1 and (members[0]!=new or before[members[0]]!=after[new]):invalid()
        for old,target in list(origins.items()):
            if target in mapping:origins[old]=mapping[target]
        for key in before:del active[key]
        active.update(after)
    if len(set(final_ids.values()))!=len(ids) or set(final_ids.values())!=set(active):invalid()
    stable_by_working={working:stable for stable,working in final_ids.items()}
    if {old:stable_by_working[target] for old,target in origins.items()}!=manifest['id_map']:invalid()
    by_id={item['knowledge_id']:item for item in result['items']}
    for stable,working in final_ids.items():
        expected=digest({**identity(by_id[stable]),'knowledge_id':working})
        if active[working]['identity_sha256']!=expected or active[working]['sha256']!=manifest['merge_final_item_hashes'][stable]:invalid()


def directed_relation_key(value):
    left,right,kind=value['from_id'],value['to_id'],value['type']
    return tuple(sorted((left,right)))+(kind,) if kind in ('alternative','related') else (left,right,kind)


def validate_manifest(result,receipt,manifest,relations=()):
    if (receipt.get('contract')!=CONTRACT or not isinstance(manifest,dict) or digest(manifest)!=receipt.get('manifest_sha256')
            or receipt.get('unit_audits')!=manifest.get('unit_audits') or receipt.get('coverage_audits')!=manifest.get('coverage_audits')):
        fail('最终分片审核清单未绑定完整持久结果','SLICING_FINALAUDIT_REQUIRED')
    units=manifest['unit_audits'];coverage=manifest['coverage_audits']
    ids={i['knowledge_id'] for i in result['items']}
    if (not units or not coverage or unique([key for row in units for key in row['knowledge_ids']],'最终知识审核覆盖')!=ids
            or unique([tuple(obj) for row in coverage for obj in row['objects']],'最终来源审核覆盖')!={(c['object_type'],c['object_id']) for c in result['coverage']}
            or set(manifest['stable_ids'])!=ids or set(manifest['id_map'])!=set(manifest['original_ids']) or set(manifest['id_map'].values())!=ids):
        fail('最终分片审核或原始ID闭包覆盖不完整','SLICING_FINALAUDIT_REQUIRED')
    records=[*manifest['merge'],*manifest['candidates'],*manifest['relations'],*manifest.get('relation_batches',[]),*units,*coverage]
    unique([row['execution_id'] for row in records],'末端实际执行凭据')
    if receipt['execution_id']!=coverage[-1]['execution_id'] or any(not row.get('input_sha256') or not row.get('result_sha256') for row in records):
        fail('最终分片缺少实际模型输入/输出凭据','SLICING_FINALAUDIT_REQUIRED')
    pair_coverage={tuple(sorted(pair)) for row in manifest['candidates'] for pair in combinations(row['knowledge_ids'],2)}
    if not manifest['candidates'] or {key for row in manifest['candidates'] for key in row['knowledge_ids']}!=ids or not set(combinations(sorted(ids),2))<=pair_coverage:
        fail('跨分片关系候选比较覆盖不完整','SLICING_FINALAUDIT_REQUIRED')
    final_hashes=manifest.get('merge_final_item_hashes',{})
    merge_pairs={tuple(sorted(pair)) for row in manifest['merge'] for pair in combinations(row['item_hashes'],2)}
    prefilter=manifest.get('merge_comparison_contract')==MERGE_PREFILTER
    excluded=set()
    if prefilter:
        excluded={tuple(sorted((final_hashes[a['knowledge_id']],final_hashes[b['knowledge_id']]))) for a,b in combinations(result['items'],2) if merge_identity(a)!=merge_identity(b)}
        if {tuple(row) for row in manifest.get('identity_incompatible_pairs',[])}!=excluded:fail('非同一任务的归并排除证明不完整','SLICING_FINALAUDIT_REQUIRED')
    if set(final_hashes)!=ids or not set(combinations(sorted(final_hashes.values()),2))<=merge_pairs|excluded or not prefilter and len(ids)==1 and not set(final_hashes.values())<={h for row in manifest['merge'] for h in row['item_hashes']}:
        fail('最终归并身份比较覆盖不完整','SLICING_FINALAUDIT_REQUIRED')
    validate_merge_lineage(result,manifest)
    if manifest.get('relation_batch_contract')=='complete-pair-judgments-in-batches-v1':
        from .slicing_relation_batches import validate as validate_batches
        validate_batches(manifest,relations)
        return
    if manifest.get('relation_batches'):fail('历史关系协议不能附带批量凭据','SLICING_FINALAUDIT_REQUIRED')
    actual={directed_relation_key(row['relation']):row for row in relations}
    if len(actual)!=len(relations) or len(manifest['relations'])!=len(relations):fail('逐对关系审核凭据覆盖不完整','SLICING_FINALAUDIT_REQUIRED')
    seen=set()
    for row in manifest['relations']:
        pair=row['pair']
        if not isinstance(pair,list) or len(pair)!=3:fail('逐对关系方向凭据无效','SLICING_FINALAUDIT_REQUIRED')
        key=directed_relation_key({'from_id':pair[0],'to_id':pair[1],'type':pair[2]})
        value=actual.get(key)
        if key in seen or value is None or row.get('decision')!=value['decision'] or row['result_sha256']!=digest(value):
            fail('逐对关系方向、审核决定或完整结果与实际凭据不符','SLICING_FINALAUDIT_REQUIRED')
        seen.add(key)


def replay_terminal(plan,groups,planning,*,resolve,effect,receipts,history_length):
    from .slicing_engine import _coverage
    from .slicing_document import preflight_request
    from app.agents.model_adapter import ModelConfig
    from app.errors import AppError
    enabled(plan)
    registry=plan['registry'];sources=registry['sources'];assets=registry['assets'];occurrences=registry['occurrences']
    config=ModelConfig(base_url=plan.get('model_policy_base_url','https://unused.invalid'),config_version=plan.get('model_version'),**plan['model_wire'])
    original=[deepcopy(item) for group in groups for item in group['items']]
    original_ids=unique([i['knowledge_id'] for i in original],'归并原始ID')
    working={item['knowledge_id']:item for item in original};origin_map={key:key for key in original_ids}
    issues=[deepcopy(issue) for group in groups for issue in group['issues']]
    merge_seen=set();single_seen=set();merge_proofs=[]
    prefilter=plan.get('merge_comparison_contract')==MERGE_PREFILTER

    def projection(ids):
        from .slicing_parser import text_projection
        return text_projection(registry,sorted(set(ids)))

    committed_effect=effect
    def require_slots(minimum):
        # Necessary remaining obligations only, never a completion guarantee.
        # Every receipt already consumed also occupies one fixed model slot.
        if len(receipts)+minimum>plan['budget']['max_model_calls']:
            fail('已知剩余归并、关系或双重审核至少所需调用超出预约，未签发或截断','SLICING_BUDGET_EXCEEDED')
    def effect(stage,data):
        # Budget packing is pure. The selected complete request is authorized
        # afresh on every replay before its receipt is consumed or requested.
        ids=[source['source_id'] for source in data['sources']]
        if resolve(ids)!=projection(ids):fail('分片来源不再对应固定注册表','SLICING_SOURCE_CHANGED')
        return committed_effect(stage,data)

    def source_ids(items):
        return sorted({sid for item in items for sid in item['source_ids']}|
            {occurrences[oid]['source_id'] for item in items for oid in item.get('attachment_occurrence_ids',[])})
    def related_issues(ids):return [issue for issue in issues if not issue['source_ids'] or set(issue['source_ids'])&set(ids)]
    def proof(stage,data,**scope):
        entry=receipts[-1]
        if entry['stage']!=stage or entry.get('effect_input_sha256',entry['input_sha256'])!=digest(data):fail('分片未绑定实际模型凭据','SLICING_RECEIPT_INVALID')
        return {'execution_id':entry['execution_id'],'input_sha256':entry['input_sha256'],'result_sha256':entry['result_sha256'],**scope}

    def fits(stage,data,output=None):
        # A conservative byte upper bound also leaves room for returning intact
        # existing records. It never assumes provider-reported usage.
        if output is not None and len(encoded(output).encode())+256>plan['budget']['per_call_output_tokens']:return False
        try:preflight_request(plan,{'stage':stage,'input':data},config=config);return True
        except AppError as error:
            if error.code=='SLICING_CONTEXT_BUDGET_EXCEEDED' or error.code=='VALIDATION_ERROR' and error.message.startswith('变量替换后的提示词超过'):return False
            raise

    def pack(keys,seed,build,stage,estimate):
        selected=list(seed)
        if not fits(stage,build(selected),estimate(selected)):
            fail('最小完整语义对象或一对对象仍超过分片请求/输出预算，未裁剪原文或知识','SLICING_ATOMIC_CONTEXT_TOO_LARGE')
        for key in keys:
            if key in selected:continue
            candidate=selected+[key]
            if fits(stage,build(candidate),estimate(candidate)):selected=candidate
        return selected

    def merge_data(keys):
        items=[working[key] for key in keys];ids=source_ids(items)
        return {'terminal_contract':CONTRACT,'groups':[{'items':items}],'batch_item_ids':keys,'issues':related_issues(ids),**projection(ids)}
    def merge_estimate(keys):return {'items':[working[key] for key in keys],'id_map':[{'old_id':key,'new_id':key,'reason':'保持独立知识'} for key in keys],'issues':[]}

    while True:
        keys=sorted(working)
        missing=next(((a,b) for a,b in combinations(keys,2) if pair_key(working[a],working[b]) not in merge_seen and (not prefilter or merge_identity(working[a])==merge_identity(working[b]))),None)
        if len(keys)==1:
            if prefilter or digest(working[keys[0]]) in single_seen:break
            seed=keys
        elif missing is None:break
        else:seed=list(missing)
        eligible=[key for key in keys if merge_identity(working[key])==merge_identity(working[seed[0]])] if prefilter else keys
        selected=pack(eligible,seed,merge_data,'merge',merge_estimate)
        data=merge_data(selected);before=[working[key] for key in selected]
        require_slots(4) # This merge, candidates, unit audit, source audit.
        merged,request=effect('merge',data)
        if request:return {'next':request}
        new_ids=check_items(merged['items'],sources,assets,occurrences)
        mappings=merged['id_map']
        if unique([m['old_id'] for m in mappings],'分片归并去向')!=set(selected) or {m['new_id'] for m in mappings}!=new_ids or any(not m['reason'].strip() for m in mappings):fail('分片归并须为全部旧ID提供唯一真实去向')
        if new_ids&(set(working)-set(selected)):fail('分片归并ID与其他完整知识冲突')
        mapping={m['old_id']:m['new_id'] for m in mappings}
        for item in merged['items']:
            members=[working[key] for key in selected if mapping[key]==item['knowledge_id']]
            if len(members)==1:
                if item!=members[0]:fail('未实际归并的知识必须逐字段原样保留','SLICING_MERGE_CHANGED')
            else:
                if len({(m['task_key'],m['knowledge_layer'],m['parent_task_key']) for m in members})!=1 or (item['task_key'],item['knowledge_layer'],item['parent_task_key'])!=(members[0]['task_key'],members[0]['knowledge_layer'],members[0]['parent_task_key']):fail('不能归并不同任务或父子检索目的')
                if set(item['source_ids'])!={sid for m in members for sid in m['source_ids']} or set(item['occurrence_ids'])!={oid for m in members for oid in m['occurrence_ids']}:fail('归并丢失或增加来源/图片出现')
                if set(item.get('attachment_occurrence_ids',[]))!={oid for m in members for oid in m.get('attachment_occurrence_ids',[])}:fail('归并丢失或增加原图附件')
                if not {digest(e) for m in members for e in m['evidence']}<={digest(e) for e in item['evidence']}:fail('归并丢失原有完整逐字证据')
                if not {c['text'] for m in members for c in m['claims']}<={c['text'] for c in item['claims']}:fail('归并丢失原有逐结论记录')
                if not {digest(b) for m in members for b in m['branches']}<={digest(b) for b in item['branches']}:fail('归并丢失原有条件分支')
        merge_proofs.append(proof('merge',data,item_hashes=[digest(i) for i in before],id_map=deepcopy(mappings),
            before_items={i['knowledge_id']:item_witness(i) for i in before},after_items={i['knowledge_id']:item_witness(i) for i in merged['items']}))
        merge_seen.update(item_pairs(before));single_seen.update(digest(i) for i in before)
        for old,target in list(origin_map.items()):
            if target in mapping:origin_map[old]=mapping[target]
        for key in selected:del working[key]
        working.update({i['knowledge_id']:deepcopy(i) for i in merged['items']})
        issues.extend(deepcopy(merged['issues']))

    merged_items=[working[key] for key in sorted(working)]
    excluded=incompatible_pairs(merged_items) if prefilter else set()
    if not item_pairs(merged_items)<=merge_seen|excluded:fail('最终归并对覆盖不完整')
    stable,stable_map=stable_ids(merged_items,registry['source_sha256'])
    remap={old:stable_map[target] for old,target in origin_map.items()}
    if set(remap)!=original_ids or set(remap.values())!={i['knowledge_id'] for i in stable}:fail('原始ID至稳定ID闭包不完整')
    current={'complete':all(g['complete'] for g in groups),'items':stable,'coverage':_coverage(groups,remap,stable),'issues':issues}
    check_result(current,sources,assets,occurrences)
    by_id={i['knowledge_id']:i for i in stable};keys=sorted(by_id)
    assessed=set();candidate_proofs=[];pairs={}

    def candidate_data(ids):
        items=[by_id[key] for key in ids]
        return {'terminal_contract':CONTRACT,'items':items,**projection(source_ids(items))}
    first=True
    while first or not item_pairs(stable)<=assessed:
        first=False
        missing=next(((a,b) for a,b in combinations(keys,2) if pair_key(by_id[a],by_id[b]) not in assessed),None)
        seed=list(missing) if missing else keys[:1]
        selected=pack(keys,seed,candidate_data,'candidates',lambda ids:{'assessed_ids':ids,'pairs':[]})
        require_slots(3+((len(pairs)+15)//16 if plan.get('relation_batch_contract')=='complete-pair-judgments-in-batches-v1' else len(pairs)))
        data=candidate_data(selected);candidates,request=effect('candidates',data)
        if request:return {'next':request}
        if unique(candidates['assessed_ids'],'分片关系评估ID')!=set(selected):fail('关系候选未完整评估本批全体知识')
        candidate_proofs.append(proof('candidates',data,knowledge_ids=selected))
        assessed.update(item_pairs([by_id[key] for key in selected]))
        batch_pairs=set()
        for pair in candidates['pairs']:
            left,right,kind=pair['from_id'],pair['to_id'],pair['candidate_type']
            if left==right or {left,right}-set(selected) or kind not in ('prerequisite','next_step','alternative','exception','related') or not pair['reason'].strip():fail('关系候选端点、类型或理由无效')
            key=tuple(sorted((left,right)))+(kind,) if kind in ('alternative','related') else (left,right,kind)
            if key in batch_pairs:fail('单批关系候选重复')
            batch_pairs.add(key);pairs.setdefault(key,deepcopy(pair))
        if len(pairs)>plan.get('max_relation_pairs',16):fail('完整关系候选超出预约，未裁掉跨片关系','SLICING_RELATION_LIMIT')

    relations=[];relation_proofs=[]
    relation_batches=[]
    batched=plan.get('relation_batch_contract')=='complete-pair-judgments-in-batches-v1'
    if batched:
        from .slicing_relation_batches import audit as audit_batches
        outcome,request=audit_batches(plan,pairs,by_id,source_ids=source_ids,projection=projection,pack=pack,effect=effect,proof=proof,require_slots=require_slots,sources=sources,assets=assets,occurrences=occurrences)
        if request:return {'next':request}
        relations=outcome['relations'];relation_batches=outcome['proofs'];issues.extend(outcome['issues'])
    for relation_index,(key,pair) in enumerate([] if batched else sorted(pairs.items())):
        require_slots(len(pairs)-relation_index+2)
        left,right=by_id[pair['from_id']],by_id[pair['to_id']]
        data={'terminal_contract':CONTRACT,'pair':pair,'from_item':left,'to_item':right,**projection(source_ids([left,right]))}
        relation,request=effect('relation',data)
        if request:return {'next':request}
        value=relation['relation']
        if (value['from_id'],value['to_id'],value['type'])!=(pair['from_id'],pair['to_id'],pair['candidate_type']):fail('逐对审核更改关系端点、方向或类型')
        check_evidence(value['evidence'],{sid:sources[sid] for sid in source_ids([left,right])},assets,occurrences)
        if relation['decision']=='accept' and not value['evidence']:fail('接受关系缺少逐字证据')
        relations.append(relation);relation_proofs.append(proof('relation',data,pair=list(key),decision=relation['decision']))
        if relation['decision']=='pending_review':issues.append({'rule':'RELATION_PENDING','severity':'blocker','status':'open','message':relation['reason'],'source_ids':source_ids([left,right]),'occurrence_ids':[]})

    tasks=[task for group in planning for task in group['tasks']]
    for task in tasks:
        if task['requires_overview'] and not any(i['knowledge_layer']=='scenario_overview' and i['task_key']==task['task_key'] for i in stable):
            issues.append({'rule':'OVERVIEW_MISSING','severity':'blocker','status':'open','message':'原始规划要求的流程或全集总览尚未形成完整知识','source_ids':task['core_source_ids'],'occurrence_ids':task['image_occurrence_ids']})
    for item in stable:
        if item['parent_task_key'] and not any(parent['task_key']==item['parent_task_key'] and parent['knowledge_layer']=='scenario_overview' for parent in stable):fail('知识父task_key没有真实完整总览')

    pre_audit={'merge':merge_proofs,'merge_initial_items':{i['knowledge_id']:item_witness(i) for i in original},
        'merge_final_working_ids':{stable_map[i['knowledge_id']]:i['knowledge_id'] for i in merged_items},
        'merge_final_item_hashes':{stable_map[i['knowledge_id']]:digest(i) for i in merged_items},'candidates':candidate_proofs,'relations':relation_proofs,'original_ids':sorted(original_ids),'stable_ids':keys,'id_map':remap}
    if prefilter:pre_audit.update(merge_comparison_contract=MERGE_PREFILTER,identity_incompatible_pairs=[list(pair) for pair in sorted(excluded)])
    if batched:pre_audit.update(relation_batch_contract=plan['relation_batch_contract'],relation_batches=relation_batches)
    candidate={'result':current,'relation_results':relations,'id_map':remap,'terminal_manifest':pre_audit}
    candidate_hash=digest(candidate)
    unit_proofs=[];audited={};unit_pending=list(keys)

    def obligations(items,ids):
        names={i['task_key'] for i in items}|{i['parent_task_key'] for i in items}
        return [t for t in tasks if t['task_key'] in names or set(t['core_source_ids'])&set(ids)]
    def unit_data(ids):
        items=[by_id[key] for key in ids]
        parents=[i for i in stable if i['knowledge_layer']=='scenario_overview' and i['task_key'] in {v['parent_task_key'] for v in items} and i['knowledge_id'] not in ids]
        sids=source_ids(items+parents);local_occ={oid:o for oid,o in occurrences.items() if o['source_id'] in sids}
        coverage=[]
        for kind,objects,field in (('source',sids,'source_ids'),('occurrence',local_occ,'occurrence_ids')):
            for oid in objects:
                linked=[i['knowledge_id'] for i in items if oid in (i['source_ids'] if kind=='source' else item_occurrences(i))]
                coverage.append({'object_type':kind,'object_id':oid,'disposition':'represented' if linked else 'excluded_with_reason','knowledge_ids':linked,'reason':'' if linked else '仅为本分片完整上下文；全文覆盖另行审核'})
        local={'complete':True,'items':items,'coverage':coverage,'issues':related_issues(sids)}
        return {'terminal_contract':CONTRACT,'audit_facet':'units','global_candidate_sha256':candidate_hash,'audit_item_ids':ids,'candidate':local,'context_items':parents,'relation_results':[r for r in relations if r['relation']['from_id'] in ids or r['relation']['to_id'] in ids],'planning_obligations':obligations(items,sids),**projection(sids)}
    while unit_pending:
        require_slots(2) # At least this unit shard and one source shard.
        estimate=lambda ids:({'complete':True,'verdicts':[{key:by_id[identifier][key] for key in ('knowledge_id','status','semantic_checks')} for identifier in ids],'issues':[]} if plan.get('native_reference_contract')=='full-native-source-references-v1' else unit_data(ids)['candidate'])
        selected=pack(unit_pending,unit_pending[:1],unit_data,'finalaudit',estimate)
        data=unit_data(selected);final,request=effect('finalaudit',data)
        if request:return {'next':request}
        local_sources={s['source_id']:sources[s['source_id']] for s in data['sources']}
        local_occ={o['occurrence_id']:occurrences[o['occurrence_id']] for o in data['occurrences']}
        check_result(final,local_sources,assets,local_occ)
        if unique([i['knowledge_id'] for i in final['items']],'最终知识分片ID')!=set(selected):fail('最终审核丢失或增加分片知识')
        if any(identity(item)!=identity(by_id[item['knowledge_id']]) for item in final['items']):fail('最终审核修改事实、ID或证据须重新归并审核','SLICING_FINALAUDIT_CHANGED')
        audited.update({i['knowledge_id']:i for i in final['items']})
        issues.extend(deepcopy(final['issues']))
        if not final['complete']:current['complete']=False
        unit_proofs.append(proof('finalaudit',data,audit_facet='units',knowledge_ids=selected,source_ids=list(local_sources)))
        unit_pending=[key for key in unit_pending if key not in selected]

    coverage_proofs=[];coverage_rows=[];pending_sources=sorted(sources)
    audited_items=[audited[key] for key in keys];unit_verdicts_hash=digest({'items':audited_items,'proofs':unit_proofs})
    def coverage_data(sids):
        items=[i for i in audited_items if set(source_ids([i]))&set(sids)]
        return {'terminal_contract':CONTRACT,'audit_facet':'coverage','global_candidate_sha256':candidate_hash,'unit_verdicts_sha256':unit_verdicts_hash,'items':items,'planning_obligations':obligations(items,sids),'previous_coverage':[c for c in current['coverage'] if c['object_type']=='source' and c['object_id'] in sids or c['object_type']=='occurrence' and occurrences[c['object_id']]['source_id'] in sids],**projection(sids)}
    def expected_rows(sids):
        return [('source',sid) for sid in sids]+[('occurrence',oid) for oid,o in occurrences.items() if o['source_id'] in sids]
    def coverage_estimate(sids):
        return {'complete':True,'coverage':[{'object_type':kind,'object_id':oid,'disposition':'represented','knowledge_ids':[i['knowledge_id'] for i in audited_items if oid in (i['source_ids'] if kind=='source' else item_occurrences(i))],'reason':''} for kind,oid in expected_rows(sids)],'issues':[]}
    while pending_sources:
        require_slots(1)
        selected=pack(pending_sources,pending_sources[:1],coverage_data,'finalaudit',coverage_estimate)
        data=coverage_data(selected);final,request=effect('finalaudit',data)
        if request:return {'next':request}
        if unique([(c['object_type'],c['object_id']) for c in final['coverage']],'来源覆盖分片对象')!=set(expected_rows(selected)):fail('来源/图片覆盖审核遗漏、重复或越界')
        for row in final['coverage']:
            linked={i['knowledge_id'] for i in audited_items if row['object_id'] in (i['source_ids'] if row['object_type']=='source' else item_occurrences(i))}
            if not set(row['knowledge_ids'])<=set(keys):fail('覆盖引用不存在的最终知识')
            if row['disposition']=='represented' and (set(row['knowledge_ids'])!=linked or not linked):fail('来源覆盖须列全部真实引用知识')
            if row['disposition']=='excluded_with_reason' and linked:fail('不能将仍被知识使用的来源排除')
            if row['disposition']!='represented' and not row['reason'].strip():fail('来源待审或排除必须有具体理由')
        coverage_rows.extend(deepcopy(final['coverage']));issues.extend(deepcopy(final['issues']))
        if not final['complete']:current['complete']=False
        coverage_proofs.append(proof('finalaudit',data,audit_facet='coverage',objects=[list(p) for p in expected_rows(selected)],source_ids=selected))
        pending_sources=[sid for sid in pending_sources if sid not in selected]

    if len(receipts)!=(history_length() if callable(history_length) else history_length):fail('最终分片审核完成后存在多余模型步骤','SLICING_RECEIPT_INVALID')
    result={'complete':current['complete'],'items':audited_items,'coverage':coverage_rows,'issues':list({digest(i):i for i in issues}.values())}
    check_result(result,sources,assets,occurrences)
    manifest={**pre_audit,'unit_audits':unit_proofs,'coverage_audits':coverage_proofs,'all_sources':sorted(sources),'all_occurrences':sorted(occurrences)}
    receipt={'contract':CONTRACT,'stage':'finalaudit','execution_id':receipts[-1]['execution_id'],'candidate_sha256':candidate_hash,'result_sha256':digest(result),'manifest_sha256':digest(manifest),'unit_audits':unit_proofs,'coverage_audits':coverage_proofs}
    return {'result':result,'relations':relations,'id_map':remap,'receipts':deepcopy(receipts),'candidate_sha256':candidate_hash,'final_audit_receipt':receipt,'terminal_manifest':manifest}
