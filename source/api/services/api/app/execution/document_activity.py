"""Read-only document activity and admission deduplication, same actor only."""
import json
from sqlalchemy import select
from app.db import Resource
from app.errors import AppError
from app.agents.builtin_chunker import digest
from app.execution.models import ExecutionBlob,ExecutionRun

ACTIVE={'queued','running','resuming','paused','waiting_approval','outcome_unknown'}

def active_document_job(session,context,document_id):
    rows=session.execute(select(Resource,ExecutionRun).join(ExecutionRun,ExecutionRun.run_id==Resource.resource_id).where(
        Resource.kind=='run',Resource.owner_id==context.operator_id,
        Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id,
        ExecutionRun.principal_id==context.operator_id,ExecutionRun.tenant_id==context.tenant_id,
        ExecutionRun.workspace_id==context.workspace_id).order_by(Resource.id.desc()))
    for resource,binding in rows:
        run=json.loads(resource.payload)
        if run.get('status') not in ACTIVE or not run.get('semantic_job'):continue
        marker=run['semantic_job'];blob=session.get(ExecutionBlob,marker.get('plan_ref'))
        if blob is None or blob.run_id!=run['id']:
            raise AppError('SEMANTIC_PLAN_INVALID','存在无法核对的活动文档任务，未创建新任务',409)
        wrapper=json.loads(blob.payload)
        if (wrapper.get('run_id')!=run['id'] or digest(wrapper)!=marker.get('plan_sha256')
            or wrapper.get('actor')!=[context.tenant_id,context.workspace_id,context.operator_id]):
            raise AppError('SEMANTIC_PLAN_INVALID','活动任务的主体或固定计划无法核对',409)
        plan=wrapper['plan']
        if plan['document_id']!=document_id:continue
        return {'job_id':plan['job_id'],'run_id':run['id'],'status':run['status'],
            'reservation':{**wrapper['reservation'],'job_timeout_seconds':plan.get('time_budget',{}).get('wall_time_seconds')},
            'requires_publish':True,'manual_publish_required':True,
            'finalizer_contract_version':'knowledge-semantic-finalizer-v2','existing_active_job':True}
    return None
