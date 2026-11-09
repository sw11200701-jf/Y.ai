"""Pure workflow file interchange. Callers own authorization and persistence."""
import hashlib
from copy import deepcopy
from pydantic import ValidationError
from app.contracts import WorkflowDefinition
from app.errors import AppError
from app.runner.validation import validate_workflow
from app.runner.registry import definition
from jsonschema import Draft202012Validator
from . import dify
from .parsing import FileError, bounded, dump, parse, MAX_BYTES
from .security import diagnostic, sanitize, strip_identity, strip_runtime_context
from .bindings import CONVERTER_VERSION, slots, replace

FORMATS = {'native-json', 'native-yaml', 'dify-yaml'}


def _validate(workflow, diagnostics):
    limits = workflow.get('limits', {})
    if isinstance(limits, dict) and 'max_concurrency' in limits and (type(limits['max_concurrency']) is not int or not 1 <= limits['max_concurrency'] <= 4):
        diagnostics.append(diagnostic('unsupported', 'CONCURRENCY_LIMIT_TYPE', '$.workflow.limits.max_concurrency', 'max_concurrency须为1至4的整数，不接受布尔值或文本隐式转换'))
    graphs = [(workflow, '$.workflow')]
    while graphs:
        graph, path = graphs.pop()
        declarations = graph.get('variables', {})
        if isinstance(declarations, dict) and {'system', 'user'}.intersection(declarations):
            diagnostics.append(diagnostic('unsupported', 'READONLY_VARIABLE_DECLARATION', path + '.variables', 'system和user为服务端只读池，不能声明为可写全局变量'))
        for i, node in enumerate(graph.get('nodes', []) if isinstance(graph.get('nodes'), list) else []):
            values = [node.get('inputs', {})] if isinstance(node, dict) else []
            while values:
                value = values.pop()
                if isinstance(value, dict):
                    ref = value.get('$ref')
                    if isinstance(ref, str) and ref.split('.')[0] in {'system', 'user'} and ref not in {'system.run_id', 'system.tenant_id', 'system.workspace_id', 'user.id', 'user.role', 'user.permissions'}:
                        diagnostics.append(diagnostic('unsupported', 'READONLY_VARIABLE_REFERENCE', f'{path}.nodes[{i}].inputs', '该系统/用户引用不在文件契约的只读白名单内，不能携带私有权限或身份扩展字段'))
                    values.extend(value.values())
                elif isinstance(value, list):
                    values.extend(value)
            if isinstance(node, dict) and isinstance(node.get('config'), dict) and isinstance(node['config'].get('body'), dict):
                graphs.append((node['config']['body'], f'{path}.nodes[{i}].config.body'))
    for i, node in enumerate(workflow.get('nodes', []) if isinstance(workflow.get('nodes'), list) else []):
        if not isinstance(node, dict):
            continue
        spec = definition(node.get('type')) if isinstance(node.get('type'), str) else None
        if spec is None:
            diagnostics.append(diagnostic('unsupported', 'UNKNOWN_NODE', f'$.workflow.nodes[{i}]', '当前节点注册表没有该节点类型'))
        elif isinstance(node.get('config', {}), dict) and next(Draft202012Validator(spec['config_schema']).iter_errors(node.get('config', {})), None):
            diagnostics.append(diagnostic('unsupported', 'NODE_CONFIG', f'$.workflow.nodes[{i}].config', '节点配置缺少必填项、含未知字段或类型范围不符合当前注册表'))
    try:
        validate_workflow(WorkflowDefinition.model_validate(workflow))
    except AppError as exc:
        code, message = 'ENGINE_VALIDATION', '当前引擎校验未通过；请检查节点配置和图结构'
        for fragment, category, explanation in (
            ('Only aggregate may merge', 'BRANCH_MERGE', '仅聚合节点允许汇合多路分支；不能直接转换Dify汇合边'),
            ('Branch-dependent reference', 'BRANCH_REFERENCE', '分支输出不是必经节点，当前引擎要求显式可选引用；转换不能擅自改变缺值语义'),
            ('Forward/non-ancestor', 'REFERENCE_SCOPE', '变量引用跨作用域、前向或不在当前节点祖先路径'),
            ('Unreachable', 'GRAPH_REACHABILITY', '存在不可达节点'),
            ('one default outgoing', 'GRAPH_PARALLEL', '当前节点只允许一条默认出口，不能直接表达Dify并行分叉'),
        ):
            if fragment in exc.message:
                code, message = category, explanation
                break
        diagnostics.append(diagnostic('unsupported', code, '$.workflow', message))
    except (ValidationError, AppError, ValueError, TypeError, KeyError, RecursionError, AttributeError):
        # Engine errors may contain customer values/secret schema fields; never echo.
        diagnostics.append(diagnostic('unsupported', 'ENGINE_VALIDATION', '$.workflow', '当前引擎校验未通过；请检查必填类型、节点配置、可达性、分支汇合和引用作用域'))


def preview_import(content: bytes, *, filename: str = '') -> dict:
    diagnostics, bindings = [], []
    result = dict(format='unknown', converter_version=CONVERTER_VERSION, source_version=None, source_hash=hashlib.sha256(content).hexdigest() if isinstance(content, bytes) else None,
                  workflow=None, diagnostics=diagnostics, bindings=bindings, can_confirm=False, pending=None)
    try:
        source, serialization = parse(content)
        if source.get('format') == 'zhangpu-workflow' and isinstance(source.get('workflow'), dict):
            source['workflow'] = strip_runtime_context(source['workflow'], diagnostics)
        elif 'nodes' in source and 'edges' in source:
            source = strip_runtime_context(source, diagnostics)
        source = sanitize(source, diagnostics)
        result['pending'] = source
        if source.get('kind') == 'app' or 'workflow' in source and 'app' in source:
            result.update(format='dify-yaml', source_version=source.get('version'))
            workflow, resource_list = dify.load(source, diagnostics)
        elif source.get('format') == 'zhangpu-workflow':
            result.update(format='native-' + serialization, source_version=source.get('schemaVersion'))
            if source.get('schemaVersion') != '1.0' or source.get('exportVersion') != '1.0.0' or set(source) - {'format', 'schemaVersion', 'exportVersion', 'workflow'}:
                diagnostics.append(diagnostic('unsupported', 'NATIVE_VERSION', '$', '原生文件封装版本或字段未支持'))
            workflow = source.get('workflow')
        elif 'nodes' in source and 'edges' in source:
            result.update(format='native-' + serialization, source_version='legacy-bare')
            workflow = source
        else:
            raise FileError('UNKNOWN_FORMAT', '未识别工作流结构；后缀不决定文件语义')
        if type(workflow) is dict:
            workflow = strip_identity(workflow, diagnostics)
            _validate(workflow, diagnostics)
        else:
            diagnostics.append(diagnostic('unsupported', 'INVALID_WORKFLOW', '$.workflow', '工作流必须是对象'))
        blocked = any(d['severity'] in {'unsupported', 'lossy'} for d in diagnostics)
        if not blocked:
            bindings.extend(slot for slot, _ in slots(workflow))
            if bindings and result['format'] != 'dify-yaml':
                diagnostics.append(diagnostic('requires_binding', 'RESOURCE_REBIND', '$.workflow', '文件资源标识不授予权限，须重新选择并验证当前可用资源'))
        result['workflow'] = None if blocked else workflow
        result['can_confirm'] = not blocked and not bindings and not any(d['severity'] == 'requires_binding' for d in diagnostics)
        # Preserve migration source only in this transient DTO, never persistence.
        if result['can_confirm']:
            result['pending'] = None
    except FileError as exc:
        item = diagnostic('unsupported', exc.code, '$', exc.message)
        if exc.line is not None:
            item.update(line=exc.line, column=exc.column)
        diagnostics.append(item)
    return result


def apply_bindings(preview: dict, binding_resolutions: list) -> dict:
    """Apply only server-validated typed selections to a freshly reparsed preview.

    This is not an authorization boundary. The HTTP service must never pass a
    client-supplied preview or invoke this before resolving every target resource.
    """
    return replace(preview, binding_resolutions, _validate)


def export_workflow(workflow: dict, *, format: str = 'native-json', target_version: str = '0.4.0', model_bindings: dict | None = None) -> dict:
    diagnostics = []
    result = dict(format=format, converter_version=CONVERTER_VERSION, target_version=target_version if format == 'dify-yaml' else '1.0', content=None,
                  media_type='application/json' if format == 'native-json' else 'application/yaml', extension='.json' if format == 'native-json' else '.yaml', diagnostics=diagnostics)
    try:
        bounded(workflow)
        if type(workflow) is not dict or format not in FORMATS:
            raise FileError('UNKNOWN_FORMAT', '导出格式或工作流类型无效')
        workflow = strip_identity(sanitize(strip_runtime_context(deepcopy(workflow), diagnostics), diagnostics), diagnostics)
        _validate(workflow, diagnostics)
        if any(d['severity'] in {'unsupported', 'lossy'} for d in diagnostics):
            return result
        if format == 'dify-yaml':
            value = dify.export(workflow, diagnostics, target_version, model_bindings)
            blocked = any(d['severity'] != 'warning' for d in diagnostics)
        else:
            value = dict(format='zhangpu-workflow', schemaVersion='1.0', exportVersion='1.0.0', workflow=workflow)
            blocked = any(d['severity'] in {'unsupported', 'lossy'} for d in diagnostics)
        if not blocked and value is not None:
            content = dump(value, 'json' if format == 'native-json' else 'yaml')
            if len(content.encode('utf-8')) > MAX_BYTES:
                raise FileError('FILE_LIMIT', '导出内容超过2MiB限制')
            result['content'] = content
    except (FileError, RecursionError) as exc:
        diagnostics.append(diagnostic('unsupported', getattr(exc, 'code', 'FILE_LIMIT'), '$', getattr(exc, 'message', '文件嵌套超过限制')))
    return result


__all__ = ['preview_import', 'apply_bindings', 'export_workflow', 'CONVERTER_VERSION']
