"""Conservative aggregation of immutable independent model verdicts."""
from copy import deepcopy
from .slicing_contracts import digest,fail

def combine_audits(candidate,reports):
    if len(reports)!=2:fail('独立审核凭据数量不符','SLICING_RECEIPT_INVALID')
    original={item['knowledge_id']:item for item in candidate['items']}
    indexed=[]
    for report in reports:
        values={item['knowledge_id']:item for item in report['items']}
        if len(values)!=len(report['items']) or set(values)!=set(original):fail('独立审核知识集合发生变化','SLICING_RECEIPT_INVALID')
        for identifier,item in values.items():
            baseline=original[identifier]
            if {k:v for k,v in item.items() if k not in ('status','semantic_checks')}!={k:v for k,v in baseline.items() if k not in ('status','semantic_checks')}:fail('独立审核不得改写正文或原文证据','SLICING_RECEIPT_INVALID')
            if set(item['semantic_checks'])!=set(baseline['semantic_checks']):fail('独立审核维度不完整','SLICING_RECEIPT_INVALID')
        indexed.append(values)
    result=deepcopy(candidate)
    for item in result['items']:
        identifier=item['knowledge_id']
        item['semantic_checks']={key:all(values[identifier]['semantic_checks'][key] is True for values in indexed) for key in item['semantic_checks']}
        item['status']='approved' if all(values[identifier]['status']=='approved' for values in indexed) and all(item['semantic_checks'].values()) else 'pending_review'
    result['complete']=all(report['complete'] is True for report in reports)
    result['issues']=[];seen=set()
    for report in reports:
        for issue in report['issues']:
            key=digest(issue)
            if key not in seen:result['issues'].append(deepcopy(issue));seen.add(key)
    return result
