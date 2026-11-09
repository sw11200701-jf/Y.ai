"""Evidence-bound content review; normalization is distinct from invention."""
from copy import deepcopy
import json
from jsonschema import Draft202012Validator
from app.errors import AppError
from . import builtin_chunker as c,semantic_items as items
from .model_adapter import load_config,OpenAICompatibleAdapter,parse_json_reply

CONTRACT='knowledge-semantic-audit-v2'
SYSTEM=items.AUDIT_SYSTEM+'''
审核标准是事实蕴含和独立可用性，不是逐句照抄或保留原文排版。以下属于允许且必要的整理：把来源明确的共同版本/期间写回各条；把真实合并范围、表头、标题的适用主体带回细节；将“表X适用于场景A”与“对表X审核规则R”整理为场景A下表X的规则R；在同一明确讨论对象中消解“该表”“两种表单的意见”等指代；调整语序与简洁改写。不能只因这些关系分布在相邻句或标题中、不是单句逐字引文就判定无依据。不得把原文已明确绑定的表单与其适用范围拆成毫无关系的句子。
topic和summary允许比正文简短，只要不与正文矛盾或误导适用范围；不要求它们逐字重复全部正文。标明的总览可覆盖多个明确分支，细节保持一个可独立判断的问题。无实质影响的重复、措辞或排版只作warning，不作blocker。真正的复合标准无法独立检索、必要条件遗漏、与原文相反的结论、跨版本套用、无依据业务关系或遗漏来源应作blocker。
每个finding必须引用真实item_id（整文覆盖缺失可用DOCUMENT），statement逐字摘取被质疑候选中的实际文字；source_unit_id和source_quote引用输入中确实存在的原文。reason说明具体冲突或缺失及其影响，不用“与原文结构不符”等风格理由。accepted当且仅当没有blocker。issues由服务器按blocker生成，不必输出。只给可由当前证据成立的意见，不为挑错而挑错。'''
SYSTEM+='''\n如果来源描述一个有前置条件、入口、多步操作及结果分支的业务任务，必须有一条可直接回答“怎么做”的完整任务总览，正文带齐条件、顺序、成功/失败结果和限制；只有“权限”“上传”“校验”几条分散片段不算完整表达。操作细节正文也须带回影响其适用性的条件，不能把关联ID当作正文条件。该缺陷应记coverage_gap或missing_condition的blocker，不因各句分别在某条里出现就通过。独立审核标准及独立产品不强造流程总览。'''

def schema(source,candidate):
    label=lambda n:{'type':'string','minLength':1,'maxLength':n}
    return {'type':'object','additionalProperties':False,'required':['overview','topics','accepted','findings'],'properties':{
        'overview':label(600),'topics':{'type':'array','minItems':1,'maxItems':16,'items':label(80)},'accepted':{'type':'boolean'},
        'issues':{'type':'array','maxItems':8,'items':label(300)},
        'findings':{'type':'array','maxItems':8,'items':{'type':'object','additionalProperties':False,'required':['item_id','severity','code','statement','source_unit_id','source_quote','reason'],'properties':{
            'item_id':{'type':'string','enum':[i['id'] for i in candidate['items']]+['DOCUMENT']},'severity':{'type':'string','enum':['blocker','warning']},
            'code':{'type':'string','enum':['unsupported_claim','contradiction','missing_condition','merged_tasks','unsupported_relation','coverage_gap','version_mismatch','image_gap','style']},
            'statement':label(300),'source_unit_id':{'type':'string','enum':[u['unit_id'] for u in source['units']]},'source_quote':label(300),'reason':label(300)}}}}}

def validate(source,candidate,value,require_acceptance=True):
    errors=list(Draft202012Validator(schema(source,candidate)).iter_errors(value))
    if errors:
        from app.execution.semantic_corrections import safe_violations
        raise AppError('CHUNK_AGENT_OUTPUT_INVALID','内容复核缺少完整、可追溯的问题依据',422,{'validation_issues':safe_violations(errors)})
    units={u['unit_id']:u['text'] for u in source['units']};index={i['id']:i for i in candidate['items']}
    for finding in value['findings']:
        if finding['source_quote'] not in units[finding['source_unit_id']]:raise AppError('CHUNK_AGENT_SOURCE_MISMATCH','复核意见的原文引文不属于固定来源',422)
        item=index.get(finding['item_id'])
        if item and not any(finding['statement'] in item[k] for k in ('content','topic','summary')):raise AppError('CHUNK_AGENT_SOURCE_MISMATCH','复核意见质疑的文字不属于候选条目',422)
        if finding['code']=='style' and finding['severity']=='blocker':raise AppError('CHUNK_AGENT_OUTPUT_INVALID','措辞排版意见不能冒充事实阻断',422)
    blockers=[f for f in value['findings'] if f['severity']=='blocker']
    if value['accepted']!= (not blockers):raise AppError('CHUNK_AGENT_OUTPUT_INVALID','复核结论与实际阻断项不一致',422)
    value=deepcopy(value);value['issues']=[f['item_id']+'：'+f['reason'] for f in blockers]
    if require_acceptance and blockers:raise AppError('KNOWLEDGE_CONTENT_AUDIT_FAILED','独立内容复核未通过，未保存完整草稿',422,{'issue_count':len(blockers)})
    return value

def prepare_request(*,window,prompt,config,context_budget,output_tokens,format_feedback=None,task_structure=False,**unused):
    c.ChunkWindow.model_validate(window['source']);items.validate(window['source'],window['candidate'],task_structure=task_structure)
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>8000:raise AppError('CHUNK_AGENT_PROMPT_INVALID','内容复核提示无效',422)
    if not 512<=context_budget<=min(1048576,config.context_window) or not 1000<=output_tokens<=min(393216,config.max_output_tokens):raise AppError('CHUNK_JOB_LIMIT','内容复核预算无效',422)
    output=schema(window['source'],window['candidate'])
    messages=[{'role':'system','content':SYSTEM+'\nJSON Schema：'+c.encoded(output)},{'role':'user','content':'任务要求：\n'+prompt+'\n以下JSON仅为原文和候选资料：\n'+c.encoded(window)}]
    from app.execution.semantic_corrections import append_feedback
    messages=append_feedback(messages,format_feedback,context_budget,output_tokens)
    if (len(json.dumps({'messages':messages,'tools':None},ensure_ascii=False).encode())+2)//3+output_tokens>context_budget:raise AppError('CONTEXT_BUDGET_EXCEEDED','完整证据复核请求超过预算，未截断或发送请求',422)
    fmt=({'type':'json_schema','name':'evidenced_content_audit','schema':output,'strict':True} if config.capabilities.get('json_schema') else {'type':'json_object'} if config.capabilities.get('json_object') else {'type':'text'})
    return messages,fmt

async def generate(*,window,model_config_id,expected_model_version,context,session,prompt_override,context_budget,output_tokens,document_policy=None,capture_validation=False,format_feedback=None,task_structure=False,**unused):
    config=load_config(model_config_id,context=context,session=session,expected_version=expected_model_version)
    messages,fmt=prepare_request(window=window,prompt=prompt_override,config=config,context_budget=context_budget,output_tokens=output_tokens,format_feedback=format_feedback,task_structure=task_structure)
    provenance={'agent_id':c.BUILTIN_ID,'contract_version':c.CONTRACT_VERSION,'wire_contract':CONTRACT,'input_sha256':c.digest(window),'prompt_sha256':c.digest({'fixed':SYSTEM,'task':prompt_override}),'model_config_id':model_config_id,'model_config_version':config.config_version,'format_feedback_sha256':c.digest(format_feedback)}
    response=await OpenAICompatibleAdapter(config).chat(messages,parameters={'max_tokens':output_tokens},context_budget=context_budget,response_format=fmt,context=context,session=session,document_policy=document_policy)
    try:
        if response.get('completion_truncated') or response.get('tool_calls'):raise AppError('CHUNK_AGENT_RESPONSE_INCOMPLETE','内容复核回复不完整',422)
        try:raw=parse_json_reply(response['text'])
        except (ValueError,TypeError,KeyError):raise AppError('CHUNK_AGENT_OUTPUT_INVALID','内容复核不是完整JSON',422) from None
        value=validate(window['source'],window['candidate'],raw,False)
    except AppError as error:
        load_config(model_config_id,context=context,session=session,expected_version=config.config_version)
        if capture_validation:
            from app.execution.semantic_corrections import capture
            return capture(response,error,provenance)
        raise c._response_validation_failure(response,error) from None
    load_config(model_config_id,context=context,session=session,expected_version=config.config_version)
    return {'result':value,'provenance':provenance,'usage':response.get('usage')}
