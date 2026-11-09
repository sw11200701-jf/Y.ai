"""Read-only form suggestions, never configured resources or authorization."""
from .schemas import ProviderPreset

CHECKED_AT = '2026-10-21'


def provider_presets():
    # Return fresh validated values: no mutable shared state, writes or discovery.
    definitions = (
        ('volcengine_ark', '火山方舟', '北京地域普通按量 Chat 接口',
         'https://ark.cn-beijing.volces.com/api/v3',
         'https://docs.volcengine.com/docs/ark/chat-api?lang=zh', [
             '适用于北京地域普通按量 API Key，不适用于 Coding Plan 或 Agent Plan。',
             '请在控制台开通服务并手动添加模型或推理接入点标识；模型能力须按所选模型声明。']),
        ('aliyun_bailian', '阿里云百炼', '北京地域 OpenAI Chat 兼容接口',
         'https://dashscope.aliyuncs.com/compatible-mode/v1',
         'https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope', [
             '此共享北京域名仍可用；官方推荐复制控制台的业务空间专属地址。',
             '业务空间专属地址示例：https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1。',
             '请使用与地址地域一致的 API Key；更换地域或业务空间时核对凭据，不自动切换。']),
        ('deepseek', 'DeepSeek', 'DeepSeek 普通 Chat 兼容接口',
         'https://api.deepseek.com', 'https://api-docs.deepseek.com/zh-cn/', [
             '使用普通 Chat API Key；模型标识以控制台及官方最新文档为准，手动添加。',
             '模板不声明模型的参数或结构输出能力。']),
        ('openai', 'OpenAI', 'OpenAI Chat Completions 接口',
         'https://api.openai.com/v1', 'https://platform.openai.com/docs/api-reference/chat', [
             '手动添加账号可用的模型；参数和结构输出能力按模型分别配置。']),
        ('custom', '自定义 OpenAI-compatible', '填写兼容 Chat Completions 的模型服务地址',
         '', 'https://platform.openai.com/docs/api-reference/chat', [
             '填写基址，不填写完整 /chat/completions 地址；支持空路径、/v1、/api/v3、/compatible-mode/v1。',
             '本机或内网服务仍需管理员明确允许；模板不会自动授权网络访问。']),
    )
    return {'items': [ProviderPreset(id=key, name=name, description=description,
        base_url=base_url, documentation_url=documentation_url,
        documentation_checked_at=CHECKED_AT, updated_at=CHECKED_AT,
        endpoint_notes=notes).model_dump()
        for key, name, description, base_url, documentation_url, notes in definitions]}
