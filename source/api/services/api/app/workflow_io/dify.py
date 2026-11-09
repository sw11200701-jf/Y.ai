"""Dify App DSL adapter for a deliberately bounded, semantic subset.

Pinned reference: langgenius/dify cd47a47c3b4acc4d4457b73777722602c33bc950.
No Dify runtime imports. Unsupported semantics always block the candidate.
"""
import re
import math
from copy import deepcopy
from .security import diagnostic

VERSIONS = {'0.3.1', '0.4.0'}
TARGET_VERSION = '0.4.0'
NAME = re.compile(r'^[A-Za-z_\u3400-\u9fff][A-Za-z_0-9\u3400-\u9fff]*$')
TOKENS = re.compile(r'{{#([^#]+)#}}')
COMMON = {'type', 'title', 'desc', 'selected', 'version', 'retry_config', 'error_strategy', 'default_value'}
NODE_FIELDS = {'id', 'type', 'data', 'position', 'positionAbsolute', 'height', 'width', 'selected', 'sourcePosition', 'targetPosition', 'zIndex'}
OPS = {'is': 'eq', 'is not': 'ne', 'contains': 'contains'}


class Conversion:
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        self.bindings = []

    def block(self, path, message, code='DIFY_UNSUPPORTED'):
        self.diagnostics.append(diagnostic('unsupported', code, path, message))

    def fields(self, item, allowed, path):
        if set(item) - set(allowed):
            self.block(path, '含尚无等价映射的配置字段，已保留待迁移内容')

    def ui(self, item, allowed, path):
        if type(item) is not dict:
            self.block(path, '画布UI元信息必须是对象', 'DIFY_UI_INVALID')
            return {}
        self.fields(item, allowed, path)
        return item

    def parallel_boundary(self, edges, path, *, native=False):
        counts = {}
        for edge in edges:
            port = edge.get('source_port', 'default') if native else edge.get('sourceHandle', 'source')
            key = (edge['source'], port)
            counts[key] = counts.get(key, 0) + 1
        if any(count > 1 for count in counts.values()):
            self.block(path, '当前Dify固定版本与中央并行fan-out/join/跳过及预算语义尚未验证等价；请使用原生JSON/YAML保留多边', 'DIFY_PARALLEL_UNVERIFIED')

    def coordinates(self, value, path, *, viewport=False):
        def finite(number):
            try:
                return type(number) in (int, float) and math.isfinite(number)
            except OverflowError:
                return False
        keys = {'x', 'y', 'zoom'} if viewport else {'x', 'y'}
        if (type(value) is not dict or set(value) != keys
                or any(not finite(v) for v in value.values())
                or viewport and value['zoom'] <= 0):
            self.block(path, '画布坐标须为有限数值，视口缩放须大于零', 'DIFY_UI_INVALID')
            return {'x': 0, 'y': 0, **({'zoom': 1} if viewport else {})}
        return deepcopy(value)

    def selector(self, parts, ids, starts, types, path):
        if not isinstance(parts, list) or len(parts) < 2 or any(not isinstance(p, str) for p in parts):
            raise ValueError()
        root, *fields = parts
        if root not in ids or any(not (NAME.fullmatch(p) or p.isdigit()) for p in fields):
            self.block(path, '变量引用不存在或跨越系统、会话、环境、子图作用域')
            return {'$ref': 'inputs.__unresolved'}
        if root in starts:
            if len(fields) != 1:
                self.block(path, '当前start只支持已声明文本输入，不支持嵌套文件或对象路径')
            return {'$ref': 'inputs.' + '.'.join(fields)}
        if types[root] == 'template-transform' and fields[0] == 'output':
            fields[0] = 'text'
        if types[root] not in {'llm', 'template-transform'} or fields != ['text']:
            self.block(path, '该节点输出selector尚无等价映射')
        return {'$ref': 'nodes.' + ids[root] + '.' + '.'.join(fields)}

    def prompt(self, text, variables, ids, starts, types, path):
        if not isinstance(text, str):
            raise ValueError()
        def replace(match):
            key = f'v{len(variables)}'
            variables[key] = self.selector(match.group(1).split('.'), ids, starts, types, path)
            return '{{ ' + key + ' }}'
        result = TOKENS.sub(replace, text)
        if '{{' in TOKENS.sub('', text) or '{%' in text:
            self.block(path, '提示词或模板仅支持显式Dify selector；不支持Jinja表达式')
        return result

    def load(self, source):
        self.fields(source, {'app', 'kind', 'version', 'workflow', 'dependencies'}, '$')
        if source.get('kind') != 'app' or source.get('version') not in VERSIONS:
            self.block('$', '仅支持固定Dify提交的App DSL 0.3.1/0.4.0', 'DIFY_VERSION')
        app, body = source['app'], source['workflow']
        self.fields(app, {'mode', 'name', 'description', 'icon', 'icon_type', 'icon_background', 'use_icon_as_answer_icon'}, '$.app')
        if app.get('mode') not in {'workflow', 'advanced-chat'}:
            self.block('$.app.mode', '仅支持workflow或无会话状态的advanced-chat图')
        self.fields(body, {'graph', 'features', 'environment_variables', 'conversation_variables', 'rag_pipeline_variables'}, '$.workflow')
        for key in ('environment_variables', 'conversation_variables', 'rag_pipeline_variables'):
            if body.get(key):
                self.block('$.workflow.' + key, '环境、会话或管线变量需显式迁移作用域与重新配置，不能直接执行')
        if source.get('dependencies'):
            self.diagnostics.append(diagnostic('requires_binding', 'DEPENDENCIES_REBIND', '$.dependencies', '插件依赖仅作迁移信息，不自动安装；须重新授权绑定'))
            self.bindings.append(dict(key='dependencies', kind='plugin', path='$.dependencies'))
        features = body.get('features', {})
        allowed_features = {'file_upload', 'opening_statement', 'retriever_resource', 'sensitive_word_avoidance', 'speech_to_text', 'suggested_questions', 'suggested_questions_after_answer', 'text_to_speech'}
        self.fields(features, allowed_features, '$.workflow.features')
        for key, value in features.items():
            # Retrieval citation display has no execution effect; preserve in metadata.
            if key != 'retriever_resource' and (isinstance(value, dict) and value.get('enabled') or key in {'opening_statement', 'suggested_questions'} and value):
                self.block('$.workflow.features', '启用的文件、语音、开场白或安全功能尚无等价应用映射')
        graph = body['graph']
        self.fields(graph, {'nodes', 'edges', 'viewport'}, '$.workflow.graph')
        self.parallel_boundary(graph['edges'], '$.workflow.graph.edges')
        nodes = graph['nodes']
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 64 or len(graph['edges']) > 256:
            raise ValueError()
        ids = {n['id']: 'n' + str(i) for i, n in enumerate(nodes)}
        if len(ids) != len(nodes) or any(not isinstance(k, str) for k in ids):
            raise ValueError()
        types = {n['id']: n['data']['type'] for n in nodes}
        starts = {k for k, t in types.items() if t == 'start'}
        if len(starts) != 1:
            self.block('$.workflow.graph', '必须有唯一start节点；不支持子图入口或多入口')
        viewport = self.coordinates(graph.get('viewport', {'x': 0, 'y': 0, 'zoom': 1}), '$.workflow.graph.viewport', viewport=True)
        # Native mode selects orchestration, not whether individual nodes use an
        # LLM. These converted graphs are fixed direct workflows; model mode is
        # reserved by the central scheduler for an authorized Agent planner.
        workflow = dict(name=app.get('name', '导入工作流'), schemaVersion='2.0', mode='demo', nodes=[], edges=[],
                        input_schema={'type': 'object', 'properties': {}, 'required': []},
                        ui={'viewport': deepcopy(viewport)},
                        interchange={'dify': {'app': app, 'features': features, 'viewport': deepcopy(viewport)}})
        branch_ports = {}
        for i, raw in enumerate(nodes):
            loc = f'$.workflow.graph.nodes[{i}]'
            self.fields(raw, NODE_FIELDS, loc)
            data, typ = raw['data'], raw['data']['type']
            if data.get('version', '1') != '1':
                self.block(loc, '节点版本未固定支持')
            retry = data.get('retry_config') or {}
            if retry.get('enabled') or retry.get('retry_enabled') or data.get('error_strategy') or data.get('default_value'):
                self.block(loc, '节点重试、错误分支或失败默认值与现引擎不等价')
            position = self.coordinates(raw.get('position', {'x': i * 300, 'y': 100}), loc + '.position')
            if 'positionAbsolute' in raw:
                self.coordinates(raw['positionAbsolute'], loc + '.positionAbsolute')
            title = data.get('title', typ)
            if type(title) is not str:
                self.block(loc + '.data.title', '节点显示名称必须是文本', 'DIFY_UI_INVALID')
            node = dict(id=ids[raw['id']], type=typ, config={}, inputs={}, position=deepcopy(position),
                        title=title, description=data.get('desc', ''), ui={'position': deepcopy(position), 'label': title},
                        interchange={'dify_id': raw['id'], 'layout': {k: v for k, v in raw.items() if k not in {'id', 'data'} }})
            if typ == 'start':
                self.fields(data, COMMON | {'variables'}, loc)
                for j, variable in enumerate(data.get('variables', [])):
                    self.fields(variable, {'variable', 'label', 'description', 'type', 'required', 'hide', 'max_length', 'options', 'allowed_file_types', 'allowed_file_extensions', 'allowed_file_upload_methods'}, loc)
                    key, vt = variable['variable'], variable['type']
                    if not NAME.fullmatch(key) or key in workflow['input_schema']['properties']:
                        raise ValueError()
                    if vt not in {'text-input', 'paragraph', 'select'}:
                        self.block(loc, '文件、数字或复合输入需单独核对类型与运行语义；当前仅映射文本与选项')
                        continue
                    schema = {'type': 'string', 'title': variable.get('label', key), 'description': variable.get('description', '')}
                    if variable.get('max_length') is not None:
                        schema['maxLength'] = variable['max_length']
                    if vt == 'select':
                        schema['enum'] = variable.get('options', [])
                    workflow['input_schema']['properties'][key] = schema
                    if variable.get('required'):
                        workflow['input_schema']['required'].append(key)
                node['interchange']['variables'] = data.get('variables', [])
            elif typ == 'end':
                self.fields(data, COMMON | {'outputs'}, loc)
                # Native end is control-only: runtime intentionally excludes it
                # from workflow.outputs. answer returns the named values.
                node['type'] = 'answer'
                for output in data['outputs']:
                    self.fields(output, {'variable', 'value_selector', 'value_type'}, loc)
                    key = output['variable']
                    if key in node['inputs']:
                        raise ValueError()
                    node['inputs'][key] = self.selector(output['value_selector'], ids, starts, types, loc)
            elif typ == 'if-else':
                self.fields(data, COMMON | {'cases', 'conditions', 'logical_operator'}, loc)
                cases = data.get('cases') or [{'case_id': 'true', 'conditions': data.get('conditions'), 'logical_operator': data.get('logical_operator', 'and')}]
                if len(cases) != 1 or len(cases[0]['conditions'] or []) != 1:
                    self.block(loc, 'Dify多case或AND/OR条件组尚无已验证的等价转换，请保留迁移预览或使用原生JSON/YAML', 'DIFY_CONDITION_GROUP_UNSUPPORTED')
                    continue
                case = cases[0]
                self.fields(case, {'case_id', 'id', 'conditions', 'logical_operator'}, loc)
                condition = case['conditions'][0]
                self.fields(condition, {'id', 'variable_selector', 'comparison_operator', 'value', 'varType'}, loc)
                op = OPS.get(condition['comparison_operator'])
                if not op or condition.get('varType', 'string') != 'string' or not isinstance(condition.get('value'), str):
                    self.block(loc, '仅支持字符串is/is not/contains；数值强制转换与空值判断需单独迁移')
                    continue
                node['type'] = 'if_else'
                node['config'] = {'operator': op}
                node['inputs'] = {'left': self.selector(condition['variable_selector'], ids, starts, types, loc), 'right': condition.get('value')}
                branch_ports[raw['id']] = {case['case_id']: 'true', 'false': 'false'}
            elif typ == 'llm':
                self.fields(data, COMMON | {'model', 'prompt_template', 'prompt_config', 'memory', 'context', 'vision', 'structured_output', 'structured_output_enabled', 'reasoning_format'}, loc)
                if (data.get('memory') or {}).get('enabled') or (data.get('context') or {}).get('enabled') or (data.get('vision') or {}).get('enabled') or (data.get('prompt_config') or {}).get('jinja2_variables'):
                    self.block(loc, '记忆、检索上下文、视觉或Jinja变量尚无等价映射')
                if data.get('reasoning_format', 'tagged') != 'tagged':
                    self.block(loc, '推理输出模式尚无等价映射')
                model = data['model']
                self.fields(model, {'provider', 'name', 'mode', 'completion_params'}, loc)
                if model.get('mode') != 'chat':
                    self.block(loc, '仅支持chat模型消息格式')
                variables, messages = {}, []
                for message in data['prompt_template']:
                    self.fields(message, {'role', 'text', 'id', 'edition_type', 'jinja2_text'}, loc)
                    if message.get('edition_type', 'basic') != 'basic' or message.get('jinja2_text'):
                        self.block(loc, 'Jinja提示词不支持直接迁移')
                    messages.append({'role': message['role'], 'content': self.prompt(message['text'], variables, ids, starts, types, loc)})
                node['inputs'] = {'variables': variables}
                node['config'] = {'model_config_id': '__REBIND_MODEL__', 'prompt_messages': messages, 'model_parameters': model.get('completion_params', {})}
                if data.get('structured_output_enabled'):
                    self.block(loc, 'Dify结构化输出的原生/提示降级策略与当前response_format不能直接等同')
                self.bindings.append(dict(key=f'model_{i}', kind='model', path=f'nodes[{i}].config.model_config_id'))
                self.diagnostics.append(diagnostic('requires_binding', 'MODEL_REBIND', loc, '请显式选择当前有权使用且语义兼容的模型配置'))
                node['interchange']['model_hint'] = {k: model[k] for k in ('provider', 'name', 'mode')}
            elif typ == 'template-transform':
                self.fields(data, COMMON | {'template', 'variables'}, loc)
                variables = {}
                for variable in data.get('variables', []):
                    self.fields(variable, {'variable', 'value_selector', 'value_type'}, loc)
                    key = variable['variable']
                    if not NAME.fullmatch(key) or key in variables:
                        raise ValueError()
                    variables[key] = self.selector(variable['value_selector'], ids, starts, types, loc)
                template = data['template']
                rest = re.sub(r'{{\s*[A-Za-z_][A-Za-z_0-9]*\s*}}', '', template)
                if '{{' in rest or '{%' in template or '__' in template:
                    self.block(loc, '仅支持简单变量替换模板，不支持属性、过滤器或控制语句')
                node.update(type='template', inputs=variables, config={'template': template})
            else:
                self.block(loc, '该节点没有已验证等价转换；代码/工具/HTTP/知识/聚合/提取/分类/循环等须保留迁移', 'DIFY_NODE_UNSUPPORTED')
            workflow['nodes'].append(node)
        for i, edge in enumerate(graph['edges']):
            loc = f'$.workflow.graph.edges[{i}]'
            self.fields(edge, {'id', 'source', 'target', 'sourceHandle', 'targetHandle', 'type', 'data', 'zIndex', 'selected'}, loc)
            edge_data = edge.get('data', {})
            self.fields(edge_data, {'isInIteration', 'isInLoop', 'sourceType', 'targetType'}, loc)
            if edge_data.get('isInIteration') or edge_data.get('isInLoop'):
                self.block(loc, '子图边与父图作用域不可混用，须显式迁移')
            if edge['source'] not in ids or edge['target'] not in ids:
                raise ValueError()
            port = edge.get('sourceHandle', 'source')
            if edge['source'] in branch_ports:
                port = branch_ports[edge['source']].get(port)
            elif port == 'source':
                port = 'default'
            if port not in {'default', 'true', 'false'} or edge.get('targetHandle', 'target') != 'target':
                self.block(loc, '未知分支handle或目标端口')
            workflow['edges'].append(dict(source=ids[edge['source']], target=ids[edge['target']], source_port=port or 'unresolved', target_port='default'))
        return workflow


def load(source, diagnostics):
    converter = Conversion(diagnostics)
    try:
        return converter.load(source), converter.bindings
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        converter.block('$', 'Dify文件结构、字段类型或必填配置无效', 'DIFY_INVALID')
        return None, converter.bindings


def export(workflow, diagnostics, target_version, model_bindings):
    c = Conversion(diagnostics)
    if target_version != TARGET_VERSION:
        c.block('$', 'Dify导出目标固定为0.4.0（Dify 1.9.1）', 'DIFY_VERSION')
    c.fields(workflow, {'name', 'schemaVersion', 'version', 'mode', 'input_schema', 'variables', 'limits', 'nodes', 'edges', 'interchange', 'viewport', 'description', 'ui'}, '$')
    if workflow.get('schemaVersion') != '2.0':
        c.block('$', '仅导出2.0固定图', 'DIFY_SCHEMA_UNSUPPORTED')
    if workflow.get('variables'):
        c.block('$.variables', '中央全局变量的声明、赋值和作用域无等价Dify映射；请使用原生JSON/YAML', 'DIFY_GLOBALS_UNSUPPORTED')
    if workflow.get('limits', {'max_steps': 256, 'timeout_seconds': 120}) != {'max_steps': 256, 'timeout_seconds': 120}:
        c.block('$.limits', '执行步骤、截止时间及max_concurrency无法作为目标Dify图的等价预算导出', 'DIFY_EXECUTION_LIMITS_UNSUPPORTED')
    c.parallel_boundary(workflow['edges'], '$.edges', native=True)
    from app.runner.variables import references
    if any(ref['$ref'].split('.')[0] in {'system', 'user'} for node in workflow['nodes'] for ref in references(node.get('inputs', {}))):
        c.block('$.nodes', '当前服务端只读system/user引用与Dify系统/终端用户身份不等价，禁止替换为文件身份值', 'DIFY_READONLY_CONTEXT_UNSUPPORTED')
    nodes = workflow['nodes']
    starts = [n['id'] for n in nodes if n['type'] in {'start', 'input'}]
    if len(starts) != 1:
        c.block('$', '导出需要唯一start/input入口')
        return None
    types = {n['id']: n['type'] for n in nodes}
    schema = workflow.get('input_schema', {'type': 'object'})
    c.fields(schema, {'type', 'properties', 'required'}, '$.input_schema')

    def selector(ref):
        if type(ref) is not dict or set(ref) != {'$ref'}:
            raise ValueError()
        parts = ref['$ref'].split('.')
        if parts[0] == 'inputs' and len(parts) == 2 and parts[1] in schema.get('properties', {}):
            return [starts[0], parts[1]]
        if parts[0] == 'nodes' and len(parts) == 3 and parts[1] in types:
            if types[parts[1]] == 'template' and parts[2] == 'text':
                parts[2] = 'output'
            elif types[parts[1]] != 'llm' or parts[2] != 'text':
                raise ValueError()
            return parts[1:]
        raise ValueError()

    def prompt(text, variables):
        def sub(match):
            key = match.group(1).strip()
            return '{{#' + '.'.join(selector(variables[key])) + '#}}'
        if not isinstance(text, str):
            raise ValueError()
        return re.sub(r'{{(.*?)}}', sub, text)

    ui = c.ui(workflow.get('ui', {}), {'viewport'}, '$.ui')
    viewport = ui.get('viewport', workflow.get('viewport', workflow.get('interchange', {}).get('dify', {}).get('viewport') or {'x': 0, 'y': 0, 'zoom': 1}))
    graph = {'nodes': [], 'edges': [], 'viewport': c.coordinates(viewport, '$.ui.viewport', viewport=True)}
    try:
        for i, node in enumerate(nodes):
            loc, typ = f'$.nodes[{i}]', node['type']
            c.fields(node, {'id', 'type', 'config', 'inputs', 'position', 'title', 'description', 'interchange', 'ui'}, loc)
            node_ui = c.ui(node.get('ui', {}), {'position', 'label'}, loc + '.ui')
            if 'label' in node_ui and type(node_ui['label']) is not str:
                c.block(loc + '.ui.label', '节点显示名称必须是文本', 'DIFY_UI_INVALID')
            config, inputs = node.get('config', {}), node.get('inputs', {})
            data = {'type': typ, 'title': node_ui.get('label') or node.get('title', typ), 'desc': node.get('description', '')}
            if typ in {'start', 'input'}:
                if config or inputs:
                    raise ValueError()
                data.update(type='start', variables=[])
                old_variables = {v['variable']: v for v in node.get('interchange', {}).get('variables', [])}
                for key, prop in schema.get('properties', {}).items():
                    c.fields(prop, {'type', 'title', 'description', 'maxLength', 'enum'}, '$.input_schema')
                    if prop.get('type') != 'string':
                        raise ValueError()
                    variable = deepcopy(old_variables.get(key, {}))
                    variable.update(variable=key, label=prop.get('title', key), description=prop.get('description', ''), type='select' if 'enum' in prop else ('paragraph' if variable.get('type') == 'paragraph' else 'text-input'), required=key in schema.get('required', []), max_length=prop.get('maxLength'), options=prop.get('enum', []))
                    data['variables'].append(variable)
            elif typ in {'end', 'output', 'answer'}:
                if typ == 'output' and config.get('presentation') == 'json':
                    c.block(loc + '.config.presentation', 'Dify end无法等价表达章谱typed JSON展示配置；请使用原生JSON/YAML保留presentation=json', 'DIFY_JSON_PRESENTATION_UNSUPPORTED')
                    continue
                if config:
                    raise ValueError()
                if typ == 'end' and inputs:
                    c.block(loc, '原生end仅结束控制流，不产生流程输出；不能作为Dify命名输出导出')
                if any(type(value) is not dict or set(value) != {'$ref'} for value in inputs.values()):
                    c.block(loc + '.inputs', 'Dify end只能选择变量，当前固定字面量或可选引用无法等价输出；请使用原生JSON/YAML保留值和类型', 'DIFY_OUTPUT_VALUE_UNSUPPORTED')
                    continue
                data.update(type='end', outputs=[{'variable': key, 'value_selector': selector(value)} for key, value in inputs.items()])
            elif typ == 'if_else':
                if 'conditions' in inputs or 'logical_operator' in config:
                    c.block(loc, '章谱AND/OR嵌套条件组与目标Dify条件的类型、短路及空值语义尚未验证等价', 'DIFY_CONDITION_GROUP_UNSUPPORTED')
                    continue
                if set(config) != {'operator'} or set(inputs) != {'left', 'right'} or config['operator'] not in OPS.values() or not isinstance(inputs['right'], str):
                    raise ValueError()
                data.update(type='if-else', cases=[{'case_id': 'true', 'logical_operator': 'and', 'conditions': [{'variable_selector': selector(inputs['left']), 'comparison_operator': {v:k for k,v in OPS.items()}[config['operator']], 'value': inputs['right'], 'varType': 'string'}]}])
            elif typ == 'llm':
                c.fields(config, {'model_config_id', 'model_config_version', 'prompt_messages', 'model_parameters', 'system_prompt', 'response_format'}, loc)
                if config.get('response_format', {'type': 'text'}) != {'type': 'text'} or set(inputs) - {'variables', 'prompt'}:
                    raise ValueError()
                model = (model_bindings or {}).get(config.get('model_config_id'))
                if not model or set(model) != {'provider', 'name', 'mode'} or model['mode'] != 'chat':
                    diagnostics.append(diagnostic('requires_binding', 'MODEL_REBIND', loc, '导出需要服务端提供受权Dify模型映射'))
                    continue
                messages = config.get('prompt_messages')
                if messages is None:
                    messages = ([{'role': 'system', 'content': config['system_prompt']}] if config.get('system_prompt') else []) + [{'role': 'user', 'content': inputs['prompt']}]
                data.update(model={**model, 'completion_params': config.get('model_parameters', {})}, prompt_template=[{'role': m['role'], 'text': prompt(m['content'], inputs.get('variables', {}))} for m in messages], context={'enabled': False}, vision={'enabled': False})
            elif typ == 'template':
                if set(config) != {'template'}:
                    raise ValueError()
                template = config['template']
                if '{%' in template or '{{' in re.sub(r'{{\s*[A-Za-z_][A-Za-z_0-9]*\s*}}', '', template):
                    raise ValueError()
                data.update(type='template-transform', template=template, variables=[{'variable': k, 'value_selector': selector(v)} for k,v in inputs.items()])
            else:
                c.block(loc, '该节点语义超出Dify导出可表达子集')
                continue
            layout = deepcopy(node.get('interchange', {}).get('layout', {}))
            position = node_ui.get('position', node.get('position', {'x': i * 300, 'y': 100}))
            layout.update(id=node['id'], type='custom', data=data, position=c.coordinates(position, loc + '.ui.position'))
            if 'positionAbsolute' in layout:
                layout['positionAbsolute'] = deepcopy(layout['position'])
            graph['nodes'].append(layout)
        for i, edge in enumerate(workflow['edges']):
            c.fields(edge, {'source', 'target', 'source_port', 'target_port'}, f'$.edges[{i}]')
            port = edge.get('source_port', 'default')
            if port not in {'default', 'true', 'false'} or edge.get('target_port', 'default') != 'default':
                raise ValueError()
            graph['edges'].append(dict(id='e'+str(i), source=edge['source'], target=edge['target'], sourceHandle='source' if port == 'default' else port, targetHandle='target', type='custom'))
        app = deepcopy(workflow.get('interchange', {}).get('dify', {}).get('app', {}))
        app.update(name=workflow.get('name', '导出工作流'), mode='workflow')
        return dict(kind='app', version=target_version, app=app, dependencies=[], workflow=dict(graph=graph, features=workflow.get('interchange', {}).get('dify', {}).get('features', {}), environment_variables=[], conversation_variables=[]))
    except (KeyError, TypeError, ValueError, AttributeError):
        c.block('$', '输出、输入、提示词、参数或配置无法无损表达为目标Dify结构')
        return None
