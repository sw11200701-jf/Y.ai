"""Server-only completed semantic read proof; never an execution/send permit."""
import hashlib,json
from copy import deepcopy
from sqlalchemy import select
from app.db import Resource
from app.auth import validate_context
from app.errors import AppError
from app.execution.models import ExecutionRun,ExecutionStep,ExecutionBlob
from app.agents.builtin_chunker import digest

CONTRACT='semantic-completed-read-receipt-v1'

def invalid():raise AppError('SEMANTIC_COMPLETED_READ_INVALID','完整文档任务的已提交读取凭据无效或不可访问',409)

def make_completed_readers(run_id):
    """04 callbacks are closed over a server-selected run, never HTTP data."""
    from app.db import SessionLocal
    with SessionLocal() as lookup:
        payload=lookup.scalar(select(Resource.payload).where(Resource.kind=='run',Resource.resource_id==run_id))
        value=json.loads(payload) if payload else {}
        marker=value.get('semantic_job') or {}
        bound=lookup.get(ExecutionBlob,marker.get('plan_ref'))
        private=json.loads(bound.payload) if bound and bound.run_id==run_id else {}
        if private.get('plan',{}).get('recipe_version')=='portable-slicing-1.0.0':
            from app.execution.slicing_read_receipt import make_readers
            return make_readers(run_id)
    def checked(session,context,job_id):
        validate_context(context,session)
        from app.rbac import require_any_permission,RUN_PERMISSIONS
        require_any_permission(context,session,RUN_PERMISSIONS)
        body=session.scalar(select(Resource.payload).where(Resource.kind=='run',Resource.resource_id==run_id,
            Resource.owner_id==context.operator_id,Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
        binding=session.get(ExecutionRun,run_id,populate_existing=True)
        if body is None or binding is None or (binding.principal_id,binding.tenant_id,binding.workspace_id)!=(context.operator_id,context.tenant_id,context.workspace_id):invalid()
        run=json.loads(body);marker=run.get('semantic_job') or {};row=session.get(ExecutionBlob,marker.get('plan_ref'))
        if run.get('status')!='succeeded' or row is None or row.run_id!=run_id:invalid()
        wrapper=json.loads(row.payload);plan=wrapper.get('plan',{})
        if (hashlib.sha256((run_id+':semantic-plan:'+row.payload).encode()).hexdigest()!=row.id
                or wrapper.get('run_id')!=run_id or wrapper.get('actor')!=[context.tenant_id,context.workspace_id,context.operator_id]
                or digest(wrapper)!=marker.get('plan_sha256') or marker.get('job_id')!=job_id or plan.get('job_id')!=job_id
                or plan.get('finalizer_contract_version')!='knowledge-semantic-finalizer-v2'
                or run.get('semantic_finalized')!={'job_id':job_id,'document_version_id':plan.get('document_version_id')}
                or binding.dsl_hash!=(run.get('engine') or {}).get('dsl_sha256')):invalid()
        from app.execution.storage import engine_key
        if binding.engine_key!=engine_key(run['engine']):invalid()
        from app.agents.model_adapter import load_config
        load_config(plan['model_config_id'],context=context,session=session)
        from app.execution.semantic_jobs import stage_ids
        ids=stage_ids(plan)
        correcting=plan.get('format_corrections') is not None
        if correcting:
            from app.execution.semantic_corrections import allowance
            if not len(ids)<=binding.model_calls<=len(ids)+allowance(plan) or wrapper.get('reservation',{}).get('model_calls')!=plan['budget']['max_model_calls']:invalid()
        elif binding.model_calls!=len(ids) or wrapper.get('reservation',{}).get('model_calls')!=len(ids):invalid()
        rows=list(session.scalars(select(ExecutionStep).where(ExecutionStep.run_id==run_id)))
        expected={f'semantic:{job_id}:model:{step}' for step in ids}|{f'semantic:{job_id}:finalize'}
        if (not correcting and ({r.node_key for r in rows}!=expected or len(rows)!=len(expected))) or any(r.status!='succeeded' or not r.side_effect or not r.result_ref or not r.completed_at for r in rows):invalid()
        def blob(ref):
            value=session.get(ExecutionBlob,ref)
            if value is None or value.run_id!=run_id or hashlib.sha256((run_id+':'+value.payload).encode()).hexdigest()!=value.id:invalid()
            return json.loads(value.payload)
        def step(key):
            identifier=hashlib.sha256((run_id+':'+binding.dsl_hash+':'+key).encode()).hexdigest();value=session.get(ExecutionStep,identifier)
            if value is None or value.run_id!=run_id or value.node_key!=key or value.status!='succeeded' or not value.side_effect:invalid()
            return value
        final=step(f'semantic:{job_id}:finalize');before=blob(final.input_ref);state=blob(final.result_ref);output=state.get('frames',{}).get('root',{}).get('outputs',{})
        expected_output={'job_id':job_id,'document_id':plan['document_id'],'document_version_id':plan['document_version_id'],'status':'validated','requires_publish':True,'manual_publish_required':True}
        proof=state.get('semantic_finalize_receipt',{})
        if correcting:
            from app.execution.semantic_corrections import actual_ids
            actual=actual_ids(plan,state);expected=set(actual)|{f'semantic:{job_id}:finalize'}
            if len(actual)!=binding.model_calls or {r.node_key for r in rows}!=expected or len(rows)!=len(expected) or before.get('semantic_attempts')!=state.get('semantic_attempts') or before.get('format_corrections_used',0)!=state.get('format_corrections_used',0):invalid()
        if (before.get('semantic_plan_sha256')!=marker['plan_sha256'] or state.get('semantic_plan_sha256')!=marker['plan_sha256']
                or before.get('semantic_steps')!=state.get('semantic_steps') or set(state.get('semantic_steps',{}))!=set(ids)
                or output!=expected_output or run.get('outputs')!=expected_output or binding.state_ref!=final.result_ref
                or proof.get('contract_version')!='semantic-committed-finalize-v1' or proof.get('run_id')!=run_id
                or proof.get('job_id')!=job_id or proof.get('document_version_id')!=plan['document_version_id']
                or proof.get('execution_id')!=final.execution_id or proof.get('plan_sha256')!=digest(plan)
                or not isinstance(proof.get('version_snapshot_sha256'),str) or len(proof['version_snapshot_sha256'])!=64):invalid()
        return run,binding,wrapper,plan,final,state,blob,step

    def read_completion(*,operation='read_committed_result',session,context,job_id,document_version_id,expected_plan_sha256):
        if operation!='read_committed_result':invalid()
        run,binding,wrapper,plan,final,state,blob,step=checked(session,context,job_id)
        if document_version_id!=plan['document_version_id'] or expected_plan_sha256!=digest(plan):invalid()
        internal={'contract_version':CONTRACT,'operation':operation,'run_id':run_id,'job_id':job_id,'document_version_id':document_version_id,
            'plan_sha256':digest(plan),'plan_wrapper_sha256':run['semantic_job']['plan_sha256'],
            'execution_id':final.execution_id,'result_sha256':digest(state),
            'version_snapshot_sha256':state['semantic_finalize_receipt']['version_snapshot_sha256'],
            'finalize_execution_id':final.execution_id,'final_state_ref':final.result_ref,'final_state_sha256':digest(state),
            'model_step_count':binding.model_calls,'read_only':True}
        # One exact adapter from central private proof to04's sealed read DTO.
        return {'contract_version':'knowledge-semantic-completed-read-v1',
            **{key:internal[key] for key in ('operation','job_id','document_version_id','plan_sha256','execution_id','result_sha256','version_snapshot_sha256')}}

    def read_step(*,session,context,job_id,step_id,expected_input_sha256):
        run,binding,wrapper,plan,final,state,blob,step=checked(session,context,job_id)
        from app.execution.semantic_jobs import stage_ids
        if step_id not in stage_ids(plan):invalid()
        if plan.get('format_corrections') is not None:
            from app.execution.semantic_corrections import read_step_chain
            return read_step_chain(session=session,binding=binding,run=run,plan=plan,step_id=step_id,expected_input_sha256=expected_input_sha256)
        row=step(f'semantic:{job_id}:model:{step_id}');before=blob(row.input_ref);after=blob(row.result_ref)
        metadata=(after.get('semantic_steps') or {}).get(step_id,{});envelope=metadata.get('envelope')
        if (before.get('semantic_plan_sha256')!=run['semantic_job']['plan_sha256'] or after.get('semantic_plan_sha256')!=run['semantic_job']['plan_sha256']
                or state['semantic_steps'].get(step_id)!=metadata or metadata.get('execution_id')!=row.execution_id
                or metadata.get('issued') is not True or metadata.get('input_sha256')!=expected_input_sha256
                or not isinstance(envelope,dict) or metadata.get('result_sha256')!=digest(envelope)
                or type(metadata.get('lease_generation')) is not int or not 1<=metadata['lease_generation']<=binding.generation
                or metadata.get('reserved_context_tokens')!=plan['budget']['per_call_context_budget']):invalid()
        return {'execution_id':row.execution_id,'result_sha256':digest(envelope),'envelope':deepcopy(envelope)}
    return read_completion,read_step
