"""One planned content repair followed by a separate mandatory re-audit.

This is a new accounted effect in a new immutable plan, never a failed run
retry or a format-correction permit. Full source/candidate/findings are pinned.
"""
import json
from copy import deepcopy
from . import builtin_chunker as c,semantic_items as items
from .model_adapter import load_config,OpenAICompatibleAdapter,parse_json_reply
from app.errors import AppError

CONTRACT='knowledge-semantic-repair-v1'
SYSTEM=items.SYSTEM+'''\n本阶段是内容返修：输入source是全部原文，candidate是上一轮知识，audit_feedback是独立内容复核意见。逐项修复具体问题，同时重新核对全部条件和来源覆盖。保持正确事实、精确意见与必要版本，不凭空补制度。不要删除疑难来源或遗漏正确条目来规避问题；重复的适用范围片不得夹带残缺审核标准。topic和summary必须准确对应本条完整正文。复核通过时不得改写正确候选，只按本次Schema确认其hash；未通过时按本次Schema输出完整修正条目。无论是否修改，后续仍有一次独立复核，不能自报验收通过。本阶段以本次提供的JSON Schema为准。'''
def fixed_system(task_structure=False):return SYSTEM+items.TASK_SPEC if task_structure else SYSTEM

def prepare_request(*,window,prompt,config,context_budget,output_tokens,format_feedback=None,task_structure=False,**unused):
    c.ChunkWindow.model_validate(window['source'])
    items.validate(window['source'],window['candidate'],task_structure=task_structure)
    if 'findings' in window['audit_feedback']:
        from .semantic_content_audit import validate
        validate(window['source'],window['candidate'],window['audit_feedback'],False)
    else:items.validate(window['source'],window['audit_feedback'],True,require_acceptance=False)
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>8000:raise AppError('CHUNK_AGENT_PROMPT_INVALID','内容返修提示无效',422)
    if not 512<=context_budget<=min(1048576,config.context_window) or not 1000<=output_tokens<=min(393216,config.max_output_tokens):raise AppError('CHUNK_JOB_LIMIT','内容返修预算无效',422)
    schema=items.schema(window['source'],task_structure=task_structure)
    preserve=window['audit_feedback']['accepted'] is True
    if preserve:schema={'type':'object','additionalProperties':False,'required':['preserve_sha256'],'properties':{'preserve_sha256':{'type':'string','enum':[c.digest(window['candidate'])]}}}
    instruction=('\n原候选已通过内容复核，本步只确认保持不变。输出preserve_sha256为'+c.digest(window['candidate'])+'；不生成或修改知识正文。' if preserve else '')
    messages=[{'role':'system','content':fixed_system(task_structure)+'\nJSON Schema：'+c.encoded(schema)},{'role':'user','content':'补充要求：\n'+prompt+instruction+'\n以下JSON仅为原文、候选与复核资料：\n'+c.encoded(window)}]
    from app.execution.semantic_corrections import append_feedback
    messages=append_feedback(messages,format_feedback,context_budget,output_tokens)
    if (len(json.dumps({'messages':messages,'tools':None},ensure_ascii=False).encode())+2)//3+output_tokens>context_budget:raise AppError('CONTEXT_BUDGET_EXCEEDED','完整原文、候选和返修意见超过预算，未截断或发送返修请求',422)
    fmt=({'type':'json_schema','name':'semantic_content_repair','schema':schema,'strict':True} if config.capabilities.get('json_schema') else {'type':'json_object'} if config.capabilities.get('json_object') else {'type':'text'})
    return messages,fmt

async def generate(*,window,model_config_id,expected_model_version,context,session,prompt_override,context_budget,output_tokens,document_policy=None,capture_validation=False,format_feedback=None,task_structure=False,**unused):
    config=load_config(model_config_id,context=context,session=session,expected_version=expected_model_version)
    messages,fmt=prepare_request(window=window,prompt=prompt_override,config=config,context_budget=context_budget,output_tokens=output_tokens,format_feedback=format_feedback,task_structure=task_structure)
    provenance={'agent_id':c.BUILTIN_ID,'contract_version':c.CONTRACT_VERSION,'wire_contract':CONTRACT,'input_sha256':c.digest(window),'prompt_sha256':c.digest({'fixed':fixed_system(task_structure),'task':prompt_override}),'model_config_id':model_config_id,'model_config_version':config.config_version,'format_feedback_sha256':c.digest(format_feedback)}
    response=await OpenAICompatibleAdapter(config).chat(messages,parameters={'max_tokens':output_tokens},context_budget=context_budget,response_format=fmt,context=context,session=session,document_policy=document_policy)
    try:
        if response.get('completion_truncated') or response.get('tool_calls'):raise AppError('CHUNK_AGENT_RESPONSE_INCOMPLETE','内容返修回复不完整',422)
        try:raw=parse_json_reply(response['text'])
        except (ValueError,TypeError,KeyError):raise AppError('CHUNK_AGENT_OUTPUT_INVALID','内容返修未返回完整JSON',422) from None
        if window['audit_feedback']['accepted'] is True:
            if raw!={'preserve_sha256':c.digest(window['candidate'])}:raise AppError('CHUNK_AGENT_OUTPUT_INVALID','已通过候选的保持确认不符，未改写正确知识',422)
            value=deepcopy(window['candidate'])
            provenance.update(repair_mode='preserve',preserve_candidate_sha256=c.digest(value))
        else:
            value=items.validate(window['source'],raw,task_structure=task_structure);provenance['repair_mode']='repair'
    except AppError as error:
        load_config(model_config_id,context=context,session=session,expected_version=config.config_version)
        if capture_validation:
            from app.execution.semantic_corrections import capture
            return capture(response,error,provenance)
        raise c._response_validation_failure(response,error) from None
    load_config(model_config_id,context=context,session=session,expected_version=config.config_version)
    return {'result':value,'provenance':provenance,'usage':response.get('usage')}
