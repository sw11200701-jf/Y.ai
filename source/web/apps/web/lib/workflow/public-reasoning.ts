/** View-only DTO projection. Current-run authorization and redaction remain server responsibilities. */
export type PublicReasoningStatus = 'available' | 'truncated' | 'disabled' | 'not_returned' | 'redacted' | 'not_adapted';
export type TraceReasoning = {
  available: boolean; text?: string | null; kind?: 'provider_reasoning' | 'provider_summary' | 'provider_explanation';
  source?: string; status?: PublicReasoningStatus; truncated?: boolean; profile_id?: string; protocol?: string;
  model_revision?: string; profile_verification?: string; unavailable_reason?: string | null;
};
export type ReasoningDisplay = {
  title: string; status: PublicReasoningStatus | 'unreported' | 'invalid' | 'legacy_available';
  statusLabel: string; message: string; text?: string; source?: string; sourceLabel?: string;
  profileLabel?: string; revision?: string; verification?: string;
};
const profiles = {
  aliyun_public_reasoning_v1: { label: '阿里云公开推理 v1', kind: 'provider_reasoning' },
  ark_public_reasoning_v1: { label: '方舟公开推理 v1', kind: 'provider_reasoning' },
  ark_public_summary_v1: { label: '方舟公开摘要 v1', kind: 'provider_summary' },
  deepseek_public_reasoning_v1: { label: 'DeepSeek公开推理 v1', kind: 'provider_reasoning' },
} as const;
const sources = {
  'provider.message.reasoning_content': '回复消息中的公开文字',
  'provider.delta.reasoning_content': 'SSE回复中的公开文字（请求收完后展示）',
} as const;
const statusCopy = {
  available: ['完整返回', '本次公开文本完整返回，供应商未报告截断。'],
  truncated: ['已截断', '公开文本已截断，仅显示本次可展示的部分。'],
  disabled: ['已关闭', '本次模型配置已关闭公开推理模式。'],
  not_returned: ['本次未返回', '已选择明确的适配协议，但供应商本次未返回可读公开文字。'],
  redacted: ['已隐藏', '公开文字因脱敏或私有上下文保护而隐藏。'],
  not_adapted: ['尚未适配', '本次回复尚未选择或通过明确的公开内容适配，不能判断供应商是否提供。'],
} as const;
const plain = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const boundedText = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0 && Array.from(value).length <= 4000 && new TextEncoder().encode(value).length <= 8192;
const noExtra = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).every(key => keys.includes(key));
const invalid = (): ReasoningDisplay => ({ title: '公开推理与摘要', status: 'invalid', statusLabel: '展示信息无效', message: '返回的公开内容格式或状态不一致，文字已隐藏；请核对适配协议。' });
const unreported = (): ReasoningDisplay => ({ title: '公开推理与摘要', status: 'unreported', statusLabel: '未提供展示状态', message: '本次运行没有明确的公开内容展示状态，无法判断是否已适配或供应商是否返回。' });

/** Never infer reasoning from answers, vendor names, events, opaque fields, or arbitrary error strings. */
export function publicReasoningDisplay(value: unknown): ReasoningDisplay {
  if (value === undefined || value === null) return unreported();
  if (!plain(value)) return invalid();
  if (value.status === 'not_adapted' && value.available === false && value.text == null && noExtra(value, ['available', 'status', 'unavailable_reason'])) {
    return { title: '公开推理与摘要', status: 'not_adapted', statusLabel: statusCopy.not_adapted[0], message: statusCopy.not_adapted[1] };
  }
  if (Object.hasOwn(value, 'profile_id')) {
    if (!noExtra(value, ['available', 'status', 'profile_id', 'protocol', 'model_revision', 'profile_verification', 'kind', 'source', 'truncated', 'text', 'unavailable_reason'])) return invalid();
    const profile = typeof value.profile_id === 'string' && Object.hasOwn(profiles, value.profile_id) ? profiles[value.profile_id as keyof typeof profiles] : undefined;
    const source = typeof value.source === 'string' && Object.hasOwn(sources, value.source) ? sources[value.source as keyof typeof sources] : undefined;
    const status = typeof value.status === 'string' && value.status !== 'not_adapted' && Object.hasOwn(statusCopy, value.status) ? value.status as Exclude<PublicReasoningStatus, 'not_adapted'> : undefined;
    if (!profile || !source || !status || value.protocol !== 'openai_chat_completions' || value.profile_verification !== 'administrator_declared' || value.kind !== profile.kind || typeof value.model_revision !== 'string' || !value.model_revision.trim() || Array.from(value.model_revision).length > 200) return invalid();
    const readable = status === 'available' || status === 'truncated';
    if (readable ? value.available !== true || !boundedText(value.text) || value.truncated !== (status === 'truncated') || value.unavailable_reason != null : value.available !== false || value.text != null || value.truncated !== false || typeof value.unavailable_reason !== 'string' || !value.unavailable_reason || Array.from(value.unavailable_reason).length > 200) return invalid();
    return {
      title: profile.kind === 'provider_summary' ? '供应商摘要' : '供应商公开推理内容', status,
      statusLabel: statusCopy[status][0], message: statusCopy[status][1], ...(readable ? { text: value.text as string } : {}),
      source: value.source as string, sourceLabel: source, profileLabel: profile.label, revision: value.model_revision,
      verification: '管理员声明的版本适配；未代表平台已实测供应商',
    };
  }
  // Retain only the two explicitly supported legacy public fields, without claiming completeness or a profile.
  if (value.available === true && !Object.hasOwn(value, 'status') && noExtra(value, ['available', 'kind', 'source', 'text', 'unavailable_reason']) && value.unavailable_reason == null && boundedText(value.text) &&
      (value.kind === 'provider_summary' && value.source === 'provider.message.reasoning_summary' || value.kind === 'provider_explanation' && value.source === 'provider.message.reasoning_explanation')) {
    return { title: value.kind === 'provider_summary' ? '供应商摘要' : '供应商公开推理说明', status: 'legacy_available', statusLabel: '旧版公开字段 · 完整性未标注', message: '此运行使用旧版显式公开字段，未提供版本适配与截断状态。', text: value.text, source: value.source, sourceLabel: '旧版回复消息的显式公开字段' };
  }
  if (value.available === false && value.text == null && !Object.hasOwn(value, 'status') && noExtra(value, ['available', 'unavailable_reason'])) return unreported();
  return invalid();
}
