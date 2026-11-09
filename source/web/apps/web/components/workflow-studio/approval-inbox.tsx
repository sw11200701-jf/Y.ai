'use client';
import { useEffect, useRef, useState } from 'react';
import { Modal } from 'antd';
import { currentIdentityEpoch } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { workflowApi } from '@/lib/workflow/api';
import type { ApprovalDecision, ApprovalInboxItem } from '@/lib/workflow/types';
import { RunApprovals } from './run-approvals';
import styles from './studio.module.css';

const epoch = () => typeof currentIdentityEpoch === 'function' ? currentIdentityEpoch() : 0;
/** Same-user inbox metadata only. Never load another executor's private run/state/trace. */
export function ApprovalInbox({ open, container, onClose, onDecided }: { open: boolean; container: () => HTMLElement; onClose: () => void; onDecided: (runId: string) => void }) {
  const [items, setItems] = useState<ApprovalInboxItem[]>([]); const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false);
  const ticket = useRef(0); const lock = useRef(false); const alive = useRef(true); const isOpen = useRef(open); isOpen.current = open;
  const current = (id: number, actor: number) => alive.current && isOpen.current && id === ticket.current && actor === epoch();
  const read = async (id: number, actor: number) => {
    const response = await workflowApi.approvalInbox(); if (!current(id, actor)) return;
    if (!Array.isArray(response.items) || response.items.some(item => typeof item.id !== 'string' || typeof item.run_id !== 'string' || typeof item.title !== 'string' || typeof item.can_approve !== 'boolean')) throw new Error('审批收件箱格式不完整，请重新读取');
    setItems(response.items);
  };
  const refresh = async () => {
    if (lock.current || !isOpen.current) return; lock.current = true; const id = ++ticket.current; const actor = epoch(); setBusy(true); setError(''); setItems([]);
    try { await read(id, actor); } catch (error) { if (current(id, actor)) setError(errorMessage(error)); }
    finally { if (current(id, actor)) { lock.current = false; setBusy(false); } }
  };
  useEffect(() => { alive.current = true; return () => { alive.current = false; ticket.current++; }; }, []);
  useEffect(() => {
    ticket.current++; lock.current = false; setItems([]); setError(''); setNotice(''); setBusy(false);
    if (open) void refresh();
  }, [open]);
  const close = () => { ticket.current++; lock.current = false; setItems([]); setError(''); setNotice(''); setBusy(false); onClose(); };
  const closeRef = useRef(close); closeRef.current = close;
  useEffect(() => { const expired = () => closeRef.current(); window.addEventListener('zhangpu:session-expired', expired); window.addEventListener('zhangpu:permissions-changed', expired); return () => { window.removeEventListener('zhangpu:session-expired', expired); window.removeEventListener('zhangpu:permissions-changed', expired); }; }, []);
  const decide = async (approvalId: string, decision: ApprovalDecision, note: string) => {
    const item = items.find(item => item.id === approvalId);
    if (lock.current || !item || !item.can_approve || item.status !== 'pending' || !['waiting_approval', 'paused'].includes(item.run_status)) return;
    lock.current = true; const id = ++ticket.current; const actor = epoch(); setBusy(true); setError(''); setNotice('');
    try {
      const result = await workflowApi.approvalDecision(item.run_id, item.id, decision, note);
      if (!current(id, actor)) return;
      if (result.id !== item.id || result.run_id !== item.run_id) throw new Error('审批记录标识不一致，请重新读取');
      setItems([]); setNotice('审批结果已记录。执行者需要在运行中确认是否继续，不会代执行者自动恢复。'); onDecided(item.run_id);
      await read(id, actor);
    } catch (error) { if (current(id, actor)) { setItems([]); setError(errorMessage(error)); } }
    finally { if (current(id, actor)) { lock.current = false; setBusy(false); } }
  };
  const runs = [...new Set(items.map(item => item.run_id))];
  return <Modal open={open} title="我的待审批事项" footer={null} getContainer={container} onCancel={close} width={720}><div className={styles.approvalInbox}><p className={styles.hint}>仅显示服务端为当前用户返回的审批事项。每项能否处理由当前权限、指定审批人、有效期与运行状态共同决定。</p><button disabled={busy} onClick={() => void refresh()}>{busy ? '正在读取或处理…' : '刷新审批事项'}</button>{error && <p role="alert" className={styles.errorText}>{error}</p>}{notice && <p role="status" className={styles.notice}>{notice}</p>}{runs.map(runId => <section key={runId}><p className={styles.hint}>运行标识：{runId}</p><RunApprovals items={items.filter(item => item.run_id === runId)} decidableIds={items.filter(item => item.run_id === runId && item.can_approve === true && item.status === 'pending' && ['waiting_approval', 'paused'].includes(item.run_status)).map(item => item.id)} disabled={busy} onDecision={(id, decision, note) => void decide(id, decision, note)} /></section>)}{!busy && !error && !items.length && <p className={styles.hint}>当前没有待审批事项。</p>}{items.length >= 100 && <p className={styles.hint}>当前服务最多返回100条，请处理后刷新查看其他事项。</p>}</div></Modal>;
}
