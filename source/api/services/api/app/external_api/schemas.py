"""Application API v1 DTOs. Inputs never become identity or execution policy."""
from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .values import canonical_payload_digest, reject_credentials

Scope = Literal['app.read','workflow.invoke','chat.invoke','runs.read','events.read','runs.cancel','runs.resume','files.upload','files.read','trace.read','conversations.read']
Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')]
Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @model_validator(mode='after')
    def no_credentials(self):
        reject_credentials(self.model_dump(mode='json'))
        return self


class Quotas(StrictDTO):
    requests_per_minute: int = Field(default=60, ge=1, le=6000)
    concurrent_runs: int = Field(default=2, ge=1, le=100)
    daily_invocations: int = Field(default=1000, ge=1, le=100000)
    daily_tokens: int = Field(default=1000000, ge=1, le=1000000000)


class ResourceConstraint(StrictDTO):
    kind: Identifier
    resource_id: Identifier
    version_id: Identifier | None = None
    actions: list[Literal['use','read','download_original','export']] = Field(min_length=1, max_length=4)


class ResourceConstraints(StrictDTO):
    # Empty means no source grant, never workspace-wide permission.
    resources: list[ResourceConstraint] = Field(default_factory=list, max_length=256)
    allowed_entities: list[Annotated[str, Field(min_length=1,max_length=100)]] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def unique_resources(self):
        pairs=[(r.kind,r.resource_id) for r in self.resources]
        if len(pairs)!=len(set(pairs)):raise ValueError('资源约束不能重复')
        return self


class RuntimePolicy(StrictDTO):
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    token_reservation: int = Field(default=8192, ge=1, le=1000000)
    expose_trace: bool = False
    allow_resume: bool = False
    max_file_bytes: int = Field(default=10485760, ge=1, le=10485760)
    allowed_file_types: list[Literal['text','image','audio','pdf','docx','xlsx','csv']] = Field(default_factory=list, max_length=7)
    max_streams: int = Field(default=2, ge=1, le=10)
    stream_lease_seconds: int = Field(default=30, ge=10, le=120)


class ApplicationCreate(StrictDTO):
    name: str = Field(min_length=1, max_length=100)
    type: Literal['agent','workflow']
    description: str = Field(default='', max_length=2000)


class ApplicationPatch(StrictDTO):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal['active','disabled','deleted'] | None = None
    quotas: Quotas | None = None


class Source(StrictDTO):
    kind: Literal['agent','workflow']
    id: Identifier
    version_id: Identifier | None = None
    expected_sha256: Digest | None = None

    @model_validator(mode='after')
    def published_agent(self):
        if self.kind == 'agent' and self.version_id is None:
            raise ValueError('发布Agent应用必须选择已发布Agent版本')
        return self


class VersionPreview(StrictDTO):
    source: Source
    runtime_policy: RuntimePolicy = Field(default_factory=RuntimePolicy)


class VersionPublish(VersionPreview):
    expected_application_revision: int = Field(ge=1)
    expected_source_sha256: Digest


class ActivateVersion(StrictDTO):
    version_id: Identifier
    expected_revision: int = Field(ge=1)


class ExpiringDTO(StrictDTO):
    expires_at: str | None = None

    @field_validator('expires_at')
    @classmethod
    def aware_timestamp(cls,value):
        if value is not None:
            parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
            if parsed.tzinfo is None: raise ValueError('到期时间必须包含时区')
        return value


class ClientCreate(ExpiringDTO):
    name: str = Field(min_length=1, max_length=100)
    default_version_id: Identifier
    scopes: list[Scope] = Field(min_length=1, max_length=11)
    external_user_mode: Literal['client','required'] = 'client'
    quotas: Quotas = Field(default_factory=Quotas)
    resource_constraints: ResourceConstraints = Field(default_factory=ResourceConstraints)


class ClientPatch(ExpiringDTO):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: Literal['active','disabled'] | None = None
    scopes: list[Scope] | None = Field(default=None, min_length=1, max_length=11)
    quotas: Quotas | None = None
    resource_constraints: ResourceConstraints | None = None


class KeyCreate(ExpiringDTO):
    name: str = Field(min_length=1, max_length=100)
    version_ids: list[Identifier] | None = Field(default=None, min_length=1, max_length=16)
    default_version_id: Identifier | None = None
    scopes: list[Scope] | None = Field(default=None, min_length=1, max_length=11)
    quotas: Quotas | None = None
    resource_constraints: ResourceConstraints | None = None


class KeyRotate(StrictDTO):
    expected_revision: int = Field(ge=1)
    old_key_valid_until: str | None = None

    @field_validator('old_key_valid_until')
    @classmethod
    def timestamp(cls,value):return ExpiringDTO.aware_timestamp(value)


class WorkflowInvocation(StrictDTO):
    inputs: dict = Field(default_factory=dict)
    user: str | None = Field(default=None, min_length=1, max_length=128)
    wait_seconds: int = Field(default=0, ge=0, le=20)

    @field_validator('inputs')
    @classmethod
    def bounded_inputs(cls,value):
        canonical_payload_digest(value,maximum_bytes=131072)
        reserved={'system','actor','roles','policy','workflow','debug','node_test','model_id','model_config_id','data_binding','secret_ref'}
        if set(value)&reserved:raise ValueError('业务输入包含受控执行字段')
        return value


class Message(StrictDTO):
    model_config=ConfigDict(extra='forbid',strict=True,json_schema_extra={'allOf':[
        {'anyOf':[{'required':['text'],'properties':{'text':{'type':'string','minLength':1,'pattern':r'\S'}}},
                  {'required':['attachment_ids'],'properties':{'attachment_ids':{'type':'array','minItems':1}}}]},
        {'if':{'properties':{'type':{'const':'text'}},'required':['type']},'then':{'properties':{'attachment_ids':{'maxItems':0}}}}
    ]})
    type: Literal['text','image','audio','file','mixed']
    text: str = Field(default='',max_length=16000)
    attachment_ids: list[Identifier] = Field(default_factory=list,max_length=8)

    @model_validator(mode='after')
    def content(self):
        if not self.text.strip() and not self.attachment_ids:raise ValueError('消息必须包含文本或受控附件')
        if self.type=='text' and self.attachment_ids:raise ValueError('文本消息不能带附件')
        return self


class ChatInvocation(WorkflowInvocation):
    message: Message
    conversation_id: Identifier | None = None


class PartitionAction(StrictDTO):
    user: str | None = Field(default=None,min_length=1,max_length=128)


class RuntimeAuthority(StrictDTO):
    schema_version: Literal['application-authority-v1'] = 'application-authority-v1'
    key_id: Identifier
    client_id: Identifier
    principal_id: Identifier
    application_id: Identifier
    version_id: Identifier
    tenant_id: Identifier
    workspace_id: Identifier
    scopes: list[Scope]
    external_user_hash: Digest
    resource_constraints: dict
    runtime_policy: dict
    credential_revision: int = Field(ge=1)


class ReleaseBundleV1(StrictDTO):
    schema_version: Literal['application-release-v1'] = 'application-release-v1'
    workflow_snapshot: dict
    agent_snapshots: dict
    model_bindings: dict
    dependency_manifest: list[dict]
    agent_data_scope: dict | list
    agent_knowledge_scope: dict | list
    scoped_bindings: dict | list
    input_schema: dict
    output_schema: dict
    runtime_policy: dict
    source_identity: dict


class KeyMetadata(StrictDTO):
    key_id: Identifier
    name: str
    prefix: str
    last4: str
    status: Literal['active','revoked','expired']
    revision: int
    scopes: list[Scope]
    version_ids: list[Identifier]
    default_version_id: Identifier
    expires_at: str | None
    quotas: Quotas
    resource_constraints: ResourceConstraints
    created_at: str
    last_used_at: str | None = Field(default=None,description='最后一次新幂等调用持久受理时间；不含认证/查询/重放；历史未知为null')
    last_used_semantics: Literal['last_new_invocation_admitted'] = 'last_new_invocation_admitted'


class KeyReceipt(BaseModel):
    # Deliberate one-time exception; must never be passed to Resource/audit.
    model_config = ConfigDict(extra='forbid')
    key: KeyMetadata
    api_key: str = Field(repr=False)
    old_key_valid_until: str | None = None


class ApplicationView(StrictDTO):
    application_id: Identifier
    name: str
    type: Literal['agent','workflow']
    description: str
    status: Literal['active','disabled','deleted']
    revision: int
    active_version_id: Identifier | None
    created_at: str
    updated_at: str
    permissions: list[str] = Field(default_factory=list)


class ClientView(StrictDTO):
    client_id: Identifier
    application_id: Identifier
    name: str
    status: Literal['active','disabled']
    revision: int
    default_version_id: Identifier
    scopes: list[Scope]
    external_user_mode: Literal['client','required']
    expires_at: str | None
    quotas: Quotas
    resource_constraints: ResourceConstraints
    created_at: str


class VersionView(StrictDTO):
    version_id: Identifier
    application_id: Identifier
    version: int
    status: Literal['published','disabled']
    source: Source
    source_sha256: Digest
    bundle_sha256: Digest
    input_schema: dict
    output_schema: dict
    runtime_policy: RuntimePolicy
    created_at: str


class ResourceOption(StrictDTO):
    label: str = Field(min_length=1,max_length=150)
    constraint: ResourceConstraint
    required: bool = True


class OptionBlocker(StrictDTO):
    code: str = Field(pattern=r'^[A-Z][A-Z0-9_]{0,79}$')
    message: str = Field(min_length=1,max_length=200)


class ClientOptionsView(StrictDTO):
    application_id: Identifier
    version_id: Identifier
    application_revision: int
    scopes: list[Scope]
    default_scopes: list[Scope]
    quotas: Quotas
    external_user_modes: list[Literal['client','required']]
    resource_options: list[ResourceOption] = Field(max_length=256)
    allowed_entities: list[Annotated[str,Field(min_length=1,max_length=100)]] = Field(max_length=100)
    resource_constraints: ResourceConstraints
    blockers: list[OptionBlocker] = Field(max_length=32)
    can_create_client: bool
    public_base_url: str | None = Field(max_length=500)
    public_base_url_status: Literal['configured','development','not_configured']


RunStatus=Literal['queued','running','resuming','paused','waiting_approval','outcome_unknown','succeeded','failed','cancelled','interrupted']


class TokenCounts(StrictDTO):
    prompt_tokens: int | None = Field(default=None,ge=0,le=2**53-1)
    completion_tokens: int | None = Field(default=None,ge=0,le=2**53-1)
    total_tokens: int | None = Field(default=None,ge=0,le=2**53-1)


class UsageView(StrictDTO):
    status: Literal['known','partial','unknown','reserved']
    tokens: TokenCounts
    reserved_tokens: int | None = Field(ge=0,description='仍保留的保守预约；不是实际用量。历史无法确定时null')
    source: Literal['provider_reported','no_model_calls','persisted_ledger','unavailable','pending']

    @model_validator(mode='after')
    def evidence(self):
        counts=self.tokens.model_dump(exclude_none=True)
        if self.status=='known' and 'total_tokens' not in counts:raise ValueError('已知用量必须有已记录的有效总数')
        if self.status=='partial' and not counts:raise ValueError('部分用量必须有已记录的有效数字')
        if self.status in {'unknown','reserved'} and counts:raise ValueError('未知或预约状态不能冒充实际数字')
        if self.source=='no_model_calls' and (self.status!='known' or any(counts.values())):raise ValueError('未调用模型来源只能对应明确已知零')
        return self


class TimingView(StrictDTO):
    status: Literal['pending','known','unknown']
    started_at: str | None
    finished_at: str | None
    elapsed_ms: int | None = Field(ge=0)
    includes_queue: Literal[True] = True
    basis: Literal['admission_to_terminal'] = 'admission_to_terminal'


class CallView(StrictDTO):
    request_id: Identifier
    run_id: Identifier
    application_id: Identifier
    client_id: Identifier
    key_id: Identifier
    key_prefix: str
    version_id: Identifier
    status: RunStatus
    safe_code: str | None
    usage_status: Literal['known','partial','unknown','reserved']
    usage: UsageView
    timing: TimingView
    created_at: str
    updated_at: str


class CallPage(StrictDTO):
    items: list[CallView]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1,le=100)
