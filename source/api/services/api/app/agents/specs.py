"""Agent studio drafts. Credentials/endpoints are never accepted here."""

import hashlib
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, field_serializer, model_validator, model_serializer

from app.contracts import FactQuery, MessageEnvelope, ToolBinding
from app.plugins.specs import StrictModel, check_schema
from .skill_bindings import SkillBinding


def binding_id(binding: ToolBinding | dict) -> str:
    data = binding.model_dump() if isinstance(binding, ToolBinding) else binding
    return "binding_" + hashlib.sha256("\x00".join(data.get(k) or "" for k in (
        "plugin_version_id", "tool_id", "connection_id")).encode()).hexdigest()[:24]


class ModelParameters(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    temperature: float = Field(default=0, ge=0, le=2, strict=True)
    max_tokens: int = Field(default=4096, ge=1, le=393216, strict=True)
    top_p: float | None = Field(default=None, ge=0, le=1, strict=True)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2, strict=True)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2, strict=True)
    stop: list[str] | None = Field(default=None, max_length=4)
    seed: int | None = Field(default=None, ge=-2147483648, le=2147483647, strict=True)

    @model_validator(mode="before")
    @classmethod
    def no_nulls(cls, value):
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("模型参数不能为null，请移除未设置项")
        return value

    @field_validator("stop")
    @classmethod
    def bounded_stop(cls, value):
        if value is not None and any(not 1 <= len(item) <= 200 for item in value):
            raise ValueError("每个停止词须为1至200字符")
        return value

    @model_serializer(mode="wrap")
    def omit_unset_options(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class ToolSettings(StrictModel):
    binding_id: str
    parameter_mode: Literal["automatic", "fixed"] = "automatic"
    fixed_arguments: dict = Field(default_factory=dict)
    enabled: bool = True


class KnowledgeResource(StrictModel):
    resource_id: str
    enabled: bool = True


class DataResource(StrictModel):
    kind: Literal["database", "data_table"]
    resource_id: str = Field(min_length=1, max_length=100)
    version_id: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool = True

    @model_validator(mode="after")
    def table_version_only(self):
        if self.kind == "database" and self.version_id:
            raise ValueError("固定版本须选择具体数据表")
        return self


STUDIO_FIELDS = {"system_prompt", "model_parameters", "input_schema", "tool_settings", "knowledge_resources", "data_resources",
                 "opening_message", "suggested_questions", "output_schema", "max_rounds", "context_budget", "document_tools"}


class AgentWrite(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    agent_type: Literal["simple", "advanced"] | None = None
    bindings: list[ToolBinding] = Field(default_factory=list, max_length=16)
    document_tools:list[Literal['document.word.v1','document.excel.v1','document.powerpoint.v1']]=Field(default_factory=list,max_length=3)
    skill_bindings: list[SkillBinding] = Field(default_factory=list,max_length=8)
    planner_mode: Literal["demo", "model"] = "model"
    model_config_id: str | None = Field(default=None, max_length=100)
    model_config_version: str | None = Field(default=None, max_length=100)
    main_workflow_id: str | None = None
    system_prompt: str = Field(default="", max_length=16000)
    model_parameters: ModelParameters = Field(default_factory=ModelParameters)
    input_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False})
    tool_settings: list[ToolSettings] = Field(default_factory=list, max_length=16)
    knowledge_resources: list[KnowledgeResource] = Field(default_factory=list, max_length=16)
    data_resources: list[DataResource] = Field(default_factory=list, max_length=16)
    opening_message: str = Field(default="", max_length=2000)
    suggested_questions: list[str] = Field(default_factory=list, max_length=10)
    output_schema: dict = Field(default_factory=lambda: {"type": "object"})
    max_rounds: int = Field(default=4, ge=1, le=8)
    context_budget: int = Field(default=8192, ge=512, le=1048576)

    @field_serializer("model_parameters")
    def serialize_parameter_presence(self, value):
        # Omitted legacy field keeps its defaults. An explicit object is a full
        # replacement: absent keys were removed by the editor, including {}.
        return value.model_dump(exclude_unset="model_parameters" in self.model_fields_set)

    @field_validator("bindings", mode="before")
    @classmethod
    def strict_bindings(cls, value):
        if isinstance(value, list):
            for binding in value:
                data = binding.model_dump() if isinstance(binding, ToolBinding) else binding
                if isinstance(data, dict) and (not {"plugin_version_id", "tool_id"}.issubset(data) or set(data) - {"plugin_version_id", "tool_id", "connection_id"}):
                    raise ValueError("绑定只接受已发布版本、工具和连接 ID")
        return value

    @field_validator("input_schema")
    @classmethod
    def input_valid(cls, value):
        return check_schema(value, business_input=True)

    @field_validator("output_schema")
    @classmethod
    def output_valid(cls, value):
        return check_schema(value)

    @field_validator("suggested_questions")
    @classmethod
    def short_questions(cls, value):
        if any(not isinstance(q, str) or not q.strip() or len(q) > 1000 for q in value):
            raise ValueError("建议问题须为1至1000字符")
        return value

    @model_validator(mode="after")
    def references(self):
        ids = [binding_id(b) for b in self.bindings]
        setting_ids = [s.binding_id for s in self.tool_settings]
        if len(set(ids)) != len(ids) or len(set(setting_ids)) != len(setting_ids):
            raise ValueError("同一工具绑定或配置不能重复")
        if any(key not in ids for key in setting_ids):
            raise ValueError("参数配置引用了未绑定的工具")
        resources = [r.resource_id for r in self.knowledge_resources]
        if len(set(resources)) != len(resources):
            raise ValueError("资料资源不能重复")
        data_ids = [(r.kind, r.resource_id) for r in self.data_resources]
        if len(set(data_ids)) != len(data_ids):
            raise ValueError("数据资源范围不能重复")
        return self


class AgentDebug(StrictModel):
    prompt: str = Field(default="", max_length=4000)
    query: FactQuery = Field(default_factory=FactQuery)
    inputs: dict = Field(default_factory=dict)
    message: MessageEnvelope | None = None
    agent_version_id: str | None = None
