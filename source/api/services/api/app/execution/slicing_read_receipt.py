"""Read-only portable job proofs, accepting only the actually executed prefix."""
from copy import deepcopy
import hashlib
import json
from sqlalchemy import select
from app.db import Resource
from app.errors import AppError
from app.auth import validate_context
from app.execution.models import ExecutionRun, ExecutionStep, ExecutionBlob
from app.agents.builtin_chunker import digest


def invalid(): raise AppError('SLICING_COMPLETED_READ_INVALID', '切片完整提交凭据无效或当前不可访问', 409)


def make_readers(run_id):
    def checked(session, context, job_id):
        validate_context(context, session)
        from app.rbac import require_any_permission, RUN_PERMISSIONS
        require_any_permission(context, session, RUN_PERMISSIONS)
        body = session.scalar(select(Resource.payload).where(Resource.kind == 'run', Resource.resource_id == run_id,
            Resource.owner_id == context.operator_id, Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id))
        binding = session.get(ExecutionRun, run_id, populate_existing=True)
        if not body or not binding or (binding.principal_id, binding.tenant_id, binding.workspace_id) != (context.operator_id, context.tenant_id, context.workspace_id): invalid()
        run = json.loads(body)
        marker = run.get('semantic_job') or {}
        row = session.get(ExecutionBlob, marker.get('plan_ref'))
        if run.get('status') != 'succeeded' or not row or row.run_id != run_id: invalid()
        wrapper = json.loads(row.payload)
        plan = wrapper.get('plan', {})
        if (hashlib.sha256((run_id + ':semantic-plan:' + row.payload).encode()).hexdigest() != row.id
                or wrapper.get('run_id') != run_id or wrapper.get('actor') != [context.tenant_id, context.workspace_id, context.operator_id]
                or digest(wrapper) != marker.get('plan_sha256') or plan.get('job_id') != job_id or marker.get('job_id') != job_id
                or plan.get('recipe_version') != 'portable-slicing-1.0.0'
                or run.get('semantic_finalized') != {'job_id': job_id, 'document_version_id': plan.get('document_version_id')}
                or binding.dsl_hash != (run.get('engine') or {}).get('dsl_sha256')): invalid()
        from app.execution.storage import engine_key
        if binding.engine_key != engine_key(run['engine']): invalid()
        from app.knowledge.slicing_document import source_guard, slot_ids
        source_guard(plan, context=context, session=session, completed=True)

        def blob(ref):
            value = session.get(ExecutionBlob, ref)
            if not value or value.run_id != run_id or hashlib.sha256((run_id + ':' + value.payload).encode()).hexdigest() != value.id: invalid()
            return json.loads(value.payload)

        def step(key):
            identifier = hashlib.sha256((run_id + ':' + binding.dsl_hash + ':' + key).encode()).hexdigest()
            value = session.get(ExecutionStep, identifier)
            if not value or value.run_id != run_id or value.node_key != key or value.status != 'succeeded' or not value.side_effect or not value.result_ref or not value.completed_at: invalid()
            return value

        final = step(f'semantic:{job_id}:finalize')
        before, state = blob(final.input_ref), blob(final.result_ref)
        metadata = state.get('semantic_steps', {})
        ids = slot_ids(plan)[:len(metadata)]
        if not ids or set(metadata) != set(ids) or binding.model_calls != len(ids) or len(ids) > wrapper['reservation']['model_calls']: invalid()
        rows = list(session.scalars(select(ExecutionStep).where(ExecutionStep.run_id == run_id)))
        expected_keys = {f'semantic:{job_id}:model:{slot}' for slot in ids} | {f'semantic:{job_id}:finalize'}
        if len(rows) != len(expected_keys) or {r.node_key for r in rows} != expected_keys or any(r.status != 'succeeded' or not r.side_effect or not r.result_ref for r in rows): invalid()
        output = state.get('frames', {}).get('root', {}).get('outputs', {})
        proof = state.get('semantic_finalize_receipt', {})
        if (before.get('semantic_plan_sha256') != marker['plan_sha256'] or state.get('semantic_plan_sha256') != marker['plan_sha256']
                or before.get('semantic_steps') != metadata or output != run.get('outputs') or binding.state_ref != final.result_ref
                or output.get('document_version_id') != plan['document_version_id'] or output.get('job_id') != job_id
                or output.get('status') != 'validated' or output.get('manual_publish_required') is not True
                or output.get('requires_publish') is not True or output.get('recipe_version') != plan['recipe_version']
                or output.get('artifact_ids')!=run.get('artifact_ids') or run.get('knowledge_csv_artifact_ids')!=run.get('artifact_ids')
                or proof.get('execution_id') != final.execution_id or proof.get('plan_sha256') != digest(plan)
                or proof.get('document_version_id') != plan['document_version_id'] or proof.get('run_id') != run_id
                or proof.get('job_id') != job_id or proof.get('contract_version') != 'semantic-committed-finalize-v1'): invalid()
        if metadata[ids[-1]].get('envelope', {}).get('provenance', {}).get('stage') != 'finalaudit': invalid()
        return run, binding, plan, final, state, ids, blob, step

    def read_completion(*, operation, session, context, job_id, document_version_id, expected_plan_sha256):
        run, binding, plan, final, state, ids, blob, step = checked(session, context, job_id)
        if operation != 'read_committed_result' or document_version_id != plan['document_version_id'] or expected_plan_sha256 != digest(plan): invalid()
        return {'contract_version': 'knowledge-semantic-completed-read-v1', 'operation': operation, 'job_id': job_id,
            'document_version_id': document_version_id, 'plan_sha256': digest(plan), 'execution_id': final.execution_id,
            'result_sha256': digest(state), 'version_snapshot_sha256': state['semantic_finalize_receipt']['version_snapshot_sha256']}

    def read_step(*, session, context, job_id, step_id, expected_input_sha256):
        run, binding, plan, final, state, ids, blob, step = checked(session, context, job_id)
        if step_id not in ids: invalid()
        row = step(f'semantic:{job_id}:model:{step_id}')
        before, after = blob(row.input_ref), blob(row.result_ref)
        metadata = after.get('semantic_steps', {}).get(step_id, {})
        envelope = metadata.get('envelope')
        if (metadata != state['semantic_steps'][step_id] or metadata.get('execution_id') != row.execution_id
                or metadata.get('issued') is not True or metadata.get('input_sha256') != expected_input_sha256
                or not isinstance(envelope, dict) or metadata.get('result_sha256') != digest(envelope)
                or before.get('semantic_plan_sha256') != run['semantic_job']['plan_sha256']
                or after.get('semantic_plan_sha256') != run['semantic_job']['plan_sha256']
                or type(metadata.get('lease_generation')) is not int or not 1 <= metadata['lease_generation'] <= binding.generation
                or metadata.get('reserved_context_tokens') != plan['budget']['per_call_context_budget']): invalid()
        return {'execution_id': row.execution_id, 'result_sha256': digest(envelope), 'envelope': deepcopy(envelope)}
    return read_completion, read_step


def validate_completed(plan, *, read_step, read_completion, context, session):
    from app.knowledge.slicing_document import reconstruct, source_guard
    from app.knowledge import service
    from app.knowledge.semantic_completed_read import completed_version_sha256
    root, _, _ = source_guard(plan, context=context, session=session, completed=True)
    version = service.checked_version(plan['document_version_id'], root, context, session)
    proof = read_completion(operation='read_committed_result', session=session, context=context, job_id=plan['job_id'],
        document_version_id=version['id'], expected_plan_sha256=digest(plan))
    if proof.get('version_snapshot_sha256') != completed_version_sha256(version): invalid()
    result = reconstruct(plan, read_step=read_step, context=context, session=session, completed=True)
    if (version.get('portable_result') != result or version.get('semantic_completion', {}).get('payload_sha256') != digest(result)
            or version['semantic_completion'].get('plan_sha256') != digest(plan)): invalid()
    return {'status': version['status'], 'document_version_id': version['id'], 'read_only': True}
