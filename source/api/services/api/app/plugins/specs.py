"""Module-owned API validation, separate from shared persistence contracts."""

import re
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from pydantic import AliasChoices, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .errors import PluginError
from .spec_base import StrictModel
from .fields import FieldDefinition, compile_fields

RESERVED_ARGUMENTS = {
    "url", "base_url", "host", "hostname", "headers", "authorization", "token",
    "api_key", "apikey", "secret", "secret_ref", "password", "access_token", "refresh_token",
    "bearer_token", "credential", "credential_ref", "tenant_id", "workspace_id",
    "actor_id", "operator_id", "allowed_entities", "allowed_origins", "allowed_binding_ids",
    "connection_id", "binding_id", "plugin_version_id", "tool_id",
}


def check_schema(schema: dict[str, Any], *, business_input: bool = False) -> dict:
    from jsonschema import Draft202012Validator

    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise ValueError("无效的 JSON Schema") from exc

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if any(key in value for key in ("$ref", "$dynamicRef", "$recursiveRef")):
                raise ValueError("Demo Schema 不支持引用，请内联字段定义")
            if business_input:
                for key in value.get("properties", {}):
                    if key.lower() in RESERVED_ARGUMENTS:
                        raise ValueError("业务 Schema 不得包含地址、凭据或企业上下文字段")
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(schema)
    if business_input:
        if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
            raise ValueError("业务输入须为 object 且 additionalProperties=false")
    return schema


class EndpointDraft(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    operation_id: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
                                     validation_alias=AliasChoices("operation_id", "operationId"))
    name: str = Field(min_length=1, max_length=100)
    method: Literal["GET", "POST"] = "GET"
    request_encoding: Literal["json", "form_urlencoded", "multipart"] = "json"
    path: str = Field(min_length=1, max_length=500)
    read_only: Literal[True] = True
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    input_fields: list[FieldDefinition] | None = Field(default=None, max_length=128)
    output_fields: list[FieldDefinition] | None = Field(default=None, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def visual_schemas(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        for direction in ("input", "output"):
            field_key, schema_key = direction + "_fields", direction + "_schema"
            if data.get(field_key) is not None:
                if not isinstance(data[field_key], list):
                    continue  # Normal Pydantic list validation supplies the field location.
                fields = []
                for index, item in enumerate(data[field_key]):
                    try:
                        fields.append(item if isinstance(item, FieldDefinition) else FieldDefinition.model_validate(item))
                    except ValidationError as exc:
                        errors = [{**error, "loc": (field_key, index, *error["loc"])} for error in exc.errors()]
                        raise ValidationError.from_exception_data(cls.__name__, errors) from exc
                compiled = compile_fields(fields, output=direction == "output", method=data.get("method", "GET"))
                if schema_key in data and data[schema_key] != compiled:
                    raise ValueError("可视化字段与提交的JSON Schema不一致，请以字段重新编译")
                data[schema_key] = compiled
        return data

    @model_validator(mode="after")
    def compatible_operation_id(self):
        if self.method != "POST" and self.request_encoding != "json":
            raise ValueError("表单编码仅支持POST接口")
        self.operation_id = self.operation_id or self.id
        placeholders = re.findall(r"{([A-Za-z_][A-Za-z0-9_-]*)}", self.path)
        path_fields = [f.name for f in self.input_fields or [] if f.position == "path"]
        if set(placeholders) != set(path_fields):
            raise ValueError("路径模板与必填path字段必须一一对应")
        if ("{" in re.sub(r"{[A-Za-z_][A-Za-z0-9_-]*}", "", self.path)
                or "}" in re.sub(r"{[A-Za-z_][A-Za-z0-9_-]*}", "", self.path)):
            raise ValueError("路径模板无效")
        return self

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        decoded = unquote(unquote(value))
        parsed = urlsplit(decoded)
        if (not value.startswith("/") or decoded.startswith("//") or parsed.scheme
                or parsed.netloc or parsed.query or parsed.fragment or "\\" in decoded
                or any(part in {".", ".."} for part in decoded.split("/"))
                or any(ord(c) < 32 for c in decoded) or "%" in decoded):
            raise ValueError("接口路径须为固定的根相对路径，不含查询、模板或跳转")
        return value

    @field_validator("input_schema")
    @classmethod
    def input_valid(cls, value: dict) -> dict:
        return check_schema(value, business_input=True)

    @field_validator("output_schema")
    @classmethod
    def output_valid(cls, value: dict) -> dict:
        return check_schema(value)


class ToolDraft(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    endpoint_id: str

    @field_validator("description", mode="before")
    @classmethod
    def required_description(cls, value):
        if isinstance(value, str) and not value.strip():
            raise PydanticCustomError("tool_description_required", "请填写工具用途说明，不能仅为空格")
        return value


class PluginDraft(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    default_connection_id: str | None = Field(default=None, validation_alias=AliasChoices("default_connection_id", "connection_id"))


    endpoints: list[EndpointDraft] = Field(default_factory=list, max_length=32)
    tools: list[ToolDraft] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def valid_references(self):
        endpoint_ids = [e.id for e in self.endpoints]
        tool_ids = [t.id for t in self.tools]
        operations = [e.operation_id for e in self.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)) or len(tool_ids) != len(set(tool_ids)):
            raise ValueError("接口和工具 ID 不得重复")
        if len(operations) != len(set(operations)):
            raise ValueError("同一插件的 operation_id 不得重复")
        if any(t.endpoint_id not in endpoint_ids for t in self.tools):
            raise ValueError("工具引用了不存在的接口")
        return self


class BindingSelection(StrictModel):
    plugin_version_id: str
    tool_id: str
    connection_id: str | None = None


def validate_arguments(schema: dict, arguments: dict) -> None:
    from jsonschema import Draft202012Validator, FormatChecker

    if not isinstance(arguments, dict):
        raise PluginError("VALIDATION_ERROR", "工具业务参数须为对象", 422)

    def forbidden(value: Any) -> bool:
        if isinstance(value, dict):
            return any(str(k).lower() in RESERVED_ARGUMENTS or forbidden(v) for k, v in value.items())
        if isinstance(value, list):
            return any(forbidden(v) for v in value)
        return False

    if forbidden(arguments):
        raise PluginError("CONNECTION_BLOCKED", "业务参数不能指定地址、凭据或企业上下文", 403)
    if not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(arguments):
        raise PluginError("VALIDATION_ERROR", "业务参数不符合已发布工具的 Schema", 422)
