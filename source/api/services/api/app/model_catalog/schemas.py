from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.agents.reasoning_profiles import ReasoningProfile
from app.agents.vision import VisionProfile

Parameter = Literal['temperature', 'top_p', 'max_tokens', 'frequency_penalty', 'presence_penalty', 'stop', 'seed']
PresetKey = Literal['custom', 'openai', 'volcengine_ark', 'aliyun_bailian', 'deepseek']
Visibility = Literal['public', 'private']


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Capabilities(Strict):
    parameters: list[Parameter] = Field(default_factory=lambda: ['temperature', 'max_tokens'], max_length=7)
    tools: bool = False
    json_object: bool = False
    json_schema: bool = False
    reasoning_summary: bool = False
    reasoning_explanation: bool = False
    reasoning_profile: ReasoningProfile | None = None
    vision_profile: VisionProfile | None = None

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.parameters)) != len(self.parameters):
            raise ValueError('参数不能重复')
        return self


class ProviderWrite(Strict):
    name: str = Field(min_length=1, max_length=100)
    protocol: Literal['openai_compatible'] = 'openai_compatible'
    preset: PresetKey = 'custom'
    base_url: str = Field(min_length=1, max_length=500)
    auth_type: Literal['api_key', 'none']
    api_key: SecretStr | None = Field(default=None, max_length=8192)
    clear_api_key: bool = False
    retain_api_key: bool = False
    timeout_seconds: float = Field(default=20, ge=.1, le=300)
    allow_private_network: bool = False
    enabled: bool = True
    visibility: Visibility | None = None


class ProviderPatch(Strict):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    protocol: Literal['openai_compatible'] | None = None
    preset: PresetKey | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    auth_type: Literal['api_key', 'none'] | None = None
    api_key: SecretStr | None = Field(default=None, max_length=8192)
    clear_api_key: bool = False
    retain_api_key: bool = False
    timeout_seconds: float | None = Field(default=None, ge=.1, le=300)
    allow_private_network: bool | None = None
    enabled: bool | None = None
    visibility: Visibility | None = None

    @model_validator(mode='after')
    def no_nulls(self):
        if any(getattr(self, key) is None for key in self.model_fields_set - {'api_key'}):
            raise ValueError('配置字段不能为null')
        return self


class ModelWrite(Strict):
    name: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=200)
    purpose: Literal['chat'] = 'chat'
    enabled: bool = True
    workspace_use: bool = False
    visibility: Visibility | None = None
    context_window: int = Field(default=131072, ge=512, le=1048576)
    max_output_tokens: int = Field(default=16384, ge=1, le=393216)
    capabilities: Capabilities = Field(default_factory=Capabilities)

    @model_validator(mode='after')
    def budgets(self):
        if self.capabilities.reasoning_profile and self.capabilities.reasoning_profile.model_id != self.model_name:
            raise ValueError('公开思考profile必须绑定当前模型ID')
        if self.capabilities.vision_profile and self.capabilities.vision_profile.model_id != self.model_name:
            raise ValueError('视觉profile必须绑定当前模型ID')
        reasoning,vision=self.capabilities.reasoning_profile,self.capabilities.vision_profile
        if reasoning and vision and (reasoning.profile_id=='deepseek_public_reasoning_v1' or reasoning.model_revision!=vision.model_revision):
            raise ValueError('公开推理与视觉须声明相同具体版本，当前DeepSeek profile不支持视觉联用')
        if self.max_output_tokens >= self.context_window:
            raise ValueError('输出上限必须小于上下文窗口')
        return self


class ModelPatch(Strict):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    purpose: Literal['chat'] | None = None
    enabled: bool | None = None
    workspace_use: bool | None = None
    visibility: Visibility | None = None
    context_window: int | None = Field(default=None, ge=512, le=1048576)
    max_output_tokens: int | None = Field(default=None, ge=1, le=393216)
    capabilities: Capabilities | None = None

    @model_validator(mode='after')
    def no_nulls(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError('配置字段不能为null')
        return self


class Empty(Strict):
    pass


class ReviewDecision(Strict):
    expected_revision: int = Field(ge=1)
    decision: Literal['approve', 'reject']


class SharingView(Strict):
    visibility: Visibility
    requested_visibility: Visibility | None
    review_status: Literal['approved', 'pending', 'rejected']
    can_manage: bool
    approved: bool


class ProviderView(SharingView):
    id: str
    name: str
    protocol: Literal['openai_compatible']
    preset: PresetKey
    base_url: str
    auth_type: Literal['api_key', 'none']
    timeout_seconds: float
    allow_private_network: bool
    enabled: bool
    revision: int
    created_at: str
    updated_at: str
    has_key: bool
    source: Literal['managed']


class ModelView(ModelWrite, SharingView):
    visibility: Visibility
    id: str
    provider_id: str
    revision: int
    config_version: str
    source: Literal['managed']
    created_at: str
    updated_at: str


class ProviderList(Strict):
    items: list[ProviderView]


class ProviderPreset(Strict):
    id: PresetKey
    name: str
    description: str
    protocol: Literal['openai_compatible'] = 'openai_compatible'
    base_url: str
    auth_type: Literal['api_key'] = 'api_key'
    documentation_url: str
    documentation_checked_at: str
    updated_at: str
    endpoint_notes: list[str]


class ProviderPresetList(Strict):
    items: list[ProviderPreset]


class ModelList(Strict):
    items: list[ModelView]


class UsableModel(Strict):
    id: str
    name: str
    provider_name: str
    source: Literal['managed', 'legacy_file']
    config_version: str
    capabilities: Capabilities
    context_window: int
    max_output_tokens: int


class UsableList(Strict):
    items: list[UsableModel]
