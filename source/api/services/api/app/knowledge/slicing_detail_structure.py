"""Structural companion details, only for newly pinned native plans."""
from app.errors import AppError
CONTRACT='overview-requires-independent-details-v1'

def check_overview_details(result,contract=None):
    if contract is None:return
    if contract!=CONTRACT:raise AppError('SLICING_RESULT_INVALID','总览与细节结构合同不受支持',422)
    items=result['items'];roots={}
    normalized=lambda item:' '.join(item.get('content','').split())
    for item in items:
        if item.get('knowledge_layer')=='scenario_overview' and item.get('task_key'):
            roots.setdefault(item['task_key'],[]).append(item)
    missing=[]
    for key,parents in roots.items():
        bodies={normalized(item) for item in parents}
        children=[item for item in items if item.get('knowledge_layer')=='atomic_detail' and item.get('parent_task_key')==key and item.get('source_ids') and normalized(item) and normalized(item) not in bodies]
        if not children:missing.append(key)
    if missing:
        raise AppError('SLICING_RESULT_INVALID','已有完整任务/清单总览缺少独立可检索细节。真实多步任务或完整清单须保留总览，并依据原文逐字段、逐审核标准、逐步骤补齐自足的atomic_detail，用parent_task_key指向总览；不能用一条综合详情或复制总览代替细节。若原件不是多步任务或完整清单，应据实改为独立atomic_detail，不能制造父子条目。',422,{'rule':'OVERVIEW_INDEPENDENT_DETAILS_REQUIRED','missing_parent_task_keys':sorted(missing),'overview_ids':[item['knowledge_id'] for key in missing for item in roots[key]]})
