"""Bounded, JSON-typed YAML/JSON parsing. No object construction or aliases."""
import json
import math
import re
import yaml

MAX_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 40
MAX_ITEMS = 40000


class FileError(ValueError):
    def __init__(self, code, message, line=None, column=None):
        super().__init__(message)
        self.code, self.message, self.line, self.column = code, message, line, column


class JsonLoader(yaml.SafeLoader):
    yaml_implicit_resolvers = {}


for tag, pattern, chars in (
    ('bool', r'(?:true|false)\Z', 'tf'),
    ('null', r'null\Z', 'n'),
    ('int', r'-?(?:0|[1-9][0-9]*)\Z', '-0123456789'),
    ('float', r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+(?:[eE][+-]?[0-9]+)?|[eE][+-]?[0-9]+)\Z', '-0123456789'),
):
    JsonLoader.add_implicit_resolver('tag:yaml.org,2002:' + tag, re.compile(pattern), list(chars))


def pairs(items):
    result = {}
    for key, value in items:
        if type(key) is not str or key in result:
            raise FileError('DUPLICATE_KEY', '对象键必须是唯一字符串')
        result[key] = value
    return result


def mapping(loader, node):
    try:
        return pairs((loader.construct_object(k), loader.construct_object(v)) for k, v in node.value)
    except FileError as exc:
        exc.line, exc.column = node.start_mark.line + 1, node.start_mark.column + 1
        raise


JsonLoader.add_constructor('tag:yaml.org,2002:map', mapping)


def bounded(value):
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > MAX_DEPTH or count > MAX_ITEMS:
            raise FileError('FILE_LIMIT', '文件嵌套或配置项数量超过限制')
        if type(item) is dict:
            if any(type(k) is not str for k in item):
                raise FileError('INVALID_TYPE', '对象键必须是字符串')
            stack.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            stack.extend((v, depth + 1) for v in item)
        elif item is not None and type(item) not in (str, bool, int, float):
            raise FileError('INVALID_TYPE', '仅允许JSON兼容的配置类型')
        elif type(item) is float and not math.isfinite(item):
            raise FileError('INVALID_NUMBER', '不允许非有限数字')
        elif type(item) is str and len(item) > 262144:
            raise FileError('FILE_LIMIT', '单个字符串超过限制')
    return value


def parse(content: bytes):
    if not isinstance(content, bytes) or not content or len(content) > MAX_BYTES:
        raise FileError('FILE_LIMIT', '文件须为非空UTF-8文本，且不超过2MiB')
    try:
        source = content.decode('utf-8-sig')
        serialization = 'yaml'
        if source.lstrip().startswith(('{', '[')):
            try:
                value = json.loads(source, object_pairs_hook=pairs,
                                   parse_constant=lambda _: (_ for _ in ()).throw(FileError('INVALID_NUMBER', '不允许非有限数字')))
                serialization = 'json'
            except json.JSONDecodeError:
                # Flow-style YAML is valid too; it still passes every YAML guard.
                pass
        if serialization == 'yaml':
            depth, count = 0, 0
            for token in yaml.scan(source):
                count += 1
                if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken, yaml.tokens.TagToken, yaml.tokens.DirectiveToken)):
                    raise FileError('UNSAFE_YAML', '不允许YAML引用、锚点、显式标签或指令')
                if isinstance(token, (yaml.tokens.BlockMappingStartToken, yaml.tokens.BlockSequenceStartToken, yaml.tokens.FlowMappingStartToken, yaml.tokens.FlowSequenceStartToken)):
                    depth += 1
                if isinstance(token, (yaml.tokens.BlockEndToken, yaml.tokens.FlowMappingEndToken, yaml.tokens.FlowSequenceEndToken)):
                    depth -= 1
                if depth > MAX_DEPTH or count > MAX_ITEMS * 8:
                    raise FileError('FILE_LIMIT', '文件嵌套或配置项数量超过限制')
            value = yaml.load(source, Loader=JsonLoader)
            serialization = 'yaml'
        bounded(value)
        if type(value) is not dict:
            raise FileError('INVALID_ROOT', '文件根须为对象')
        return value, serialization
    except FileError:
        raise
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        mark = getattr(exc, 'problem_mark', None)
        raise FileError('PARSE_ERROR', '文件格式损坏、重复文档或键类型无效',
                        mark.line + 1 if mark else getattr(exc, 'lineno', None),
                        mark.column + 1 if mark else getattr(exc, 'colno', None)) from None
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
        raise FileError('PARSE_ERROR', '文件编码、类型或嵌套无效') from None


def dump(value, serialization):
    bounded(value)
    if serialization == 'json':
        return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
    # Quoting JSON-resolver strings avoids YAML1.1 reader drift too (on, dates).
    class Dumper(yaml.SafeDumper):
        pass
    Dumper.add_representer(str, lambda d, v: d.represent_scalar('tag:yaml.org,2002:str', v, style='"'))
    return yaml.dump(value, Dumper=Dumper, allow_unicode=True, sort_keys=False)
