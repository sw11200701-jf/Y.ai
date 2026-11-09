"""Actual runtime response/event DTOs used by routes AND contract export."""
from typing import Annotated,Literal
from pydantic import Field,JsonValue,model_validator,ConfigDict
from .schemas import StrictDTO,Identifier,Digest,RunStatus,UsageView,TimingView,Message


class RunLinks(StrictDTO):
    self: str = Field(pattern=r'^/api/open/v1/runs/[A-Za-z0-9_-]+$')
    events: str = Field(pattern=r'^/api/open/v1/runs/[A-Za-z0-9_-]+/events$')
    cancel: str = Field(pattern=r'^/api/open/v1/runs/[A-Za-z0-9_-]+/cancel$')


class RunReceipt(StrictDTO):
    model_config=ConfigDict(extra='forbid',strict=True,json_schema_extra={'allOf':[
        {'if':{'properties':{'status':{'const':'succeeded'}},'required':['status']},
         'then':{'required':['outputs','usage'],'properties':{'outputs':{'type':'object'},'usage':{'type':'object'}}},
         'else':{'not':{'required':['outputs']}}}
    ]})
    request_id: Identifier
    application_id: Identifier
    version_id: Identifier
    run_id: Identifier
    conversation_id: Identifier | None = None
    status: RunStatus
    created_at: str
    links: RunLinks
    replayed: bool
    outputs: dict[str,JsonValue] | None = Field(default=None,max_length=256,description='仅succeeded时存在，结构由已发布output_schema约束')
    usage: UsageView | None = None
    timing: TimingView | None = None

    @model_validator(mode='after')
    def terminal_output(self):
        if self.status=='succeeded' and (self.outputs is None or self.usage is None):raise ValueError('成功回执必须有公开outputs与用量状态')
        if self.status!='succeeded' and 'outputs' in self.model_fields_set:raise ValueError('非成功状态不能提供部分内部输出')
        return self


class PublicCapabilities(StrictDTO):
    token_streaming: Literal[False]
    progress_events: Literal[True]
    trace: bool
    resume: bool


class ApplicationInfo(StrictDTO):
    application_id: Identifier
    version_id: Identifier
    status: Literal['active']
    type: Literal['agent','workflow']
    capabilities: PublicCapabilities


class ParameterLimits(StrictDTO):
    input_bytes: Literal[131072]
    text_characters: Literal[16000]
    wait_seconds: Literal[20]
    timeout_seconds: int = Field(ge=1,le=600)


class FilePolicy(StrictDTO):
    max_file_bytes: int = Field(ge=1,le=10485760)
    allowed_file_types: list[Literal['text','image','audio','pdf','docx','xlsx','csv']] = Field(max_length=7)


class ApplicationParameters(StrictDTO):
    application_id: Identifier
    version_id: Identifier
    input_schema: dict[str,JsonValue] = Field(description='固定发布版的JSON Schema；不是任意执行DSL')
    output_schema: dict[str,JsonValue]
    limits: ParameterLimits
    file_policy: FilePolicy
    modes: list[Literal['async','bounded_wait']]


class FileMetadata(StrictDTO):
    file_id: Identifier
    filename: str = Field(min_length=1,max_length=150)
    media_type: str = Field(pattern=r'^[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+$')
    size_bytes: int = Field(ge=1,le=10485760)
    sha256: Digest
    created_at: str


class UserHistoryItem(StrictDTO):
    role: Literal['user']
    content: Message


class AssistantHistoryItem(StrictDTO):
    role: Literal['assistant']
    content: dict[str,JsonValue] = Field(max_length=256,description='仅已授权的成功最终outputs，按固定发布output_schema约束')


HistoryItem=Annotated[UserHistoryItem|AssistantHistoryItem,Field(discriminator='role')]


class ConversationView(StrictDTO):
    conversation_id: Identifier
    revision: int = Field(ge=0)
    messages: list[HistoryItem] = Field(max_length=10,description='完整user/assistant对，总体不超过32KiB')
    active_run_id: Identifier | None


class TraceStep(StrictDTO):
    status: RunStatus


class TraceView(StrictDTO):
    status: RunStatus | Literal['unknown']
    steps: list[TraceStep] = Field(max_length=256)


class ErrorBody(StrictDTO):
    code: str = Field(pattern=r'^[A-Z][A-Z0-9_]{0,79}$')
    message: str
    details: JsonValue = Field(description='仅安全字段或null，不回输入、Key、路径或堆栈')


class ErrorEnvelope(StrictDTO):
    error: ErrorBody
    request_id: str | None


class ProgressEvent(StrictDTO):
    sequence: int = Field(ge=1)
    type: Literal['progress']
    scope: Literal['run','step','approval','effect']
    status: RunStatus


class ResultEvent(StrictDTO):
    sequence: int = Field(ge=1)
    type: Literal['result']
    scope: Literal['run']
    status: Literal['succeeded']
    outputs: dict[str,JsonValue] = Field(max_length=256)
    usage: UsageView


class StreamError(StrictDTO):
    code: str = Field(pattern=r'^[A-Z][A-Z0-9_]{0,79}$')
    request_id: str


class ContractInfo(StrictDTO):
    title: str
    version: str


class ContractServer(StrictDTO):
    url: str


class OpenAPIContract(StrictDTO):
    openapi: str
    info: ContractInfo
    paths: dict[str,dict[str,JsonValue]]
    components: dict[str,JsonValue]
    servers: list[ContractServer] = Field(default_factory=list)
    application_contract: dict[str,JsonValue] | None = Field(default=None,alias='x-application-contract')
