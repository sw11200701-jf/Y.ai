"""Portable configuration filtering; deliberately no credential resolution."""
import re

PLACEHOLDER = '__RECONFIGURE_SECRET__'
IDENTITY = {'id', 'tenant_id', 'workspace_id', 'owner', 'owner_id', 'actor_id', 'operator_id', 'grants', 'permissions', 'created_by', 'updated_by'}
# GET detail DTO capabilities/revision are transport metadata, not portable graph
# configuration. Filter only graph roots/bodies, never same-named business values.
RUNTIME_FIELDS = {'system', 'user', 'execution_identity', 'variable_pool', 'trace', 'events', 'outputs', 'presentations', 'request', 'access', 'graph_sha256'}
SECRET = re.compile(r'^(?:(?:[a-z0-9]+[-_])*(?:api[-_]?key|access[-_]?token|refresh[-_]?token|token|password|secret|secret_ref|authorization|cookie|set-cookie|credential(?:s|_id)?))$', re.I)
RESOURCES = {'model_config_id': 'model', 'agent_id': 'agent', 'plugin_version_id': 'plugin', 'connection_id': 'connection', 'knowledge_base_ids': 'knowledge', 'file_ids': 'document', 'file_id': 'document', 'database_id': 'database', 'table_version_id': 'table', 'workflow_id': 'workflow', 'binding': 'plugin'}


def diagnostic(severity, code, path, message):
    return dict(severity=severity, code=code, path=path, message=message)


def sanitize(value, diagnostics, path='$'):
    if type(value) is list:
        return [sanitize(v, diagnostics, f'{path}[{i}]') for i, v in enumerate(value)]
    if type(value) is not dict:
        return value
    secret_record = value.get('value_type') == 'secret' or value.get('type') == 'secret' or bool(SECRET.fullmatch(str(value.get('name', value.get('key', '')))))
    result = {}
    for i, (key, item) in enumerate(value.items()):
        loc = f'{path}.fields[{i}]'
        if SECRET.fullmatch(key) or (key.lower() == 'headers' and isinstance(item, str) and re.search(r'(?im)^\s*(authorization|cookie|x-api-key)\s*:', item)) or (secret_record and key in {'value', 'default', 'content'}):
            result[key] = PLACEHOLDER
            diagnostics.append(diagnostic('requires_binding', 'SECRET_REDACTED', loc, '凭据已替换为占位，须在目标环境重新配置'))
        else:
            result[key] = sanitize(item, diagnostics, loc)
    return result


def strip_runtime_context(workflow, diagnostics, path='$.workflow'):
    """Only actual workflow roots/bodies; a business literal may also be a graph."""
    result = {key: value for key, value in workflow.items() if key not in RUNTIME_FIELDS}
    if len(result) != len(workflow):
        diagnostics.append(diagnostic('warning', 'RUNTIME_CONTEXT_IGNORED', path, '运行身份、系统/用户池、执行结果、权限投影和保存版本不是便携配置，已排除；运行时只采用当前服务端身份'))
    for i, node in enumerate(result.get('nodes', []) if isinstance(result.get('nodes'), list) else []):
        if isinstance(node, dict) and node.get('type') in ('iteration', 'loop') and isinstance(node.get('config'), dict) and isinstance(node['config'].get('body'), dict):
            node['config']['body'] = strip_runtime_context(node['config']['body'], diagnostics, f'{path}.nodes[{i}].config.body')
    return result


def strip_identity(workflow, diagnostics):
    result = {k: v for k, v in workflow.items() if k not in IDENTITY}
    if len(result) != len(workflow):
        diagnostics.append(diagnostic('warning', 'IDENTITY_IGNORED', '$', '文件身份、授权及源流程ID不参与新草稿创建'))
    for node in result.get('nodes', []) if isinstance(result.get('nodes'), list) else []:
        if isinstance(node, dict) and isinstance(node.get('config'), dict) and isinstance(node['config'].get('body'), dict):
            node['config']['body'] = strip_identity(node['config']['body'], diagnostics)
    return result


def resource_bindings(value, path='$'):
    result = []
    if type(value) is dict:
        for i, (key, child) in enumerate(value.items()):
            loc = f'{path}.fields[{i}]'
            if key in RESOURCES and child:
                result.append(dict(key=f'resource_{len(result)}_{loc}', kind=RESOURCES[key], path=loc))
            result.extend(resource_bindings(child, loc))
    elif type(value) is list:
        for i, child in enumerate(value):
            result.extend(resource_bindings(child, f'{path}[{i}]'))
    return result
