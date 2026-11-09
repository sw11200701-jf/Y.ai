"""Parent and reviewed one-hop expansion; all targets must be currently visible."""
from copy import deepcopy


def verified_published_version(version, *, context, session):
    """Verify provenance without granting access to the creator's private Run.

    The caller's current version/chunk/source scope is checked by the retrieval
    service. A published library may be read by users other than its creator.
    """
    import hashlib
    import json
    from sqlalchemy import select
    from app.db import Resource
    from app.errors import AppError
    from app.execution.models import ExecutionRun, ExecutionStep, ExecutionBlob
    from app.execution.storage import engine_key
    from app.knowledge.slicing_contracts import digest, quality_gate
    from app.knowledge.slicing_engine import RecipeReplay
    from app.knowledge.slicing_parser import text_projection
    from app.knowledge.semantic_completed_read import completed_version_sha256
    try:
        if version.get('status') != 'published': return False
        run_id = version['portable_execution_run_id']
        row = session.scalar(select(Resource).where(Resource.kind == 'run', Resource.resource_id == run_id,
            Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id))
        binding = session.get(ExecutionRun, run_id)
        if row is None or binding is None: return False
        run = json.loads(row.payload)
        marker = run.get('semantic_job') or {}
        bound = session.get(ExecutionBlob, marker.get('plan_ref'))
        if run.get('status') != 'succeeded' or bound is None or bound.run_id != run_id: return False
        wrapper = json.loads(bound.payload); plan = wrapper['plan']
        if (digest(wrapper) != marker['plan_sha256'] or hashlib.sha256((run_id + ':semantic-plan:' + bound.payload).encode()).hexdigest() != bound.id
                or plan['document_version_id'] != version['id'] or version['knowledge_base_id'] not in plan['source_scope']
                or plan['actor'] != [binding.tenant_id, binding.workspace_id, binding.principal_id]
                or wrapper['actor'] != plan['actor'] or binding.principal_id != row.owner_id
                or binding.dsl_hash != run['engine']['dsl_sha256'] or binding.engine_key != engine_key(run['engine'])): return False

        from . import service
        import base64
        root=service.entry(version['resource_kind'],version['resource_id'],context,session)
        original=service.raw_file({**root,'file_id':version['file_id']},context,session)
        if hashlib.sha256(base64.b64decode(original['content_base64'],validate=True)).hexdigest()!=plan['source_sha256']:return False
        if 'image_reviews_sha256' in plan and plan['image_reviews_sha256']!=digest(original.get('image_evidence_reviews') or {}):return False

        def blob(ref):
            value = session.get(ExecutionBlob, ref)
            if value is None or value.run_id != run_id or hashlib.sha256((run_id + ':' + value.payload).encode()).hexdigest() != value.id: raise ValueError('blob')
            return json.loads(value.payload)

        def step(key):
            identifier = hashlib.sha256((run_id + ':' + binding.dsl_hash + ':' + key).encode()).hexdigest()
            value = session.get(ExecutionStep, identifier)
            if value is None or value.node_key != key or value.run_id != run_id or value.status != 'succeeded' or not value.side_effect or not value.completed_at: raise ValueError('step')
            return value

        final = step(f'semantic:{plan["job_id"]}:finalize')
        state, before = blob(final.result_ref), blob(final.input_ref)
        proof = state['semantic_finalize_receipt']
        slots = state.get('semantic_steps', {})
        if (proof['execution_id'] != final.execution_id or proof['plan_sha256'] != digest(plan)
                or proof['version_snapshot_sha256'] != completed_version_sha256(version)
                or before.get('semantic_steps') != slots or binding.state_ref != final.result_ref
                or state['frames']['root']['outputs'] != run.get('outputs') or binding.model_calls != len(slots)
                or set(slots) != {'slot_' + str(i) for i in range(len(slots))}): return False
        machine = RecipeReplay(plan, resolve_context=lambda ids: text_projection(plan['registry'], ids))
        history = []
        for index in range(len(slots)):
            request = machine.replay(history)['next']
            slot = 'slot_' + str(index)
            entry = slots[slot]
            row = step(f'semantic:{plan["job_id"]}:model:{slot}')
            original, after = blob(row.input_ref), blob(row.result_ref)
            envelope = entry['envelope']; provenance = envelope['provenance']
            data = {'stage': request['stage'], 'input': request['input']}
            from app.agents.model_adapter import ModelConfig
            from .slicing_document import preflight_request
            from .slicing_contracts import stage_settings
            wire=preflight_request(plan,data,config=ModelConfig(base_url=plan.get('model_policy_base_url','https://unused.invalid'),config_version=plan.get('model_version'),**plan['model_wire']))
            if (entry != after['semantic_steps'][slot] or entry['execution_id'] != row.execution_id or entry['issued'] is not True
                    or entry['input_sha256'] != digest(data) or entry['result_sha256'] != digest(envelope)
                    or provenance.get('request_sha256')!=wire['request_sha256'] or provenance.get('prompt_sha256')!=wire['prompt_sha256']
                    or provenance['stage'] != request['stage'] or provenance['input_sha256'] != digest(data)
                    or provenance['model_config_id'] != plan['model_config_id'] or provenance['model_config_version'] != plan['model_version']
                    or original.get('semantic_plan_sha256') != marker['plan_sha256'] or after.get('semantic_plan_sha256') != marker['plan_sha256']): return False
            history.append({'stage': request['stage'], 'input_sha256': request['input_sha256'], 'execution_id': row.execution_id,
                            'result': envelope['result'], 'result_sha256': digest(envelope['result']), 'slot_id': slot,
                            **({'response_validation':envelope['response_validation']} if 'response_validation' in envelope else {})})
        reconstructed = machine.replay(history)
        if reconstructed != version['portable_result'] or digest(reconstructed) != version['semantic_completion']['payload_sha256']: return False
        quality_gate(reconstructed['result'], final_audit_receipt=reconstructed['final_audit_receipt'], candidate_sha256=reconstructed['candidate_sha256'], relations=reconstructed['relations'],terminal_manifest=reconstructed.get('terminal_manifest'))
        # Ensure there are no unaccounted paid/file steps behind this witness.
        rows = list(session.scalars(select(ExecutionStep).where(ExecutionStep.run_id == run_id)))
        return len(rows) == len(slots) + 1
    except (AppError, KeyError, ValueError, TypeError, IndexError): return False


def expand_hits(hits, versions, *, visible, verify, max_chars=6000, max_per_hit=3):
    result, used, warnings, remaining = [], [], [], max_chars
    for hit in hits:
        value = deepcopy(hit)
        version = versions.get(hit['version_id'])
        if not version or not version.get('portable_result'):
            result.append(value); continue
        if not verify(version):
            warnings.append('此版本当前不能验证完整审核与权限，未展开父片或关联片')
            result.append(value); continue
        readable = {c['id']: c for c in visible(version) if c.get('enabled', True) and c.get('text') and c['text'] != '***'}
        anchor = readable.get(hit['id'])
        if not anchor:
            result.append(value); continue
        candidates = []
        parent = anchor.get('parent_task_key') or anchor.get('task_key')
        for chunk in readable.values():
            if chunk['id'] != anchor['id'] and chunk.get('knowledge_layer') == 'scenario_overview' and chunk.get('task_key') == parent:
                candidates.append((chunk['id'], 'parent_overview'))
        from .slicing_task_navigation import declared_links
        declared=declared_links(version['portable_result'].get('result',{}).get('items',[]))
        for identifier,kind in declared.get(anchor['id'],[]):
            if kind=='task_detail' and identifier in readable:candidates.append((identifier,'declared_task_detail'))
        for audited in version['portable_result'].get('relations', []):
            if audited.get('decision') != 'accept': continue
            relation = audited['relation']
            if anchor['id'] == relation['from_id']: candidates.append((relation['to_id'], 'verified_' + relation['type']))
            elif anchor['id'] == relation['to_id']: candidates.append((relation['from_id'], 'verified_' + relation['type']))
        expanded, seen = [], {anchor['id']}
        for identifier, reason in candidates:
            if identifier in seen or identifier not in readable: continue
            seen.add(identifier)
            chunk = readable[identifier]
            if len(expanded) >= max_per_hit or len(chunk['text']) > remaining:
                warnings.append('父片/一跳上下文达到完整单元预算；未截断正文或递归展开')
                continue
            remaining -= len(chunk['text'])
            expanded.append({'id': identifier, 'text': chunk['text'], 'source_ref': chunk['source_ref'],
                             'version_id': version['id'], 'reason': reason})
            used.append(identifier)
        value['expanded_context'] = expanded
        value['retrieval_text'] = hit['text'] + ''.join('\n\n[' + item['reason'] + ']\n' + item['text'] for item in expanded)
        result.append(value)
    return result, used, list(dict.fromkeys(warnings))
