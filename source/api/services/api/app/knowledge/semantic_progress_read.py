"""Read only committed window results; never claim a complete draft or execute."""
import hashlib,json
from sqlalchemy import select
from app.db import get_resource
from app.errors import AppError
from app.execution.models import ExecutionRun,ExecutionStep,ExecutionBlob
from app.agents.builtin_chunker import digest
from . import service as s

def progress(*,document_id,run_id,context,session,offset=0,limit=20):
    root=s.entry('knowledge_document',document_id,context,session,write=True)
    s.require_full_document_maintenance(root,context,session)
    run=get_resource(session,'run',run_id,context)
    binding=session.get(ExecutionRun,run_id)
    if not binding or (binding.principal_id,binding.tenant_id,binding.workspace_id)!=(context.operator_id,context.tenant_id,context.workspace_id):
        raise AppError('NOT_FOUND','处理记录不可访问',404)
    from app.execution.storage import engine_key
    if binding.dsl_hash!=run.get('engine',{}).get('dsl_sha256') or binding.engine_key!=engine_key(run['engine']):
        raise AppError('SEMANTIC_RECEIPT_INVALID','处理记录固定引擎不符',409)
    from app.execution.semantic_jobs import load_plan
    wrapper=load_plan(session,context,run,revalidate=False,historical_metadata=run['status'] in ('failed','cancelled','outcome_unknown'))
    plan=wrapper['plan']
    if plan.get('recipe_version')=='portable-slicing-1.0.0' or plan.get('format_corrections') is None or plan['document_id']!=document_id:
        raise AppError('NOT_FOUND','此处理记录不适用于文档窗口预览',404)
    from .semantic_document import _source,_validate_source_plan,_validated_results
    _,file,content=_source(document_id,context,session);_validate_source_plan(plan,file,content)
    if file['sha256']!=plan['source_sha256'] or file['id']!=plan['file_id']:
        raise AppError('SEMANTIC_RECEIPT_INVALID','来源与固定处理计划不符',409)
    from app.execution.semantic_corrections import read_step_chain,key
    def reader(*,session,context,job_id,step_id,expected_input_sha256):
        if job_id!=plan['job_id'] or step_id not in plan['budget']['window_ids']:
            raise AppError('SEMANTIC_STEP_INVALID','不是当前任务原文窗口',409)
        # Validate blob bytes as opaque hashes before the existing receipt and
        # source-coverage validators inspect their data. No live lease is minted.
        rows=list(session.scalars(select(ExecutionStep).where(ExecutionStep.run_id==run_id,
            ExecutionStep.node_key.in_([key(job_id,step_id,i) for i in range(3)]))))
        for step in rows:
            if step.status!='succeeded':continue
            for ref in (step.input_ref,step.result_ref):
                blob=session.get(ExecutionBlob,ref)
                if not blob or blob.run_id!=run_id or hashlib.sha256((run_id+':'+blob.payload).encode()).hexdigest()!=blob.id:
                    raise AppError('SEMANTIC_RECEIPT_INVALID','已提交窗口内容hash不符',409)
        return read_step_chain(session=session,binding=binding,run=run,plan=plan,step_id=step_id,expected_input_sha256=expected_input_sha256)
    completed=0
    for window in plan['windows']:
        try:
            reader(session=session,context=context,job_id=plan['job_id'],step_id=window['input']['window_id'],expected_input_sha256=digest(window['input']))
        except AppError as e:
            if e.code in ('SEMANTIC_STEP_NOT_COMMITTED','CHUNK_AGENT_OUTPUT_INVALID'):break
            raise
        completed+=1
    boundary=plan['windows'][completed]['input']['window_id'] if completed<len(plan['windows']) else plan['budget']['summary_steps'][0]['summary_id']
    _,_,chunks,_=_validated_results(plan,read_step=reader,context=context,session=session,until=boundary)
    # Recheck current source/authority before returning the preview to its owner.
    s.entry('knowledge_document',document_id,context,session,write=True)
    _,fresh_file,_=_source(document_id,context,session)
    if fresh_file['sha256']!=plan['source_sha256']:raise AppError('SEMANTIC_RECEIPT_INVALID','核验期间来源已变化',409)
    fresh=load_plan(session,context,run,revalidate=False,historical_metadata=run['status'] in ('failed','cancelled','outcome_unknown'))
    if digest(fresh)!=digest(wrapper):raise AppError('SEMANTIC_RECEIPT_INVALID','核验期间处理计划已变化',409)
    return {'contract':'committed-window-preview-v1','run_id':run_id,'document_id':document_id,
        'status':run['status'],'completed_windows':completed,'total_windows':len(plan['windows']),
        'total_chunks':len(chunks),'items':[{k:c[k] for k in ('unit_id','topic','summary','text','locator') if k in c} for c in chunks[offset:offset+limit]],
        'offset':offset,'limit':limit,'complete_document':False,'read_only':True,'publish_allowed':False,'model_requests':0}
