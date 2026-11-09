'use client';
import { useEffect, useRef, useState } from 'react';
import { workflowApi } from '@/lib/workflow/api';
import { emptyMessage, localFileError, messageError, messageKinds, validateUploadedAttachment } from '@/lib/workflow/messages';
import type { MessageDraft } from '@/lib/workflow/types';
import { errorMessage } from '@/lib/errors';
import styles from './studio.module.css';
export type MessageComposerProps = { value: MessageDraft; onChange: (value: MessageDraft) => void; disabled?: boolean; identityKey?: string; onUploadingChange?: (uploading: boolean) => void; optional?: boolean };
export function MessageComposer({ value: providedValue, onChange, disabled, identityKey, onUploadingChange, optional=false }: MessageComposerProps) {
  const [uploading, setUploading] = useState(false); const [error, setError] = useState('');
  const identity = useRef(identityKey); const value = identity.current === identityKey ? providedValue : emptyMessage();
  const uploadEpoch = useRef(0); const mounted = useRef(true); const latest = useRef(value); latest.current = value;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; uploadEpoch.current++; }; }, []);
  useEffect(() => { uploadEpoch.current++; setUploading(false); setError(''); if (identity.current !== identityKey) { identity.current = identityKey; onChange(emptyMessage()); onUploadingChange?.(false); } }, [identityKey]);
  const current = messageKinds.find(k => k.type === value.type)!;
  const optionalEmpty=optional&&value.type==='text'&&!value.text.trim()&&value.attachments.length===0;
  return <section className={styles.messageComposer} aria-label="测试消息">
    <div className={styles.messageKinds}>{messageKinds.map(kind => <button type="button" key={kind.type} aria-pressed={value.type === kind.type} disabled={disabled || uploading} onClick={() => { uploadEpoch.current++; setError(''); onChange({ type: kind.type, text: value.text, attachments: [] }); }}><span>{kind.glyph}</span>{kind.label}</button>)}</div>
    <label className={styles.field}><span>{value.type === 'text' || value.type === 'mixed' ? (optional&&value.type==='text'?'消息内容（可选）':'消息内容（必填）') : '补充说明（可选）'}</span><textarea aria-label="消息内容" value={value.text} disabled={disabled || uploading} rows={4} placeholder="输入本次测试消息" onChange={e => onChange({ ...value, text: e.target.value })} /></label>
    {value.type !== 'text' && <label className={styles.uploadControl}><span>{uploading ? '正在上传并校验…' : '选择附件并上传'}</span><input type="file" aria-label="消息附件" accept={current.accept} multiple={value.type !== 'audio'} disabled={disabled || uploading} onChange={async event => {
      const selected = Array.from(event.target.files || []); event.target.value = ''; if (!selected.length) return;
      const max = value.type === 'audio' ? 1 : 8;
      if (value.attachments.length + selected.length > max) { setError(`此消息最多允许${max}个附件，请先移除多余项`); return; }
      const invalid = selected.map(file => localFileError(file, value.type)).find(Boolean); if (invalid) { setError(invalid); return; }
      const token = ++uploadEpoch.current; setUploading(true); onUploadingChange?.(true); setError(''); const accepted = [...value.attachments];
      try { for (const file of selected) { const metadata = validateUploadedAttachment(await workflowApi.uploadAttachment(file)); if (!mounted.current || token !== uploadEpoch.current) return; if (!accepted.some(a => a.id === metadata.id)) accepted.push(metadata); onChange({ ...latest.current, attachments: accepted.slice() }); } }
      catch (e) { if (mounted.current && token === uploadEpoch.current) setError(errorMessage(e)); }
      finally { if (mounted.current && token === uploadEpoch.current) { setUploading(false); onUploadingChange?.(false); } }
    }} /><small>每个附件≤10MiB；仅使用当前账号有权访问的上传附件。服务器复核类型与内容。</small></label>}
    <ul className={styles.attachments}>{value.attachments.map(a => <li key={a.id}><div><strong>{a.filename}</strong><small>{a.media_kind} · {Math.ceil(a.size_bytes / 1024)} KiB</small></div><button type="button" aria-label={`移除附件 ${a.filename}`} disabled={disabled || uploading} onClick={() => onChange({ ...value, attachments: value.attachments.filter(x => x.id !== a.id) })}>移除</button></li>)}</ul>
    {['image', 'audio', 'mixed'].includes(value.type) && <p className={styles.hint}>上传和触发不会自动识别图片或语音。后续节点需要已配置的视觉或语音能力，缺失时会明确失败。</p>}
    {error && <p role="alert" className={styles.errorText}>{error}</p>}{optionalEmpty ? <p className={styles.hint}>留空时使用任务描述和业务输入。</p> : messageError(value) && <p className={styles.hint}>{messageError(value)}</p>}
  </section>;
}
export default MessageComposer;
