from pydantic import Field, model_validator
from .schemas import StrictModel, FieldSchema, QualityRules, BatchRevision
from app.data_access import ControlledQuery


class DatabaseWrite(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class DataField(FieldSchema):
    meaning: str = Field(default="", max_length=500)
    unit: str = Field(default="", max_length=100)
    dimension: bool = False
    caliber: str = Field(default="", max_length=1000)


class TableWrite(DatabaseWrite):
    schema_fields: list[DataField] = Field(min_length=1, max_length=100)
    mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    quality_rules: QualityRules = Field(default_factory=QualityRules)

    @model_validator(mode="after")
    def unique_fields(self):
        if len({f.name for f in self.schema_fields}) != len(self.schema_fields):
            raise ValueError("字段名不可重复")
        return self


class DataRevision(BatchRevision):
    schema_fields: list[DataField] | None = Field(default=None, max_length=100)


class TableQuery(StrictModel):
    version_id: str | None = None
    query: ControlledQuery = Field(default_factory=ControlledQuery)
