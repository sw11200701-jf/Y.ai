"""Read committed final-audit blockers without admitting or replaying execution."""
from copy import deepcopy
import hashlib
import json
from sqlalchemy import select
from app.db import get_resource
from app.errors import AppError
from app.execution.models import ExecutionRun, ExecutionStep, ExecutionBlob
from .slicing_contracts import digest, quality_gate, fail


def pending_result(*, document_id, run_id, context, session):
    from app.execution.trace import authorize_view
    from app.execution.semantic_jobs import load_plan
    from .slicing_document import reconstruct, source_guard, slot_ids
    authorize_view(session, context, run_id)
    run = get_resource(session, 'run', run_id, context)
    if run.get('status') != 'failed' or run.get('error', {}).get('code') != 'SLICING_PENDING_REVIEW':
        fail('此运行没有已确认的最终独审待审结果', 'SLICING_REVIEW_UNAVAILABLE')
    binding = session.get(ExecutionRun, run_id)
    if not binding or (binding.principal_id, binding.tenant_id, binding.workspace_id) != (context.operator_id, context.tenant_id, context.workspace_id):
        raise AppError('NOT_FOUND', '待审记录不存在或无权访问', 404)
    from app.execution.storage import engine_key
    if binding.dsl_hash != run.get('engine', {}).get('dsl_sha256') or binding.engine_key != engine_key(run['engine']):
        fail('待审运行引擎与固定图不符', 'SLICING_REVIEW_INVALID')
    plan = load_plan(session, context, run, revalidate=False)['plan']
    if plan.get('recipe_version') != 'portable-slicing-1.0.0' or plan['document_id'] != document_id:
        fail('待审记录与文档不符', 'SLICING_REVIEW_INVALID')
    # This intentionally uses current full maintenance/model/Agent scope checks.
    # It is a failed-task review, never a historical successful-result exception.
    source_guard(plan, context=context, session=session)

    def blob(ref):
        row = session.get(ExecutionBlob, ref)
        if not row or row.run_id != run_id or hashlib.sha256((run_id + ':' + row.payload).encode()).hexdigest() != row.id:
            fail('待审步骤缺少完整真实账本', 'SLICING_REVIEW_INVALID')
        return json.loads(row.payload)

    state = blob(binding.state_ref)
    metadata = state.get('semantic_steps', {})
    ids = slot_ids(plan)[:len(metadata)]
    rows = list(session.scalars(select(ExecutionStep).where(ExecutionStep.run_id == run_id)))
    by_key = {row.node_key: row for row in rows}
    expected_keys = {f'semantic:{plan["job_id"]}:model:{slot}' for slot in ids}
    if (not ids or set(metadata) != set(ids) or set(by_key) != expected_keys or len(rows) != len(ids)
            or binding.model_calls != len(ids) or binding.steps != len(ids)
            or run.get('artifact_ids') or run.get('semantic_finalized')):
        fail('待审运行步骤或终态不完整', 'SLICING_REVIEW_INVALID')
    last = by_key[f'semantic:{plan["job_id"]}:model:{ids[-1]}']
    if binding.state_ref != last.result_ref or metadata[ids[-1]].get('envelope', {}).get('provenance', {}).get('stage') != 'finalaudit':
        fail('待审结果不是最后已提交独审', 'SLICING_REVIEW_INVALID')

    def read_step(*, session, context, job_id, step_id, expected_input_sha256):
        if job_id != plan['job_id'] or step_id not in ids: fail('待审步骤不属于此任务', 'SLICING_REVIEW_INVALID')
        row = by_key[f'semantic:{job_id}:model:{step_id}']
        expected = hashlib.sha256((run_id + ':' + binding.dsl_hash + ':' + row.node_key).encode()).hexdigest()
        if row.execution_id != expected or row.status != 'succeeded' or not row.side_effect or not row.completed_at or not row.result_ref:
            fail('待审仅允许读取已提交模型步骤', 'SLICING_REVIEW_INVALID')
        before, after = blob(row.input_ref), blob(row.result_ref)
        entry = after.get('semantic_steps', {}).get(step_id, {})
        envelope = entry.get('envelope')
        if (entry != metadata[step_id] or entry.get('execution_id') != row.execution_id or entry.get('issued') is not True
                or entry.get('input_sha256') != expected_input_sha256 or not isinstance(envelope, dict)
                or entry.get('result_sha256') != digest(envelope)
                or before.get('semantic_plan_sha256') != run['semantic_job']['plan_sha256']
                or after.get('semantic_plan_sha256') != run['semantic_job']['plan_sha256']
                or type(entry.get('lease_generation')) is not int or not 1 <= entry['lease_generation'] <= binding.generation
                or entry.get('reserved_context_tokens') != plan['budget']['per_call_context_budget']):
            fail('待审步骤来源或预约凭据不符', 'SLICING_REVIEW_INVALID')
        return {'execution_id': row.execution_id, 'result_sha256': digest(envelope), 'envelope': deepcopy(envelope)}

    result = reconstruct(plan, read_step=read_step, context=context, session=session)
    try:
        quality_gate(result['result'], final_audit_receipt=result['final_audit_receipt'], candidate_sha256=result['candidate_sha256'], relations=result['relations'],terminal_manifest=result.get('terminal_manifest'))
    except AppError as error:
        if error.code != 'SLICING_PENDING_REVIEW': raise
    else:
        fail('此结果并非待审状态', 'SLICING_REVIEW_INVALID')
    value = result['result']
    return {'contract': 'readonly_pending_slicing_v1', 'read_only': True, 'manual_review_required': True, 'can_publish': False,
            'document_id': document_id, 'run_id': run_id, 'source_sha256': plan['source_sha256'],
            'candidate_sha256': result['candidate_sha256'], 'final_audit_execution_id': result['final_audit_receipt']['execution_id'],
            'items': deepcopy(value['items']), 'issues': deepcopy(value['issues']), 'coverage': deepcopy(value['coverage']),
            'item_count': len(value['items'])}
