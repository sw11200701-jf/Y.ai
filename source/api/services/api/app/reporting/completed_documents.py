"""Bounded historical READ proof for newly committed Office artifacts only.

The creation writer records the exact Resource-JSON source closure. Its hash is
bound into the real tool step's content-addressed result. Old artifacts never
receive a witness from current data; unsupported or incomplete histories retain
the existing policy-epoch fence. This module cannot resume or write a Run.
"""
import hashlib
import json
import base64
from pathlib import Path
from sqlalchemy import select
from app.db import Resource
from app.data_models import DataDerivation, DataResourceLink, DataTableBinding
from app.execution.models import ExecutionRun, ExecutionStep, ExecutionBlob
from app.errors import AppError

VERSION = 'office-completed-source-closure-v1'
META = ('id', 'artifact_kind', 'contract_version', 'run_id', 'execution_id',
        'node_key', 'tool_id', 'renderer_version', 'input_sha256',
        'user_input_sha256', 'type', 'filename', 'mime_type', 'sha256',
        'size_bytes', 'created_at', 'data_sources', 'path')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _owned(row, context):
    return row is not None and (row.owner_id, row.tenant_id, row.workspace_id) == (
        context.actor_id if getattr(context,'principal_type','human')=='platform' else context.operator_id, context.tenant_id, context.workspace_id)


def _raw(session, context, kind, identifier, *, owned=True):
    row = session.scalar(select(Resource).where(Resource.kind == kind,
        Resource.resource_id == identifier, Resource.tenant_id == context.tenant_id,
        Resource.workspace_id == context.workspace_id))
    if row is None or owned and not _owned(row, context):
        raise ValueError('unproven resource identity')
    return row


def normalized_sources(sources):
    result = {}
    for item in sources:
        key = (item['kind'], item['resource_id'])
        result[key] = {'kind': key[0], 'resource_id': key[1], 'action': 'export_detail'}
    return [result[key] for key in sorted(result)]


def source_closure(session, context, sources, *, historical=False):
    """No authorization state is saved or replaced; only original content hashes.

    Historical mode bypasses recursive epoch checks ONLY inside this validator:
    each recorded edge/actor is traversed explicitly and every closure resource
    must currently grant unrestricted use AND export. No public auth entrypoint
    accepts this mode. The original creation path uses ordinary authorization.
    """
    from app.auth import validate_context
    from app.data_access import (DATA_KINDS, _row, authorize_data_resource,
        _authorize_subject_resource, _unrestricted)
    user = validate_context(context, session)
    if getattr(context, 'principal_type', 'human') not in {'human','platform'}:
        raise ValueError('unsupported principal')
    rows, visiting, pending_content = {}, set(), set()

    def full(kind, identifier):
        for action in ('use', 'export_detail'):
            if historical:
                decision = _authorize_subject_resource(session=session, context=context,
                    user=user, kind=kind, resource_id=identifier, action=action,
                    check_derivation=False)
                if context.principal_type=='platform':
                    from app.data_access import intersect_platform_grantor
                    decision=intersect_platform_grantor(decision,session,context,user)
            else:
                decision = authorize_data_resource(session=session, context=context,
                    kind=kind, resource_id=identifier, action=action)
            if not _unrestricted(decision):
                raise ValueError('narrowed current authority')

    def visit(row, depth):
        if (row is None or row.kind not in DATA_KINDS or depth > 16
                or (row.tenant_id, row.workspace_id) != (context.tenant_id, context.workspace_id)):
            raise ValueError('unsupported source closure')
        if row.id in visiting:
            raise ValueError('cyclic source closure')
        if row.id in rows:
            return
        if len(rows) >= 128:
            raise ValueError('source closure limit')
        full(row.kind, row.resource_id)
        visiting.add(row.id)
        payload = json.loads(row.payload)
        if row.kind in {'document', 'knowledge_file', 'data_file'}:
            original = base64.b64decode(payload['content_base64'], validate=True)
            if hashlib.sha256(original).hexdigest() != payload['sha256']:
                raise ValueError('source file integrity mismatch')
        # These are content/provenance references, not just ACL parents. A
        # version's original file can have a stricter current grant of its own.
        # Queue after the ACL graph finishes: file->document parent links are
        # legitimate reverse edges, not a derivation/authorization cycle.
        file_kind = None
        if row.kind == 'knowledge_version':
            if payload.get('resource_kind') == 'knowledge_document':file_kind = 'document'
            elif payload.get('resource_kind') == 'knowledge_dataset':file_kind = 'knowledge_file'
            else:raise ValueError('unknown version content owner')
        elif row.kind == 'knowledge_document':file_kind = 'document'
        elif row.kind in {'knowledge_dataset', 'knowledge_batch'}:file_kind = 'knowledge_file'
        elif row.kind in {'data_table_version', 'data_batch'}:file_kind = 'data_file'
        if file_kind and payload.get('file_id'):
            pending_content.add((file_kind, payload['file_id'], payload.get('source_sha256')))
        if row.kind == 'knowledge_chunk' and payload.get('version_id'):
            pending_content.add(('knowledge_version', payload['version_id'], None))
        parent = session.get(DataResourceLink, row.id, populate_existing=True)
        link = {'parent_pk': parent.parent_pk, 'inherit_access': parent.inherit_access} if parent else None
        implicit = None
        # Match the existing knowledge-parent fallback exactly; never invent a
        # parent from a display name or substitute current metadata for history.
        if parent is None and row.kind.startswith('knowledge_') and row.kind != 'knowledge_base':
            pkind = pid = None
            if (row.kind == 'knowledge_version' and payload.get('resource_kind') in DATA_KINDS
                    and payload.get('resource_id') != row.resource_id):
                pkind, pid = payload['resource_kind'], payload.get('resource_id')
            elif payload.get('knowledge_base_id'):
                pkind, pid = 'knowledge_base', payload['knowledge_base_id']
            if pid:
                found = _row(session, context, pkind, pid)
                implicit = {'parent_pk': found.id, 'inherit_access': False}
        table = session.get(DataTableBinding, row.id, populate_existing=True)
        binding = {'database_pk': table.database_pk} if table else None
        edges = []
        for edge in session.scalars(select(DataDerivation).where(DataDerivation.resource_pk == row.id)):
            if edge.actor_id != context.operator_id or edge.required_action not in {'use', 'export_detail'}:
                raise ValueError('unproven derivation actor or action')
            edges.append({'source_pk': edge.source_pk, 'actor_id': edge.actor_id,
                          'required_action': edge.required_action, 'policy_revision': edge.policy_revision})
        edges.sort(key=lambda edge: edge['source_pk'])
        rows[row.id] = {'resource_pk': row.id, 'kind': row.kind, 'resource_id': row.resource_id,
            'owner_id': row.owner_id, 'payload_sha256': hashlib.sha256(row.payload.encode()).hexdigest(),
            'link': link, 'implicit_link': implicit, 'table_binding': binding, 'derivations': edges}
        related = [edge['source_pk'] for edge in edges]
        if link: related.append(link['parent_pk'])
        if implicit: related.append(implicit['parent_pk'])
        if binding: related.append(binding['database_pk'])
        for identifier in sorted(set(related)):
            visit(session.get(Resource, identifier, populate_existing=True), depth + 1)
        visiting.remove(row.id)

    roots = []
    for source in normalized_sources(sources):
        if source['kind'] not in DATA_KINDS:
            raise ValueError('unsupported source kind')
        full(source['kind'], source['resource_id'])
        row = _row(session, context, source['kind'], source['resource_id'])
        roots.append({**source, 'resource_pk': row.id})
        visit(row, 0)
    checked_content = set()
    while pending_content:
        kind, identifier, expected_sha = pending_content.pop()
        identity = (kind, identifier, expected_sha)
        if identity in checked_content:continue
        checked_content.add(identity)
        row = _raw(session, context, kind, identifier, owned=False)
        if kind in {'document', 'knowledge_file', 'data_file'}:
            value = json.loads(row.payload)
            content = base64.b64decode(value['content_base64'], validate=True)
            actual_sha = hashlib.sha256(content).hexdigest()
            if actual_sha != value['sha256'] or expected_sha is not None and actual_sha != expected_sha:
                raise ValueError('original file content changed')
        visit(row, 0)
    return {'roots': roots, 'rows': [rows[key] for key in sorted(rows)]}


def capture_source_closure(session, context, sources):
    """Unsupported/partial sources keep their old strict read behavior."""
    from app.data_access import policy_version
    try:
        closure = source_closure(session, context, sources)
        return {'policy_revision': policy_version(session, context), 'closure': closure}
    except (AppError, ValueError, TypeError, KeyError, AttributeError):
        return None


def make_read_proof(value, context, captured):
    if captured is None:
        return None
    return {'version': VERSION, 'actor_id': context.operator_id,
        'tenant_id': context.tenant_id, 'workspace_id': context.workspace_id,
        'policy_revision': captured['policy_revision'], 'source_closure': captured['closure'],
        'metadata_sha256': digest({key: value[key] for key in META})}


def artifact_receipt(value, execution_id):
    if value['execution_id'] != execution_id:
        raise ValueError('wrong artifact execution')
    proof = value.get('read_proof')
    return {'artifact_id': value['id'], 'execution_id': execution_id,
        'node_key': value['node_key'], 'tool_id': value['tool_id'],
        'renderer_version': value['renderer_version'], 'sha256': value['sha256'],
        'input_sha256': value['input_sha256'],
        'read_proof_sha256': digest(proof) if proof is not None else None}


def _blob(session, run_id, reference):
    row = session.get(ExecutionBlob, reference)
    if (row is None or row.run_id != run_id
            or hashlib.sha256((run_id + ':' + row.payload).encode()).hexdigest() != reference):
        raise ValueError('invalid committed content address')
    return json.loads(row.payload)


def _bindings(session, context, row, sources, epoch):
    # The Agent may report the same already-bound source more than once. The
    # immutable relational edge is a set; distinct actions still cannot merge.
    expected = sorted({(source['kind'], source['resource_id'], source.get('action', 'use')) for source in sources})
    actual = []
    for edge in session.scalars(select(DataDerivation).where(DataDerivation.resource_pk == row.id)):
        source = session.get(Resource, edge.source_pk)
        if (source is None or edge.actor_id != context.operator_id or edge.policy_revision != epoch
                or (source.tenant_id, source.workspace_id) != (context.tenant_id, context.workspace_id)):
            raise ValueError('original derivation changed')
        actual.append((source.kind, source.resource_id, edge.required_action))
    if sorted(actual) != expected:
        raise ValueError('original source set changed')


def validate_completed_document_target(session, context, target):
    """True proven; False recognized-invalid; None unsupported (old fence stays)."""
    if target.kind not in {'run', 'artifact'} or getattr(context, 'principal_type', 'human') not in {'human','platform'}:
        return None
    try:
        payload = json.loads(target.payload)
        if target.kind == 'artifact' and payload.get('artifact_kind') != 'generated_document':
            return None
        runrow = target if target.kind == 'run' else _raw(session, context, 'run', payload.get('run_id'))
        run = json.loads(runrow.payload)
        if not run.get('document_artifact_ids') or run.get('status') != 'succeeded':
            return None
        # No historical backfill. All files in this run must have been created
        # with an actual witness; a mixture cannot authorize cached run outputs.
        artifacts = []
        for identifier in run['document_artifact_ids']:
            row = _raw(session, context, 'artifact', identifier)
            value = json.loads(row.payload)
            if value.get('read_proof') is None:
                return None
            artifacts.append((row, value))
        return _validate(session, context, target, runrow, run, artifacts)
    except (AppError, ValueError, TypeError, KeyError, IndexError, AttributeError, OSError):
        return False


def _validate(session, context, target, runrow, run, artifacts):
    from app.execution.storage import COMPLETION_KEY, encode, engine_key
    from app.reporting.document_artifacts import input_identity, sources_for_run
    from app.reporting.document_renderers import MIME
    from app.settings import settings
    if not _owned(target, context) or not _owned(runrow, context) or run.get('id') != runrow.resource_id:
        return False
    run_id = runrow.resource_id
    binding = session.get(ExecutionRun, run_id, populate_existing=True)
    if (binding is None or (binding.principal_id, binding.tenant_id, binding.workspace_id) !=
            (context.operator_id, context.tenant_id, context.workspace_id)
            or binding.dsl_hash != hashlib.sha256(encode(run['workflow_snapshot']).encode()).hexdigest()
            or binding.dsl_hash != run['engine']['dsl_sha256'] or binding.engine_key != engine_key(run['engine'])):
        return False
    final_id = hashlib.sha256((run_id + ':' + binding.dsl_hash + ':' + COMPLETION_KEY).encode()).hexdigest()
    final = session.get(ExecutionStep, final_id)
    if (final is None or final.run_id != run_id or final.node_key != COMPLETION_KEY
            or final.status != 'succeeded' or final.side_effect or not final.completed_at
            or final.input_ref != final.result_ref or final.result_ref != binding.state_ref):
        return False
    steps = list(session.scalars(select(ExecutionStep).where(ExecutionStep.run_id == run_id)))
    if not steps or any(step.status != 'succeeded' or not step.completed_at for step in steps):
        return False
    state = _blob(session, run_id, final.result_ref)
    root = state.get('frames', {}).get('root')
    if (root is None or digest(root.get('pool')) != digest(run.get('variable_pool'))
            or digest(root.get('outputs')) != digest(run.get('outputs'))
            or root['pool']['system']['run_id'] != run_id
            or root['pool']['system']['tenant_id'] != context.tenant_id
            or root['pool']['system']['workspace_id'] != context.workspace_id
            or root['pool']['user']['id'] != context.operator_id
            or state['budget']['model_calls'] != binding.model_calls
            or state['budget']['tool_calls'] != binding.tool_calls):
        return False
    seen_sources, epochs = set(), set()
    file_root = settings.artifact_dir.resolve()
    for row, value in artifacts:
        proof = value['read_proof']
        if (set(proof) != {'version', 'actor_id', 'tenant_id', 'workspace_id', 'policy_revision', 'source_closure', 'metadata_sha256'}
                or proof['version'] != VERSION or (proof['actor_id'], proof['tenant_id'], proof['workspace_id']) !=
                    (context.operator_id, context.tenant_id, context.workspace_id)
                or proof['metadata_sha256'] != digest({key: value[key] for key in META})
                or value['run_id'] != run_id or value['id'] != row.resource_id
                or value['artifact_kind'] != 'generated_document'
                or value['contract_version'] != 'generated-document-artifact-v1'
                or value['renderer_version'] != 'general-office-v1'
                or value['tool_id'] != {'docx': 'document.word.v1', 'xlsx': 'document.excel.v1', 'pptx': 'document.powerpoint.v1'}[value['type']]
                or value['user_input_sha256'] != input_identity(run)
                or value['id'] not in run.get('artifact_ids', [])):
            return False
        epoch = proof['policy_revision']; epochs.add(epoch)
        _bindings(session, context, row, value['data_sources'], epoch)
        closure = source_closure(session, context, value['data_sources'], historical=True)
        if digest(closure) != digest(proof['source_closure']):
            return False
        seen_sources.update((item['kind'], item['resource_id']) for item in value['data_sources'])
        expected_id = hashlib.sha256((run_id + ':' + binding.dsl_hash + ':' + value['node_key']).encode()).hexdigest()
        step = session.get(ExecutionStep, value['execution_id'])
        if (step is None or value['execution_id'] != expected_id or step.run_id != run_id
                or step.node_key != value['node_key'] or not step.side_effect or step.status != 'succeeded'
                or not step.completed_at or not step.result_ref):
            return False
        committed = _blob(session, run_id, step.result_ref)
        receipts = [receipt for slot in committed.get('agents', {}).values()
                    for receipt in slot.get('document_artifact_receipts', [])]
        receipts += committed.get('document_node_receipts', {}).get(value['execution_id'], [])
        if artifact_receipt(value, value['execution_id']) not in receipts:
            return False
        path = Path(value['path']).resolve()
        expected_root = file_root / 'documents' / run_id
        if (not path.is_relative_to(expected_root) or not expected_root.is_relative_to(file_root)
                or not path.is_file() or value['mime_type'] != MIME[value['type']]
                or path.stat().st_size != value['size_bytes']
                or hashlib.sha256(path.read_bytes()).hexdigest() != value['sha256']):
            return False
    if (len(epochs) != 1 or seen_sources != {(item['kind'], item['resource_id']) for item in sources_for_run(run)}
            or target.kind == 'artifact' and target.resource_id not in run['document_artifact_ids']):
        return False
    _bindings(session, context, runrow, run.get('data_sources', []), next(iter(epochs)))
    return True
