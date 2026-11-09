'use client';
import { useState } from 'react';
import { Alert, Button, Input, Select, Space } from 'antd';
import type { ApplicationCreateBody, ApplicationDto } from '@/lib/application-api-client';
import styles from './application-api-view.module.css';

export default function ApplicationApiBasicForm({ initial, defaultKind, busy, onSubmit, onCancel, onDirtyChange }: { initial?: ApplicationDto; defaultKind?:'agent'|'workflow'; busy: boolean; onSubmit: (body: ApplicationCreateBody & {status?:'active'|'disabled'}) => void; onCancel: () => void; onDirtyChange?:()=>void }) {
  const [name, setName] = useState(initial?.name || '');
  const [description, setDescription] = useState(initial?.description || '');
  const [kind, setKind] = useState<'agent' | 'workflow'>(initial?.type || defaultKind || 'agent');
  const [error, setError] = useState('');
  const [status,setStatus]=useState<'active'|'disabled'>(initial?.status==='disabled'?'disabled':'active');
  function submit() {
    if (!name.trim()) { setError('请填写应用名称。'); return; }
    onSubmit({ name: name.trim(), description, type: kind, ...(initial?{status}:{}) });
  }
  return <div className={styles.form}>
    {error && <Alert type="error" message={error} />}
    <label className={styles.field}>应用名称<Input aria-label="应用名称" value={name} disabled={busy} maxLength={100} onChange={event => {onDirtyChange?.();setName(event.target.value);}} /></label>
    <label className={styles.field}>应用类型<Select aria-label="应用类型" value={kind} disabled={busy || !!initial} options={[{ value: 'agent', label: '智能体' }, { value: 'workflow', label: '工作流' }]} onChange={value=>{onDirtyChange?.();setKind(value);}} /></label>
    <label className={styles.field}>用途说明<Input.TextArea aria-label="应用用途说明" value={description} disabled={busy} maxLength={2000} rows={3} onChange={event => {onDirtyChange?.();setDescription(event.target.value);}} /></label>
    {initial&&<label className={styles.field}>应用状态<Select aria-label="应用状态" value={status} disabled={busy} options={[{value:'active',label:'启用'},{value:'disabled',label:'停用'}]} onChange={value=>{onDirtyChange?.();setStatus(value);}}/><small className={styles.muted}>保存停用将立即阻止应用 Key 调用；现有调用按服务端执行账本处理。</small></label>}
    <Space><Button disabled={busy} onClick={onCancel}>取消</Button><Button type="primary" loading={busy} onClick={submit}>保存应用</Button></Space>
  </div>;
}
