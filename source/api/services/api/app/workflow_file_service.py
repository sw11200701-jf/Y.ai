"""Authenticated file transport/persistence; conversion belongs to workflow_io."""
import base64
from copy import deepcopy
import hashlib
import hmac
import json
import secrets
import time
import re
from pydantic import ValidationError
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from app.db import Resource, get_resource, list_resources, save_resource, new_id
from app.errors import AppError
from app.settings import settings
from app.contracts import WorkflowDefinition
from app.runner.validation import validate_workflow

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def gate(session, context):
    from app.auth import validate_context
    from app.rbac import require_any_permission
    validate_context(context, session); require_any_permission(context, session, ('page.ai',))

def converter_version():
    folder = Path(__file__).with_name('workflow_io')
    return digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob('*.py'))})

def key():
    path = settings.data_dir / 'workflow-file-signing.key'; path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as output: output.write(secrets.token_bytes(32))
    except FileExistsError: pass
    value = path.read_bytes()
    if len(value) != 32: raise AppError('FILE_PREVIEW_UNAVAILABLE', '文件预览服务暂不可用', 503)
    return value

def scope(context): return [context.tenant_id, context.workspace_id, context.operator_id]

def preview(content, filename, session, context):
    gate(session, context)
    from app.workflow_io import preview_import
    result = preview_import(content, filename=filename)
    expires = int(time.time()) + 900
    body = dict(scope=scope(context), source_hash=result['source_hash'], converter_version=converter_version(), expires_at=expires)
    encoded = base64.urlsafe_b64encode(json.dumps(body, separators=(',', ':')).encode()).decode()
    token = encoded + '.' + hmac.new(key(), encoded.encode(), hashlib.sha256).hexdigest()
    return {**result, 'preview_token': token, 'converter_version': body['converter_version'], 'expires_at': expires}

def verify_token(token, content, source_hash, context):
    try:
        encoded, signature = token.split('.')
        if not hmac.compare_digest(signature, hmac.new(key(), encoded.encode(), hashlib.sha256).hexdigest()): raise ValueError()
        body = json.loads(base64.urlsafe_b64decode(encoded))
        if body['scope'] != scope(context) or body['expires_at'] < time.time(): raise ValueError()
        if body['source_hash'] != source_hash or source_hash != hashlib.sha256(content).hexdigest(): raise ValueError()
        if body['converter_version'] != converter_version(): raise ValueError()
    except (ValueError, AssertionError, KeyError, TypeError):
        raise AppError('FILE_PREVIEW_EXPIRED', '文件预览已失效或内容已变化，请重新预览', 409) from None

def resolve(bindings, selections, session, context):
    from app.data_access import authorize_data_resource
    if not isinstance(selections, list) or len(selections) != len(bindings):
        raise AppError('INVALID_BINDING', '请为每项文件资源重新选择当前可用资源', 422)
    slots = {b['key']: b for b in bindings}; resolved = []; seen = set()
    def data(kind, identifier, scope=None):
        authorize_data_resource(session=session, context=context, kind=kind, resource_id=identifier, agent_scope=scope)
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) != {'key', 'kind', 'target'}:
            raise AppError('INVALID_BINDING', '资源选择格式无效', 422)
        if not isinstance(selection['key'], str) or not isinstance(selection['kind'], str):
            raise AppError('INVALID_BINDING', '资源选择位置或类型无效', 422)
        slot = slots.get(selection['key']); target = selection['target']
        if slot is None or selection['key'] in seen or selection['kind'] != slot['kind'] or not isinstance(target, dict):
            raise AppError('INVALID_BINDING', '资源选择位置或类型无效', 422)
        if set(target) - set(slot['target_fields']) or not set(slot['required_fields']) <= set(target):
            raise AppError('INVALID_BINDING', '请补全确切资源与版本', 422)
        for field, value in target.items():
            if field == 'binding': continue
            values = value if field in {'file_ids', 'knowledge_base_ids', 'version_ids'} else [value]
            if not isinstance(values, list) or not 1 <= len(values) <= 64 or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', v) for v in values):
                raise AppError('INVALID_BINDING', '资源标识格式无效', 422)
        kind = slot['kind']; target = deepcopy(target); seen.add(slot['key'])
        if kind == 'model':
            from app.model_catalog.service import resolve_runtime_model
            model = resolve_runtime_model(target['model_config_id'], context=context, session=session, expected_version=target['model_config_version'])
            target['model_config_version'] = model.config_version
        elif kind == 'plugin':
            from app.plugins.specs import BindingSelection
            from app.plugins.service import materialize_binding
            try: parsed = BindingSelection.model_validate(target['binding'])
            except ValidationError: raise AppError('INVALID_BINDING', '工具绑定格式无效', 422) from None
            target['binding'] = materialize_binding(parsed, context, session).model_dump(mode='json')
        elif kind == 'agent':
            get_resource(session, 'agent', target['agent_id'], context)
            version = get_resource(session, 'agent_version', target['agent_version_id'], context)
            if version.get('agent_id') != target['agent_id'] or version.get('status') != 'published':
                raise AppError('INVALID_BINDING', '资源或固定版本不可用', 422)
        elif kind == 'document': data('document', target['file_id'])
        elif kind == 'batch': get_resource(session, 'import_batch', target['batch_id'], context)
        elif kind == 'database':
            database_scope = [{'kind': 'database', 'resource_id': target['database_id']}] if target.get('database_id') else None
            if target.get('database_id'): data('database', target['database_id'])
            if target.get('table_id'):
                data('data_table', target['table_id'], database_scope)
            if target.get('table_version_id'):
                table_scope = [{'kind': 'data_table', 'resource_id': target['table_id']}] if target.get('table_id') else None
                data('data_table_version', target['table_version_id'], table_scope or database_scope)
                if table_scope and database_scope: data('data_table_version', target['table_version_id'], database_scope)
        elif kind == 'knowledge':
            for field, resource_kind in [('file_ids', 'document'), ('knowledge_base_ids', 'knowledge_base')]:
                values = target.get(field, [])
                if not isinstance(values, list) or len(values) > 64 or any(not isinstance(v, str) for v in values):
                    raise AppError('INVALID_BINDING', '知识资源选择格式无效', 422)
                for identifier in values: data(resource_kind, identifier)
            for identifier in target.get('version_ids', []):
                row = session.scalar(select(Resource).where(Resource.tenant_id==context.tenant_id, Resource.workspace_id==context.workspace_id,
                    Resource.resource_id==identifier, Resource.kind.in_(['knowledge_version', 'knowledge_source_version'])))
                if row is None: raise AppError('NOT_FOUND', '资源不存在或不可用', 404)
                if row.kind == 'knowledge_source_version':
                    from app.knowledge_models import KnowledgeSourceVersion
                    version = session.get(KnowledgeSourceVersion, identifier, populate_existing=True)
                    if version is None or version.status != 'published': raise AppError('INVALID_BINDING', '固定知识版本尚不可用', 422)
                elif json.loads(row.payload).get('status') != 'published':
                    raise AppError('INVALID_BINDING', '固定知识版本尚不可用', 422)
                parents = [{'kind':'knowledge_base','resource_id':v} for v in target.get('knowledge_base_ids', [])]
                data(row.kind, identifier, parents or None)
        else: raise AppError('INVALID_BINDING', '该资源类型尚不支持确认', 422)
        resolved.append({'key': slot['key'], 'kind': kind, 'target': target})
    return resolved

def confirm(content, filename, token, source_hash, idempotency_key, selections, name, session, context):
    # SQLite cannot safely upgrade two deferred SAVEPOINT readers to writers.
    # Reserve its single writer before reading the receipt or checking bindings.
    # Keep this in the caller's transaction; do not commit auth or draft changes.
    if session.get_bind().dialect.name == 'sqlite':
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            try:
                connection.exec_driver_sql('BEGIN IMMEDIATE')
            except OperationalError as exc:
                if getattr(exc.orig, 'sqlite_errorcode', 0) & 255 in {5, 6}:
                    raise AppError('FILE_CONFIRM_BUSY', '其他确认正在保存，请保留当前内容并使用原确认编号重试', 503) from None
                raise
    from app.execution.node_settings import view
    gate(session, context); verify_token(token, content, source_hash, context)
    from app import workflow_io
    result = workflow_io.preview_import(content, filename=filename)
    if result['bindings']:
        resolved = resolve(result['bindings'], selections, session, context)
        apply = getattr(workflow_io, 'apply_bindings', None)
        if apply is None: raise AppError('FILE_BINDING_UNAVAILABLE', '资源转换接点尚未就绪，请保留预览稍后重试', 503)
        bound = apply(result, resolved)
        result = {**result, **bound}
    elif selections: raise AppError('INVALID_BINDING', '文件不接受额外资源选择', 422)
    if not result.get('can_confirm') or not result.get('workflow'):
        raise AppError('FILE_IMPORT_BLOCKED', '文件仍有未解决的语义或资源问题', 422, details={'diagnostics': result['diagnostics']})
    workflow = WorkflowDefinition.model_validate(result['workflow'])
    validate_workflow(workflow)
    def models(nodes):
        for node in nodes:
            config = node.get('config', {})
            if config.get('model_config_id'):
                from app.model_catalog.service import resolve_runtime_model
                from app.agents.model_adapter import effective_parameters
                model = resolve_runtime_model(config['model_config_id'], context=context, session=session,
                    expected_version=config.get('model_config_version'))
                effective_parameters(config.get('model_parameters'), model)
            if isinstance(config.get('body'), dict): models(config['body'].get('nodes', []))
    models(workflow.model_dump(mode='json')['nodes'])
    payload = workflow.model_dump(mode='json')
    if name: payload['name'] = name
    receipt_id = 'wfi_' + digest([scope(context), idempotency_key])
    request_hash = digest([source_hash, converter_version(), selections, name])
    def existing():
        row = session.scalar(select(Resource).where(Resource.tenant_id == context.tenant_id, Resource.workspace_id == context.workspace_id,
            Resource.kind == 'workflow_file_receipt', Resource.resource_id == receipt_id, Resource.owner_id == context.operator_id))
        if row is None: return None
        value = json.loads(row.payload)
        if value['request_hash'] != request_hash: raise AppError('FILE_IDEMPOTENCY_CONFLICT', '相同确认编号已用于不同内容，请重新确认', 409)
        return get_resource(session, 'workflow', value['workflow_id'], context)
    report = {k: result[k] for k in ('format', 'source_version', 'source_hash', 'diagnostics')}
    found = existing()
    if found: return {'workflow': view(found), 'report': report, 'replayed': True}
    payload['id'] = new_id('workflow')
    try:
        with session.begin_nested():
            save_resource(session, 'workflow_file_receipt', receipt_id, {'request_hash': request_hash, 'workflow_id': payload['id']}, context)
            save_resource(session, 'workflow', payload['id'], payload, context)
    except IntegrityError:
        found = existing()
        if found: return {'workflow': view(found), 'report': report, 'replayed': True}
        raise
    return {'workflow': view(payload), 'report': report, 'replayed': False}

def export(body, session, context):
    gate(session, context)
    from app.workflow_io import export_workflow
    value = get_resource(session, 'workflow', body.workflow_id, context) if body.source == 'saved' else body.workflow
    if body.expected_sha256 and digest(value) != body.expected_sha256: raise AppError('FILE_SOURCE_CHANGED', '所选流程已变化，请重新读取后导出', 409)
    for identifier in body.model_bindings:
        from app.model_catalog.service import resolve_runtime_model
        resolve_runtime_model(identifier, context=context, session=session)
    result = export_workflow(value, format=body.format, target_version=body.target_version, model_bindings=body.model_bindings)
    report = {k: result[k] for k in ('format', 'target_version', 'diagnostics')}; report.update(source=body.source, source_hash=digest(value)); report['blocked'] = result['content'] is None
    file = None
    if result['content'] is not None:
        content = result['content'].encode('utf-8')
        file = dict(filename='workflow-export' + result['extension'], media_type=result['media_type'],
            content_base64=base64.b64encode(content).decode(), sha256=hashlib.sha256(content).hexdigest(), size=len(content))
    return {'report': report, 'file': file}

def catalog(session, context):
    gate(session, context)
    from app.model_catalog.service import usable_catalog
    from app.plugins.service import materialize_binding
    tools = []
    connections = list_resources(session, 'connection', context)
    for version in list_resources(session, 'plugin_version', context):
        if version.get('status') != 'published': continue
        for tool in version.get('tools', []):
            for connection in connections:
                try:
                    binding = materialize_binding({'plugin_version_id': version['id'], 'tool_id': tool['id'], 'connection_id': connection['id']}, context, session)
                    tools.append({**binding.model_dump(mode='json'), 'name': tool['name']})
                except AppError: continue
    resources = []
    for kind in ['knowledge_base', 'knowledge_source', 'document', 'knowledge_file', 'database', 'data_table', 'data_table_version', 'knowledge_version', 'knowledge_source_version', 'agent', 'agent_version', 'workflow', 'connection', 'import_batch']:
        for value in list_resources(session, kind, context):
            if value.get('enabled', True) and value.get('status') not in {'disabled', 'archived'}:
                resources.append({'kind': kind, 'resource_id': value['id'], 'name': value.get('name', value.get('title', '可用资源')), **{k:value[k] for k in ['agent_id','table_id','database_id','source_id','knowledge_base_id','kb_id','status'] if k in value}})
    models = usable_catalog(session, context)
    return {'models': models.get('items', []) if isinstance(models, dict) else models, 'tools': tools, 'resources': resources, 'binding_protocol': 'workflow-file-bindings-v1'}
