"""Typed binding slots. Pure replacement of server-validated resource selections.

No paths, credentials, authorization flags or generic patches from the client.
Slot locators are recomputed exclusively from the server-reparsed workflow.
"""
import hashlib
import json
import re
from copy import deepcopy
from .security import diagnostic

CONVERTER_VERSION = 'workflow-file-io/1.1.5'
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$')


def slots(workflow):
    """Return (public slot, private list of exact target locators) pairs."""
    result = []

    def add(kind, base, fields, required):
        key = 'b_' + hashlib.sha256(json.dumps([kind, base, fields], separators=(',', ':')).encode()).hexdigest()[:20]
        public = dict(key=key, kind=kind, path='$.' + '.'.join(str(p) for p in base),
                      target_fields=list(fields), required_fields=list(required))
        result.append((public, {field: (*base, *suffix) for field, suffix in fields.items()}))

    def walk(graph, scope=()):
        for i, node in enumerate(graph.get('nodes', [])):
            base = (*scope, 'nodes', i)
            typ, config, inputs = node['type'], node.get('config', {}), node.get('inputs', {})
            if typ == 'llm' or typ in {'parameter_extractor', 'question_classifier'} and (config.get('model_config_id') or config.get('mode') == 'model'):
                add('model', base, {'model_config_id': ('config', 'model_config_id'), 'model_config_version': ('config', 'model_config_version')}, ('model_config_id', 'model_config_version'))
            if typ == 'agent':
                add('agent', base, {'agent_id': ('config', 'agent_id'), 'agent_version_id': ('config', 'agent_version_id')}, ('agent_id', 'agent_version_id'))
            if typ in {'plugin', 'http'} and config.get('binding'):
                add('plugin', (*base, 'config'), {'binding': ('binding',)}, ('binding',))
            if typ == 'document_extractor' and isinstance(inputs.get('file_id'), str) and inputs['file_id']:
                add('document', base, {'file_id': ('inputs', 'file_id')}, ('file_id',))
            if typ == 'knowledge_retrieval':
                fields = {k: ('inputs', k) for k in ('file_ids', 'knowledge_base_ids', 'version_ids') if isinstance(inputs.get(k), list) and inputs[k] and all(isinstance(v, str) for v in inputs[k])}
                if fields:
                    add('knowledge', base, fields, tuple(fields))
            if typ == 'database_query':
                fields = {k: ('config', k) for k in ('database_id', 'table_version_id') if config.get(k)}
                if isinstance(inputs.get('table_id'), str) and inputs['table_id']:
                    fields.update(database_id=('config', 'database_id'), table_version_id=('config', 'table_version_id'), table_id=('inputs', 'table_id'))
                if fields:
                    add('database', base, fields, tuple(fields))
            if typ == 'query':
                if isinstance(inputs.get('query'), dict) and isinstance(inputs['query'].get('batch_id'), str) and inputs['query']['batch_id']:
                    add('batch', (*base, 'inputs', 'query'), {'batch_id': ('batch_id',)}, ('batch_id',))
                for j, source in enumerate(inputs.get('sources', [])):
                    if isinstance(source, dict) and source.get('type') == 'plugin' and source.get('binding'):
                        add('plugin', (*base, 'inputs', 'sources', j), {'binding': ('binding',)}, ('binding',))
                    elif isinstance(source, dict) and source.get('type') == 'batch' and source.get('batch_id'):
                        add('batch', (*base, 'inputs', 'sources', j), {'batch_id': ('batch_id',)}, ('batch_id',))
            if typ in {'iteration', 'loop'} and isinstance(config.get('body'), dict):
                walk(config['body'], (*base, 'config', 'body'))
    walk(workflow)
    return result


def _identifier(value):
    return isinstance(value, str) and bool(ID.fullmatch(value)) and not value.startswith('__')


def _target_valid(slot, target):
    if type(target) is not dict or set(target) - set(slot['target_fields']) or not set(slot['required_fields']) <= set(target):
        return False
    for field, value in target.items():
        if field == 'binding':
            if type(value) is not dict or set(value) - {'plugin_version_id', 'tool_id', 'connection_id'} or not {'plugin_version_id', 'tool_id'} <= set(value):
                return False
            if any(not _identifier(v) for k, v in value.items() if k != 'connection_id' or v is not None):
                return False
        elif field in {'file_ids', 'knowledge_base_ids', 'version_ids'}:
            if not isinstance(value, list) or not 1 <= len(value) <= 64 or any(not _identifier(v) for v in value) or len(set(value)) != len(value):
                return False
        elif not _identifier(value):
            return False
    return True


def replace(preview, resolutions, validate):
    """Return ConfirmResult; caller MUST supply a fresh server-generated preview."""
    diagnostics = []
    result = dict(workflow=None, converter_version=CONVERTER_VERSION, source_hash=preview.get('source_hash'),
                  can_confirm=False, diagnostics=diagnostics, resolved_bindings=[])

    def fail(code, message):
        diagnostics.append(diagnostic('unsupported', code, '$.binding_resolutions', message))
        return result

    if preview.get('converter_version') != CONVERTER_VERSION or not isinstance(preview.get('workflow'), dict):
        return fail('BINDING_PREVIEW', '预览版本或工作流不可确认，请重新预览原文件')
    if any(d.get('severity') in {'unsupported', 'lossy'} for d in preview.get('diagnostics', [])):
        return fail('BINDING_BLOCKED', '尚有不支持或有损语义，不能通过资源绑定绕过')
    # Secrets/dependencies without an executable typed slot cannot be acknowledged
    # away by a boolean. They must first be repaired in the source configuration.
    if any(d.get('severity') == 'requires_binding' and d.get('code') not in {'MODEL_REBIND', 'RESOURCE_REBIND'} for d in preview.get('diagnostics', [])):
        return fail('BINDING_UNRESOLVED', '存在不能通过资源选择解决的凭据或依赖迁移项，请先修复文件')
    actual = {slot['key']: (slot, locators) for slot, locators in slots(preview['workflow'])}
    if not isinstance(resolutions, list) or len(resolutions) > 256:
        return fail('BINDING_FORMAT', '资源绑定必须是受限的选择列表')
    seen = set()
    candidate = deepcopy(preview['workflow'])
    for resolution in resolutions:
        if type(resolution) is not dict or set(resolution) != {'key', 'kind', 'target'} or not isinstance(resolution['key'], str):
            return fail('BINDING_FORMAT', '每项绑定仅接受key、kind和结构化target，不接受路径或授权标记')
        key = resolution['key']
        if key not in actual or key in seen:
            return fail('BINDING_SLOT', '资源绑定位置未知或重复，不能应用任意路径修改')
        slot, locators = actual[key]
        if resolution['kind'] != slot['kind'] or not _target_valid(slot, resolution['target']):
            return fail('BINDING_TYPE', '资源选择类型、固定版本或目标字段无效')
        seen.add(key)
        for field, value in resolution['target'].items():
            locator = locators[field]
            parent = candidate
            for part in locator[:-1]:
                if isinstance(parent, dict):
                    parent = parent.setdefault(part, {})
                else:
                    parent = parent[part]
            parent[locator[-1]] = deepcopy(value)
        result['resolved_bindings'].append({'key': key, 'kind': slot['kind']})
    if seen != set(actual):
        return fail('BINDING_MISSING', '必须为全部执行资源重新选择受权目标及确切版本')
    validate(candidate, diagnostics)
    if any(d['severity'] in {'unsupported', 'lossy', 'requires_binding'} for d in diagnostics):
        return result
    diagnostics.extend(deepcopy([d for d in preview.get('diagnostics', []) if d.get('severity') == 'warning']))
    result.update(workflow=candidate, can_confirm=True)
    return result
