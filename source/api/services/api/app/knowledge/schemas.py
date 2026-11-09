from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class LibraryWrite(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)

class DocumentOverview(StrictModel):
    topic: str = Field(default="", max_length=300)
    subject: str = Field(default="", max_length=300)
    period: str = Field(default="", max_length=120)
    summary: str = Field(default="", max_length=2000)

class FieldSchema(StrictModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f]+$")
    type: Literal["string", "integer", "decimal", "boolean", "date"] = "string"
    required: bool = False
    unique: bool = False
    format: Literal["email", "iso_date", "code"] | None = None
    max_length: int = Field(default=2000, ge=1, le=5000)

class QualityRules(StrictModel):
    unique_rows: bool = True

class DatasetWrite(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    dataset_type: Literal["generic", "finance"] = "generic"
    schema_fields: list[FieldSchema] = Field(default_factory=list, max_length=100)
    mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    quality_rules: QualityRules = Field(default_factory=QualityRules)

class RowCorrection(StrictModel):
    row: int = Field(ge=2, le=5001)
    column: str = Field(min_length=1, max_length=120)
    value: str | int | bool | None

class CleaningRule(StrictModel):
    field: str
    operation: Literal["trim", "uppercase", "lowercase"]

class BatchRevision(StrictModel):
    schema_fields: list[FieldSchema] | None = Field(default=None, max_length=100)
    mapping: dict[str, str] | None = None
    quality_rules: QualityRules | None = None
    corrections: list[RowCorrection] = Field(default_factory=list, max_length=1000)
    cleaning: list[CleaningRule] = Field(default_factory=list, max_length=100)

class ChunkCorrection(StrictModel):
    chunk_id: str
    text: str | None = Field(default=None, min_length=1, max_length=32700)
    enabled: bool | None = None

    @model_validator(mode="after")
    def has_change(self):
        if self.text is None and self.enabled is None: raise ValueError("请提供正文或启用状态")
        return self

class KnowledgeAddition(StrictModel):
    source_chunk_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=32700)
    topic: str = Field(default='', max_length=200)
    enabled: bool = True

class DocumentRevision(StrictModel):
    corrections: list[ChunkCorrection] = Field(default_factory=list, max_length=1000)
    base_version_id: str
    additions: list[KnowledgeAddition] = Field(default_factory=list, max_length=100)
    deletions: list[str] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def has_changes(self):
        if not (self.corrections or self.additions or self.deletions): raise ValueError('请提供知识修改')
        return self

class ChunkPatch(StrictModel):
    base_version_id: str
    text: str | None = Field(default=None, min_length=1, max_length=32700)
    enabled: bool | None = None

    @model_validator(mode="after")
    def has_change(self):
        if self.text is None and self.enabled is None: raise ValueError("请提供正文或启用状态")
        return self

class ParseRequest(StrictModel):
    strategy: Literal["length", "paragraph"] = "length"
    chunk_size: int = Field(default=1000, ge=100, le=2000)
    chunk_overlap: int = Field(default=100, ge=0, le=200)

    @model_validator(mode="after")
    def reasonable_overlap(self):
        if self.chunk_overlap > self.chunk_size // 2: raise ValueError("重叠不得超过切片长度一半")
        return self

class PublishRequest(StrictModel):
    version_id: str

class QueryRequest(StrictModel):
    version_id: str | None = None

class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    knowledge_base_ids: list[str] = Field(min_length=1, max_length=20)
    file_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)
    version_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)
    top_k: int = Field(default=5, ge=1, le=20)

class Grant(StrictModel):
    user_id: str
    permission: Literal["read", "write"]

class AccessWrite(StrictModel):
    grants: list[Grant] = Field(default_factory=list, max_length=50)
    cascade: bool = False
