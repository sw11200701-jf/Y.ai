"""Fresh, workspace-scoped model references. Never serialize ResolvedModel."""
from dataclasses import dataclass, field
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
from sqlalchemy import select, update
from pydantic import ValidationError
from app.db import Resource, new_id, utc_now, get_resource, list_resources
from app.auth import validate_context
from app.rbac import require_permission, require_any_permission, RUN_PERMISSIONS, audit
from app.errors import AppError
from .schemas import ModelWrite, Capabilities
from .security import validate_model_endpoint, validate_model_url

PROVIDER = 'model_provider'
MODEL = 'model_configuration'
_synthetic_test_access = ContextVar('catalog_synthetic_test_access', default=None)


def scoped(session, context, kind, identifier=None):
    query = select(Resource).where(Resource.tenant_id == context.tenant_id,
        Resource.workspace_id == context.workspace_id, Resource.kind == kind)
    if identifier is not None:
        query = query.where(Resource.resource_id == identifier)
    return query.execution_options(populate_existing=True)


def manage(context, session, *, write=False):
    require_permission(context, session, 'page.models')
    if write:
        require_permission(context, session, 'models.manage')


def managed_row(session, context, kind, identifier, *, write=False):
    manage(context, session, write=write)
    get_resource(session, kind, identifier, context, 'write' if write else 'read')
    row = session.scalar(scoped(session, context, kind, identifier))
    if row is None or json.loads(row.payload).get('archived'):
        raise AppError('NOT_FOUND', '模型配置不存在或无权访问', 404)
    return row, json.loads(row.payload)


def provider_view(value):
    keys = ('id', 'name', 'protocol', 'preset', 'base_url', 'auth_type', 'timeout_seconds',
            'allow_private_network', 'enabled', 'revision', 'created_at', 'updated_at')
    return {**{key: value[key] for key in keys}, 'has_key': bool(value.get('secret_ref')), 'source': 'managed'}


def model_view(value, provider):
    keys = ('id', 'provider_id', 'name', 'model_name', 'purpose', 'enabled', 'workspace_use',
            'context_window', 'max_output_tokens', 'capabilities', 'revision', 'created_at', 'updated_at')
    return {**{key: value[key] for key in keys}, 'config_version': version(value, provider), 'source': 'managed'}


def version(model, provider):
    return f'p{provider["revision"]}:m{model["revision"]}'


def insert(session, context, kind, value):
    stamp = utc_now()
    value = {**value, 'revision': 1, 'created_at': stamp, 'updated_at': stamp}
    session.add(Resource(kind=kind, resource_id=value['id'], tenant_id=context.tenant_id,
        workspace_id=context.workspace_id, owner_id=context.operator_id,
        payload=json.dumps(value, ensure_ascii=False), created_at=stamp, updated_at=stamp))
    audit(session, context, 'model_catalog_create', kind, value['id'])
    session.commit()
    return value


def replace(session, context, row, previous, changes, expected_revision):
    if previous['revision'] != expected_revision:
        raise AppError('MODEL_VERSION_CHANGED', '配置已变化，请刷新后重试', 409)
    value = {**previous, **changes, 'revision': previous['revision'] + 1, 'updated_at': utc_now()}
    result = session.execute(update(Resource).where(Resource.id == row.id, Resource.payload == row.payload)
        .values(payload=json.dumps(value, ensure_ascii=False), updated_at=value['updated_at']),
        execution_options={'synchronize_session': False})
    if result.rowcount != 1:
        session.rollback()
        raise AppError('MODEL_VERSION_CHANGED', '配置被其他操作更新，请刷新后重试', 409)
    audit(session, context, 'model_catalog_update', row.kind, row.resource_id,
          {'revision': value['revision'], 'fields': sorted(key for key in changes if key != 'secret_ref')})
    session.commit()
    session.expire(row)
    return value


def credential_changes(body, old=None):
    old = old or {}
    values = body.model_dump(exclude_unset=True, exclude={'expected_revision', 'api_key', 'clear_api_key', 'retain_api_key'})
    raw = body.api_key.get_secret_value() if body.api_key is not None else ''
    if any(c in raw for c in '\r\n') or raw and not raw.strip():
        raise AppError('VALIDATION_ERROR', '认证密钥格式无效', 422)
    if raw and body.clear_api_key:
        raise AppError('VALIDATION_ERROR', '不能同时提交密钥和清除密钥', 422)
    endpoint = validate_model_url(values.get('base_url', old.get('base_url')))
    if not old or raw or {'base_url', 'allow_private_network'} & values.keys():
        validate_model_endpoint(endpoint, values.get('allow_private_network', old.get('allow_private_network', False)))
    values['base_url'] = endpoint
    if old.get('secret_ref') and endpoint != old['base_url'] and not (raw or body.clear_api_key or body.retain_api_key):
        raise AppError('VALIDATION_ERROR', '更换地址时请明确确认沿用密钥或提交新密钥', 422)
    ref = None if body.clear_api_key else old.get('secret_ref')
    auth = values.get('auth_type', old.get('auth_type'))
    if auth == 'none' and (raw or ref):
        raise AppError('VALIDATION_ERROR', '无密钥认证需要明确清除已有密钥', 422)
    if auth == 'api_key' and not (raw or ref):
        raise AppError('MODEL_CREDENTIAL_MISSING', '密钥认证平台需要配置密钥', 409)
    if raw:
        from app.secrets import store_secret
        ref = new_id('model_credential')
        try:
            store_secret(ref, raw)
        except Exception:
            raise AppError('MODEL_CREDENTIAL_MISSING', '密钥加密保存失败，配置未更新', 503) from None
    values['secret_ref'] = ref
    return values


def forget_old_secret(old, new):
    if old.get('secret_ref') and old['secret_ref'] != new.get('secret_ref'):
        from app.secrets import delete_secret
        try:
            delete_secret(old['secret_ref'])
        except Exception:
            # DB already revoked the reference; never restore it on vault I/O failure.
            # The inaccessible orphan remains encrypted and can be cleaned offline.
            pass


def create_provider(session, context, body):
    manage(context, session, write=True)
    # Defaults must be stored too; patches intentionally only contain sent fields.
    complete = type(body).model_validate(body.model_dump())
    values = complete.model_dump(exclude={'api_key', 'clear_api_key', 'retain_api_key'})
    values.update(credential_changes(complete))
    values['id'] = new_id('mp')
    return provider_view(insert(session, context, PROVIDER, values))


def update_provider(session, context, identifier, body):
    row, previous = managed_row(session, context, PROVIDER, identifier, write=True)
    if previous['revision'] != body.expected_revision:
        raise AppError('MODEL_VERSION_CHANGED', '平台配置已变化，请刷新后重试', 409)
    changes = credential_changes(body, previous)
    value = replace(session, context, row, previous, changes, body.expected_revision)
    forget_old_secret(previous, value)
    return provider_view(value)


def create_model(session, context, provider_id, body):
    _, provider = managed_row(session, context, PROVIDER, provider_id, write=True)
    value = body.model_dump()
    value.update(id=new_id('mc'), provider_id=provider_id)
    return model_view(insert(session, context, MODEL, value), provider)


def update_model(session, context, identifier, body):
    row, previous = managed_row(session, context, MODEL, identifier, write=True)
    _, provider = managed_row(session, context, PROVIDER, previous['provider_id'], write=True)
    changes = body.model_dump(exclude_unset=True, exclude={'expected_revision'})
    try:
        ModelWrite.model_validate({key: changes.get(key, previous[key]) for key in ModelWrite.model_fields})
    except ValidationError:
        raise AppError('VALIDATION_ERROR', '模型能力或输出/上下文上限配置无效', 422) from None
    return model_view(replace(session, context, row, previous, changes, body.expected_revision), provider)


def references(session, context, identifier):
    def workflow_uses(value):
        for node in value.get('nodes', []):
            config = node.get('config', {})
            if node.get('type') in ('llm', 'parameter_extractor', 'question_classifier') and config.get('model_config_id') == identifier:
                return True
            if node.get('type') in ('iteration', 'loop') and workflow_uses(config.get('body', {})):
                return True
        return False
    def contains(kind, value):
        if kind == 'workflow':
            return workflow_uses(value)
        if kind == 'agent':
            return value.get('model_config_id') == identifier
        if kind == 'agent_version':
            return value.get('config', {}).get('model_config_id') == identifier
        return (identifier in value.get('model_bindings', {}) or workflow_uses(value.get('workflow_snapshot', {}))
            or workflow_uses((value.get('request') or {}).get('workflow') or {})
            or any(config.get('model_config_id') == identifier for config in value.get('agent_snapshots', {}).values()))
    result = []
    # Includes run snapshots: a historical reference cannot silently become another model.
    for row in session.scalars(select(Resource).where(Resource.tenant_id == context.tenant_id,
            Resource.workspace_id == context.workspace_id, Resource.kind.in_(('workflow', 'agent', 'agent_version', 'run')))):
        if contains(row.kind, json.loads(row.payload)):
            result.append(row)
    return result


def archive_model(session, context, identifier, expected_revision):
    row, value = managed_row(session, context, MODEL, identifier, write=True)
    managed_row(session, context, PROVIDER, value['provider_id'], write=True)
    if references(session, context, identifier):
        raise AppError('MODEL_IN_USE', '模型仍被工作流、智能体或运行快照引用，可停用但不能删除', 409)
    replace(session, context, row, value, {'archived': True, 'enabled': False}, expected_revision)
    return {'deleted': True}


def archive_provider(session, context, identifier, expected_revision):
    row, value = managed_row(session, context, PROVIDER, identifier, write=True)
    children = [json.loads(item.payload) for item in session.scalars(scoped(session, context, MODEL))]
    if any(item['provider_id'] == identifier and not item.get('archived') for item in children):
        raise AppError('MODEL_IN_USE', '平台仍有模型，请先处理平台内模型', 409)
    updated = replace(session, context, row, value, {'archived': True, 'enabled': False, 'secret_ref': None}, expected_revision)
    forget_old_secret(value, updated)
    return {'deleted': True}


@dataclass(frozen=True)
class ResolvedModel:
    base_url: str
    model: str
    secret_ref: str | None = field(repr=False)
    timeout_seconds: float
    allow_private_network: bool
    model_config_id: str
    config_version: str
    capabilities: dict
    context_window: int
    max_output_tokens: int


def legacy_configs(session, context, *, strict=True):
    from app.auth_models import Installation, User
    installation = session.get(Installation, 1)
    admin = session.get(User, installation.admin_user_id) if installation else None
    if admin is None or (admin.tenant_id, admin.workspace_id) != (context.tenant_id, context.workspace_id):
        return {}
    filename = os.environ.get('FINANCE_MODEL_CONFIG_FILE')
    if not filename:
        return {}
    try:
        with Path(filename).open('rb') as source:
            raw = source.read(262145)
        if len(raw) > 262144:
            raise ValueError()
        values = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(values, dict) or len(values) > 100:
            raise ValueError()
        result = {}
        for key, item in values.items():
            if not isinstance(key, str) or not 1 <= len(key) <= 100 or not isinstance(item, dict):
                raise ValueError()
            if set(item) - {'base_url', 'model', 'secret_ref', 'timeout_seconds'}:
                raise ValueError()
            url = validate_model_url(item['base_url'])
            model = item['model'];timeout = item.get('timeout_seconds', 20);secret = item.get('secret_ref')
            if not isinstance(model, str) or not 1 <= len(model) <= 200 or type(timeout) not in (int, float) or not .1 <= timeout <= 300 or secret is not None and not isinstance(secret, str):
                raise ValueError()
            result[key] = ResolvedModel(url, model, secret, timeout, True, key,
                'legacy:' + hashlib.sha256(raw).hexdigest(), Capabilities(tools=True, json_object=True).model_dump(), 8192, 4096)
        return result
    except (OSError, ValueError, KeyError, TypeError, AppError):
        if not strict:
            return {}
        raise AppError('MODEL_NOT_CONFIGURED', '旧本机模型文件不可用，请由管理员核对', 503) from None


def can_use(session, context, value):
    if getattr(context,'principal_type','human')=='platform':
        from app.platform_api import require_token_resource
        row=session.scalar(scoped(session,context,MODEL,value['id']))
        if row is None:return False
        try:require_token_resource(session,context,row,'read')
        except AppError as exc:
            if exc.status_code in {403,404}:return False
            raise
        return True
    if value.get('workspace_use') and getattr(context,'principal_type','human')!='service':
        return True
    try:
        get_resource(session, MODEL, value['id'], context)
        return True
    except AppError as exc:
        if exc.status_code == 404:
            return False
        raise


@contextmanager
def bind_model_test_access(*, context, session, config_id):
    """Server-only lease for this fixed synthetic test, never a general run grant."""
    _, value = managed_row(session, context, MODEL, config_id, write=True)
    managed_row(session, context, PROVIDER, value['provider_id'], write=True)
    identity = (context.tenant_id, context.workspace_id, context.operator_id, context.session_id, config_id)
    token = _synthetic_test_access.set(identity)
    try:
        yield
    finally:
        _synthetic_test_access.reset(token)


def resolve_runtime_model(config_id, *, context, session, expected_version=None):
    lease = (context.tenant_id, context.workspace_id, context.operator_id, context.session_id, config_id)
    if _synthetic_test_access.get() == lease:
        # Fresh management permission and write ACL on every adapter re-resolution.
        _, managed = managed_row(session, context, MODEL, config_id, write=True)
        managed_row(session, context, PROVIDER, managed['provider_id'], write=True)
    else:
        require_any_permission(context, session, RUN_PERMISSIONS)
    row = session.scalar(scoped(session, context, MODEL, config_id))
    machine=getattr(context,'principal_type','human')=='service'
    legacy = {} if machine else legacy_configs(session, context, strict=row is None)
    if row is not None and config_id in legacy:
        raise AppError('MODEL_ID_AMBIGUOUS', '模型ID与旧本机配置冲突，请管理员核对', 409)
    if row is None:
        config = legacy.get(config_id)
        if config is None:
            raise AppError('MODEL_NOT_AVAILABLE', '所选模型不存在或未获授权', 404)
    else:
        value = json.loads(row.payload)
        if value.get('archived') or not can_use(session, context, value):
            raise AppError('MODEL_NOT_AVAILABLE', '所选模型不存在或未获授权', 404)
        parent = session.scalar(scoped(session, context, PROVIDER, value['provider_id']))
        if parent is None:
            raise AppError('MODEL_NOT_AVAILABLE', '所选模型平台不存在或不可用', 404)
        provider = json.loads(parent.payload)
        if machine or context.principal_type=='platform':get_resource(session,PROVIDER,value['provider_id'],context)
        if provider.get('archived') or not provider['enabled'] or not value['enabled']:
            raise AppError('MODEL_DISABLED', '所选模型或平台已停用，请重新核对配置', 409)
        if provider['auth_type'] == 'api_key' and not provider.get('secret_ref'):
            raise AppError('MODEL_CREDENTIAL_MISSING', '所选模型平台缺少认证密钥', 409)
        config = ResolvedModel(validate_model_url(provider['base_url']), value['model_name'], provider.get('secret_ref'),
            provider['timeout_seconds'], provider['allow_private_network'], config_id, version(value, provider),
            value['capabilities'], value['context_window'], value['max_output_tokens'])
    if expected_version is not None and config.config_version != expected_version:
        raise AppError('MODEL_VERSION_CHANGED', '模型配置已变化，请重新选择并保存模型版本', 409)
    return config


def usable_catalog(session, context):
    require_any_permission(context, session, RUN_PERMISSIONS)
    items = []
    managed = {row.resource_id: json.loads(row.payload) for row in session.scalars(scoped(session, context, MODEL))}
    legacy = legacy_configs(session, context, strict=False)
    for identifier in sorted(set(managed) | set(legacy)):
        try:
            config = resolve_runtime_model(identifier, context=context, session=session)
        except AppError as exc:
            if exc.code in {'MODEL_NOT_AVAILABLE', 'MODEL_DISABLED', 'MODEL_CREDENTIAL_MISSING'}:
                continue
            raise
        value = managed.get(identifier)
        provider_name = '本机配置（只读）'
        if value:
            provider_name = json.loads(session.scalar(scoped(session, context, PROVIDER, value['provider_id'])).payload)['name']
        items.append({'id': identifier, 'name': value['name'] if value else identifier, 'provider_name': provider_name,
            'source': 'managed' if value else 'legacy_file', 'config_version': config.config_version,
            'capabilities': config.capabilities, 'context_window': config.context_window, 'max_output_tokens': config.max_output_tokens})
    return {'items': items}


def pin_run_models(payload, *, context, session):
    bindings = {}
    def bind(value):
        identifier = value.get('model_config_id')
        if identifier and value.get('mode') != 'demo':
            config = resolve_runtime_model(identifier, context=context, session=session,
                expected_version=value.get('model_config_version'))
            value['model_config_version'] = config.config_version
            if identifier in bindings and bindings[identifier] != config.config_version:
                raise AppError('MODEL_VERSION_CHANGED', '运行中包含不同的模型配置版本', 409)
            bindings[identifier] = config.config_version
    # Walk only actual execution config, never user messages/business inputs.
    def workflow(definition):
        for node in definition.get('nodes', []):
            config = node.get('config', {})
            if node.get('type') in ('llm', 'parameter_extractor', 'question_classifier'):
                if config.get('model_config_id') and config.get('mode') != 'demo':
                    bind(config)
            if node.get('type') in ('iteration', 'loop'):
                workflow(config.get('body', {}))
    workflow(payload.get('workflow_snapshot', {}))
    for config in payload.get('agent_snapshots', {}).values():
        # Agent business data may contain arbitrary keys; only its model selection is trusted config.
        selection = {key: config[key] for key in ('mode', 'model_config_id', 'model_config_version') if key in config}
        bind(selection)
        if 'model_config_version' in selection:
            config['model_config_version'] = selection['model_config_version']
    payload['model_bindings'] = bindings
    return bindings
