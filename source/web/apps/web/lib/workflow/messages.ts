import type { AttachmentMetadata, MessageDraft, MessageEnvelope, MessageKind } from './types';
export const messageKinds: { type: MessageKind; label: string; glyph: string; accept?: string }[] = [
  { type: 'image', label: '图片消息', glyph: '▧', accept: '.png,.jpg,.jpeg,.webp' },
  { type: 'audio', label: '语音消息', glyph: '◉', accept: '.wav,.mp3,.m4a' },
  { type: 'text', label: '文字消息', glyph: 'T' },
  { type: 'file', label: '文件消息', glyph: '▤', accept: '.txt,.md,.pdf,.docx,.xlsx' },
  { type: 'mixed', label: '混合消息', glyph: '⊞', accept: '.png,.jpg,.jpeg,.webp,.wav,.mp3,.m4a,.txt,.md,.pdf,.docx,.xlsx' },
];
export const emptyMessage = (): MessageDraft => ({ type: 'text', text: '', attachments: [] });
export function messageError(draft: MessageDraft): string | null {
  const count = draft.attachments.length;
  if (count > 8) return '一条消息最多包含8个附件';
  if (new Set(draft.attachments.map(a => a.id)).size !== count) return '消息中不能重复引用同一附件';
  if (draft.attachments.some(a => !a.id || a.size_bytes > 10 * 1024 * 1024)) return '附件未完成上传或超过10MiB限制';
  if (draft.type === 'text') return !draft.text.trim() ? '请输入文字内容' : count ? '文字消息不能带附件' : null;
  if (draft.type === 'mixed') return !draft.text.trim() || !count ? '混合消息需要文字和至少一个附件' : null;
  if (!count) return '请先选择并上传附件';
  if (draft.type === 'image' && draft.attachments.some(a => a.media_kind !== 'image')) return '图片消息只能选择图片附件';
  if (draft.type === 'audio' && (count !== 1 || draft.attachments[0].media_kind !== 'audio')) return '语音消息需要且只能包含一个音频附件';
  if (draft.type === 'file' && draft.attachments.some(a => a.media_kind !== 'document')) return '文件消息只能选择文档附件';
  return null;
}
export function toMessageEnvelope(draft: MessageDraft): MessageEnvelope {
  const error = messageError(draft); if (error) throw new Error(error);
  return { type: draft.type, ...(draft.text.trim() ? { text: draft.text } : {}), ...(draft.attachments.length ? { attachment_ids: draft.attachments.map(a => a.id) } : {}) };
}
export function localFileError(file: { name: string; size: number }, kind: MessageKind): string | null {
  if (file.size > 10 * 1024 * 1024) return `${file.name} 超过10MiB`;
  const extension = file.name.split('.').pop()?.toLowerCase();
  const supported = ({ image: ['png', 'jpg', 'jpeg', 'webp'], audio: ['wav', 'mp3', 'm4a'], file: ['txt', 'md', 'pdf', 'docx', 'xlsx'], mixed: ['png', 'jpg', 'jpeg', 'webp', 'wav', 'mp3', 'm4a', 'txt', 'md', 'pdf', 'docx', 'xlsx'], text: [] } as Record<MessageKind, string[]>)[kind];
  return !extension || !supported.includes(extension) ? `${file.name} 不属于此消息支持的文件类型` : null;
}
export function validateUploadedAttachment(value: AttachmentMetadata): AttachmentMetadata {
  if (!value?.id || !['image', 'audio', 'document'].includes(value.media_kind) || !value.filename || !Number.isFinite(value.size_bytes)) throw new Error('服务器未返回有效的附件信息');
  // Keep only controlled metadata. No raw response, paths, URLs or credentials enter message state.
  return { id: value.id, filename: value.filename, mime: value.mime, size_bytes: value.size_bytes, media_kind: value.media_kind, ...(value.sha256 ? { sha256: value.sha256 } : {}) };
}
