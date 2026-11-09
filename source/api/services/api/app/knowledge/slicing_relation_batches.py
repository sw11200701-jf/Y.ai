"""Full per-pair judgments in bounded requests, each tied to one real receipt."""
from copy import deepcopy
from .slicing_contracts import digest,fail,check_evidence

CONTRACT='complete-pair-judgments-in-batches-v1'
MAX_BATCH=16

def audit(plan,pairs,by_id,*,source_ids,projection,pack,effect,proof,require_slots,sources,assets,occurrences):
    ordered=sorted(pairs);pending=list(range(len(ordered)));relations=[];proofs=[];issues=[]
    def build(indices):
        rows=[];items=[]
        for index in indices:
            pair=pairs[ordered[index]];left=by_id[pair['from_id']];right=by_id[pair['to_id']]
            rows.append({'pair':pair,'from_item':left,'to_item':right});items.extend((left,right))
        return {'terminal_contract':plan['terminal_contract'],'relation_batch_contract':CONTRACT,'pairs':rows,**projection(source_ids(items))}
    def estimate(indices):
        return {'relations':[{'decision':'reject','reason':'Complete independent pair judgment',
            'relation':{'from_id':pairs[ordered[index]]['from_id'],'to_id':pairs[ordered[index]]['to_id'],
                'type':pairs[ordered[index]]['candidate_type'],'source_ids':[]}} for index in indices]}
    while pending:
        require_slots((len(pending)+MAX_BATCH-1)//MAX_BATCH+2)
        selected=pack(pending[:MAX_BATCH],pending[:1],build,'relation',estimate)
        data=build(selected);reply,request=effect('relation',data)
        if request:return None,request
        rows=reply['relations'];expected={(pairs[ordered[i]]['from_id'],pairs[ordered[i]]['to_id'],pairs[ordered[i]]['candidate_type']):i for i in selected}
        keys=[(r['relation']['from_id'],r['relation']['to_id'],r['relation']['type']) for r in rows]
        if len(keys)!=len(set(keys)) or set(keys)!=set(expected):fail('批量逐对审核遗漏、重复或改变关系端点/方向/类型')
        for row,key in zip(rows,keys):
            left,right=by_id[key[0]],by_id[key[1]];sids=source_ids([left,right])
            check_evidence(row['relation']['evidence'],{sid:sources[sid] for sid in sids},assets,occurrences)
            if row['decision']=='accept' and not row['relation']['evidence']:fail('接受关系缺少实际完整来源依据')
            if row['decision']=='pending_review':issues.append({'rule':'RELATION_PENDING','severity':'blocker','status':'open','message':row['reason'],'source_ids':sids,'occurrence_ids':[]})
        relations.extend(deepcopy(rows));proofs.append(proof('relation',data,pairs=[list(k) for k in keys]))
        pending=[index for index in pending if index not in selected]
    return {'relations':relations,'proofs':proofs,'issues':issues},None

def validate(manifest,relations):
    if manifest.get('relations'):fail('批量关系协议不能混用单对凭据','SLICING_FINALAUDIT_REQUIRED')
    actual={(r['relation']['from_id'],r['relation']['to_id'],r['relation']['type']):r for r in relations}
    if len(actual)!=len(relations):fail('批量关系存在重复端点','SLICING_FINALAUDIT_REQUIRED')
    seen=set()
    for proof in manifest.get('relation_batches',[]):
        keys=[tuple(key) for key in proof['pairs']]
        if not keys or len(keys)>MAX_BATCH or len(keys)!=len(set(keys)) or seen.intersection(keys) or not set(keys)<=actual.keys():fail('批量关系审核凭据覆盖不完整','SLICING_FINALAUDIT_REQUIRED')
        if digest({'relations':[actual[key] for key in keys]})!=proof['result_sha256']:fail('批量关系结果与实际模型凭据不符','SLICING_FINALAUDIT_REQUIRED')
        seen.update(keys)
    if seen!=set(actual):fail('批量关系遗漏实际逐对审核凭据','SLICING_FINALAUDIT_REQUIRED')
