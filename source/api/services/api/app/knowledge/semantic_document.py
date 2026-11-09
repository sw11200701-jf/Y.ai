"""Knowledge-owned planning, one-effect operations and atomic semantic drafts.

Not an HTTP API/queue. Central durable execution must pin the returned plan and
provide a committed-step reader; raw model JSON is never execution evidence.
"""
import base64
from copy import deepcopy
import hashlib
from app.errors import AppError
from app.db import new_id
from app.data_access import policy_version
from app.agents import builtin_chunker as chunks
from app.agents import document_overview as overview
from app.agents import table_record_chunker as table_records
from app.agents import semantic_items
from app.agents import semantic_relations
from app.agents import semantic_repairs
from app.agents import semantic_content_audit
from app.agents import semantic_compact
from . import semantic_item_windows as item_windows
from app.imports.documents import _segments, _locator_text, MAX_TEXT, MAX_CHUNKS
from .semantic_chunking import segment_manifest_sha256, validate_agent_window


def fail(message):
    raise AppError("KNOWLEDGE_SEMANTIC_JOB_INVALID", message, 409)


def require_finalizer(finalizer, *, context, session, plan=None):
    if not callable(finalizer):
        raise AppError("KNOWLEDGE_SEMANTIC_FINALIZER_UNAVAILABLE", "当前候选未注册语义草稿提交器，未发送模型请求", 503)
    state = finalizer(operation="check_ready", context=context, session=session, plan=plan, payload=None)
    if not isinstance(state, dict) or state.get("contract_version") != "knowledge-semantic-finalizer-v2" or state.get("ready") is not True:
        raise AppError("KNOWLEDGE_SEMANTIC_FINALIZER_UNAVAILABLE", "当前草稿提交能力尚未就绪，未发送模型请求", 503)


def windows_from_bytes(*, content, kind, document_version_id, transport_version=None):
    """Transport windows only. No rule window is a publishable semantic chunk."""
    if transport_version==item_windows.TRANSPORT:
        return item_windows.windows(content,kind,document_version_id)
    if transport_version=='xlsx-records-v6':
        if kind!='xlsx':fail('表格记录合同不适用于此格式')
        from .xlsx_record_windows import windows
        return windows(content,document_version_id)
    if transport_version in ('document-spans-v3','document-boundaries-v4','document-boundaries-v5'):
        return _excel_windows(content,document_version_id,max_units=2,kind=kind)
    if transport_version in ('xlsx-packed-cells-v1','xlsx-packed-cell-spans-v2'):
        if kind != 'xlsx': fail('表格装载版本与文件格式不符')
        return _excel_windows(content, document_version_id, max_units=2 if transport_version=='xlsx-packed-cell-spans-v2' else 16)
    if transport_version is not None: fail('不支持的原文装载版本')
    windows, units, mapping, size, total = [], [], [], 0, 0
    def flush():
        nonlocal units, mapping, size
        if not units: return
        window = chunks.ChunkWindow(document_version_id=document_version_id,
            window_id=f"window_{len(windows)}", units=units).model_dump(mode="json")
        windows.append({"input": window, "segments": mapping,
            "input_sha256": chunks.digest(window), "manifest_sha256": segment_manifest_sha256(mapping)})
        units, mapping, size = [], [], 0
        if len(windows) > 32:
            raise AppError("CHUNK_JOB_LIMIT", "整文超过32个装载窗口，请拆分文档；未发送模型请求", 422)
    for number, (text, locator) in enumerate(_segments(content, kind)):
        total += len(text)
        if total > MAX_TEXT: raise AppError("DOCUMENT_LIMIT", "正文超过提取硬限，未截断", 422)
        for start in range(0, len(text), 2000):
            piece = text[start:start+2000]
            if not piece.strip(): continue
            if len(units) == 16 or size + len(piece) > 2000: flush()
            uid = f"source_{number}_{start}"
            units.append({"unit_id": uid, "text": piece})
            mapping.append({"id": uid, "text": piece, "locator": deepcopy(locator)})
            size += len(piece)
    flush()
    if not windows: raise AppError("DOCUMENT_NO_TEXT", "没有可提取正文，不执行模型切片", 422)
    return windows


def _excel_windows(content, document_version_id, *, max_units=16, kind='xlsx'):
    """Pack short cells for transport; retain every cell and character offset.

    A packed unit is not a semantic chunk. The model still chooses spans, and
    server-only source maps bind those spans back to the original cells.
    """
    windows, units, segments, source_map = [], [], [], {}
    text, parts, sheet, total, size = '', [], None, 0, 0

    def flush_window():
        nonlocal units, segments, source_map, size
        if not units: return
        value = chunks.ChunkWindow(document_version_id=document_version_id,
            window_id=f'window_{len(windows)}', units=units).model_dump(mode='json')
        windows.append({'input': value, 'segments': segments, 'source_map': source_map,
            'input_sha256': chunks.digest(value), 'manifest_sha256': segment_manifest_sha256(segments)})
        units, segments, source_map, size = [], [], {}, 0
        if len(windows) > 32:
            raise AppError('CHUNK_JOB_LIMIT', '完整文档超出本任务的32批容量；未截断、未发送模型请求。请按章节或工作表分开提交', 422)

    def flush_unit():
        nonlocal text, parts, sheet, size
        if not parts: return
        if size + len(text) > 2000 or len(units) == max_units: flush_window()
        uid = f'packed_{len(windows)}_{len(units)}'
        locator = _packed_locator(parts)
        units.append({'unit_id': uid, 'text': text})
        segments.append({'id': uid, 'text': text, 'locator': locator})
        source_map[uid] = parts
        size += len(text)
        text, parts, sheet = '', [], None

    for number, (original, locator) in enumerate(_segments(content, kind)):
        total += len(original)
        if total > MAX_TEXT: raise AppError('DOCUMENT_LIMIT', '正文超过提取硬限，未截断', 422)
        # 1000-character units fit two per shared 2000-character model window.
        for offset in range(0, len(original), 1000):
            piece = original[offset:offset+1000]
            if not piece.strip(): continue
            if text and ((kind=='xlsx' and sheet != locator['sheet']) or len(text) + 1 + len(piece) > 1000): flush_unit()
            if text: text += '\n'
            start = len(text)
            text += piece
            parts.append({'source_id': f'source_{number}_{offset}', 'source_offset': offset,
                'start': start, 'end': len(text), 'locator': deepcopy(locator),
                'text_sha256': hashlib.sha256(piece.encode('utf-8')).hexdigest()})
            sheet = locator.get('sheet')
    flush_unit()
    flush_window()
    if not windows: raise AppError('DOCUMENT_NO_TEXT', '没有可提取正文，不执行模型切片', 422)
    return windows


def _packed_locator(parts):
    locator=deepcopy(parts[0]['locator'])
    if locator.get('kind')=='cell' and all(p['locator'].get('sheet')==locator['sheet'] for p in parts):
        if parts[-1]['locator']['cell']!=locator['cell']:locator['cell']+=':'+parts[-1]['locator']['cell']
        locator['formula_text']=any(p['locator']['formula_text'] for p in parts)
    return locator


def _bind_excel_sources(mapped, spec):
    """Keep model semantic boundaries, adding exact cell-level evidence spans."""
    source_map = spec['source_map']
    units = {u['unit_id']: u['text'] for u in spec['input']['units']}
    for item in mapped['chunks']:
        spans = []
        for part in source_map[item['unit_id']]:
            start, end = max(item['offset_start'], part['start']), min(item['offset_end'], part['end'])
            if start >= end: continue
            text = units[item['unit_id']][start:end]
            offset = part['source_offset'] + start - part['start']
            spans.append({'locator': deepcopy(part['locator']), 'offset_start': offset,
                'offset_end': offset + len(text), 'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                'chunk_start': start - item['offset_start'], 'chunk_end': end - item['offset_start']})
        if not spans: fail('模型切片没有原始单元格证据')
        locator = deepcopy(item['locator']) if spec.get('record_contract')=='xlsx-records-v6' else _packed_locator(spans)
        marker='xlsx-packed-cells-v1' if locator.get('kind')=='cell' else 'source-packed-segments-v1'
        item.update(locator=locator, source_spans=spans, transport_version=marker)
    return mapped


def _source(document_id, context, session):
    from . import service
    root = service.entry("knowledge_document", document_id, context, session, write=True)
    service.require_full_document_maintenance(root, context, session)
    file = service.raw_file(root, context, session)
    try: content = base64.b64decode(file["content_base64"], validate=True)
    except (ValueError, TypeError): fail("原文件编码无效")
    if hashlib.sha256(content).hexdigest() != file["sha256"]: fail("原文件hash变化")
    from app.imports.document_container import validate_container
    validate_container(content, file['format'])
    return root, file, content


def prepare_document(*, document_id, model_config_id, context, session, max_model_calls,
                     max_total_tokens, context_budget, output_tokens, prompt_override=None,
                     overview_prompt_override=None, finalizer=None, model_execution_mode='auto'):
    require_finalizer(finalizer, context=context, session=session, plan={"document_id": document_id})
    from app.agents.model_adapter import load_config
    root, file, content = _source(document_id, context, session)
    if file['format'] in ('docx','pdf'):
        from .slicing_parser import parse_document,IMAGE_LAYOUT_V2
        layout=parse_document(content,file['format'],file_id=file['id'],version_sha256=file['sha256'],perform_ocr=False,image_layout_version=IMAGE_LAYOUT_V2)
        if layout['occurrences']:
            raise AppError('SLICING_IMAGE_REVIEW_REQUIRED','此文档包含图片，须通过高级切片流程核对图像证据及授权范围；未按纯文本任务遗漏图片或发送模型请求',422)
    if not model_config_id: raise AppError("MODEL_NOT_CONFIGURED", "请为切片Agent配置模型", 422)
    config = load_config(model_config_id, context=context, session=session)
    from app.agents.reasoning_profiles import document_invocation
    invocation = document_invocation(config,model_execution_mode)
    version_id = new_id("knowledge_version")
    from .xlsx_record_windows import is_tabular
    transport = item_windows.TRANSPORT
    windows = windows_from_bytes(content=content, kind=file["format"], document_version_id=version_id, transport_version=transport)
    single_window_overview=len(windows)==1
    budget_args=dict(max_model_calls=max_model_calls,max_total_tokens=max_total_tokens,context_budget=context_budget,output_tokens=output_tokens,single_window_overview=single_window_overview,include_relations=True)
    window_ids=[w['input']['window_id'] for w in windows]
    budget=item_windows.plan_budget(window_ids,**budget_args)
    # Independent content review is mandatory. Content repair is an explicitly
    # reserved extra effect; a smaller budget can never waive a failed review.
    include_repair=(budget['max_model_calls']+2*len(windows)<=max_model_calls and (budget['max_model_calls']+2*len(windows))*context_budget<=max_total_tokens)
    if include_repair:budget=item_windows.plan_budget(window_ids,include_content_repair=True,**budget_args)
    from app.execution.semantic_corrections import reserve
    budget,correction=reserve(budget,max_calls=max_model_calls,max_tokens=max_total_tokens,record_contract=transport=='xlsx-records-v6')
    prompt = semantic_items.DEFAULT_PROMPT if prompt_override is None else prompt_override
    summary_prompt = overview.DEFAULT_PROMPT if overview_prompt_override is None else overview_prompt_override
    for window in windows:
        (semantic_items.prepare_request if transport==item_windows.TRANSPORT else table_records.prepare_record_request if transport=='xlsx-records-v6' else chunks.prepare_chunk_request)(window=window["input"], prompt=prompt, config=config,
            context_budget=context_budget, output_tokens=output_tokens, task_structure=True,compact_output=True,scope_inheritance=True,span_only=transport in ('xlsx-packed-cell-spans-v2','document-spans-v3'),boundary_only=transport in ('document-boundaries-v4','document-boundaries-v5'),bounded_labels=transport in ('document-boundaries-v5','xlsx-records-v6'))
    # Full reduction content is budget-checked again immediately before each effect.
    if not isinstance(summary_prompt, str) or not summary_prompt.strip() or len(summary_prompt) > 8000:
        raise AppError("CHUNK_AGENT_PROMPT_INVALID", "整文任务提示无效", 422)
    return {"job_id": new_id("knowledge_job"), "document_id": document_id, "document_version_id": version_id,
        "actor": [context.tenant_id, context.workspace_id, context.operator_id], "source_sha256": file["sha256"],
        "file_id": file["id"], "parent_draft_id": root.get("draft_version_id"),
        **({'transport_version': transport} if transport else {}),'relation_contract':semantic_relations.CONTRACT,
        'task_structure_contract':semantic_items.TASK_CONTRACT,'compact_output_contract':semantic_compact.SCOPE_CONTRACT,'single_window_overview':single_window_overview,
        **({'content_repair_contract':semantic_repairs.CONTRACT,'content_preserve_contract':'accepted-content-hash-v1',
        'content_repair_prompt_sha256':chunks.digest({'fixed':semantic_repairs.fixed_system(True),'task':prompt})} if include_repair else {}),
        'content_audit_contract':semantic_content_audit.CONTRACT,'content_audit_prompt_sha256':chunks.digest({'fixed':semantic_content_audit.SYSTEM,'task':prompt}),
        "policy_revision": policy_version(session, context), "windows": windows, "budget": budget,
        "model_config_id": model_config_id, "model_version": config.config_version,
        "model_invocation": invocation,"format_corrections":correction,
        "prompt": prompt, "overview_prompt": summary_prompt,
        "prompt_sha256": chunks.digest({"fixed": semantic_items.fixed_system(True,True,True) if transport==item_windows.TRANSPORT else table_records.SYSTEM if transport=='xlsx-records-v6' else chunks.BOUNDED_SYSTEM if transport=='document-boundaries-v5' else chunks.BOUNDARY_SYSTEM if transport in ('document-boundaries-v4','document-boundaries-v5') else chunks.SPAN_SYSTEM if transport in ('xlsx-packed-cell-spans-v2','document-spans-v3') else chunks.FIXED_SYSTEM, "task": prompt}),
        "overview_prompt_sha256": chunks.digest({"fixed": overview.BOUNDED_COMPACT_SYSTEM if transport in ('document-boundaries-v5','xlsx-records-v6') else overview.COMPACT_SYSTEM if transport in ('xlsx-packed-cell-spans-v2','document-spans-v3','document-boundaries-v4','document-boundaries-v5') else overview.SYSTEM, "task": summary_prompt})}


def revalidate_document(plan, context, session):
    from app.agents.model_adapter import load_config
    load_config(plan["model_config_id"], context=context, session=session, expected_version=plan["model_version"])
    if plan["actor"] != [context.tenant_id, context.workspace_id, context.operator_id]: fail("处理任务不属于当前主体")
    root, file, content = _source(plan["document_id"], context, session)
    if (file["id"], file["sha256"]) != (plan["file_id"], plan["source_sha256"]): fail("原始来源变化，请新建任务")
    if (root.get("draft_version_id"), policy_version(session, context)) != (plan["parent_draft_id"], plan["policy_revision"]):
        # A committed draft may be reread only through its exact immutable plan
        # stamp and post-write policy generation, never by substituting a parent.
        from .semantic_finalizer import validate_completed
        validate_completed(plan=plan, root=root, context=context, session=session)
    _validate_source_plan(plan, file, content)
    return root


def _validate_source_plan(plan, file, content):
    """Pure source/window/budget binding shared by strict execution and reads."""
    from .window_fingerprint import fingerprint
    expected_digest, window_ids = fingerprint(content=content, kind=file["format"],
        version_id=plan["document_version_id"], transport_version=plan.get('transport_version'))
    if expected_digest != chunks.digest(plan["windows"]): fail("任务窗口不再对应当前原文")
    budget = plan["budget"]
    expected_budget = (item_windows.plan_budget if plan.get("transport_version")==item_windows.TRANSPORT else overview.plan_document)(list(window_ids),
        max_model_calls=budget["max_model_calls"], max_total_tokens=budget["reserved_total_tokens"],
        context_budget=budget["per_call_context_budget"], output_tokens=budget["per_call_output_tokens"],
        **({'include_relations':True} if plan.get('relation_contract')==semantic_relations.CONTRACT else {}),
        **({'single_window_overview':True} if plan.get('single_window_overview') is True else {}),
        **({'include_content_repair':True} if plan.get('content_repair_contract')==semantic_repairs.CONTRACT else {}))
    if plan.get('format_corrections') is not None:
        from app.execution.semantic_corrections import reserve,allowance
        allowance(plan)
        expected_budget,correction=reserve(expected_budget,max_calls=budget['max_model_calls'],max_tokens=budget['reserved_total_tokens'],record_contract=plan.get('transport_version')=='xlsx-records-v6')
        if correction!=plan['format_corrections']:fail('格式纠正预约与预算不符')
    if chunks.digest(budget) != chunks.digest(expected_budget): fail("归纳树或总预算偏离完整原文计划")


def validated_steps(plan, *, read_step, context, session, until=None):
    """read_step is CENTRAL ONLY: kwargs below; returns a committed receipt.

    Receipt: {execution_id,result_sha256,envelope:{result,provenance,usage}}.
    Callback must check durable success, lease, actor/job/input, budget and ACL.
    It is never supplied through HTTP or constructed from model claims.
    """
    from contextlib import nullcontext
    batch=getattr(read_step,'batch',None)
    with batch(session=session,context=context) if callable(batch) else nullcontext():
        revalidate_document(plan, context, session)
        return _validated_results(plan, read_step=read_step, context=context, session=session, until=until)


def _validated_results(plan, *, read_step, context, session, until=None):
    """Pure receipt validation; entry points must first perform their own gate."""
    refs, bound, receipts = {}, [], []
    stages = [(w["input"]["window_id"], w["input"], w) for w in plan["windows"]]
    stages += [(s["summary_id"], None, s) for s in plan["budget"]["summary_steps"]]
    for key, data, spec in stages:
        is_window = data is not None
        if not is_window:
            if spec.get('repair_window_id'):
                wid=spec['repair_window_id']
                source=next(w['input'] for w in plan['windows'] if w['input']['window_id']==wid)
                data={'source':source,'candidate':refs['window:'+wid],'audit_feedback':refs['audit:'+wid]}
            elif spec.get('relation_catalog'):
                data={'items':[{'id':c['semantic_local_id'],'content':c['text'],'evidence':c['evidence_text']} for c in bound]}
            elif spec.get('audit_window_id'):
                wid=spec['audit_window_id']
                source=next(w['input'] for w in plan['windows'] if w['input']['window_id']==wid)
                data={'source':source,'candidate':refs['window:'+wid]}
            else:
                data = {"document_version_id": plan["document_version_id"], "summary_id": key,
                    "items": [overview.summary_item(ref, refs[ref]) for ref in spec["input_reference_ids"]]}
        if key == until: return data, refs, bound, receipts
        receipt = read_step(session=session, context=context, job_id=plan["job_id"], step_id=key,
            expected_input_sha256=chunks.digest(data))
        if not isinstance(receipt, dict) or not receipt.get("execution_id"): fail("缺少中央已提交执行凭据")
        try:
            envelope = receipt["envelope"]
            if receipt["result_sha256"] != chunks.digest(envelope): fail("持久结果hash不符")
            provenance = envelope["provenance"]
            if not isinstance(provenance, dict) or "result" not in envelope: fail("中央结果格式不完整")
        except (KeyError, TypeError, ValueError): fail("中央结果格式不完整")
        if any(provenance.get(k) != value for k, value in {
            "agent_id": chunks.BUILTIN_ID, "contract_version": chunks.CONTRACT_VERSION,
            "model_config_id": plan["model_config_id"], "model_config_version": plan["model_version"],
            "input_sha256": chunks.digest(data), "prompt_sha256": plan.get('content_repair_prompt_sha256') if spec.get('repair_window_id') else chunks.digest({"fixed":semantic_relations.SYSTEM,"task":plan["prompt"]}) if spec.get('relation_catalog') else plan.get('content_audit_prompt_sha256',chunks.digest({"fixed":semantic_items.AUDIT_SYSTEM,"task":plan["prompt"]})) if spec.get("audit_window_id") else plan["prompt_sha256"] if is_window else plan["overview_prompt_sha256"]}.items()):
            fail("执行结果的固定模型、提示或输入不匹配")
        if spec.get('repair_window_id'):
            if plan.get('content_repair_contract')!=semantic_repairs.CONTRACT or provenance.get('wire_contract')!=semantic_repairs.CONTRACT:fail('内容返修协议不符')
            value=semantic_items.validate(data['source'],envelope['result'],task_structure=plan.get('task_structure_contract')==semantic_items.TASK_CONTRACT)
            if plan.get('content_preserve_contract')=='accepted-content-hash-v1' and data['audit_feedback']['accepted'] is True and (value!=data['candidate'] or provenance.get('repair_mode')!='preserve' or provenance.get('preserve_candidate_sha256')!=chunks.digest(value)):fail('已通过候选被返修步骤改写')
            source_spec=next(w for w in plan['windows'] if w['input']['window_id']==spec['repair_window_id'])
            bound[:]=[c for c in bound if c['semantic_local_id'].split(':')[0]!=spec['repair_window_id']]
            bound.extend(item_windows.bind(data['source'],source_spec,value)['chunks'])
            refs['window:'+spec['repair_window_id']]=value
            refs[spec['reference_id']]=value
        elif spec.get('relation_catalog'):
            if plan.get('relation_contract')!=semantic_relations.CONTRACT or provenance.get('wire_contract')!=semantic_relations.CONTRACT:fail('整文关联协议不符')
            value=semantic_relations.validate(data,envelope['result'])
            refs[spec['reference_id']]=value
            for item in bound:
                rels=[r for r in value['relations'] if item['semantic_local_id'] in (r['from_id'],r['to_id'])]
                item['semantic_related_ids']=list(dict.fromkeys(r['to_id'] if r['from_id']==item['semantic_local_id'] else r['from_id'] for r in rels))
                item['semantic_relations']=deepcopy(rels)
        elif plan.get('transport_version')==item_windows.TRANSPORT and (is_window or spec.get('audit_window_id')):
            audit=bool(spec.get('audit_window_id'))
            audit_v2=audit and plan.get('content_audit_contract')==semantic_content_audit.CONTRACT
            if provenance.get('wire_contract')!=(semantic_content_audit.CONTRACT if audit_v2 else semantic_items.CONTRACT+('-audit' if audit else '')):fail('知识整理或独立复核协议不符')
            if is_window and plan.get('compact_output_contract') in (semantic_compact.CONTRACT,semantic_compact.SCOPE_CONTRACT) and provenance.get('item_wire_contract')!=plan['compact_output_contract']:fail('紧凑知识输出协议不符')
            value=(semantic_content_audit.validate(data['source'],data['candidate'],envelope['result'],not spec.get('repair_follows',False)) if audit_v2 else semantic_items.validate(data['source'] if audit else data,envelope['result'],audit,require_acceptance=not spec.get('repair_follows',False),task_structure=plan.get('task_structure_contract')==semantic_items.TASK_CONTRACT))
            if audit:refs[spec['reference_id']]=value
            else:
                bound.extend(item_windows.bind(data,spec,value)['chunks'])
                refs['window:'+key]=value
        elif is_window:
            if plan.get('transport_version') in ('xlsx-packed-cell-spans-v2','document-spans-v3','document-boundaries-v4','document-boundaries-v5','xlsx-records-v6') and provenance.get('wire_contract')!=(table_records.CONTRACT if plan.get('transport_version')=='xlsx-records-v6' else chunks.BOUNDED_CONTRACT if plan.get('transport_version')=='document-boundaries-v5' else chunks.BOUNDARY_CONTRACT if plan.get('transport_version') in ('document-boundaries-v4','document-boundaries-v5') else chunks.SPAN_CONTRACT): fail('切片范围输出合同不符')
            value = chunks.validate_result(data, envelope["result"])
            mapped = validate_agent_window(window=data, raw_result=value, segments=spec["segments"],
                expected_input_sha256=spec["input_sha256"], expected_manifest_sha256=spec["manifest_sha256"])
            if plan.get('transport_version') in ('xlsx-packed-cells-v1','xlsx-packed-cell-spans-v2','document-spans-v3','document-boundaries-v4','document-boundaries-v5','xlsx-records-v6'): mapped = _bind_excel_sources(mapped, spec)
            bound.extend(mapped["chunks"])
            refs["window:" + key] = value
        else:
            if plan.get('transport_version') in ('xlsx-packed-cell-spans-v2','document-spans-v3','document-boundaries-v4','document-boundaries-v5','xlsx-records-v6') and provenance.get('overview_wire_contract')!=('knowledge-overview-bounded-v3' if plan.get('transport_version') in ('document-boundaries-v5','xlsx-records-v6') else 'knowledge-overview-compact-v2'): fail('紧凑归纳合同不符')
            if provenance.get("overview_contract_version") != overview.OVERVIEW_CONTRACT_VERSION: fail("整文归纳合同不符")
            refs[spec["reference_id"]] = overview.validate_overview(data, envelope["result"])
        if any(r["execution_id"] == receipt["execution_id"] for r in receipts): fail("不同步骤复用了同一执行凭据")
        receipts.append({"step_id": key, "execution_id": receipt["execution_id"], "result_sha256": receipt["result_sha256"]})
    if until is not None: fail("步骤不属于固定计划")
    final_ref=plan['budget']['final_reference_id']
    final=refs[final_ref]
    if plan.get('single_window_overview') is True:
        if len(plan['windows'])!=1 or final_ref!='window:'+plan['windows'][0]['input']['window_id']:fail('完整窗口概述来源不符')
        # Reuse the actual full-window generation overview after independent
        # review, with explicit provenance; do not fabricate a summary receipt.
        final=overview.validate_overview({'document_version_id':plan['document_version_id'],'summary_id':'reviewed_single_window','items':[overview.summary_item(final_ref,final)]},
            {'overview':final['overview'],'overview_evidence_ids':[final_ref],'topics':[{'text':t,'evidence_ids':[final_ref]} for t in final['topics']]})
    return final, bound, receipts


async def execute_semantic_step(*, plan, step_id, read_step, context, session, finalizer=None,format_feedback=None):
    """ONE model effect; call only inside 01's durable issued operation."""
    require_finalizer(finalizer, context=context, session=session, plan=plan)
    data, _, _, _ = validated_steps(plan, read_step=read_step, context=context, session=session, until=step_id)
    common = dict(model_config_id=plan["model_config_id"], expected_model_version=plan["model_version"],
        context_budget=plan["budget"]["per_call_context_budget"], output_tokens=plan["budget"]["per_call_output_tokens"], context=context, session=session,
        document_policy=plan.get('model_invocation'),bounded_labels=plan.get('transport_version') in ('document-boundaries-v5','xlsx-records-v6'),**({'capture_validation':True,'format_feedback':format_feedback} if plan.get('format_corrections') is not None else {}))
    task_args={'task_structure':plan.get('task_structure_contract')==semantic_items.TASK_CONTRACT}
    if step_id.startswith(('audit_','reaudit_')) and plan.get('content_audit_contract')==semantic_content_audit.CONTRACT:
        result=await semantic_content_audit.generate(window=data,prompt_override=plan['prompt'],**task_args,**common)
    elif step_id.startswith('repair_') and plan.get('content_repair_contract')==semantic_repairs.CONTRACT:
        result=await semantic_repairs.generate(window=data,prompt_override=plan['prompt'],**task_args,**common)
    elif step_id=='relations_full' and plan.get('relation_contract')==semantic_relations.CONTRACT:
        result=await semantic_relations.generate(window=data,prompt_override=plan['prompt'],**common)
    elif plan.get('transport_version')==item_windows.TRANSPORT and (step_id in plan['budget']['window_ids'] or step_id.startswith(('audit_','reaudit_'))):
        result=await semantic_items.generate(window=data,prompt_override=plan['prompt'],audit=step_id.startswith(('audit_','reaudit_')),compact_output=plan.get('compact_output_contract') in (semantic_compact.CONTRACT,semantic_compact.SCOPE_CONTRACT),scope_inheritance=plan.get('compact_output_contract')==semantic_compact.SCOPE_CONTRACT,**task_args,**common)
    elif step_id in plan["budget"]["window_ids"]:
        result = await (table_records.generate_records if plan.get('transport_version')=='xlsx-records-v6' else chunks.generate_chunks)(window=data, prompt_override=plan["prompt"], span_only=plan.get('transport_version') in ('xlsx-packed-cell-spans-v2','document-spans-v3'),boundary_only=plan.get('transport_version') in ('document-boundaries-v4','document-boundaries-v5'), **common)
    else:
        result = await overview.generate_overview(data=data, prompt_override=plan["overview_prompt"], compact=plan.get('transport_version') in ('xlsx-packed-cell-spans-v2','document-spans-v3','document-boundaries-v4','document-boundaries-v5','xlsx-records-v6'), **common)
    revalidate_document(plan, context, session)
    return result


def finalize_document(*, plan, read_step, context, session, finalizer=None):
    require_finalizer(finalizer, context=context, session=session, plan=plan)
    revalidate_document(plan, context, session)
    final, semantic, receipts = validated_steps(plan, read_step=read_step, context=context, session=session)
    payload = _draft_payload(plan, final, semantic, receipts)
    with session.begin_nested():
        revalidate_document(plan, context, session)
        result = finalizer(operation="commit_draft", context=context, session=session, plan=plan, payload=payload)
        if (not isinstance(result, dict) or result.get("id") != plan["document_version_id"]
                or result.get("status") != "validated" or result.get("requires_publish") is not True
                or result.get("manual_publish_required") is not True):
            fail("草稿提交器未返回正确待发布版本，已撤销本次写入")
    # Caller commits in the same central terminal transaction; never publish here.
    return result


def _draft_payload(plan, final, semantic, receipts):
    """Exact original commit payload, also reconstructed for completed reads."""
    if not semantic or len(semantic) > MAX_CHUNKS: fail("完整模型切片数量超限，未生成半份草稿")
    materialized = []
    for item in semantic:
        # Unit IDs contain the original segment offset; spans remain exact.
        start = (0 if item.get('source_spans')
                 else int(item["unit_id"].rsplit("_", 1)[1])) + item["offset_start"]
        end = start + len(item["text"])
        ref = f"document:{plan['file_id']}:{_locator_text(item['locator'])}:chars:{start}-{end}"
        extra = {}
        if item.get('source_spans'):
            ref = f"document:{plan['file_id']}:{_locator_text(item['locator'])}:transport:{item['unit_id']}:chars:{start}-{end}"
            extra['source_spans'] = [{**s, 'source_ref': f"document:{plan['file_id']}:{_locator_text(s['locator'])}:chars:{s['offset_start']}-{s['offset_end']}"}
                for s in item['source_spans']]
        materialized.append({**item, **extra, "source_ref": ref, "original_source_ref": ref,
            "offset_start": start, "offset_end": end, "strategy": "agent"})
    return {"chunks": materialized, "semantic_overview": final, "execution_receipts": receipts,
        "overview": {"topic": final["topics"][0]["text"], "summary": final["overview"]}}
