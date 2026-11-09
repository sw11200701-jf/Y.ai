"""Canonical contract2.0.0; compatible DSL1.0/2.0 and REST1.0.1. Decimal text only."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

class ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow")

class BusinessContext(ContractModel):
    tenant_id: str
    workspace_id: str
    operator_id: str
    actor_id: str
    principal_type: Literal["human", "service", "platform"] = "human"
    role: Literal["super_admin", "admin", "member", "service"]
    session_id: str | None
    application_id: str | None = None
    application_version_id: str | None = None
    client_id: str | None = None
    auth_key_id: str | None = None
    external_user_hash: str | None = None
    credential_revision: int | None = None
    role_id: str | None = None
    permissions: list[str] = Field(default_factory=list)
    must_change_password: bool = False
    allowed_entities: list[str] = Field(default_factory=list)
    allowed_binding_ids: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:8011"])

class ToolBinding(ContractModel):
    plugin_version_id: str
    tool_id: str
    connection_id: str | None = None

class PluginVersion(ContractModel):
    id: str
    plugin_id: str
    version: int
    name: str
    tools: list[dict[str, Any]]
    status: Literal["published", "disabled"] = "published"
    created_at: str

class Connection(ContractModel):
    id: str
    name: str
    base_url: str
    auth_type: Literal["none", "api_key", "bearer"] = "none"
    secret_ref: str | None = None
    allowed_hosts: list[str] = Field(default_factory=list)

class FinancialFact(ContractModel):
    id: str | None = None
    entity: str
    period: str
    metric_code: str
    amount: str = Field(pattern=r"^-?\d+(\.\d+)?$")
    currency: str = "CNY"
    unit: str = "yuan"
    scope: str = "consolidated"
    source_ref: str
    synthetic: bool = True

class FactQuery(ContractModel):
    # Missing financial input conveys no business subject, period or metric grant.
    # Explicit fields in stored requests and historical snapshots remain unchanged.
    entity: str = ""
    periods: list[str] = Field(default_factory=list)
    metric_codes: list[str] = Field(default_factory=list)
    currency: str = "CNY"
    unit: str = "yuan"
    scope: str = "consolidated"
    batch_id: str | None = None

class ResultDataset(ContractModel):
    id: str
    snapshot_id: str
    metric_version: str = "1.0.0"
    query: FactQuery
    columns: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    facts: list[FinancialFact] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)
    synthetic: bool = True
    created_at: str

class ToolExecutionResult(ContractModel):
    facts: list[FinancialFact] = Field(default_factory=list)
    data: Any = None
    source_refs: list[str] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)

class WorkflowNode(ContractModel):
    id: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)

class WorkflowEdge(ContractModel):
    source: str
    target: str
    source_port: str = "default"
    target_port: str = "default"

class WorkflowDefinition(ContractModel):
    id: str | None = None
    name: str = "新工作流"
    version: str = "1.0.0"
    # Legacy DAG execution tag. Model nodes select real models independently;
    # changing this default would invalidate deterministic saved DAGs.
    mode: Literal["demo", "model"] = "demo"
    schemaVersion: Literal["1.0", "2.0"] = "1.0"
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    variables: dict[str, dict[str, Any]] = Field(default_factory=dict)
    limits: dict[str, int] = Field(default_factory=lambda: {"max_steps": 256, "timeout_seconds": 120})
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]

class MessageEnvelope(BaseModel):
    model_config=ConfigDict(extra="forbid")
    type: Literal["text","image","audio","file","mixed"]
    text: str=Field(default="",max_length=16000)
    attachment_ids: list[str]=Field(default_factory=list,max_length=8)
    inputs: dict[str,Any]=Field(default_factory=dict)

class RunCreate(ContractModel):
    agent_id: str | None = None
    agent_version_id: str | None = None
    workflow_id: str | None = None
    workflow: WorkflowDefinition | None = None
    prompt: str = ""
    query: FactQuery = Field(default_factory=FactQuery)
    inputs: dict[str, Any] = Field(default_factory=dict)
    message: MessageEnvelope | None = None
    # Trusted admission metadata. These fields never grant permissions.
    draft_agent_id: str | None = None
    draft_revision: str | None = Field(default=None, max_length=128)
    run_kind: Literal["workflow", "draft", "node_test", "agent", "application"] = "workflow"
    node_test: dict[str, Any] | None = None

class Run(ContractModel):
    id: str
    status: Literal["queued", "running", "resuming", "paused", "waiting_approval", "outcome_unknown", "succeeded", "failed", "cancelled", "interrupted"]
    mode: Literal["demo", "model"] = "demo"
    created_at: str
    updated_at: str
    request: RunCreate
    dataset_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None

class RunEvent(ContractModel):
    id: int
    run_id: str
    sequence: int
    type: str
    node_id: str | None = None
    status: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: str

class Artifact(ContractModel):
    id: str
    run_id: str
    dataset_id: str
    type: Literal["docx", "xlsx", "png", "pdf"]
    filename: str
    path: str
    sha256: str
    size_bytes: int
    created_at: str
