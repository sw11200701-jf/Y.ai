"""Staged source module: lossless identity aliases, never business-text rewriting."""
from copy import deepcopy

CONTRACT='source-identity-aliases-v1'
KNOWLEDGE_CONTRACT='source-and-knowledge-identity-aliases-v2'
SINGLE={'source_id','occurrence_id','asset_id','knowledge_id','from_id','to_id','old_id','new_id'}
LISTS={'source_ids','occurrence_ids','asset_ids','knowledge_ids','assessed_ids','audit_item_ids','batch_item_ids'}
KNOWLEDGE_SINGLE={'knowledge_id','from_id','to_id','old_id','new_id'}
KNOWLEDGE_LISTS={'knowledge_ids','assessed_ids','audit_item_ids','batch_item_ids'}

def maps(plan,data=None):
    registry=plan['registry'];forward={}
    from .slicing_reading_order import ordered_identifiers
    for prefix,key in (('s','sources'),('o','occurrences'),('a','assets')):
        for number,identifier in enumerate(ordered_identifiers(registry,key),1):forward[identifier]=prefix+str(number).zfill(5)
    if plan.get('source_alias_contract')==KNOWLEDGE_CONTRACT:
        identifiers=set()
        def collect(v):
            if isinstance(v,list):
                for row in v:collect(row)
            elif isinstance(v,dict):
                for key,row in v.items():
                    if key in KNOWLEDGE_SINGLE and isinstance(row,str):identifiers.add(row)
                    elif key in KNOWLEDGE_LISTS and isinstance(row,list):identifiers.update(x for x in row if isinstance(x,str))
                    else:collect(row)
        collect(data)
        for number,identifier in enumerate(sorted(identifiers),1):forward[identifier]='k'+str(number).zfill(5)
    assert len(forward)==len(set(forward.values()))
    return forward,{alias:identifier for identifier,alias in forward.items()}

def transform(value,mapping,field=None,*,knowledge=False):
    if isinstance(value,list):
        is_ids=field in (LISTS if knowledge else {'source_ids','occurrence_ids','asset_ids'}) or isinstance(field,str) and (field.endswith('_source_ids') or field.endswith('_occurrence_ids'))
        return [mapping.get(v,v) if is_ids and isinstance(v,str) else transform(v,mapping,knowledge=knowledge) for v in value]
    if isinstance(value,dict):
        result={}
        for key,v in value.items():
            if isinstance(v,str) and (key in (SINGLE if knowledge else {'source_id','occurrence_id','asset_id'}) or key=='object_id' and value.get('object_type') in ('source','occurrence')):
                result[key]=mapping.get(v,v)
            else:result[key]=transform(v,mapping,key,knowledge=knowledge)
        return result
    return deepcopy(value)

def encode_input(plan,value):
    if plan.get('source_alias_contract') not in (CONTRACT,KNOWLEDGE_CONTRACT):return value
    forward,_=maps(plan,value);return transform(value,forward,knowledge=plan.get('source_alias_contract')==KNOWLEDGE_CONTRACT)

def decode_output(plan,value,request_input=None):
    if plan.get('source_alias_contract') not in (CONTRACT,KNOWLEDGE_CONTRACT):return value
    _,reverse=maps(plan,request_input);return transform(value,reverse,knowledge=plan.get('source_alias_contract')==KNOWLEDGE_CONTRACT)
