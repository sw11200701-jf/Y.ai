"""Private portable-recipe adapter to the existing durable semantic job ledger."""
from copy import deepcopy
from app.errors import AppError
from app.db import new_id
from app.data_access import policy_version
from .slicing_contracts import RECIPE, DEFAULTS, digest, fail, stage_settings, quality_gate
from .slicing_parser import parse_document, text_projection
from .slicing_engine import RecipeReplay, prepare_request


def is_portable(plan): return plan.get('recipe_version') == RECIPE


def minimum_model_calls(source_groups):
    return len(source_groups) * 4 + 4


def slot_ids(plan): return ['slot_' + str(i) for i in range(plan['budget']['max_model_calls'])]


def prefer_complete_native_context(plan, *, config, explicit_group_limit=False, max_sources=512, minimum_output=65536, grouped_core_sources=None, native_context_windows=False):
    """Pure full-document packing only when the complete actual wire fits.

    Sources are never summarized or omitted. Explicit user grouping remains
    authoritative; longer/lower-capacity files retain bounded source groups.
    """
    from .slicing_reading_order import ordered_identifiers
    ids=ordered_identifiers(plan['registry'],'sources');budget=plan['budget']
    if (explicit_group_limit or len(plan['source_groups'])<=1 or len(ids)>max_sources
            or budget['per_call_context_budget']<524288 or budget['per_call_output_tokens']<minimum_output):return plan
    candidate=deepcopy(plan)
    if grouped_core_sources is None:
        candidate['source_groups']=[ids];candidate['source_group_contract']='budgeted-complete-native-document-v1'
    else:
        if type(grouped_core_sources) is not int or not 1<=grouped_core_sources<=512:fail('核心来源分组预算无效')
        candidate['source_groups']=[ids[i:i+grouped_core_sources] for i in range(0,len(ids),grouped_core_sources)]
        candidate['prefetched_context_source_ids']=ids
        candidate['source_group_contract']='prefetched-native-context-grouped-core-v1'
        if native_context_windows:
            from .slicing_native_context_windows import windows,CONTRACT
            candidate.pop('prefetched_context_source_ids')
            candidate['source_context_groups']=windows(candidate['registry'],candidate['source_groups'])
            candidate['source_group_contract']=CONTRACT
    request=RecipeReplay(candidate,resolve_context=lambda selected:text_projection(candidate['registry'],selected)).replay([])['next']
    try:preflight_request(candidate,request,config=config)
    except AppError as error:
        if error.code=='SLICING_CONTEXT_BUDGET_EXCEEDED':return plan
        raise
    return candidate

def require_plan_scope(plan,current,root,file):
    """New direct-document plans keep the legacy exact-document authority.

    No configured source restriction is discarded. An unscoped legacy entry
    can process only the explicitly selected fully authorized file, not other
    files in its KB. Original portable plans retain their explicit KB scope.
    """
    kb=root['knowledge_base_id'];scope=current.get('source_scope') or []
    if plan.get('document_scope_contract')=='current-document-scope-v1':
        if plan.get('source_scope')!=[kb] or plan.get('file_id')!=file['id']:fail('固定当前文档范围不符','SLICING_SCOPE_DENIED')
        if (scope or current.get('recipe_version')=='portable-slicing-1.0.0' and not current.get('specialist_agent_id')) and kb not in scope:fail('当前配置不允许此文档所属知识库','SLICING_SCOPE_DENIED')
        if any(s.get('file_id')!=file['id'] or s.get('version_sha256')!=file['sha256'] for s in plan['registry']['sources'].values()):fail('当前文档计划不可装载其他文件','SLICING_SCOPE_DENIED')
    elif kb not in plan['source_scope'] or kb not in scope:
        fail('资料不在固定切片Agent范围与当前配置范围交集中','SLICING_SCOPE_DENIED')


def source_guard(plan, *, context, session, completed=False):
    from . import service
    from .semantic_document import _source
    from app.execution.builtin_agents import profile
    from app.agents.model_adapter import load_config
    if plan['actor'] != [context.tenant_id, context.workspace_id, context.operator_id]: fail('切片任务不属于当前主体', 'SLICING_SCOPE_DENIED')
    if completed:
        import base64, hashlib
        root = service.entry('knowledge_document', plan['document_id'], context, session)
        version = service.checked_version(plan['document_version_id'], root, context, session)
        from app.data_access import _unrestricted
        if not _unrestricted(service.version_decision(version, root, context, session)): fail('完整历史结果需要当前全文授权', 'SLICING_SCOPE_DENIED')
        file = service.raw_file({**root, 'file_id': version['file_id']}, context, session)
        content = base64.b64decode(file['content_base64'], validate=True)
        if hashlib.sha256(content).hexdigest() != plan['source_sha256']: fail('历史原件内容发生变化', 'SLICING_SOURCE_CHANGED')
    else:
        root, file, content = _source(plan['document_id'], context, session)
        if root.get('draft_version_id') != plan['parent_draft_id']:
            if root.get('draft_version_id') != plan['document_version_id']: fail('父草稿已变化', 'SLICING_SOURCE_CHANGED')
            version = service.checked_version(plan['document_version_id'], root, context, session)
            if version.get('semantic_completion', {}).get('plan_sha256') != digest(plan): fail('已提交草稿不属于固定计划')
    current = profile(session, context,plan.get("specialist_agent_id","builtin_knowledge_chunker"))
    require_plan_scope(plan,current,root,file)
    if file['id'] != plan['file_id'] or file['sha256'] != plan['source_sha256']: fail('资料来源或版本已变化', 'SLICING_SOURCE_CHANGED')
    registry = plan['registry']
    if 'image_reviews_sha256' in plan and plan['image_reviews_sha256']!=digest(file.get('image_evidence_reviews') or {}):
        fail('图像人工核查已变化或撤销，请重新建立来源计划','SLICING_IMAGE_REVIEW_CHANGED')
    if registry['source_sha256'] != plan['source_sha256'] or registry['registry_sha256'] != digest({k: registry[k] for k in ('sources', 'assets', 'occurrences')}): fail('固定解析注册表校验失败')
    if 'public_image_cache' in plan and not completed:
        from .slicing_public_image_cache import snapshot as public_image_snapshot
        if plan['public_image_cache']!=public_image_snapshot():fail('图片上传凭据已变化，未继续本次任务','SLICING_SOURCE_CHANGED')
    if registry.get('reading_order_contract'):
        from .slicing_reading_order import ordered_identifiers
        ordered_identifiers(registry,'sources')
    load_config(plan['model_config_id'], expected_version=None if completed else plan['model_version'], context=context, session=session)
    return root, file, content


def prepare_document(*, document_id, snapshot, context, session, max_model_calls, max_total_tokens, context_budget, output_tokens,current_document_scope=False):
    from .semantic_document import _source
    from app.agents.model_adapter import load_config
    root, file, content = _source(document_id, context, session)
    scope = snapshot.get('source_scope') or []
    if current_document_scope:
        from app.execution.builtin_agents import profile
        current=profile(session,context,snapshot.get("agent_id","builtin_knowledge_chunker"))
        if current.get('recipe_version')=='portable-slicing-1.0.0' and snapshot.get('agent_id')=='builtin_knowledge_chunker':fail('显式高级方案应沿用原有范围入口','SLICING_SCOPE_DENIED')
        configured=current.get('source_scope') or []
        if configured and root['knowledge_base_id'] not in configured:fail('资料不在当前配置的知识库范围内','SLICING_SCOPE_DENIED')
        scope=[root['knowledge_base_id']]
    elif root['knowledge_base_id'] not in scope: fail('请先在切片配置中明确授权此知识库范围', 'SLICING_SCOPE_DENIED')
    config = load_config(snapshot['model_config_id'], expected_version=snapshot['model_config_version'], context=context, session=session)
    from app.agents.reasoning_profiles import document_invocation
    invocation=document_invocation(config,snapshot.get('model_execution_mode','auto'))
    if not 7 <= max_model_calls <= 127 or max_model_calls * context_budget > max_total_tokens: fail('分片审核方案需至少7次调用及完整总预算预约', 'SLICING_BUDGET_INVALID')
    if context_budget > min(1048576, config.context_window) or output_tokens > min(393216, config.max_output_tokens): fail('预算超过模型能力', 'SLICING_BUDGET_INVALID')
    image_reviews=file.get('image_evidence_reviews') or {}
    from .slicing_parser import IMAGE_LAYOUT_V4
    from app.imports.document_container import canonical_format
    registry = parse_document(content, file['format'], file_id=file['id'], version_sha256=file['sha256'],perform_ocr=False,image_layout_version=IMAGE_LAYOUT_V4,collect_task_outline=canonical_format(file['format']) in ('doc','docx'))
    if image_reviews:
        from .slicing_image_reviews import apply_verified_reviews
        apply_verified_reviews(registry,file)
    from .slicing_reading_order import pin_reading_order
    pin_reading_order(registry)
    options = snapshot.get('recipe_options') or {}
    groups, group, size = [], [], 0
    limit = options.get('source_group_bytes',min(96000,max(4000,(context_budget-output_tokens)//2)))
    for sid in registry['sources']:
        from .slicing_contracts import encoded
        cost = len(encoded(text_projection(registry, [sid])).encode())
        if cost > limit: fail('单个完整版面对象超过装载预算，需更细的可信版面解析；未截断', 'SLICING_CONTEXT_BUDGET_EXCEEDED')
        if group and (size + cost > limit or len(group)>=128): groups.append(group); group, size = [], 0
        group.append(sid); size += cost
    if group: groups.append(group)
    from app.agents.slicing_specialists import SPECIALISTS,groups as specialist_groups
    specialist=snapshot.get('strategy',snapshot.get('agent_id'))
    if specialist in SPECIALISTS:groups=specialist_groups(registry,specialist,limit)
    # Only new plans use the consistent compact contract; old pinned prompts remain immutable.
    from .slicing_native_prompts import pin
    pinned_prompts=pin(snapshot.get('stage_prompts'))
    plan = {'recipe_version': RECIPE, 'job_id': new_id('knowledge_job'), 'document_id': document_id,
        'document_version_id': new_id('knowledge_version'), 'file_id': file['id'], 'source_sha256': file['sha256'],
        'parent_draft_id': root.get('draft_version_id'), 'actor': [context.tenant_id, context.workspace_id, context.operator_id],
        'policy_revision': policy_version(session, context), 'image_reviews_sha256':digest(image_reviews), 'model_config_id': snapshot['model_config_id'],
        'model_version': snapshot['model_config_version'], 'model_wire':{'model':config.model,'capabilities':deepcopy(config.capabilities),'context_window':config.context_window,'max_output_tokens':config.max_output_tokens},
        'model_invocation':invocation,'model_policy_base_url':config.base_url,'source_scope': scope, 'registry': registry, 'source_groups': groups,
        'stage_prompts': pinned_prompts, 'structural_correction_contract':'bounded-evidence-correction-v1',
        'stage_response_validation_contract':'document-stage-validates-complete-response-v1',
        'source_alias_contract':'source-and-knowledge-identity-aliases-v2',
        'planning_coverage_contract':'full-source-accounting-in-results-v1',
        'merge_comparison_contract':'same-task-identity-prefilter-v1',
        'attachment_contract':'original-image-attachments-v1',
        'reference_incidence_contract':'derived-reference-incidence-v1',
        'native_reference_contract':'full-native-source-references-v1',
        'audit_verdict_contract':'independent-unit-verdicts-v1',
        'audit_issue_retention_contract':'blocking-issues-require-repair-v1',
        'initial_audit_contract':'two-complete-independent-passes-v2',
        'overview_detail_structure_contract':'overview-requires-independent-details-v1',
        'response_feedback_contract':'precise-model-reply-feedback-v1',
        'schema_diagnostic_contract':'schema-path-feedback-v1',
        'native_knowledge_kind_contract':'bounded-document-kind-v1',
        'task_navigation_contract':'approved-declared-task-navigation-v1',
        'csv_columns_contract':'semantic-text-two-columns-v1',
        'native_attachment_context_contract':'native-attachment-context-v1',
        'native_table_delivery_contract':'complete-native-table-delivery-v1',
        'planning_context_contract':'explicit-task-source-readback-v1',
        'initial_coverage_contract':'core-source-accounting-with-full-context-v1',
        'context_navigation_contract':'native-source-navigation-v1',
        'task_context_contract':'native-task-role-context-v1',
        'group_identity_contract':'scoped-generated-knowledge-identities-v1',
        'task_fidelity_contract':'native-table-and-retrieval-task-fidelity-v1',
        'relation_batch_contract':'complete-pair-judgments-in-batches-v1',
        'max_structural_corrections':2, 'max_context_rounds': options.get('max_context_rounds', min(8,max(2,len(groups)))),
        'max_repair_rounds': options.get('max_repair_rounds', 2), 'csv_variant':options.get('csv_variant','original'), 'max_relation_pairs': options.get('max_relation_pairs', 512),
        'budget': {'max_model_calls': max_model_calls, 'reserved_total_tokens': max_model_calls * context_budget,
                   'per_call_context_budget': context_budget, 'per_call_output_tokens': output_tokens}}
    from .slicing_terminal_v2 import CONTRACT
    if current_document_scope:
        plan['document_scope_contract']='current-document-scope-v1'
        require_plan_scope(plan,current,root,file)
    plan['terminal_contract']=CONTRACT
    from .slicing_public_image_cache import snapshot as public_image_snapshot
    plan['public_image_cache']=public_image_snapshot()
    if specialist not in SPECIALISTS and file['format'] in ('xlsx','pdf'):
        plan=prefer_complete_native_context(plan,config=config,explicit_group_limit='source_group_bytes' in options,
            **({'max_sources':1536,'minimum_output':131072,'grouped_core_sources':256} if file['format']=='pdf' else {'grouped_core_sources':128,'native_context_windows':True} if file['format']=='xlsx' else {}))
        groups=plan['source_groups']
    if specialist in SPECIALISTS:
        from app.agents.slicing_specialists import contexts
        plan.update(specialist_agent_id=snapshot['agent_id'],source_group_contract='specialist-native-context-v1',
            source_context_groups=contexts(registry,groups,specialist))
    if minimum_model_calls(groups)>max_model_calls:fail('完整来源组、两次独立初审及知识/来源终审超出阶段调用预约，未丢弃尾部来源','SLICING_BUDGET_INVALID')
    # Check every initial full source group. Terminal requests are packed using
    # their actual complete records; they no longer reload all sources at once.
    for index,ids in enumerate(groups):
        from .slicing_native_prompts import navigation
        from .slicing_native_tasks import expand_task_context
        base=plan['source_context_groups'][index] if plan.get('source_group_contract')=='specialist-native-context-v1' else ids
        loaded=expand_task_context(registry,base) if plan.get('task_context_contract')=='native-task-role-context-v1' else base
        native_context=({'native_task_context':[{'source_id':sid,**registry['native_task_outline'][sid]} for sid in loaded if sid in registry.get('native_task_outline',{})]} if registry.get('native_task_outline') else {})
        preflight_request(plan,{'stage':'plan','input':{'group_index':index,'core_source_ids':ids,'context_round':0,'source_navigation':navigation(registry),**native_context,**text_projection(registry,loaded)}},config=config)
    # Refuse the first incomplete request before admission/reservation/cost.
    request = RecipeReplay(plan, resolve_context=lambda ids: text_projection(registry, ids)).replay([])['next']
    preflight_request(plan, request, config=config)
    return plan


def byte_upper_bound(wire):
    """Conservative byte-fallback BPE bound, not an exact tokenizer measurement.

    This deliberately counts schema twice and adds protocol overhead. It never
    claims provider-reported token usage or silently trims content to fit.
    """
    from .slicing_contracts import encoded
    return len(encoded(wire).encode('utf-8')) + 1024


def preflight_request(plan, request, *, config):
    budget = plan['budget']
    if budget['per_call_context_budget'] > config.context_window or budget['per_call_output_tokens'] > config.max_output_tokens: fail('固定预算超过当前模型能力')
    from app.agents.model_adapter import chat_payload
    kind='json_schema' if config.capabilities.get('json_schema') else 'json_object' if config.capabilities.get('json_object') else 'text'
    settings=stage_settings(plan.get('stage_prompts'));schema=None
    if plan.get('terminal_contract'):
        from .slicing_terminal_v2 import request_contract
        settings[request['stage']],schema=request_contract(plan,request['stage'],request['input'],settings[request['stage']])
    from .slicing_alias_transport import encode_input
    from .slicing_compact_references import compact_input
    return prepare_request(request['stage'], encode_input(plan,compact_input(plan,request['input'])), settings=settings,schema_override=schema,
        context_budget=budget['per_call_context_budget'], output_tokens=budget['per_call_output_tokens'], count_tokens=byte_upper_bound,format_kind=kind,
        wire_builder=lambda messages,fmt,output:chat_payload(config,messages,parameters={'max_tokens':output},response_format=fmt,document_policy=plan.get('model_invocation')))


def preflight_terminal_source_floor(plan, *, config):
    """Actual prompt/schema/wire cost with all sources and empty future results.

    Future candidate JSON is unknown before inference. Passing these lower-bound
    requests does not certify that a later complete request will fit; every real
    request still passes the ordinary complete-wire preflight before issuance.
    """
    projection=text_projection(plan['registry'],list(plan['registry']['sources']))
    empty={'complete':False,'items':[],'coverage':[],'issues':[]}
    inputs={
        'merge':{'groups':[],**projection},
        'candidates':{'items':[],**projection},
        'finalaudit':{'candidate':{'result':empty,'relation_results':[],'id_map':{}},'candidate_sha256':'0'*64,**projection},
    }
    floors={}
    for stage,data in inputs.items():
        try:request=preflight_request(plan,{'stage':stage,'input':data},config=config)
        except AppError as error:
            rendered_limit=(error.code=='VALIDATION_ERROR' and error.message=='变量替换后的提示词超过16000字符')
            if error.code!='SLICING_CONTEXT_BUDGET_EXCEEDED' and not rendered_limit:raise
            raise AppError('SLICING_CONTEXT_BUDGET_EXCEEDED','完整来源在'+stage+'阶段已超出请求容量，尚未提交任务或调用模型；需要有界分层处理',422,
                details={'stage':stage,'preflight':'terminal_source_floor','limit_kind':'rendered_prompt_chars' if rendered_limit else 'full_wire_tokens','future_candidates_included':False,'context_budget':plan['budget']['per_call_context_budget']}) from None
        floors[stage]={'input_token_upper_bound':request['input_tokens'],'output_tokens':request['output_tokens'],'request_sha256':request['request_sha256']}
    return {'contract':'terminal-source-floor-v1','certifies_future_full_requests':False,'stages':floors}


def reconstruct(plan, *, read_step, context, session, until=None, completed=False,through=None):
    if plan.get('terminal_contract'):
        return _reconstruct_bounded(plan,read_step=read_step,context=context,session=session,until=until,completed=completed,through=through)
    source_guard(plan, context=context, session=session, completed=completed)
    def resolve(ids):
        source_guard(plan, context=context, session=session, completed=completed)
        return text_projection(plan['registry'], ids)
    machine = RecipeReplay(plan, resolve_context=resolve)
    history = []
    for slot in slot_ids(plan):
        state = machine.replay(history)
        if 'next' not in state:
            if until is not None: fail('最终独审完成后不得继续调用模型')
            return state
        request = state['next']
        data = {'stage': request['stage'], 'input': request['input']}
        if until == slot: return data
        receipt = read_step(session=session, context=context, job_id=plan['job_id'], step_id=slot, expected_input_sha256=digest(data))
        envelope = receipt['envelope']
        provenance = envelope.get('provenance', {})
        expected = {'recipe_version': RECIPE, 'stage': request['stage'], 'model_config_id': plan['model_config_id'],
                    'model_config_version': plan['model_version'], 'input_sha256': digest(data)}
        from app.agents.model_adapter import ModelConfig
        pinned_request=preflight_request(plan,data,config=ModelConfig(base_url=plan.get('model_policy_base_url','https://unused.invalid'),config_version=plan.get('model_version'),**plan['model_wire']))
        expected.update(request_sha256=pinned_request['request_sha256'],prompt_sha256=pinned_request['prompt_sha256'])
        if any(provenance.get(k) != v for k, v in expected.items()) or receipt['result_sha256'] != digest(envelope): fail('模型阶段结果未绑定当前计划', 'SLICING_RECEIPT_INVALID')
        history.append({'stage': request['stage'], 'input_sha256': request['input_sha256'], 'execution_id': receipt['execution_id'],
                        'result': envelope['result'], 'result_sha256': digest(envelope['result']), 'slot_id': slot,
                        **({'response_validation':envelope['response_validation']} if 'response_validation' in envelope else {})})
        if through==slot:return machine.replay(history)
    state = machine.replay(history)
    if 'next' in state: fail('预约阶段调用数已耗尽，未自动重试或生成半份CSV', 'SLICING_BUDGET_EXCEEDED')
    if until is not None: fail('阶段不属于固定任务')
    return state


def _reconstruct_bounded(plan, *, read_step, context, session, until, completed, through):
    """One synchronous pass; every consumed slot still reads its real receipt.

    The reader retains fresh actor/engine/lease/plan checks for every row. The
    resolver retains fresh source/model checks for every expansion. Nothing
    survives this call, and no issued intent or await occurs inside replay.
    """
    source_guard(plan,context=context,session=session,completed=completed)
    ids=slot_ids(plan)
    if until is not None and until not in ids or through is not None and through not in ids or until is not None and through is not None:
        fail('阶段不属于固定任务','SLICING_RECEIPT_INVALID')
    stop=ids.index(until) if until is not None else ids.index(through)+1 if through is not None else len(ids)
    def resolve(source_ids):
        source_guard(plan,context=context,session=session,completed=completed)
        return text_projection(plan['registry'],source_ids)
    def read_next(index,request):
        if index>=stop:return None
        data={'stage':request['stage'],'input':request['input']}
        receipt=read_step(session=session,context=context,job_id=plan['job_id'],step_id=ids[index],expected_input_sha256=digest(data))
        envelope=receipt['envelope'];provenance=envelope.get('provenance',{})
        from app.agents.model_adapter import ModelConfig
        pinned=preflight_request(plan,data,config=ModelConfig(base_url=plan.get('model_policy_base_url','https://unused.invalid'),config_version=plan.get('model_version'),**plan['model_wire']))
        expected={'recipe_version':RECIPE,'stage':request['stage'],'model_config_id':plan['model_config_id'],
            'model_config_version':plan['model_version'],'input_sha256':digest(data),
            'request_sha256':pinned['request_sha256'],'prompt_sha256':pinned['prompt_sha256']}
        if any(provenance.get(key)!=value for key,value in expected.items()) or receipt['result_sha256']!=digest(envelope):
            fail('模型阶段结果未绑定当前计划','SLICING_RECEIPT_INVALID')
        return {'stage':request['stage'],'input_sha256':request['input_sha256'],'execution_id':receipt['execution_id'],
            'result':envelope['result'],'result_sha256':digest(envelope['result']),'slot_id':ids[index],
            **({'response_validation':envelope['response_validation']} if 'response_validation' in envelope else {})}
    state=RecipeReplay(plan,resolve_context=resolve).replay([],read_next=read_next)
    # Check current authority after the last ledger/projection read as well.
    source_guard(plan,context=context,session=session,completed=completed)
    if 'next' in state:
        if until is not None:return {'stage':state['next']['stage'],'input':state['next']['input']}
        if through is not None:return state
        fail('预约阶段调用数已耗尽，未自动重试或生成半份CSV','SLICING_BUDGET_EXCEEDED')
    if until is not None:fail('最终独审完成后不得继续调用模型')
    return state


async def execute_step(*, plan, step_id, read_step, context, session):
    from app.agents.model_adapter import load_config, OpenAICompatibleAdapter
    import json
    data = reconstruct(plan, read_step=read_step, context=context, session=session, until=step_id)
    config = load_config(plan['model_config_id'], expected_version=plan['model_version'], context=context, session=session)
    request = preflight_request(plan, data, config=config)
    fmt = request['response_format']
    response = await OpenAICompatibleAdapter(config).chat(request['messages'], response_format=fmt,
        parameters={'max_tokens': request['output_tokens']}, context_budget=plan['budget']['per_call_context_budget'], context=context, session=session,
        document_policy=plan.get('model_invocation'),
        **({'_document_stage_validation':True} if plan.get('stage_response_validation_contract')=='document-stage-validates-complete-response-v1' else {}))
    from app.agents.model_adapter import _response_validation_failure,parse_json_reply
    from .slicing_contracts import validate
    response_validation=None
    try:
        if response.get('tool_calls') or response.get('completion_truncated'):
            raise AppError('SLICING_RESPONSE_INCOMPLETE','切片回复未完整结束，未重试',422,
                {'finish_reason':response.get('finish_reason'),'tool_request_received':bool(response.get('tool_calls')),
                 **(response.get('response_usage_metadata') or {})})
        from .slicing_alias_transport import decode_output
        parsed=decode_output(plan,parse_json_reply(response['text']),data['input'])
        if plan.get('native_reference_contract')=='full-native-source-references-v1':
            from .slicing_terminal_v2 import validate_response
            from .slicing_compact_references import inflate_output
            validate_response(plan,data['stage'],data['input'],parsed,model_reply=True)
            parsed=inflate_output(plan,data['stage'],data['input'],parsed)
        from .slicing_contracts import bind_reference_incidence
        parsed=bind_reference_incidence(plan,data['stage'],data['input'],parsed)
        if plan.get('terminal_contract'):
            from .slicing_terminal_v2 import validate_response
            result=validate_response(plan,data['stage'],data['input'],parsed)
        else:result = validate(data['stage'], parsed)
    except (ValueError, KeyError, TypeError):
        if plan.get('structural_correction_contract')!='bounded-evidence-correction-v1':
            raise _response_validation_failure(response,AppError('SLICING_RESULT_INVALID','切片模型未返回有效阶段JSON',422)) from None
        result={'raw_text':response['text']};response_validation={'contract':'received-stage-json-invalid-v1','failure':'invalid_json'}
    except AppError as error:
        if error.code!='SLICING_RESULT_INVALID' or plan.get('structural_correction_contract')!='bounded-evidence-correction-v1':
            raise _response_validation_failure(response,error) from None
        result=parsed;response_validation={'contract':'received-stage-json-invalid-v1','failure':'json_schema'}
        if plan.get('response_feedback_contract')=='precise-model-reply-feedback-v1':
            response_validation['diagnostic']={'message':error.message,'details':error.details}
    source_guard(plan, context=context, session=session)
    return {'result': result, 'provenance': {'recipe_version': RECIPE, 'stage': data['stage'], 'input_sha256': digest(data),
        'request_sha256': request['request_sha256'], 'prompt_sha256': request['prompt_sha256'],
        'model_config_id': plan['model_config_id'], 'model_config_version': plan['model_version'],
        'budget_method': 'conservative_utf8_byte_upper_bound_v1', 'input_token_upper_bound': request['input_tokens']}, 'usage': response.get('usage'),
        **({'response_validation':response_validation} if response_validation else {})}
