"""Visual field compiler and typed HTTP mappings. No executable expressions."""

import json
import math
import re
from typing import Any, Literal
from urllib.parse import quote

from jsonschema import Draft202012Validator
from pydantic import Field, model_serializer

from .errors import PluginError
from .spec_base import StrictModel

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
SOURCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)*$")
PROTECTED_HEADERS = {"host", "authorization", "proxy-authorization", "cookie", "set-cookie", "content-length", "transfer-encoding", "connection", "content-type", "accept"}


class FieldDefinition(StrictModel):
    name: str = Field(default="", max_length=64)
    display_name: str = Field(default="", max_length=100)
    type: Literal["string", "integer", "number", "boolean", "object", "array"]
    required: bool = False
    default: Any = None
    example: Any = None
    description: str = Field(default="", max_length=1000)
    position: Literal["path", "query", "header", "body"] | None = None
    children: list["FieldDefinition"] | None = None
    item: "FieldDefinition | None" = None
    source_path: str | None = Field(default=None, max_length=500)
    secret: bool = False

    @model_serializer(mode="wrap")
    def omit_unset_values(self, handler):
        data = handler(self)
        for key in ("default", "example"):
            if key not in self.model_fields_set:
                data.pop(key, None)
        return data


def compile_fields(fields: list[FieldDefinition], *, output=False, method="GET") -> dict:
    count = 0

    def field_schema(field, depth, inherited=None):
        nonlocal count
        count += 1
        if count > 128 or depth > 6:
            raise ValueError("字段超过128个或嵌套超过6层")
        if field.source_path and (not output or not SOURCE.fullmatch(field.source_path)):
            raise ValueError("响应来源仅支持字段点路径和数组索引")
        if depth > 1 and field.position is not None and field.position != inherited:
            raise ValueError("嵌套字段position必须继承父字段")
        position = field.position or inherited or ("query" if method == "GET" else "body")
        if not output and method == "GET" and position == "body":
            raise ValueError("GET接口不能配置body字段")
        if field.secret:
            if {"default", "example"} & field.model_fields_set:
                raise ValueError("secret字段不能存默认值或示例")
            if not output and (depth != 1 or field.type != "string" or position != "header"):
                raise ValueError("输入secret仅支持顶层header，值由连接鉴权提供")
            if field.children is not None or field.item is not None:
                raise ValueError("secret字段不能携带嵌套默认/示例信息")
            return None
        if not output and position == "header" and (not field.name.lower().startswith("x-") or field.name.lower() in PROTECTED_HEADERS):
            raise ValueError("可填写header仅支持X-自定义头，鉴权请使用连接")
        if not output and position in {"path", "header"} and field.type in {"object", "array"}:
            raise ValueError("path/header仅支持标量字段")
        if position == "path" and not field.required:
            raise ValueError("path字段必须必填")
        schema = {"type": field.type}
        if field.display_name:
            schema["title"] = field.display_name
        if field.description:
            schema["description"] = field.description
        if field.type == "object":
            if field.children is None or field.item is not None:
                raise ValueError("object须配置children，不能配置item")
            schema.update(object_schema(field.children, depth + 1, position))
        elif field.type == "array":
            if field.item is None or field.children is not None:
                raise ValueError("array须配置item，不能配置children")
            item_schema = field_schema(field.item, depth + 1, position)
            if item_schema is None:
                raise ValueError("数组item不能是secret")
            schema["items"] = item_schema
        elif field.children is not None or field.item is not None:
            raise ValueError("标量字段不能配置children/item")
        for attr in ("default", "example"):
            if attr in field.model_fields_set:
                if not Draft202012Validator(schema).is_valid(getattr(field, attr)):
                    raise ValueError(f"{field.name}的{attr}与字段类型不符")
                schema["default" if attr == "default" else "examples"] = getattr(field, attr) if attr == "default" else [field.example]
        return schema

    def object_schema(items, depth, inherited=None):
        names = [field.name for field in items]
        if any(not NAME.fullmatch(name) for name in names) or len(set(names)) != len(names):
            raise ValueError("字段名无效或同层名称重复")
        properties, required = {}, []
        for field in items:
            schema = field_schema(field, depth, inherited)
            if schema is not None:
                properties[field.name] = schema
                if field.required:
                    required.append(field.name)
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}

    return object_schema(fields, 1)


def normalize_arguments(fields, arguments):
    if not isinstance(arguments, dict):
        raise PluginError("VALIDATION_ERROR", "表单参数须为对象", 422)

    def convert(field, value):
        if field.type == "boolean" and isinstance(value, str):
            if value.lower() not in {"true", "false"}:
                raise ValueError()
            value = value.lower() == "true"
        elif field.type == "integer" and isinstance(value, str):
            if not re.fullmatch(r"-?\d+", value):
                raise ValueError()
            value = int(value)
        elif field.type == "number" and isinstance(value, str):
            if not re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", value):
                raise ValueError()
            value = float(value)
        elif field.type in {"array", "object"} and isinstance(value, str):
            value = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError()
        if field.type == "object" and isinstance(value, dict):
            value = fill(field.children or [], value)
        if field.type == "array" and isinstance(value, list):
            value = [convert(field.item, item) for item in value]
        return value

    def fill(items, values):
        if not isinstance(values, dict):
            raise ValueError()
        result = dict(values)
        for field in items:
            if field.secret:
                if field.name in result:
                    raise PluginError("CONNECTION_BLOCKED", "secret参数只能由连接注入", 403)
            elif field.name in result:
                result[field.name] = convert(field, result[field.name])
            elif "default" in field.model_fields_set:
                result[field.name] = convert(field, field.default)
        return result

    try:
        return fill(fields, arguments)
    except PluginError:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise PluginError("VALIDATION_ERROR", "表单参数类型无效，请检查数值/布尔/嵌套字段", 422) from exc


def request_parts(fields, arguments, path, method, connection):
    query, body, headers = {}, {}, {}
    for field in fields:
        position = field.position or ("query" if method == "GET" else "body")
        if field.secret:
            expected = "authorization" if connection.get("auth_type") == "bearer" else connection.get("api_key_header", "X-API-Key").lower()
            if connection.get("auth_type", "none") == "none" or field.name.lower() != expected:
                raise PluginError("INVALID_BINDING", "secret字段与连接鉴权请求头不匹配", 422)
            continue
        if field.name not in arguments:
            continue
        value = arguments[field.name]
        if position == "path":
            text = scalar(value)
            if text in {".", "..", ""} or any(c in text for c in "/\\%?#") or any(ord(c) < 32 for c in text):
                raise PluginError("CONNECTION_BLOCKED", "path参数不能包含路径分隔或转义字符", 403)
            path = path.replace("{" + field.name + "}", quote(text, safe=""))
        elif position == "header":
            text = scalar(value)
            if any(ord(c) < 32 or ord(c) > 126 for c in text):
                raise PluginError("VALIDATION_ERROR", "自定义请求头值须为无换行ASCII文本", 422)
            if field.name.lower() == connection.get("api_key_header", "X-API-Key").lower() and connection.get("auth_type") == "api_key":
                raise PluginError("CONNECTION_BLOCKED", "业务字段不能覆盖连接凭据头", 403)
            headers[field.name] = text
        elif position == "query":
            query[field.name] = [scalar(v) for v in value] if isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value) else scalar(value)
        else:
            body[field.name] = value
    if "{" in path or "}" in path:
        raise PluginError("VALIDATION_ERROR", "接口缺少必填path参数", 422)
    return path, query, body, headers


def scalar(value):
    if isinstance(value, (bool, dict, list)) or value is None:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return str(value)


def map_output(fields, raw):
    def read(value, path):
        for part in path.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                raise KeyError()
        return value

    def mapped(field, value):
        if field.type == "object" and isinstance(value, dict):
            return objects(field.children or [], value)
        if field.type == "array" and isinstance(value, list):
            return [mapped(field.item, item) for item in value]
        return value

    def objects(items, value):
        result = {}
        for field in items:
            if field.secret:
                continue
            try:
                result[field.name] = mapped(field, read(value, field.source_path or field.name))
            except KeyError:
                if field.required:
                    raise PluginError("TOOL_ERROR", "响应缺少配置的必填映射字段", 502)
        return result

    return objects(fields, raw)
