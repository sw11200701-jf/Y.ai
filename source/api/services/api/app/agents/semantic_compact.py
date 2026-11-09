"""Compact model wire; full business bodies/evidence are never shortened.

Only presentation duplicates are derived locally. Independent content review
and the separately accounted whole-document relation assessment remain required.
"""
from copy import deepcopy
from jsonschema import Draft202012Validator
from app.errors import AppError

CONTRACT='semantic-compact-items-v1'
SCOPE_CONTRACT='semantic-compact-scoped-items-v2'
SPEC='''\n本轮只输出紧凑Schema：overview和items。每条用id、layer、parent、topic、body、sources。id为K1等；layer=rule表示独立知识/判断规则，task表示完整操作任务总览，detail表示可独立提问的操作细节。parent仅detail填写本批真实task的id，其余null。topic简短，body才是完整可检索知识；sources引用真实unit_id。系统所述content/evidence_ids分别对应body/sources；summary、topics、task_key由服务器从原有正文/主题/id投影，关联由后续独立整文关系阶段判断，本轮不重复输出这些字段。
任务总览的body必须一次写全版本、主体、前提、入口、操作顺序、成功/失败分支、限制和例外。短操作任务可用一条完整task覆盖；不要把每个步骤再单独造片，或额外输出重复的注意事项片。只有确有独立提问价值的操作细节才另建detail，正文仍带回影响适用性的条件。独立判断规则按真实问题拆开，每条自含主体、条件、结果。不能为了节约输出而删条件、原文意见、步骤、分支、例外、证据；若覆盖所需内容确实超过容量，必须由服务器拒绝不完整结果。仅输出完整JSON，不添加解释。'''

SCOPE_SPEC='''\n本轮另输出scopes数组，为每个layer=task的实际任务恰给一条{id,text,sources}。id引用本批task的id。text只写原文明确对该任务所有操作共同适用的版本、主体、前置条件与范围限制；sources引用支持这些共同条件的真实unit_id。没有共同条件时text为null、sources为空。不要把成功/失败等互斥分支的专有条件当共同前提。服务器会把同一text逐字加到该task及其每条detail正文前，并合入其证据；body因此不必重复相同共同条件，但须保留自己特有的步骤、分支和例外。独立rule仍自含全部适用条件。不得用scope推测或扩大原文范围。
每个非ctx_来源单元都必须有真实用途：表头不是业务结论，不单独造表头知识；当表头用于解释规则中的字段时，将相关真实表头unit_id作为上下文证据引用。不要漏掉主体/条件/版本单元，也不要为覆盖而引用无关来源。'''

def schema(window,scope_inheritance=False):
    string=lambda n:{'type':'string','minLength':1,'maxLength':n}
    item={'type':'object','additionalProperties':False,'required':['id','layer','parent','topic','body','sources'],'properties':{
        'id':{'type':'string','pattern':'^K[1-9][0-9]*$','maxLength':12},
        'layer':{'type':'string','enum':['rule','task','detail']},
        'parent':{'type':['string','null'],'maxLength':12},'topic':string(80),'body':string(1800),
        'sources':{'type':'array','minItems':1,'maxItems':16,'uniqueItems':True,'items':{'type':'string','enum':[u['unit_id'] for u in window['units']]}}}}
    value={'type':'object','additionalProperties':False,'required':['overview','items'],'properties':{
        'overview':string(600),'items':{'type':'array','minItems':1,'maxItems':32,'items':item}}}
    if scope_inheritance:
        value['required'].append('scopes')
        value['properties']['scopes']={'type':'array','maxItems':32,'items':{'type':'object','additionalProperties':False,'required':['id','text','sources'],'properties':{
            'id':{'type':'string','pattern':'^K[1-9][0-9]*$','maxLength':12},'text':{'type':['string','null'],'minLength':1,'maxLength':600},
            'sources':{'type':'array','maxItems':16,'uniqueItems':True,'items':{'type':'string','enum':[u['unit_id'] for u in window['units']]}}}}}
    return value

def expand(window,raw,scope_inheritance=False):
    errors=list(Draft202012Validator(schema(window,scope_inheritance)).iter_errors(raw))
    if errors:
        from app.execution.semantic_corrections import safe_violations
        raise AppError('CHUNK_AGENT_OUTPUT_INVALID','紧凑知识回复结构无效，未保存',422,{'validation_issues':safe_violations(errors)})
    scopes={}
    if scope_inheritance:
        scopes={s['id']:s for s in raw['scopes']}
        tasks={i['id'] for i in raw['items'] if i['layer']=='task'}
        if len(scopes)!=len(raw['scopes']) or set(scopes)!=tasks:raise AppError('CHUNK_AGENT_OUTPUT_INVALID','共同条件必须恰好绑定每个真实任务',422)
        if any((s['text'] is None)!=(not s['sources']) for s in scopes.values()):raise AppError('CHUNK_AGENT_OUTPUT_INVALID','共同条件文字与原文引用不完整',422)
    layers={'rule':'standalone','task':'scenario_overview','detail':'atomic_detail'}
    items=[]
    for row in raw['items']:
        # The complete original body is kept byte-for-byte. A prefix is only a
        # display summary, never a substitute for retrieval or audited content.
        scope=scopes.get(row['id'] if row['layer']=='task' else row['parent'])
        body=(scope['text']+'\n' if scope and scope['text'] is not None else '')+row['body']
        sources=list(dict.fromkeys(row['sources']+(scope['sources'] if scope else [])))
        items.append({'id':row['id'],'topic':row['topic'],'summary':body[:240],'content':body,
            'evidence_ids':deepcopy(sources),'related_ids':[],'knowledge_layer':layers[row['layer']],
            'task_key':row['id'],'parent_task_key':row['parent']})
    return {'overview':raw['overview'],'topics':list(dict.fromkeys(i['topic'] for i in items))[:16],'items':items}
