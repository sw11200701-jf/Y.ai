"""Bounded portable recipe on the original issued/committed/fenced bridge."""
from app.errors import AppError
from app.agents.builtin_chunker import digest


class PortableSlicingCompiler:
    def __init__(self, run, bridge): self.run, self.bridge = run, bridge

    def compile(self, saver):
        from langgraph.graph import StateGraph, START, END
        from app.execution.contracts import ExecutionState, ExecutionContext
        from app.execution.storage import authorize
        from app.execution.semantic_jobs import load_plan, make_step_reader, stage_ids
        from app.knowledge.slicing_document import reconstruct, execute_step, preflight_request
        from app.db import SessionLocal
        with SessionLocal() as session:
            context, _, current = authorize(session, self.run['id'])
            plan = load_plan(session, context, current)['plan']
        ids = stage_ids(plan)
        graph = StateGraph(ExecutionState, context_schema=ExecutionContext)

        for index, slot in enumerate(ids):
            async def node(state, slot=slot):
                reader = make_step_reader(self.run['id'], lease_owner=self.bridge.lease_owner)

                async def preflight(execution):
                    _, binding, current = authorize(execution.session, execution.run_id)
                    pinned = load_plan(execution.session, execution.context, current)
                    if binding.model_calls >= pinned['reservation']['model_calls']:
                        raise AppError('SLICING_BUDGET_EXCEEDED', '切片预约调用数已耗尽', 422)
                    data = reconstruct(pinned['plan'], read_step=reader, context=execution.context, session=execution.session, until=slot)
                    from app.agents.model_adapter import load_config
                    config = load_config(plan['model_config_id'], expected_version=plan['model_version'], context=execution.context, session=execution.session)
                    from app.execution.semantic_jobs import assert_model_network_policy
                    assert_model_network_policy(pinned['plan'],config)
                    request = preflight_request(plan, data, config=config)
                    from app.agents.model_requirements import preflight_model_requirements
                    fmt = request['response_format']
                    preflight_model_requirements(model_config_id=plan['model_config_id'], expected_version=plan['model_version'],
                        parameters={'max_tokens': request['output_tokens']}, context_budget=plan['budget']['per_call_context_budget'],
                        response_format=fmt, context=execution.context, session=execution.session,document_policy=plan.get('model_invocation'))
                    execution.model_context_reservation={'context_tokens':plan['budget']['per_call_context_budget'],'image_tokens':0}
                    from app.execution.document_job_budget import require_model_time
                    require_model_time(pinned['plan'],execution.budget['deadline_epoch'])

                async def operation(execution):
                    _, binding, current = authorize(execution.session, execution.run_id)
                    pinned = load_plan(execution.session, execution.context, current)
                    # The trusted execute_step reconstructs and validates the
                    # same input immediately before sending. Its provenance is
                    # built by server code, outside the provider's result JSON.
                    # Keep the historical v1 path unchanged.
                    if not pinned['plan'].get('terminal_contract'):
                        data = reconstruct(pinned['plan'], read_step=reader, context=execution.context, session=execution.session, until=slot)
                    from app.execution.document_request_wait import document_wait
                    with document_wait(pinned['plan'],execution):
                        envelope = await execution.invoke('app.knowledge.slicing_document', 'execute_step', plan=pinned['plan'], step_id=slot, read_step=reader)
                    input_sha256=envelope['provenance']['input_sha256'] if pinned['plan'].get('terminal_contract') else digest(data)
                    from app.execution.effects import get_effect_context
                    effect = get_effect_context()
                    if effect is None or effect.execution_id != execution.execution_id: raise AppError('SLICING_RECEIPT_INVALID', '缺少真实签发步骤', 409)
                    snapshot = execution.state
                    snapshot['semantic_plan_sha256'] = current['semantic_job']['plan_sha256']
                    snapshot.setdefault('semantic_steps', {})[slot] = {'execution_id': execution.execution_id, 'issued': True,
                        'lease_generation': binding.generation, 'input_sha256': input_sha256, 'result_sha256': digest(envelope),
                        'reserved_context_tokens': plan['budget']['per_call_context_budget'], 'envelope': envelope}
                    return snapshot
                return await self.bridge.step(state, node_key=f'semantic:{plan["job_id"]}:model:{slot}', operation=operation, side_effect=True, preflight=preflight)

            graph.add_node(slot, node)

            def route(state, index=index, slot=slot):
                if plan.get('terminal_contract'):
                    # A first finalaudit shard is not a completed terminal phase.
                    # Replay only this actually committed prefix under its lease.
                    reader=make_step_reader(self.run['id'],lease_owner=self.bridge.lease_owner)
                    with SessionLocal() as session:
                        context,_,current=authorize(session,self.run['id'])
                        pinned=load_plan(session,context,current)['plan']
                        result=reconstruct(pinned,read_step=reader,context=context,session=session,through=slot)
                    if 'next' not in result:return 'finalize'
                    if index+1>=len(ids):raise AppError('SLICING_BUDGET_EXCEEDED','全部最终审核分片完成前预约已耗尽，未生成半份CSV',422)
                    return ids[index+1]
                snapshot = self.bridge.read(state)
                entry = snapshot.get('semantic_steps', {}).get(slot, {})
                if entry.get('envelope', {}).get('provenance', {}).get('stage') == 'finalaudit': return 'finalize'
                if index + 1 >= len(ids): raise AppError('SLICING_BUDGET_EXCEEDED', '最终独审前预约调用数已耗尽，未补跑', 422)
                return ids[index + 1]
            graph.add_conditional_edges(slot, route, {key: key for key in [*ids[index + 1:index + 2], 'finalize']})

        async def finalize(state):
            reader = make_step_reader(self.run['id'], lease_owner=self.bridge.lease_owner)
            async def preflight(execution):
                _, _, current = authorize(execution.session, execution.run_id)
                pinned = load_plan(execution.session, execution.context, current)
                final = reconstruct(pinned['plan'], read_step=reader, context=execution.context, session=execution.session)
                from app.knowledge.slicing_contracts import quality_gate
                quality_gate(final['result'], final_audit_receipt=final['final_audit_receipt'], candidate_sha256=final['candidate_sha256'], relations=final['relations'],terminal_manifest=final.get('terminal_manifest'))

            async def operation(execution):
                from app.db import get_resource, save_resource
                if execution.session.bind.dialect.name == 'sqlite':
                    connection = execution.session.connection()
                    if not connection.connection.driver_connection.in_transaction: connection.exec_driver_sql('BEGIN IMMEDIATE')
                _, _, current = authorize(execution.session, execution.run_id)
                pinned = load_plan(execution.session, execution.context, current)
                from app.knowledge.slicing_finalizer import finalize as save_portable
                version, artifact = save_portable(pinned['plan'], read_step=reader, context=execution.context, session=execution.session)
                # CSV registration updated the same run; never overwrite it with
                # the stale pre-finalization object.
                current = get_resource(execution.session, 'run', execution.run_id, execution.context)
                current['semantic_finalized'] = {'job_id': plan['job_id'], 'document_version_id': version['id']}
                save_resource(execution.session, 'run', execution.run_id, current, execution.context)
                snapshot = execution.state
                from app.knowledge.semantic_completed_read import completed_version_sha256
                snapshot['semantic_finalize_receipt'] = {'contract_version': 'semantic-committed-finalize-v1',
                    'run_id': execution.run_id, 'job_id': plan['job_id'], 'document_version_id': version['id'],
                    'execution_id': execution.execution_id, 'plan_sha256': digest(plan), 'version_snapshot_sha256': completed_version_sha256(version)}
                from app.knowledge.slicing_finalizer import csv_receipt
                snapshot['knowledge_csv_receipt'] = csv_receipt(artifact)
                snapshot['frames']['root']['outputs'] = {'job_id': plan['job_id'], 'document_id': plan['document_id'],
                    'document_version_id': version['id'], 'status': 'validated', 'requires_publish': True,
                    'manual_publish_required': True, 'artifact_ids': [artifact['id']], 'recipe_version': plan['recipe_version']}
                return snapshot
            return await self.bridge.step(state, node_key=f'semantic:{plan["job_id"]}:finalize', operation=operation, side_effect=True, preflight=preflight)

        graph.add_node('finalize', finalize)
        graph.add_edge(START, ids[0]); graph.add_edge('finalize', END)
        return graph.compile(checkpointer=saver)
