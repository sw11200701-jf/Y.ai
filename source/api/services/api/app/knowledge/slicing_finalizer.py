"""Portable derived knowledge and real CSV artifact in one fenced transaction."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from sqlalchemy import select, update
from app.db import Resource, get_resource, save_resource, utc_now
from app.errors import AppError
from app.settings import settings
from app.data_access import _unrestricted, record_derivation, policy_version
from . import service as s
from .slicing_contracts import digest, encoded, fail, quality_gate,item_occurrences
from .slicing_csv import HEADERS, SEMANTIC_HEADERS, SEMANTIC_CONTRACT, TEXT_HEADERS, TEXT_CONTRACT, encode_csv
from .slicing_document import source_guard, reconstruct

CONTRACT = 'knowledge-portable-csv-v1'


def authorize_export(plan, root, file, *, context, session, version_id=None):
    refs = [('knowledge_base', root['knowledge_base_id']), ('knowledge_document', root['id']),
            ('document' if file['id'].startswith('document_') else 'knowledge_file', file['id'])]
    if version_id: refs.append(('knowledge_version', version_id))
    for kind, identifier in refs:
        if not _unrestricted(s.decision(session, kind, identifier, context, 'export_detail')):
            raise AppError('DATA_SCOPE_EXPORT_DENIED', '正式CSV需要全部原件、知识库及版本的当前全文明细导出权限', 403)
    return [{'kind': kind, 'resource_id': identifier, 'action': 'export_detail'} for kind, identifier in refs]


def csv_rows(result, relations, registry, *, resolve_url, include_task_navigation=False, columns_contract=None):
    if columns_contract not in (None, SEMANTIC_CONTRACT, TEXT_CONTRACT): fail('CSV列合同不受支持', 'CSV_INVALID')
    headers = TEXT_HEADERS if columns_contract == TEXT_CONTRACT else SEMANTIC_HEADERS if columns_contract == SEMANTIC_CONTRACT else HEADERS
    accepted = [r['relation'] for r in relations if r['decision'] == 'accept']
    rows = []
    from .slicing_task_navigation import declared_links,labels
    task_links=declared_links(result['items']) if include_task_navigation else {}
    for item in result['items']:
        identifier = item['knowledge_id']
        links = []
        for relation in accepted:
            if identifier not in (relation['from_id'], relation['to_id']): continue
            forward = identifier == relation['from_id']
            other = relation['to_id'] if forward else relation['from_id']
            arrow = '↔' if relation['type'] in ('alternative', 'related') else '→' if forward else '←'
            links.append(f"{relation['type']} {arrow} {other}")
        links.extend(value for value in labels(task_links,identifier) if value not in links)
        urls, positions = [], []
        for sid in item['source_ids']:
            source = registry['sources'][sid]
            positions.append(encoded({'source_id': sid, 'locator': source['locator']}))
        for oid in ([] if columns_contract == TEXT_CONTRACT else item_occurrences(item)):
            occurrence = registry['occurrences'][oid]
            url = resolve_url(occurrence)
            if not isinstance(url, str) or not url.startswith(('https://', 'http://')): fail('图片下载URL未由可信服务端注册', 'SLICING_ASSET_URL_UNAVAILABLE')
            urls.append(url)
            positions.append(encoded({'url_slot': len(urls), 'occurrence_id': oid, 'locator': occurrence['locator']}))
        rows.append(dict(zip(headers, [identifier, item['content'], '\n'.join(urls), '\n'.join(links), '\n'.join(positions)])))
    return rows


def finalize(plan, *, read_step, context, session):
    from app.reporting.document_artifacts import issued_state
    effect,_,_=issued_state(context,session)
    root, file, _ = source_guard(plan, context=context, session=session)
    state = reconstruct(plan, read_step=read_step, context=context, session=session)
    quality_gate(state['result'], final_audit_receipt=state['final_audit_receipt'], candidate_sha256=state['candidate_sha256'], relations=state['relations'],terminal_manifest=state.get('terminal_manifest'))
    sources = authorize_export(plan, root, file, context=context, session=session)
    # URL registration is deliberately not delegated to the model. The asset
    # download adapter must be registered before image-bearing CSV can commit.
    from .slicing_assets import occurrence_url
    rows = csv_rows(state['result'], state['relations'], plan['registry'],
                    resolve_url=lambda occurrence: occurrence_url(plan, occurrence, context=context, session=session),
                    include_task_navigation=plan.get('task_navigation_contract')=='approved-declared-task-navigation-v1',
                    columns_contract=plan.get('csv_columns_contract'))
    variant = plan.get('csv_variant', 'original')
    headers = TEXT_HEADERS if plan.get('csv_columns_contract') == TEXT_CONTRACT else SEMANTIC_HEADERS if plan.get('csv_columns_contract') == SEMANTIC_CONTRACT else HEADERS
    rendered = encode_csv(rows, variant=variant, headers=headers)
    if len(rendered['content']) > 10 * 1024 * 1024: fail('完整CSV超过10MiB限制，未截断', 'CSV_LIMIT')
    row = session.scalar(select(Resource).where(Resource.kind == 'knowledge_document', Resource.resource_id == root['id'],
        Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id))
    if row is None: fail('来源文档已不存在')
    old = row.payload
    if session.execute(update(Resource).where(Resource.id == row.id, Resource.payload == old).values(payload=old)).rowcount != 1: fail('来源文档并发变化')
    root, file, _ = source_guard(plan, context=context, session=session)
    if root.get('draft_version_id') != plan['parent_draft_id']: fail('父草稿已变化，禁止覆盖或重放')
    if session.scalar(select(Resource.id).where(Resource.kind == 'knowledge_version', Resource.resource_id == plan['document_version_id'])):
        fail('固定版本已经存在，禁止重复写入或覆盖')
    chunks = []
    for number, item in enumerate(state['result']['items'], 1):
        source = plan['registry']['sources'][item['source_ids'][0]]
        original = '\n'.join(e['quote'] for e in item['evidence'])
        chunks.append({**deepcopy(item), 'id': item['knowledge_id'], 'version_id': plan['document_version_id'],
            'file_id': file['id'], 'knowledge_base_id': root['knowledge_base_id'], 'sequence': number,
            'text': item['content'], 'original_text': original, 'text_sha256': s.text_hash(item['content']),
            'original_text_sha256': s.text_hash(original), 'strategy': 'semantic_derived', 'enabled': True, 'status': 'enabled',
            'source_ref': f"knowledge:{plan['document_version_id']}:{item['knowledge_id']}",
            'source_label': encoded(source['locator']), 'locator': deepcopy(source['locator']), **s.counts(item['content'])})
    version = {'id': plan['document_version_id'], 'resource_kind': 'knowledge_document', 'resource_id': root['id'],
        'knowledge_base_id': root['knowledge_base_id'], 'file_id': file['id'], 'status': 'validated', 'parent_version_id': plan['parent_draft_id'],
        'chunks': chunks, 'chunk_count': len(chunks), 'enabled_chunk_count': len(chunks), 'disabled_chunk_count': 0,
        'char_count': sum(c['char_count'] for c in chunks), 'content_sha256': s.digest(chunks), 'source_sha256': file['sha256'],
        'source_refs': [c['source_ref'] for c in chunks], 'source_text_length': sum(len(v['text']) for v in plan['registry']['sources'].values()),
        'overview': {'topic': '知识切片', 'summary': '\n'.join(i['content'] for i in state['result']['items'] if i['knowledge_layer'] == 'scenario_overview')},
        'overview_origin': 'model', 'chunking': {'strategy': 'portable_agent', 'recipe_version': plan['recipe_version']},
        'portable_execution_run_id':effect.run_id,'portable_result': state, 'portable_registry': deepcopy(plan['registry']), 'execution_receipts': state['receipts'],
        'changes': {'operation': 'portable_semantic_agent', 'job_id': plan['job_id']}, 'created_at': utc_now(),
        'errors': [], 'warnings': deepcopy(plan['registry']['warnings']), 'retrieval_method': 'keyword_overlap_v1', 'embedding_status': 'not_configured'}
    version['overview_sha256'] = s.digest(version['overview'])
    version['semantic_completion'] = {'contract_version': 'knowledge-semantic-finalizer-v2', 'recipe_version': plan['recipe_version'],
        'plan_sha256': digest(plan), 'payload_sha256': digest(state)}
    s.save_resource(session, 'knowledge_version', version['id'], version, context)
    if root.get('data_policy_generation') == 2: s.link(session, context, 'knowledge_version', version['id'], 'knowledge_document', root['id'])
    root.update(draft_version_id=version['id'], updated_at=utc_now())
    if root['status'] != 'published': root['status'] = 'validated'
    s.save_resource(session, 'knowledge_document', root['id'], root, context)
    version['semantic_completion']['policy_revision'] = policy_version(session, context)
    s.save_resource(session, 'knowledge_version', version['id'], version, context)
    sources = authorize_export(plan, root, file, context=context, session=session, version_id=version['id'])
    artifact = persist_csv(plan, version, rendered, sources, context=context, session=session)
    return version, artifact


def persist_csv(plan, version, rendered, sources, *, context, session):
    from app.reporting.document_artifacts import issued_state
    effect, generation, run = issued_state(context, session)
    identifier = 'csv_artifact_' + effect.execution_id
    if session.scalar(select(Resource.id).where(Resource.kind == 'artifact', Resource.resource_id == identifier)): fail('CSV执行目标已存在，禁止覆盖')
    directory = (settings.artifact_dir.resolve() / 'knowledge-csv' / effect.run_id).resolve()
    if not directory.is_relative_to(settings.artifact_dir.resolve()): fail('CSV目录越界')
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (effect.execution_id + '.csv')
    stage = directory / (effect.execution_id + '.stage')
    if target.exists() or stage.exists(): fail('CSV文件目标已存在，禁止自动认领或覆盖')
    with stage.open('xb') as handle:
        handle.write(rendered['content']); handle.flush(); os.fsync(handle.fileno())
    os.link(stage, target); stage.unlink()
    _, fresh_generation, current = issued_state(context, session)
    if generation != fresh_generation: fail('CSV登记前租约发生变化')
    source_guard(plan, context=context, session=session)
    value = {'id': identifier, 'artifact_kind': 'knowledge_csv', 'contract_version': CONTRACT, 'run_id': effect.run_id,
        'execution_id': effect.execution_id, 'node_key': effect.node_key, 'document_id': plan['document_id'], 'version_id': version['id'],
        'filename': '知识切片-' + version['id'] + ('-Excel安全副本' if rendered['variant'] == 'excel_safe' else '') + '.csv',
        'type':'csv','mime_type': 'text/csv; charset=utf-8', 'size_bytes': len(rendered['content']), 'sha256': hashlib.sha256(rendered['content']).hexdigest(),
        'path': str(target), 'created_at': utc_now(), 'data_sources': sources, 'variant': rendered['variant'],
        'escaped_cells': rendered['escaped_cells'], 'row_count': rendered['row_count'], 'plan_sha256': digest(plan),
        'version_payload_sha256': version['semantic_completion']['payload_sha256'], 'derivation_policy_revision': policy_version(session, context)}
    save_resource(session, 'artifact', identifier, value, context)
    record_derivation(session=session, context=context, kind='artifact', resource_id=identifier, sources=sources)
    for name in ('artifact_ids', 'knowledge_csv_artifact_ids'):
        current.setdefault(name, []).append(identifier)
    save_resource(session, 'run', effect.run_id, current, context)
    return value


def checked_csv(identifier, *, context, session):
    from app.data_access import require_derivation_access
    from app.execution.models import ExecutionRun, ExecutionStep, ExecutionBlob
    require_derivation_access(session=session, context=context, kind='artifact', resource_id=identifier, completed_read=True)
    value = get_resource(session, 'artifact', identifier, context)
    run = get_resource(session, 'run', value['run_id'], context)
    if value.get('artifact_kind') != 'knowledge_csv' or identifier not in run.get('artifact_ids', []) or identifier not in run.get('knowledge_csv_artifact_ids', []): fail('CSV未绑定当前运行')
    from app.execution.semantic_jobs import load_plan
    plan = load_plan(session, context, run, completed_read=run.get('status') == 'succeeded')['plan']
    root, file, _ = source_guard(plan, context=context, session=session, completed=True)
    authorize_export(plan, root, file, context=context, session=session, version_id=value['version_id'])
    version = s.checked_version(value['version_id'], root, context, session)
    if version.get('semantic_completion', {}).get('payload_sha256') != value['version_payload_sha256'] or value['plan_sha256'] != digest(plan): fail('CSV对应版本或固定计划不符')
    binding = session.get(ExecutionRun, run['id'])
    expected = hashlib.sha256((run['id'] + ':' + binding.dsl_hash + ':' + value['node_key']).encode()).hexdigest() if binding else None
    step = session.get(ExecutionStep, value['execution_id'])
    if (not step or step.execution_id != expected or step.status != 'succeeded' or not step.side_effect or not step.result_ref): fail('CSV尚未完成持久提交')
    blob = session.get(ExecutionBlob, step.result_ref)
    if not blob or blob.run_id != run['id']: fail('CSV缺少真实结果账本')
    receipt = json.loads(blob.payload).get('knowledge_csv_receipt', {})
    if receipt != csv_receipt(value): fail('CSV附件不符合提交回执')
    path = Path(value['path']).resolve()
    root_path = (settings.artifact_dir.resolve() / 'knowledge-csv' / run['id']).resolve()
    if not root_path.is_relative_to(settings.artifact_dir.resolve()) or not path.is_relative_to(root_path) or not path.is_file(): fail('CSV文件目录或内容不可用')
    content = path.read_bytes()
    if len(content) != value['size_bytes'] or hashlib.sha256(content).hexdigest() != value['sha256']: fail('CSV文件完整性校验失败')
    return value, content


def csv_receipt(value):
    return {'artifact_id': value['id'], 'sha256': value['sha256'], 'execution_id': value['execution_id'],
            'metadata_sha256': digest({k: v for k, v in value.items() if k != 'path'})}


def validate_completed_csv_target(session, context, target):
    """Current ACL plus original immutable finalize witness; never repair a stamp."""
    if target.kind not in ('run', 'artifact'): return None
    try:
        payload = json.loads(target.payload)
        if target.kind == 'artifact' and payload.get('artifact_kind') != 'knowledge_csv': return None
        run_id = target.resource_id if target.kind == 'run' else payload['run_id']
        row = session.scalar(select(Resource).where(Resource.kind == 'run', Resource.resource_id == run_id,
            Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id, Resource.owner_id == context.operator_id))
        if row is None: return False
        run = json.loads(row.payload)
        if not run.get('knowledge_csv_artifact_ids'): return None
        # An active run is not a completed-result target. Its ordinary live
        # owner/source checks must remain in force while the fenced finalizer
        # writes and the runtime records completion. This grants no historical
        # policy exception (None), and never admits an uncommitted artifact.
        if target.kind == 'run' and run.get('status') in ('queued', 'running', 'resuming'): return None
        if run.get('status') != 'succeeded': return False
        from app.execution.models import ExecutionBlob
        bound = session.get(ExecutionBlob, run['semantic_job']['plan_ref'])
        if not bound or bound.run_id != run_id: return False
        wrapper = json.loads(bound.payload)
        if digest(wrapper) != run['semantic_job']['plan_sha256']: return False
        plan = wrapper['plan']
        from app.execution.slicing_read_receipt import make_readers, validate_completed
        read_completion, read_step = make_readers(run_id)
        validate_completed(plan, read_completion=read_completion, read_step=read_step, context=context, session=session)
        root, file, _ = source_guard(plan, context=context, session=session, completed=True)
        expected_sources = authorize_export(plan, root, file, context=context, session=session, version_id=plan['document_version_id'])
        if target.kind == 'artifact':
            if target.resource_id not in run['knowledge_csv_artifact_ids'] or payload['data_sources'] != expected_sources: return False
            from app.data_models import DataDerivation
            actual = []
            for edge in session.scalars(select(DataDerivation).where(DataDerivation.resource_pk == target.id)):
                source = session.get(Resource, edge.source_pk)
                if not source or edge.actor_id != context.operator_id or edge.policy_revision != payload['derivation_policy_revision']: return False
                actual.append((source.kind, source.resource_id, edge.required_action))
            if sorted(actual) != sorted((v['kind'], v['resource_id'], v['action']) for v in expected_sources): return False
        return True
    except (AppError, KeyError, ValueError, TypeError, IndexError): return False
