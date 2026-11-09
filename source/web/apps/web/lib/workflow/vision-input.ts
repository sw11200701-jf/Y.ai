import type { ModelChoice } from '@/lib/model-catalog';
import type { AttachmentMetadata, MessageDraft, ObjectValue, VariableOption, WorkflowDefinition } from './types';

export type VisionProfile = { profile_id: 'openai_image_url_v1'; protocol: 'openai_chat_completions'; model_id: string; model_revision: string; input_source: 'current_run_message_attachment'; token_reservation: 'tiles512_2048_plus1024_v1' };
export function modelVisionProfile(model: ModelChoice | undefined): VisionProfile | undefined {
  const raw = (model?.capabilities as ObjectValue | undefined)?.vision_profile;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw) || Object.keys(raw).some(key => !['profile_id', 'protocol', 'model_id', 'model_revision', 'input_source', 'token_reservation'].includes(key))) return;
  if (raw.profile_id !== 'openai_image_url_v1' || raw.protocol !== 'openai_chat_completions' || raw.input_source !== 'current_run_message_attachment' || raw.token_reservation !== 'tiles512_2048_plus1024_v1' || typeof raw.model_id !== 'string' || !raw.model_id || raw.model_id.length > 200 || typeof raw.model_revision !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$/.test(raw.model_revision) || ['latest', 'auto', 'default', 'unknown'].includes(raw.model_revision.toLowerCase())) return;
  const reasoning = model?.capabilities.reasoning_profile;
  if (reasoning && (reasoning.profile_id === 'deepseek_public_reasoning_v1' || reasoning.model_id !== raw.model_id || reasoning.model_revision !== raw.model_revision)) return;
  return raw as VisionProfile;
}
/** Only connected typed image entry outputs qualify; a generic array/string never becomes a file source. */
export function visionSources(workflow: WorkflowDefinition | undefined, variables: VariableOption[]) {
  return variables.flatMap(variable => {
    const match = /^nodes\.([^.]+)\.message(?:\.(attachment_ids))?$/.exec(variable.ref);
    const node = match && workflow?.nodes.find(node => node.id === match[1]);
    if (!node || !['trigger_image', 'trigger_mixed'].includes(node.type)) return [];
    const kind = match![2] ? 'ids' : 'message';
    if (kind === 'ids' ? node.type !== 'trigger_image' || variable.type !== 'array' : variable.type !== 'object') return [];
    return [{ ...variable, kind: kind as 'ids' | 'message' }];
  });
}
export function currentImages(message?: MessageDraft): AttachmentMetadata[] {
  if (!message || !['image', 'mixed'].includes(message.type)) return [];
  const seen = new Set<string>();
  return message.attachments.filter(item => {
    if (item.media_kind !== 'image' || !/^[A-Za-z0-9_-]{1,100}$/.test(item.id) || !['image/png', 'image/jpeg', 'image/webp'].includes(item.mime) || !Number.isSafeInteger(item.size_bytes) || item.size_bytes < 1 || seen.has(item.id)) return false;
    seen.add(item.id); return true;
  });
}
export function visionInputIssues(config: ObjectValue, inputs: ObjectValue, model: ModelChoice | undefined, contract: boolean, sources: ReturnType<typeof visionSources>, message?: MessageDraft): string[] {
  const issues: string[] = [], explicit = Object.hasOwn(inputs, 'image_attachment_ids');
  const messageSource = sources.some(item => item.kind === 'message' && item.ref === inputs.message?.$ref);
  if (config.vision_enabled !== undefined && typeof config.vision_enabled !== 'boolean') issues.push('视觉开关格式异常，请明确关闭或在高级兼容中处理，原值未改变。');
  if (config.vision_enabled !== true) {
    if (explicit || messageSource) issues.push('已保存图片输入但未开启视觉；请明确开启，或移除图片选择/图片消息来源。');
    return issues;
  }
  if (!contract) issues.push('当前服务尚未声明LLM视觉输入合同，不能保存为可执行视觉配置。');
  if (!modelVisionProfile(model)) issues.push('所选模型没有可用的同版本视觉声明，或与公开推理声明冲突；请明确选择兼容模型版本。');
  if (!explicit) { if (!messageSource) issues.push('请选择本次消息中的图片，或选择已连接的图片/混合消息来源。'); return issues; }
  const value = inputs.image_attachment_ids;
  if (value && typeof value === 'object' && !Array.isArray(value) && typeof value.$ref === 'string') {
    if (!sources.some(item => item.kind === 'ids' && item.ref === value.$ref)) issues.push('已保存图片变量不是当前已连接的图片附件列表，请重新选择来源。');
    return issues;
  }
  if (!Array.isArray(value) || value.length < 1 || value.length > 4 || new Set(value).size !== value.length || value.some(id => typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(id))) return [...issues, '请选择1至4张不同的受控图片，不能填写URL、路径或其他资源ID。'];
  const images = currentImages(message), selected = value.map(id => images.find(image => image.id === id));
  if (selected.some(item => !item)) issues.push('部分已保存图片不在本次待受理消息中，请重新选择或明确移除旧选择。');
  if (selected.some(item => item && item.size_bytes > 2 * 1024 * 1024)) issues.push('单张视觉图片不能超过2MiB，请提供符合限制的图片。');
  if (selected.reduce((sum, item) => sum + (item?.size_bytes || 0), 0) > 4 * 1024 * 1024) issues.push('所选视觉图片总量不能超过4MiB。');
  return issues;
}
