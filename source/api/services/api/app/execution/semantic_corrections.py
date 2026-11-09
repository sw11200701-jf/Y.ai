"""Bounded new format-correction effects, never a replay of an unknown effect.

Every request has its own issued intent, count, completed receipt and immutable
feedback hash. Only fully received invalid model responses enter this protocol.
No raw response/source is put into diagnostics, no missing text is synthesized.
"""
import hashlib,json
from copy import deepcopy
from app.errors import AppError
from app.agents.builtin_chunker import digest

CONTRACT='semantic-format-correction-v1'
RECORD_CONTRACT='semantic-format-correction-v2'
SAFE_CODES=frozenset({'CHUNK_AGENT_OUTPUT_INVALID','CHUNK_AGENT_RESPONSE_INCOMPLETE','CHUNK_AGENT_SOURCE_MISMATCH','CHUNK_AGENT_SPAN_OVERLAP','CHUNK_AGENT_COVERAGE_INCOMPLETE','CHUNK_OVERVIEW_INVALID','CHUNK_OVERVIEW_COVERAGE_INVALID','CHUNK_OVERVIEW_EVIDENCE_INVALID'})

def allowance(plan):
    value=plan.get('format_corrections')
    if value is None:return 0
    legacy=isinstance(value,dict) and value.get('contract')==CONTRACT
    record=isinstance(value,dict) and value.get('contract')==RECORD_CONTRACT and plan.get('transport_version')=='xlsx-records-v6'
    if (not isinstance(value,dict) or not (legacy or record) or set(value)!=({'contract','max_new_calls'} if legacy else {'contract','max_new_calls','per_step_max_new_calls'})
            or type(value['max_new_calls']) is not int or not 0<=value['max_new_calls']<=(2 if legacy else 8)
            or (record and (type(value['per_step_max_new_calls']) is not int or value['per_step_max_new_calls']!=2))):
        raise AppError('SEMANTIC_PLAN_INVALID','格式纠正预约无效',409)
    return value['max_new_calls']

def per_step_allowance(plan):
    total=allowance(plan)
    return min(total,plan['format_corrections'].get('per_step_max_new_calls',total))

def reserve(base,*,max_calls,max_tokens,record_contract=False):
    value=deepcopy(base);calls=base['max_model_calls'];context=base['per_call_context_budget']
    extra=max(0,min(8 if record_contract else 2,max_calls-calls,max_tokens//context-calls))
    value.update(max_model_calls=calls+extra,reserved_total_tokens=(calls+extra)*context,includes_retry_allowance=False,format_correction_calls=extra)
    return value,({'contract':RECORD_CONTRACT,'max_new_calls':extra,'per_step_max_new_calls':2} if record_contract else {'contract':CONTRACT,'max_new_calls':extra})

def key(job,stage,index):
    return f'semantic:{job}:model:{stage}'+(f':format:{index}' if index else '')

def safe_violations(errors):
    """Keep only schema-owned paths/rules and numbers, never instance/message."""
    allowed={'overview','topics','units','through','topic','summary','chunks','unit_id','start','end','quote','text','evidence_ids','overview_evidence_ids','rows','id','items','layer','parent','body','sources'}
    result=[]
    for error in errors:
        path=[p if type(p) is int or p in allowed else '<source_unit>' for p in list(error.absolute_path)[:8]]
        rule=error.validator if error.validator in {'required','additionalProperties','type','minLength','maxLength','minItems','maxItems','enum'} else 'schema'
        item={'path':path,'rule':rule}
        if rule in {'minLength','maxLength','minItems','maxItems'}:
            item['limit']=error.validator_value
            if isinstance(error.instance,(str,list,dict)):item['observed_length']=len(error.instance)
        result.append(item)
        if len(result)==8:break
    return result

def capture(response,error,provenance):
    from app.agents.model_adapter import _response_validation_failure
    from app.execution.effects import get_effect_context,is_observed_model_failure
    observed=_response_validation_failure(response,error)
    # Pure mocks, forged provider fields, partial streams, network errors and
    # all unrecognized error classes can NEVER create a correction permit.
    if error.code not in SAFE_CODES or not is_observed_model_failure(observed,get_effect_context()):raise observed
    details=error.details if isinstance(error.details,dict) else {}
    violations=details.get('validation_issues',[])
    safe=[]
    for item in violations[:8]:
        if isinstance(item,dict) and set(item)<= {'path','rule','limit','observed_length'}:safe.append(deepcopy(item))
    issue={'code':error.code,'validation_issues':safe,'response_received':True,'outcome_known':True}
    if error.code=='CHUNK_AGENT_COVERAGE_INCOMPLETE':
        import re
        missing=details.get('missing_source_ids')
        if isinstance(missing,list) and missing and len(missing)<=16 and all(isinstance(v,str) and re.fullmatch(r'src_[0-9]+_[0-9]+',v) for v in missing):issue['missing_source_ids']=missing
    if not safe:issue['validation_issues']=[{'path':[],'rule':'valid_complete_JSON_and_schema_required'}]
    metadata={k:v for k,v in (response.get('response_usage_metadata') or {}).items() if k in {'output_limit','prompt_tokens','completion_tokens','total_tokens','reasoning_tokens'} and type(v) is int and v>=0}
    if response.get('finish_reason') in {'stop','length','tool_calls','content_filter','function_call'}:metadata['finish_reason']=response['finish_reason']
    return {'validation_issue':issue,'provenance':provenance,'usage':response.get('usage'),**({'response_metadata':metadata} if metadata else {})}

def append_feedback(messages,feedback,context_budget,output_tokens):
    if feedback is None:return messages
    if not isinstance(feedback,dict) or feedback.get('code') not in SAFE_CODES or feedback.get('response_received') is not True or feedback.get('outcome_known') is not True:
        raise AppError('SEMANTIC_RECEIPT_INVALID','纠正反馈缺少已完成格式错误凭据',409)
    messages=deepcopy(messages)
    messages.append({'role':'user','content':'上一次完整回复未通过下列格式或来源覆盖校验。这是一次有独立预算的新纠正，请重新根据上文全部原文生成符合Schema的完整JSON。missing_source_ids如存在，列出本次未覆盖的真实原文单元ID，须检查其必要知识或表头/条件上下文，给出有依据的引用；不能为覆盖而添加无关引文。不要省略原文单元，不增加额外字段，不重复解释或分析。具体约束：'+json.dumps(feedback,ensure_ascii=False,separators=(',',':'))})
    estimate=(len(json.dumps({'messages':messages,'tools':None},ensure_ascii=False).encode())+2)//3
    if estimate+output_tokens>context_budget:raise AppError('CONTEXT_BUDGET_EXCEEDED','完整格式反馈与原文超过预算，未发送纠正请求',422)
    return messages

def exhausted_error(feedback,plan,used):
    labels={'overview':'窗口概述','summary':'切片概述','topic':'主题','topics':'主题列表','through':'原文片段标识','units':'原文单元','overview_evidence_ids':'整文引用','evidence_ids':'主题引用'}
    reason='模型回复未符合完整JSON及字段结构'
    if (feedback or {}).get('code')=='CHUNK_AGENT_COVERAGE_INCOMPLETE':reason='知识回复未完整覆盖来源证据'
    for item in (feedback or {}).get('validation_issues',[])[:1]:
        field=next((labels[p] for p in reversed(item.get('path',[])) if type(p) is str and p in labels),'返回字段')
        rule=item.get('rule');limit=item.get('limit');observed=item.get('observed_length')
        if rule=='maxLength' and type(limit) is int and type(observed) is int:reason=f'{field}超过{limit}字，实际{observed}字'
        elif rule=='maxItems' and type(limit) is int:reason=f'{field}超过{limit}项'
        elif rule=='required':reason=f'{field}缺少必需字段'
        elif rule=='additionalProperties':reason=f'{field}包含不支持的额外字段'
        elif rule=='type':reason=f'{field}类型不正确'
        elif rule=='enum':reason=f'{field}不属于本批允许的标识'
    details={**(feedback or {}),'correction_calls_allowed':allowance(plan),'correction_calls_used':used,'automatic_replay':False}
    return AppError('CHUNK_AGENT_OUTPUT_INVALID',reason+'；本任务的格式纠正额度已用尽，未保存不合格切片',422,details)

def read_step_chain(*,session,binding,run,plan,step_id,expected_input_sha256):
    from app.execution.models import ExecutionStep
    from app.execution.storage import read_blob
    prior=None
    for index in range(per_step_allowance(plan)+1):
        node=key(plan['job_id'],step_id,index);identifier=hashlib.sha256((run['id']+':'+binding.dsl_hash+':'+node).encode()).hexdigest();row=session.get(ExecutionStep,identifier)
        if row is None or row.run_id!=run['id'] or row.node_key!=node or row.status!='succeeded' or not row.side_effect or not row.result_ref or not row.completed_at:
            raise AppError('SEMANTIC_STEP_NOT_COMMITTED','格式处理步骤尚未提交，禁止补造或重放',409)
        before=read_blob(session,run['id'],{'state_ref':row.input_ref});after=read_blob(session,run['id'],{'state_ref':row.result_ref})
        entries=(after.get('semantic_attempts') or {}).get(step_id,[])
        if len(entries)!=index+1:raise AppError('SEMANTIC_RECEIPT_INVALID','格式处理顺序不符',409)
        meta=entries[index];envelope=meta.get('envelope');feedback_sha=digest(prior)
        if (before.get('semantic_plan_sha256')!=run['semantic_job']['plan_sha256'] or after.get('semantic_plan_sha256')!=run['semantic_job']['plan_sha256']
            or meta.get('execution_id')!=identifier or meta.get('issued') is not True or meta.get('attempt_index')!=index
            or meta.get('input_sha256')!=expected_input_sha256 or not isinstance(envelope,dict) or meta.get('result_sha256')!=digest(envelope)
            or meta.get('format_feedback_sha256')!=feedback_sha or envelope.get('provenance',{}).get('format_feedback_sha256')!=feedback_sha
            or type(meta.get('lease_generation')) is not int or not 1<=meta['lease_generation']<=binding.generation
            or meta.get('reserved_context_tokens')!=plan['budget']['per_call_context_budget'] or binding.model_calls>plan['budget']['max_model_calls']):
            raise AppError('SEMANTIC_RECEIPT_INVALID','格式处理输入、预算或纠正凭据不符',409)
        if index and (before.get('semantic_attempts') or {}).get(step_id)!=entries[:-1]:raise AppError('SEMANTIC_RECEIPT_INVALID','格式纠正未承接上一已提交结果',409)
        if 'validation_issue' in envelope:
            if 'result' in envelope or envelope['validation_issue'].get('code') not in SAFE_CODES or envelope['validation_issue'].get('outcome_known') is not True or envelope['validation_issue'].get('response_received') is not True:
                raise AppError('SEMANTIC_RECEIPT_INVALID','格式错误结果不属于已完成响应',409)
            prior=envelope['validation_issue'];continue
        if (after.get('semantic_steps') or {}).get(step_id)!=meta:raise AppError('SEMANTIC_RECEIPT_INVALID','最终格式结果未被精确选定',409)
        return {'execution_id':identifier,'result_sha256':digest(envelope),'envelope':deepcopy(envelope)}
    raise AppError('CHUNK_AGENT_OUTPUT_INVALID','格式纠正额度已用尽，未生成或发布不合格切片',422,prior)

def actual_ids(plan,state):
    from app.execution.semantic_jobs import stage_ids
    ids=stage_ids(plan);attempts=state.get('semantic_attempts') or {}
    if set(attempts)!=set(ids):raise AppError('SEMANTIC_RECEIPT_INVALID','完成结果缺少格式处理记录',409)
    correction_count=0;actual=[]
    for logical in ids:
        rows=attempts[logical]
        if not isinstance(rows,list) or not 1<=len(rows)<=per_step_allowance(plan)+1:raise AppError('SEMANTIC_RECEIPT_INVALID','格式处理次数不符',409)
        correction_count+=len(rows)-1
        if state.get('semantic_steps',{}).get(logical)!=rows[-1] or 'validation_issue' in rows[-1].get('envelope',{}):raise AppError('SEMANTIC_RECEIPT_INVALID','最终格式结果未通过',409)
        actual.extend(key(plan['job_id'],logical,i) for i in range(len(rows)))
    if correction_count>allowance(plan) or state.get('format_corrections_used',0)!=correction_count:raise AppError('SEMANTIC_RECEIPT_INVALID','格式纠正总额度不符',409)
    return actual

def correction_node(compiler,plan,step_id):
    async def node(state):
        from app.execution.semantic_jobs import make_step_reader,load_plan,preflight_semantic_model,_domain_finalizer
        from app.execution.storage import authorize
        from app.knowledge.semantic_document import validated_steps
        reader=make_step_reader(compiler.run['id'],lease_owner=compiler.bridge.lease_owner)
        for index in range(per_step_allowance(plan)+1):
            current_state=compiler.bridge.read(state)
            entries=(current_state.get('semantic_attempts') or {}).get(step_id,[])
            if entries and 'validation_issue' not in entries[-1]['envelope']:return state
            if index<len(entries):continue
            feedback=entries[-1]['envelope']['validation_issue'] if entries else None
            if index and current_state.get('format_corrections_used',0)>=allowance(plan):
                raise exhausted_error(feedback,plan,current_state.get('format_corrections_used',0))
            async def preflight(execution):
                _,binding,current=authorize(execution.session,execution.run_id);pinned=load_plan(execution.session,execution.context,current)
                if binding.model_calls>=pinned['reservation']['model_calls']:raise AppError('CHUNK_JOB_BUDGET_EXCEEDED','文档预约调用数已耗尽',422)
                if index and execution.state.get('format_corrections_used',0)>=allowance(pinned['plan']):raise AppError('CHUNK_JOB_BUDGET_EXCEEDED','格式纠正预约调用数已耗尽',422)
                data,_,_,_=validated_steps(pinned['plan'],read_step=reader,context=execution.context,session=execution.session,until=step_id)
                preflight_semantic_model(pinned['plan'],step_id,data,context=execution.context,session=execution.session,format_feedback=feedback)
                from app.execution.document_job_budget import require_model_time
                require_model_time(pinned['plan'],execution.budget['deadline_epoch'])
            async def operation(execution):
                _,binding,current=authorize(execution.session,execution.run_id);pinned=load_plan(execution.session,execution.context,current)
                data,_,_,_=validated_steps(pinned['plan'],read_step=reader,context=execution.context,session=execution.session,until=step_id)
                from app.execution.document_request_wait import document_wait
                with document_wait(pinned['plan'],execution):
                    envelope=await execution.invoke('app.knowledge.semantic_document','execute_semantic_step',plan=pinned['plan'],step_id=step_id,read_step=reader,finalizer=_domain_finalizer,format_feedback=feedback)
                from app.execution.effects import get_effect_context
                effect=get_effect_context()
                if effect is None or effect.execution_id!=execution.execution_id:raise AppError('SEMANTIC_RECEIPT_INVALID','缺少模型副作用凭据',409)
                snapshot=execution.state;snapshot['semantic_plan_sha256']=current['semantic_job']['plan_sha256']
                meta={'execution_id':execution.execution_id,'issued':True,'attempt_index':index,'lease_generation':binding.generation,'input_sha256':digest(data),'result_sha256':digest(envelope),'format_feedback_sha256':digest(feedback),'reserved_context_tokens':pinned['plan']['budget']['per_call_context_budget'],'envelope':envelope}
                chain=snapshot.setdefault('semantic_attempts',{}).setdefault(step_id,[])
                if len(chain)!=index:raise AppError('SEMANTIC_RECEIPT_INVALID','格式处理链条变化',409)
                chain.append(meta)
                if index:snapshot['format_corrections_used']=snapshot.get('format_corrections_used',0)+1
                if 'validation_issue' not in envelope:snapshot.setdefault('semantic_steps',{})[step_id]=meta
                return snapshot
            state=await compiler.bridge.step(state,node_key=key(plan['job_id'],step_id,index),operation=operation,side_effect=True,preflight=preflight)
        entries=compiler.bridge.read(state)['semantic_attempts'][step_id]
        if 'validation_issue' not in entries[-1]['envelope']:return state
        raise exhausted_error(entries[-1]['envelope']['validation_issue'],plan,compiler.bridge.read(state).get('format_corrections_used',0))
    return node
