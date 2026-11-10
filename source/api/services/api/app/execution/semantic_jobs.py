"""Private document plans and committed-step receipts on the existing engine.

No queue, model DTO, execution identity or receipt is accepted from an HTTP body.
The knowledge finalizer must be explicitly mounted before admission is possible.
"""
import hashlib
import json
import time
from copy import deepcopy
from sqlalchemy import select
from app.db import utc_now,get_resource
from app.errors import AppError
from app.execution.models import ExecutionRun, ExecutionBlob, ExecutionStep
from app.execution.storage import encode, read_blob
from app.agents.builtin_chunker import digest

# Trusted startup registration only; never persisted or accepted from a client.
_finalizer = None
_domain_finalizer = None
FINALIZER_CONTRACT='knowledge-semantic-finalizer-v2'


def register_finalizer(callback,*,domain_finalizer):
    global _finalizer,_domain_finalizer
    if not callable(callback) or not callable(domain_finalizer) or _finalizer is not None:
        raise RuntimeError('A single server-owned finalizer must be registered')
    _finalizer,_domain_finalizer = callback,domain_finalizer


def require_finalizer():
    if _finalizer is None or _domain_finalizer is None:
        raise AppError('SEMANTIC_FINALIZER_UNAVAILABLE', '完整文档草稿审核存储尚未接入，未入队或发送模型请求', 503)


def check_finalizer(*,plan,context,session):
    require_finalizer()
    value=_domain_finalizer(operation='check_ready',plan=plan,payload=None,context=context,session=session)
    if not isinstance(value,dict) or value.get('contract_version')!=FINALIZER_CONTRACT or value.get('ready') is not True:
        raise AppError('SEMANTIC_FINALIZER_UNAVAILABLE','当前来源草稿提交能力未就绪，未发送模型请求',503)


def install_finalizer():
    from functools import partial
    from app.knowledge.semantic_document import finalize_document
    from app.knowledge.semantic_finalizer import finalize_draft,CONTRACT_VERSION
    if CONTRACT_VERSION!=FINALIZER_CONTRACT:raise RuntimeError('Incompatible document finalizer contract')
    register_finalizer(partial(finalize_document,finalizer=finalize_draft),domain_finalizer=finalize_draft)
    from app.knowledge.slicing_asset_downloads import install as install_asset_downloads
    install_asset_downloads()
    import os
    if os.environ.get('FINANCE_SLICING_OCR_QUALIFICATION'):
        from app.knowledge.slicing_winrt_ocr import install_qualified
        from app.settings import settings
        install_qualified(os.environ['FINANCE_SLICING_OCR_QUALIFICATION'],data_root=settings.data_dir)


def execution_available():
    return _finalizer is not None and _domain_finalizer is not None


def stage_ids(plan):
    if plan.get('recipe_version')=='portable-slicing-1.0.0':
        from app.knowledge.slicing_document import slot_ids
        return slot_ids(plan)
    return plan['budget']['window_ids']+[s['summary_id'] for s in plan['budget']['summary_steps']]


def persist_plan(session, run, plan, builtin_snapshot, context):
    """Called by trusted admission before its one run+authorization commit."""
    require_finalizer()
    from app.execution.builtin_agents import assert_snapshot
    from app.knowledge.semantic_document import revalidate_document
    assert_snapshot(session,context,builtin_snapshot)
    check_finalizer(plan=plan,context=context,session=session)
    if plan.get('finalizer_contract_version')!=FINALIZER_CONTRACT:
        raise AppError('SEMANTIC_PLAN_INVALID','固定计划草稿提交合同不符',409)
    if plan.get('recipe_version')=='portable-slicing-1.0.0':
        from app.knowledge.slicing_document import source_guard
        source_guard(plan,context=context,session=session)
    else:revalidate_document(plan,context,session)
    budget=plan['budget'];ids=stage_ids(plan)
    if (len(ids)!=len(set(ids)) or len(ids)+(plan.get('format_corrections') or {}).get('max_new_calls',0)!=budget['max_model_calls']
            or not 2<=len(ids)<=127
            or budget['reserved_total_tokens']!=budget['max_model_calls']*budget['per_call_context_budget'] or budget['max_model_calls']>127):
        raise AppError('SEMANTIC_PLAN_INVALID','文档固定计划或总预算无效',409)
    wrapper={'plan':deepcopy(plan),'builtin_snapshot':deepcopy(builtin_snapshot),
        'run_id':run['id'],'actor':[context.tenant_id,context.workspace_id,context.operator_id],
        'reservation':{'model_calls':budget['max_model_calls'],'total_tokens':budget['reserved_total_tokens']}}
    body=encode(wrapper);ref=hashlib.sha256((run['id']+':semantic-plan:'+body).encode()).hexdigest()
    session.add(ExecutionBlob(id=ref,run_id=run['id'],payload=body,created_at=utc_now()))
    run['semantic_job']={'job_id':plan['job_id'],'plan_ref':ref,'plan_sha256':digest(wrapper)}


def load_plan(session, context, run, *, revalidate=True, completed_read=False, historical_metadata=False):
    marker=run.get('semantic_job') or {}
    row=session.get(ExecutionBlob,marker.get('plan_ref'))
    if row is None or row.run_id!=run['id']:
        raise AppError('SEMANTIC_PLAN_INVALID','文档任务缺少服务器固定计划',409)
    wrapper=json.loads(row.payload)
    if (wrapper.get('run_id')!=run['id'] or digest(wrapper)!=marker.get('plan_sha256')
            or wrapper.get('actor')!=[context.tenant_id,context.workspace_id,context.operator_id]
            or wrapper['plan']['job_id']!=marker.get('job_id')):
        raise AppError('SEMANTIC_PLAN_INVALID','任务主体或固定计划不符',409)
    if historical_metadata and run.get('status') not in {'failed','cancelled','outcome_unknown'}:
        raise AppError('SEMANTIC_HISTORY_READ_INVALID','活动任务不能使用历史只读检查',409)
    if not completed_read and not historical_metadata:
        from app.execution.builtin_agents import assert_snapshot
        assert_snapshot(session,context,wrapper['builtin_snapshot'])
    if wrapper['plan'].get('finalizer_contract_version')!=FINALIZER_CONTRACT:
        raise AppError('SEMANTIC_PLAN_INVALID','固定计划草稿提交合同不符',409)
    if historical_metadata:
        # Configuration changes must not erase the reason a past request failed.
        # This branch never authorizes execution, continuation or a success DTO.
        from app.agents.model_adapter import load_config
        from app.knowledge.semantic_document import _source,_validate_source_plan
        plan=wrapper['plan'];load_config(plan['model_config_id'],context=context,session=session)
        root,file,content=_source(plan['document_id'],context,session)
        if file['id']!=plan['file_id'] or file['sha256']!=plan['source_sha256']:
            raise AppError('SEMANTIC_HISTORY_READ_INVALID','历史来源与固定任务不符',409)
        if plan.get('recipe_version')=='portable-slicing-1.0.0':
            from app.execution.builtin_agents import profile
            current=profile(session,context,plan.get("specialist_agent_id","builtin_knowledge_chunker"))
            from app.knowledge.slicing_document import require_plan_scope
            require_plan_scope(plan,current,root,file)
            registry=plan['registry']
            if registry['source_sha256']!=file['sha256'] or registry['registry_sha256']!=digest({k:registry[k] for k in ('sources','assets','occurrences')}):
                raise AppError('SEMANTIC_PLAN_INVALID','历史来源注册表不符',409)
        else:_validate_source_plan(plan,file,content)
        return wrapper
    if revalidate and not completed_read and run.get('status') in {'queued','running','resuming'}:
        # This canonical read guards continuation and every model/finalizer
        # preflight, including runs which already have an initial checkpoint.
        # Revalidate policy; never clamp/rewrite the plan or reset its deadline.
        from app.execution.document_job_budget import validated_time
        from app.settings import settings
        validated_time(wrapper['plan'],server_ceiling_seconds=settings.document_job_timeout_max_seconds)
    if completed_read:
        if run.get('status')!='succeeded':raise AppError('SEMANTIC_COMPLETED_READ_INVALID','只有真实成功任务可读取完成结果',409)
        from app.execution.semantic_read_receipt import make_completed_readers
        from app.knowledge.semantic_completed_read import validate_completed_read
        read_completion,read_step=make_completed_readers(run['id'])
        if wrapper['plan'].get('recipe_version')=='portable-slicing-1.0.0':
            from app.execution.slicing_read_receipt import validate_completed
            validate_completed(wrapper['plan'],read_step=read_step,read_completion=read_completion,context=context,session=session)
        else:validate_completed_read(plan=wrapper['plan'],read_step=read_step,read_completion=read_completion,context=context,session=session)
    elif revalidate:
        from app.knowledge.semantic_document import revalidate_document
        checked=wrapper['plan']
        completed=run.get('semantic_finalized')
        if completed:
            if completed!={'job_id':checked['job_id'],'document_version_id':checked['document_version_id']}:
                raise AppError('SEMANTIC_PLAN_INVALID','完整草稿终态不属于固定任务',409)
        if checked.get('recipe_version')=='portable-slicing-1.0.0':
            from app.knowledge.slicing_document import source_guard
            source_guard(checked,context=context,session=session)
        else:revalidate_document(checked,context,session)
    return wrapper


def make_step_reader(run_id, *, lease_owner):
    """04's exact callback, closed over this real run and active lease owner."""
    def read_step(*,session,context,job_id,step_id,expected_input_sha256):
        binding,run,wrapper=reader_state(session,context);plan=wrapper['plan']
        if job_id!=plan['job_id'] or step_id not in stage_ids(plan):
            raise AppError('SEMANTIC_STEP_INVALID','步骤不属于当前文档固定计划',409)
        if plan.get('format_corrections') is not None:
            from app.execution.semantic_corrections import read_step_chain
            return read_step_chain(session=session,binding=binding,run=run,plan=plan,step_id=step_id,expected_input_sha256=expected_input_sha256)
        key=f'semantic:{job_id}:model:{step_id}'
        execution_id=hashlib.sha256((run_id+':'+binding.dsl_hash+':'+key).encode()).hexdigest()
        row=session.get(ExecutionStep,execution_id)
        if (row is None or row.run_id!=run_id or row.node_key!=key
                or row.status!='succeeded' or not row.side_effect or not row.result_ref or not row.completed_at):
            raise AppError('SEMANTIC_STEP_NOT_COMMITTED','文档模型步骤尚未提交成功，禁止伪造或重放',409)
        before=read_blob(session,run_id,{'state_ref':row.input_ref})
        after=read_blob(session,run_id,{'state_ref':row.result_ref})
        metadata=(after.get('semantic_steps') or {}).get(step_id,{})
        envelope=metadata.get('envelope')
        if (before.get('semantic_plan_sha256')!=run['semantic_job']['plan_sha256']
                or after.get('semantic_plan_sha256')!=run['semantic_job']['plan_sha256']
                or metadata.get('execution_id')!=execution_id or metadata.get('issued') is not True
                or metadata.get('input_sha256')!=expected_input_sha256
                or not isinstance(envelope,dict) or metadata.get('result_sha256')!=digest(envelope)
                or type(metadata.get('lease_generation')) is not int
                or not 1<=metadata['lease_generation']<=binding.generation
                or metadata.get('reserved_context_tokens')!=plan['budget']['per_call_context_budget']
                or binding.model_calls>wrapper['reservation']['model_calls']):
            raise AppError('SEMANTIC_RECEIPT_INVALID','执行账本、输入、租约或预算凭据不符',409)
        return {'execution_id':execution_id,'result_sha256':digest(envelope),'envelope':deepcopy(envelope)}
    from app.execution.receipt_batch import install
    reader_state=install(read_step,run_id=run_id,lease_owner=lease_owner,load_plan=load_plan)
    return read_step


def model_network_snapshot(config):
    from app.model_catalog.public_resolver import POLICY,MODE
    from urllib.parse import urlsplit
    import ipaddress
    parsed=urlsplit(config.base_url)
    try:ipaddress.ip_address(parsed.hostname);literal=True
    except ValueError:literal=False
    trusted=POLICY.mode==MODE and parsed.scheme=='https' and not config.allow_private_network and not literal
    return {'policy_sha256':POLICY.sha256,'route':'trusted-public-https' if trusted else 'legacy-system',
            'resolver_allowance_seconds':POLICY.resolution_budget_seconds if trusted else 0}


def assert_model_network_policy(plan,config):
    network=model_network_snapshot(config)
    fixed=plan.get('model_network_policy')
    # Old system plans remain readable, but cannot gain a trusted public route.
    if fixed!=network and (fixed is not None or network['route']!='legacy-system'):
        raise AppError('MODEL_NETWORK_POLICY_CHANGED','模型解析策略与此运行固定快照不符，未签发调用',409)
    spec=plan.get('time_budget') or {}
    if spec.get('resolver_allowance_seconds',0)!=network['resolver_allowance_seconds']:
        raise AppError('MODEL_NETWORK_POLICY_CHANGED','固定时限没有预约当前解析策略时间，未签发调用',409)


def preflight_semantic_model(plan,step_id,data,*,context,session,format_feedback=None):
    """Pure model/config/payload checks, before durable issued intent.

    Reuses the exact domain payload builders; no source truncation, DNS, secret
    read or inference. Send-time checks remain authoritative and are not cached.
    """
    from app.agents.model_adapter import load_config
    from app.agents.model_requirements import preflight_model_requirements
    from app.agents import builtin_chunker as chunker,document_overview as overview
    config=load_config(plan['model_config_id'],context=context,session=session,expected_version=plan['model_version'])
    assert_model_network_policy(plan,config)
    budget=plan['budget'];context_budget=budget['per_call_context_budget'];output_tokens=budget['per_call_output_tokens']
    task_structure=plan.get('task_structure_contract')=='semantic-task-structure-v1'
    if step_id.startswith(('audit_','reaudit_')) and plan.get('content_audit_contract')=='knowledge-semantic-audit-v2':
        from app.agents.semantic_content_audit import prepare_request
        _,fmt=prepare_request(window=data,prompt=plan['prompt'],config=config,context_budget=context_budget,output_tokens=output_tokens,format_feedback=format_feedback,task_structure=task_structure)
    elif step_id.startswith('repair_') and plan.get('content_repair_contract')=='knowledge-semantic-repair-v1':
        from app.agents.semantic_repairs import prepare_request
        _,fmt=prepare_request(window=data,prompt=plan['prompt'],config=config,context_budget=context_budget,output_tokens=output_tokens,format_feedback=format_feedback,task_structure=task_structure)
    elif step_id=='relations_full' and plan.get('relation_contract')=='knowledge-semantic-relations-v1':
        from app.agents.semantic_relations import prepare_request
        _,fmt=prepare_request(window=data,prompt=plan['prompt'],config=config,context_budget=context_budget,output_tokens=output_tokens,format_feedback=format_feedback)
    elif plan.get('transport_version')=='document-semantic-items-v7' and (step_id in budget['window_ids'] or step_id.startswith(('audit_','reaudit_'))):
        from app.agents.semantic_items import prepare_request
        _,fmt=prepare_request(window=data,prompt=plan['prompt'],config=config,context_budget=context_budget,output_tokens=output_tokens,format_feedback=format_feedback,audit=step_id.startswith(('audit_','reaudit_')),task_structure=task_structure,compact_output=plan.get('compact_output_contract') in ('semantic-compact-items-v1','semantic-compact-scoped-items-v2'),scope_inheritance=plan.get('compact_output_contract')=='semantic-compact-scoped-items-v2')
    elif step_id in budget['window_ids']:
        prompt=plan['prompt']
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>8000:
            raise AppError('CHUNK_AGENT_PROMPT_INVALID','切片任务提示须为1至8000个字符',422)
        parsed=chunker.ChunkWindow.model_validate(data).model_dump(mode='json')
        from app.agents.table_record_chunker import prepare_record_request
        _,fmt=(prepare_record_request if plan.get('transport_version')=='xlsx-records-v6' else chunker.prepare_chunk_request)(window=parsed,prompt=prompt,config=config,context_budget=context_budget,output_tokens=output_tokens,span_only=plan.get('transport_version') in ('xlsx-packed-cell-spans-v2','document-spans-v3'),boundary_only=plan.get('transport_version') in ('document-boundaries-v4','document-boundaries-v5'),format_feedback=format_feedback,bounded_labels=plan.get('transport_version') in ('document-boundaries-v5','xlsx-records-v6'))
    else:
        parsed=overview.OverviewInput.model_validate(data).model_dump(mode='json')
        _,fmt=overview.prepare_overview_request(data=parsed,prompt=plan['overview_prompt'],config=config,context_budget=context_budget,output_tokens=output_tokens,compact=plan.get('transport_version') in ('xlsx-packed-cell-spans-v2','document-spans-v3','document-boundaries-v4','document-boundaries-v5','xlsx-records-v6'),format_feedback=format_feedback,bounded_labels=plan.get('transport_version') in ('document-boundaries-v5','xlsx-records-v6'))
    return preflight_model_requirements(model_config_id=plan['model_config_id'],expected_version=plan['model_version'],
        parameters={'max_tokens':output_tokens},context_budget=context_budget,response_format=fmt,context=context,session=session,
        document_policy=plan.get('model_invocation'))


class SemanticCompiler:
    def __init__(self,run,bridge):
        self.run,self.bridge=run,bridge

    def compile(self,saver):
        from langgraph.graph import StateGraph,START,END
        from app.execution.contracts import ExecutionState,ExecutionContext
        # Compile only immutable private server plan; authority is checked again
        # by every preflight, operation and cached read, not by this graph shape.
        from app.db import SessionLocal
        from app.execution.storage import authorize
        with SessionLocal() as session:
            context,_,run=authorize(session,self.run['id'])
            plan=load_plan(session,context,run)['plan']
        graph=StateGraph(ExecutionState,context_schema=ExecutionContext)
        previous=START
        for step_id in stage_ids(plan):
            if plan.get('format_corrections') is not None:
                from app.execution.semantic_corrections import correction_node
                graph.add_node(step_id,correction_node(self,plan,step_id));graph.add_edge(previous,step_id);previous=step_id;continue
            async def node(state,step_id=step_id):
                key=f'semantic:{plan["job_id"]}:model:{step_id}'
                reader=make_step_reader(self.run['id'],lease_owner=self.bridge.lease_owner)
                async def preflight(execution):
                    _,binding,current=authorize(execution.session,execution.run_id)
                    pinned=load_plan(execution.session,execution.context,current)
                    if binding.model_calls>=pinned['reservation']['model_calls']:
                        raise AppError('CHUNK_JOB_BUDGET_EXCEEDED','文档预约调用数已耗尽',422)
                    from app.knowledge.semantic_document import validated_steps
                    data,_,_,_=validated_steps(pinned['plan'],read_step=reader,context=execution.context,session=execution.session,until=step_id)
                    preflight_semantic_model(pinned['plan'],step_id,data,context=execution.context,session=execution.session)
                    from app.execution.document_request_wait import prepare_document_address
                    await prepare_document_address(pinned['plan'], execution)
                async def operation(execution):
                    _,binding,current=authorize(execution.session,execution.run_id)
                    pinned=load_plan(execution.session,execution.context,current)
                    from app.knowledge.semantic_document import validated_steps
                    data,_,_,_=validated_steps(pinned['plan'],read_step=reader,context=execution.context,session=execution.session,until=step_id)
                    from app.execution.document_request_wait import document_wait
                    with document_wait(pinned['plan'],execution):
                        envelope=await execution.invoke('app.knowledge.semantic_document','execute_semantic_step',
                            plan=pinned['plan'],step_id=step_id,read_step=reader,finalizer=_domain_finalizer)
                    # The bridge has already persisted this exact issued intent.
                    from app.execution.effects import get_effect_context
                    effect=get_effect_context()
                    if effect is None or effect.execution_id!=execution.execution_id:
                        raise AppError('SEMANTIC_RECEIPT_INVALID','缺少真实模型副作用上下文',409)
                    snapshot=execution.state
                    snapshot['semantic_plan_sha256']=current['semantic_job']['plan_sha256']
                    snapshot.setdefault('semantic_steps',{})[step_id]={
                        'execution_id':execution.execution_id,'issued':True,'lease_generation':binding.generation,
                        'input_sha256':digest(data),'result_sha256':digest(envelope),
                        'reserved_context_tokens':pinned['plan']['budget']['per_call_context_budget'],'envelope':envelope}
                    return snapshot
                return await self.bridge.step(state,node_key=key,operation=operation,side_effect=True,preflight=preflight)
            graph.add_node(step_id,node);graph.add_edge(previous,step_id);previous=step_id
        # No finalization can be reached without a supported startup registration.
        async def finalize(state):
            require_finalizer()
            reader=make_step_reader(self.run['id'],lease_owner=self.bridge.lease_owner)
            async def preflight(execution):
                _,_,current=authorize(execution.session,execution.run_id)
                pinned=load_plan(execution.session,execution.context,current)
                from app.knowledge.semantic_document import validated_steps
                validated_steps(pinned['plan'],read_step=reader,context=execution.context,session=execution.session)
            async def operation(execution):
                from app.db import save_resource
                # Keep the domain savepoint inside a real outer SQLite writer;
                # releasing its savepoint must not commit ahead of lease fencing.
                if execution.session.bind.dialect.name=='sqlite':
                    connection=execution.session.connection()
                    if not connection.connection.driver_connection.in_transaction:
                        connection.exec_driver_sql('BEGIN IMMEDIATE')
                _,_,current=authorize(execution.session,execution.run_id)
                pinned=load_plan(execution.session,execution.context,current)
                # The callback MUST use this same session and never commit.
                draft=_finalizer(plan=pinned['plan'],read_step=reader,context=execution.context,session=execution.session)
                if (draft['id']!=pinned['plan']['document_version_id'] or draft.get('requires_publish') is not True
                        or draft.get('manual_publish_required') is not True or draft.get('status')!='validated'
                        or draft.get('finalizer_contract_version')!=FINALIZER_CONTRACT):
                    raise AppError('SEMANTIC_FINALIZER_INVALID','完整草稿未保留固定版本与人工显式发布',409)
                current['semantic_finalized']={'job_id':pinned['plan']['job_id'],'document_version_id':draft['id']}
                save_resource(execution.session,'run',execution.run_id,current,execution.context)
                snapshot=execution.state
                from app.knowledge.semantic_completed_read import completed_version_sha256
                from app.db import get_resource
                persisted_version=get_resource(execution.session,'knowledge_version',draft['id'],execution.context)
                snapshot['semantic_finalize_receipt']={'contract_version':'semantic-committed-finalize-v1',
                    'run_id':execution.run_id,'job_id':pinned['plan']['job_id'],'document_version_id':draft['id'],
                    'execution_id':execution.execution_id,'plan_sha256':digest(pinned['plan']),
                    'version_snapshot_sha256':completed_version_sha256(persisted_version)}
                snapshot['frames']['root']['outputs']={'job_id':pinned['plan']['job_id'],
                    'document_id':pinned['plan']['document_id'],'document_version_id':draft['id'],
                    'status':'validated','requires_publish':True,'manual_publish_required':True}
                return snapshot
            return await self.bridge.step(state,node_key=f'semantic:{plan["job_id"]}:finalize',operation=operation,side_effect=True,preflight=preflight)
        graph.add_node('finalize',finalize);graph.add_edge(previous,'finalize');graph.add_edge('finalize',END)
        return graph.compile(checkpointer=saver)


def compiler_for_run(run,bridge):
    from app.execution.compiler import ProductCompiler
    if run.get('semantic_job'):
        from app.db import SessionLocal
        from app.execution.storage import authorize
        with SessionLocal() as session:
            context,_,current=authorize(session,run['id'])
            plan=load_plan(session,context,current)['plan']
        if plan.get('recipe_version')=='portable-slicing-1.0.0':
            from app.execution.slicing_jobs import PortableSlicingCompiler
            return PortableSlicingCompiler(run,bridge)
        return SemanticCompiler(run,bridge)
    return ProductCompiler(run,bridge)


def completed_state_ref(session,context,run):
    """Graph scheduler idleness is not proof that the whole draft committed."""
    wrapper=load_plan(session,context,run)
    binding=session.get(ExecutionRun,run['id'],populate_existing=True)
    key=f'semantic:{wrapper["plan"]["job_id"]}:finalize'
    identifier=hashlib.sha256((run['id']+':'+binding.dsl_hash+':'+key).encode()).hexdigest()
    row=session.get(ExecutionStep,identifier)
    if row is None or row.status!='succeeded':return None
    if row.run_id!=run['id'] or row.node_key!=key or not row.side_effect or not row.result_ref or not row.completed_at:
        raise AppError('SEMANTIC_RECEIPT_INVALID','完整草稿最终账本无效',409)
    state=read_blob(session,run['id'],{'state_ref':row.result_ref})
    output=state.get('frames',{}).get('root',{}).get('outputs',{})
    if (state.get('semantic_plan_sha256')!=run['semantic_job']['plan_sha256']
            or output.get('document_version_id')!=wrapper['plan']['document_version_id']
            or output.get('job_id')!=wrapper['plan']['job_id']
            or output.get('requires_publish') is not True or output.get('manual_publish_required') is not True):
        raise AppError('SEMANTIC_RECEIPT_INVALID','完整草稿最终状态不属于固定计划',409)
    return {'state_ref':row.result_ref}


def _document_model_choice(model_config_id,model_config_version,model_execution_mode):
    choice=None
    if model_config_id is not None or model_config_version is not None or model_execution_mode is not None:
        if not model_config_id or not model_config_version:raise AppError('DOCUMENT_MODEL_CHOICE_INVALID','本次模型必须明确选择模型及其版本',422)
        choice={'model_config_id':model_config_id,'model_config_version':model_config_version,'model_execution_mode':model_execution_mode or 'model_default'}
    return choice


def _plan_document(*,document_id,expected_revision,context,session,max_model_calls,max_total_tokens,context_budget,
                   output_tokens,job_timeout_seconds,choice,agent_id):
    from app.execution.builtin_agents import execution_snapshot
    from app.knowledge.semantic_document import prepare_document
    snapshot=execution_snapshot(session,context,document_model_choice=choice,agent_id=agent_id)
    if snapshot['profile_revision']!=expected_revision:
        raise AppError('BUILTIN_CONFIG_CONFLICT','切片配置已变，请刷新后提交',409)
    from app.agents.model_adapter import load_config
    configured=load_config(snapshot['model_config_id'],expected_version=snapshot['model_config_version'],context=context,session=session)
    if context_budget is None:
        if type(max_model_calls) is not int or not 2<=max_model_calls<=127 or type(max_total_tokens) is not int or max_total_tokens<1024:raise AppError('SLICING_BUDGET_INVALID','文档任务调用及总预算无效',422)
        context_budget=min(900000,configured.context_window,max_total_tokens//max_model_calls)
    if output_tokens is None:output_tokens=min(131072,configured.max_output_tokens)
    from app.knowledge.service import entry as document_entry
    direct_root=document_entry('knowledge_document',document_id,context,session,write=True)
    from app.knowledge.service import raw_file as document_file
    direct_kind=document_file(direct_root,context,session)['format']
    from app.imports.document_container import RULE_FORMATS
    layout_required=direct_kind not in RULE_FORMATS.values()
    if direct_kind in ('docx','pdf','xlsx') and snapshot.get('recipe_version')!='portable-slicing-1.0.0':
        from app.knowledge.semantic_document import _source
        from app.knowledge.slicing_parser import parse_document,IMAGE_LAYOUT_V2
        _,file,content=_source(document_id,context,session)
        layout=parse_document(content,direct_kind,file_id=file['id'],version_sha256=file['sha256'],perform_ocr=False,image_layout_version=IMAGE_LAYOUT_V2)
        layout_required=bool(layout['occurrences'] or layout['warnings']) or sum(len(source['text']) for source in layout['sources'].values())>8000 or direct_kind=='xlsx' and len(layout['sources'])>64
        if not layout_required:
            from app.imports.documents import _segments
            lines=lambda texts:[line.strip() for text in texts for line in text.splitlines() if line.strip()]
            # The short text protocol is eligible only when it retains the
            # complete layout text. Headers, nested textboxes, revisions or
            # other omitted objects must never disappear through fallback.
            plain=lines(text for text,_ in _segments(content,direct_kind))
            full=lines(source['text'] for source in layout['sources'].values())
            layout_required=plain!=full or any(source['source_state']!='current' or source['metadata'].get('presentation_hidden') for source in layout['sources'].values())
    if snapshot.get('recipe_version')=='portable-slicing-1.0.0' or layout_required:
        from app.knowledge.slicing_document import prepare_document as prepare_portable
        plan=prepare_portable(document_id=document_id,snapshot=snapshot,context=context,session=session,
            max_model_calls=max_model_calls,max_total_tokens=max_total_tokens,context_budget=context_budget,output_tokens=output_tokens,current_document_scope=snapshot.get('recipe_version')!='portable-slicing-1.0.0' or agent_id!='builtin_knowledge_chunker')
    else:
        plan=prepare_document(document_id=document_id,model_config_id=snapshot['model_config_id'],
            prompt_override=snapshot['prompt_override'],context=context,session=session,
            max_model_calls=max_model_calls,max_total_tokens=max_total_tokens,
            context_budget=context_budget,output_tokens=output_tokens,finalizer=_domain_finalizer,
            model_execution_mode=snapshot.get('model_execution_mode','auto'))
    plan['document_context_budget_contract']='signed-document-context-reservation-v1'
    from app.execution.document_job_budget import reserve_time
    from app.settings import settings
    plan['model_network_policy']=model_network_snapshot(configured)
    plan['time_budget']=reserve_time(model_calls=plan['budget']['max_model_calls'],provider_timeout_seconds=configured.timeout_seconds,
        requested_seconds=job_timeout_seconds,server_ceiling_seconds=settings.document_job_timeout_max_seconds,resolver_allowance_seconds=plan['model_network_policy']['resolver_allowance_seconds'],request_timeout_seconds=settings.document_model_timeout_seconds)
    plan['finalizer_contract_version']=FINALIZER_CONTRACT
    return plan,snapshot


def document_reservation(plan):
    budget=plan['budget']
    return {'model_calls':budget['max_model_calls'],'total_tokens':budget['reserved_total_tokens'],
            'job_timeout_seconds':plan['time_budget']['wall_time_seconds']}


def preview_document_budget(*,document_id,expected_revision,context,session,max_model_calls,max_total_tokens,
        context_budget,output_tokens,job_timeout_seconds,model_config_id,model_config_version,model_execution_mode,agent_id):
    require_finalizer()
    check_finalizer(plan={'document_id':document_id},context=context,session=session)
    plan,_=_plan_document(document_id=document_id,expected_revision=expected_revision,context=context,session=session,
        max_model_calls=max_model_calls,max_total_tokens=max_total_tokens,context_budget=context_budget,output_tokens=output_tokens,
        job_timeout_seconds=job_timeout_seconds,choice=_document_model_choice(model_config_id,model_config_version,model_execution_mode),
        agent_id=agent_id)
    from app.knowledge.slicing_document import minimum_model_calls
    return {'reservation':document_reservation(plan),
            'minimum_model_calls':minimum_model_calls(plan['source_groups']) if 'source_groups' in plan else None,
            'mode':'preview_only'}


async def submit_document(*,scheduler,document_id,expected_revision,context,session,
        max_model_calls=63,max_total_tokens=134217728,context_budget=None,output_tokens=None,job_timeout_seconds=None,
        model_config_id=None,model_config_version=None,model_execution_mode=None,agent_id="builtin_knowledge_chunker"):
    # Missing whole-draft capability refuses BEFORE extraction, reservation or cost.
    require_finalizer()
    from app.contracts import RunCreate,WorkflowDefinition
    check_finalizer(plan={'document_id':document_id},context=context,session=session)
    choice=_document_model_choice(model_config_id,model_config_version,model_execution_mode)
    async with scheduler.admission_lock:
        # A duplicate never reserves a second effect, including while the
        # provider outcome of a previous request is still unknown.
        from app.execution.document_activity import active_document_job
        existing=active_document_job(session,context,document_id)
        if existing is not None:
            if existing['status']=='outcome_unknown':
                raise AppError('DOCUMENT_JOB_OUTCOME_UNKNOWN', '此文档已有模型调用待核查，本次未创建新任务', 409,
                               {'job_id': existing['job_id'], 'run_id': existing['run_id']})
            if choice is not None or agent_id!="builtin_knowledge_chunker":
                previous=load_plan(session,context,get_resource(session,'run',existing['run_id'],context))['builtin_snapshot']
                if previous.get('document_model_choice')!=choice or previous.get('agent_id')!=agent_id:raise AppError('DOCUMENT_MODEL_CHOICE_CONFLICT','此文档已有不同模型的活动或待核查任务；未新增调用或切换其模型',409)
            return existing
        plan,snapshot=_plan_document(document_id=document_id,expected_revision=expected_revision,context=context,session=session,
            max_model_calls=max_model_calls,max_total_tokens=max_total_tokens,context_budget=context_budget,output_tokens=output_tokens,
            job_timeout_seconds=job_timeout_seconds,choice=choice,agent_id=agent_id)
        workflow=WorkflowDefinition(name='内置Agent文档处理',schemaVersion='2.0',
            variables={'semantic_plan_sha256':{'type':'string','default':digest(plan)}},
            limits={'max_steps':plan['budget']['max_model_calls']+1,'max_concurrency':1,'timeout_seconds':120},
            nodes=[{'id':'start','type':'start'},{'id':'answer','type':'answer','inputs':{}}],
            edges=[{'source':'start','target':'answer'}])
        # Document/model/source authorization above is its real authority.
        # A knowledge document job has no financial business-query dependency.
        request=RunCreate(workflow=workflow,prompt='执行内置文档处理任务',inputs={})
        run=await scheduler._submit(request,context,session,semantic_plan=plan,builtin_snapshot=snapshot)
        return {'job_id':plan['job_id'],'run_id':run['id'],'status':run['status'],
            'reservation':document_reservation(plan),
            'requires_publish':True,'manual_publish_required':True,'finalizer_contract_version':FINALIZER_CONTRACT}
