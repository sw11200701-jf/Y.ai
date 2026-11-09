'use client';
import { useState } from 'react';
import type { ApprovalDecision, RunApproval } from '@/lib/workflow/types';
import styles from './studio.module.css';

const statusLabels: Record<string, string> = { pending: '待审批', approved: '已同意', rejected: '已拒绝', expired: '已过期', cancelled: '已取消' };
function ApprovalForm({ item, canDecide, disabled, onDecision }: { item: RunApproval; canDecide: boolean; disabled: boolean; onDecision: (id: string, decision: ApprovalDecision, note: string) => void }) {
  const [note, setNote] = useState('');
  return <section className={styles.fieldList}><strong>{item.title}</strong><p className={styles.hint}>{statusLabels[item.status] || '状态待更新'}{item.expires_at && ` · 有效至 ${new Date(item.expires_at).toLocaleString('zh-CN')}`}</p>{item.note && <p className={styles.hint}>处理说明：{item.note}</p>}{canDecide && item.status === 'pending' && <fieldset disabled={disabled} className={styles.queryForm}><label className={styles.field}><span>处理说明（选填，最多500字）</span><textarea aria-label={`审批说明：${item.title}`} rows={2} value={note} maxLength={500} onChange={event => setNote(event.target.value)} /></label><div className={styles.linkedActions}><button type="button" className={styles.primary} onClick={() => onDecision(item.id, 'approve', note.trim())}>同意</button><button type="button" onClick={() => onDecision(item.id, 'reject', note.trim())}>拒绝</button></div></fieldset>}</section>;
}

/** Safe static request only; never receive a run pool or business row preview here. */
export function RunApprovals({ items, decidableIds, disabled = false, onDecision }: { items: RunApproval[]; decidableIds: string[]; disabled?: boolean; onDecision: (id: string, decision: ApprovalDecision, note: string) => void }) {
  if (!items.length) return null;
  return <section className={styles.approvalPanel} aria-label="运行审批"><h3>审批事项</h3>{items.map(item => <ApprovalForm key={`${item.run_id}:${item.id}`} item={item} canDecide={decidableIds.includes(item.id)} disabled={disabled} onDecision={onDecision} />)}</section>;
}
