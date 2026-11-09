"""Server-only historical READ proof for fully committed, single-source finance graphs.

No source/ledger is repaired. Unsupported histories retain the old epoch fence.
This adapter uses complete immutable fact and report snapshots, never current
payloads as a substitute for missing historical evidence.
"""
import hashlib
import json
from pathlib import Path
from sqlalchemy import select,func
from app.db import Resource
from app.data_models import DataDerivation,DataResourceLink
from app.execution.models import ExecutionRun,ExecutionStep,ExecutionBlob
from app.errors import AppError

COMPLETION_KEY='__system__:workflow_completion:v1'


def _canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def _equal(left,right):
    return _canonical(left)==_canonical(right)


def _blob(session,run_id,ref):
    row=session.get(ExecutionBlob,ref)
    if row is None or row.run_id!=run_id or hashlib.sha256((run_id+':'+row.payload).encode()).hexdigest()!=ref:
        raise ValueError('invalid committed content address')
    return json.loads(row.payload)


def _owned(row,context):
    return row is not None and (row.owner_id,row.tenant_id,row.workspace_id)==(context.operator_id,context.tenant_id,context.workspace_id)


def _row(session,context,kind,identifier):
    row=session.scalar(select(Resource).where(Resource.kind==kind,Resource.resource_id==identifier,
        Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
    if not _owned(row,context):raise ValueError('unbound resource owner')
    # This legacy proof has no parent-link snapshot. Never accept a newly
    # introduced relationship as if it were part of the original evidence.
    if session.get(DataResourceLink,row.id) is not None:raise ValueError('unproven parent relationship')
    return row


def _bindings(session,context,row,expected):
    actual=[]
    for bound in session.scalars(select(DataDerivation).where(DataDerivation.resource_pk==row.id)):
        source=session.get(Resource,bound.source_pk)
        if (source is None or bound.actor_id!=context.operator_id
                or (source.tenant_id,source.workspace_id)!=(context.tenant_id,context.workspace_id)):
            raise ValueError('invalid derivation actor or source')
        actual.append((source.kind,source.resource_id,bound.required_action))
    if sorted(actual)!=sorted(expected):raise ValueError('original source relation set changed')


def _candidate_run(session,context,target,payload):
    if target.kind=='run':return target
    if target.kind=='artifact':return _row(session,context,'run',payload.get('run_id'))
    if target.kind=='dataset':
        rows=list(session.scalars(select(Resource).where(Resource.kind=='run',Resource.owner_id==context.operator_id,
            Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id,
            func.json_extract(Resource.payload,'$.dataset_id')==target.resource_id).limit(2)))
        if len(rows)==1:return rows[0]
    return None


def validate_completed_finance_target(session,context,target):
    """True = proven, False = recognized proof invalid, None = unsupported legacy.

    The caller may ignore an old epoch only for True. False always rejects.
    None preserves unchanged-epoch historical behavior and rejects epoch drift.
    """
    if target.kind not in {'run','dataset','artifact'} or getattr(context,'principal_type','human')!='human':return None
    try:
        if not _owned(target,context):return False
        target_payload=json.loads(target.payload)
        runrow=_candidate_run(session,context,target,target_payload)
        if runrow is None:return None
        run=json.loads(runrow.payload);workflow=run.get('workflow_snapshot',{})
        nodes=workflow.get('nodes',[]);types=[node.get('type') for node in nodes]
        # A deliberately bounded compatibility adapter. No guessed branch,
        # arbitrary tool, model, partial-query or nested-flow source closure.
        if (workflow.get('schemaVersion')!='2.0' or len(nodes)!=6
                or sorted(types)!=sorted(['start','query','calculate','chart','report','output'])):return None
        if run.get('status')!='succeeded':return None
        binding=session.get(ExecutionRun,runrow.resource_id,populate_existing=True)
        if binding is None:return None
        identifier=hashlib.sha256((runrow.resource_id+':'+binding.dsl_hash+':'+COMPLETION_KEY).encode()).hexdigest()
        completion=session.get(ExecutionStep,identifier)
        # Old F has no new completion witness. It keeps its existing ACL path;
        # this function never manufactures a witness from current DB content.
        if completion is None:
            from app.execution.storage import engine_identity
            if run.get('engine',{}).get('compiler_sha256')==engine_identity(workflow)['compiler_sha256']:return False
            return None
        return _validate(session,context,target,target_payload,runrow,run,workflow,nodes,binding,completion)
    except (AppError,ValueError,TypeError,KeyError,IndexError,AttributeError,OSError):return False


def _validate(session,context,target,target_payload,runrow,run,workflow,nodes,binding,completion):
    from app.execution.storage import engine_key,encode
    from app.runner.variables import resolve
    from app.contracts import FactQuery,FinancialFact,ResultDataset
    from app.reporting.service import validate_dataset
    from app.data_access import authorize_data_resource,_unrestricted
    from app.settings import settings
    run_id=runrow.resource_id
    if (not _owned(runrow,context) or run.get('id')!=run_id or run.get('status')!='succeeded'
            or (binding.principal_id,binding.tenant_id,binding.workspace_id)!=(context.operator_id,context.tenant_id,context.workspace_id)
            or run.get('engine',{}).get('name')!='langgraph'
            or binding.dsl_hash!=hashlib.sha256(encode(workflow).encode()).hexdigest()
            or binding.dsl_hash!=run['engine']['dsl_sha256'] or binding.engine_key!=engine_key(run['engine'])):return False
    if (completion.run_id!=run_id or completion.node_key!=COMPLETION_KEY or completion.status!='succeeded'
            or completion.side_effect or not completion.completed_at or completion.input_ref!=completion.result_ref
            or completion.result_ref!=binding.state_ref):return False
    steps=list(session.scalars(select(ExecutionStep).where(ExecutionStep.run_id==run_id)))
    if not steps or any(step.status!='succeeded' or not step.completed_at for step in steps):return False
    state=_blob(session,run_id,completion.result_ref)
    if set(state.get('frames',{}))!={'root'}:return False
    root=state['frames']['root'];pool=root['pool'];outputs=pool['nodes']
    if (root.get('prefix') or root.get('repeats') or not _equal(pool,run.get('variable_pool'))
            or not _equal(root['outputs'],run.get('outputs'))
            or pool['system'].get('run_id')!=run_id or pool['system'].get('tenant_id')!=context.tenant_id
            or pool['system'].get('workspace_id')!=context.workspace_id or pool['user'].get('id')!=context.operator_id
            or set(outputs)!={node['id'] for node in nodes}):return False
    budgets=state.get('budget',{})
    if budgets.get('model_calls')!=binding.model_calls or budgets.get('tool_calls')!=binding.tool_calls:return False
    by_type={node['type']:node for node in nodes}
    for node in nodes:
        execution_id=hashlib.sha256((run_id+':'+binding.dsl_hash+':'+node['id']).encode()).hexdigest()
        step=session.get(ExecutionStep,execution_id)
        if step is None or step.node_key!=node['id'] or step.status!='succeeded':return False
        recorded=_blob(session,run_id,step.result_ref)
        if not _equal(recorded['frames']['root']['pool']['nodes'][node['id']],outputs[node['id']]):return False
    querynode=by_type['query'];query_values=resolve(querynode.get('inputs',{}),pool)
    if query_values.get('sources'):
        if any(item.get('type')!='batch' for item in query_values['sources']):return False
    query=FactQuery.model_validate(query_values['query']);batch_id=query.batch_id
    if not batch_id or batch_id.startswith('knowledge_batch_'):return None
    if any(item.get('batch_id')!=batch_id for item in query_values.get('sources',[])):return False
    original_facts=outputs[querynode['id']]['facts']
    source=_row(session,context,'import_batch',batch_id);batch=json.loads(source.payload)
    if batch.get('id')!=batch_id or batch.get('status')!='committed':return False
    # Complete original facts (including original source_ref values) are an
    # immutable content snapshot only when the query consumed the WHOLE batch.
    current_facts=[FinancialFact.model_validate(fact).model_dump(mode='json') for fact in batch['facts']]
    if not current_facts or not _equal(current_facts,original_facts):return False
    for fact in current_facts:
        if (fact['entity'] not in context.allowed_entities or fact['entity']!=query.entity
                or fact['period'] not in query.periods or fact['metric_code'] not in query.metric_codes
                or any(fact[field]!=getattr(query,field) for field in ('currency','unit','scope'))):return False
    expected_sources=[('import_batch',batch_id,'use')]
    if not _equal(run.get('data_sources'),[{'kind':'import_batch','resource_id':batch_id,'action':'use'}]):return False
    _bindings(session,context,runrow,expected_sources)
    calc=outputs[by_type['calculate']['id']];dataset_payload=calc['dataset'];dataset_id=calc['dataset_id']
    dataset_row=_row(session,context,'dataset',dataset_id)
    if (dataset_payload['id']!=dataset_id or run.get('dataset_id')!=dataset_id
            or run.get('dataset_ids')!=[dataset_id] or not _equal(json.loads(dataset_row.payload),dataset_payload)):return False
    dataset=ResultDataset.model_validate(dataset_payload)
    if not _equal([fact.model_dump(mode='json') for fact in dataset.facts],original_facts):return False
    validate_dataset(dataset=dataset,context=context)
    _bindings(session,context,dataset_row,expected_sources)
    reportnode=by_type['report'];report=outputs[reportnode['id']]
    artifacts=report['artifacts'];artifact_ids=[artifact['id'] for artifact in artifacts]
    if (len(artifacts)!=3 or {artifact['type'] for artifact in artifacts}!={'xlsx','docx','png'}
            or report['artifact_ids']!=artifact_ids or run.get('artifact_ids')!=artifact_ids
            or report.get('dataset_id')!=dataset_id or report.get('snapshot_id')!=dataset.snapshot_id):return False
    file_sources=[('dataset',dataset_id,'export_detail'),('import_batch',batch_id,'export_detail')]
    artifact_root=settings.artifact_dir.resolve()
    for artifact in artifacts:
        row=_row(session,context,'artifact',artifact['id'])
        registered=json.loads(row.payload)
        if (artifact.get('run_id')!=run_id or artifact.get('dataset_id')!=dataset_id
                or not _equal({key:value for key,value in registered.items() if key!='path'},artifact)):return False
        _bindings(session,context,row,file_sources)
        path=Path(registered['path']).resolve()
        if (not path.is_relative_to(artifact_root) or not path.is_file()
                or path.stat().st_size!=artifact['size_bytes']
                or hashlib.sha256(path.read_bytes()).hexdigest()!=artifact['sha256']):return False
    if target.kind=='artifact' and target.resource_id not in artifact_ids:return False
    if target.kind=='dataset' and target.resource_id!=dataset_id:return False
    # Actual current permissions are re-evaluated; historical stamp is never
    # rewritten. Scope narrowing/masking cannot expose an old full result.
    actions={'use','export_detail'} if target.kind=='artifact' else {'use'}
    for action in actions:
        decision=authorize_data_resource(session=session,context=context,kind='import_batch',resource_id=batch_id,action=action)
        if not _unrestricted(decision):return False
    return True
