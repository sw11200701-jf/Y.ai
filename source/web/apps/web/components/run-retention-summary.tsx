"use client";
import { useRef, useState } from 'react';
import { Button, Modal } from 'antd';
import { currentIdentityEpoch, request, time } from '@/lib/api';
import { formatError, type FormattedError } from '@/lib/errors';
import { ErrorBanner } from './shared';

type Summary = { retention_days: number; cutoff: string; completed_over_age: number; protected_over_age: number };

export default function RunRetentionSummary() {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState<Summary>();
  const [error, setError] = useState<FormattedError>();
  const generation = useRef(0);

  async function show() {
    const ticket = ++generation.current, epoch = currentIdentityEpoch();
    const current = () => ticket === generation.current && epoch === currentIdentityEpoch();
    setOpen(true);
    setLoading(true);
    setSummary(undefined);
    setError(undefined);
    try {
      const value = await request<Summary>('/runs/retention-summary');
      if (current()) setSummary(value);
    } catch (cause) {
      if (current()) setError(formatError(cause));
    } finally {
      if (current()) setLoading(false);
    }
  }

  return <>
    <Button onClick={()=>void show()}>保留期</Button>
    <Modal title="我的运行记录保留期" open={open} footer={null} onCancel={()=>{generation.current++;setOpen(false);}}>
      <ErrorBanner error={error}/>
      {loading&&<p role="status">正在检查记录…</p>}
      {summary&&<>
        <p>检查期限 {summary.retention_days} 天。截止时间 {time(summary.cutoff)}。</p>
        <p>超过期限的已结束记录 {summary.completed_over_age} 条。</p>
        <p>超过期限但仍需保全的记录 {summary.protected_over_age} 条，包括待核查调用及未结束的任务。</p>
        <p className="hint">保留期用于清点历史记录。运行结果、来源和产物继续保存在现有记录中。</p>
      </>}
    </Modal>
  </>;
}
