"""Navigation from explicit, independently approved model task parents only.

Call after the existing full-version receipt and caller ACL checks. These are
declared task links, not newly inferred or supposedly audited business edges.
"""
from collections import defaultdict

CONTRACT='approved-declared-task-navigation-v1'
def declared_links(items):
    approved=[item for item in items if item.get('status')=='approved' and item.get('semantic_checks') and all(item['semantic_checks'].values())]
    roots=defaultdict(list);result=defaultdict(list)
    for item in approved:
        if item.get('knowledge_layer')=='scenario_overview' and item.get('task_key'):
            roots[item['task_key']].append(item['knowledge_id'])
    for item in approved:
        parent=item.get('parent_task_key')
        if item.get('knowledge_layer')!='atomic_detail' or not parent or len(roots.get(parent,[]))!=1:continue
        child=item['knowledge_id'];root=roots[parent][0]
        if root==child:continue
        result[root].append((child,'task_detail'));result[child].append((root,'parent_overview'))
    return {key:list(dict.fromkeys(values)) for key,values in result.items()}

def labels(links,identifier):
    return [('步骤详情 → ' if kind=='task_detail' else '流程总览 ← ')+target for target,kind in links.get(identifier,[])]
