'use client';
import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Modal, Space } from 'antd';
import { currentIdentityEpoch } from '@/lib/api';

/** Presentation boundary only. The REST adapter must project the frozen receipt DTO. */
export type ApplicationKeyIssuance = {
  secret: string;
  name: string;
  prefix: string;
  expiresAt?: string | null;
  previousKeyExpiresAt?: string | null;
};
export type ApplicationKeyOperation = {
  action: 'create' | 'rotate';
  name: string;
  execute: () => Promise<ApplicationKeyIssuance>;
};

/** Mount for one operation only. Nothing outside this dialog receives the secret. */
export default function ApplicationApiKeyReceipt({ operation, identityKey, onClose, onIssued }: {
  operation: ApplicationKeyOperation;
  identityKey: string;
  onClose: () => void;
  onIssued: () => void;
}) {
  const [phase, setPhase] = useState<'ready' | 'busy' | 'issued' | 'uncertain'>('ready');
  const [metadata, setMetadata] = useState<Omit<ApplicationKeyIssuance, 'secret'>>();
  const [copyState, setCopyState] = useState('');
  const secret = useRef('');
  const serial = useRef(0);
  const alive = useRef(true);
  const started = useRef(false);
  const openedFor = useRef(identityKey);
  const openedEpoch = useRef(currentIdentityEpoch());
  const latest = useRef({ onClose, onIssued }); latest.current = { onClose, onIssued };

  function erase() { secret.current = ''; serial.current++; }
  function close() { erase(); setMetadata(undefined); setCopyState(''); latest.current.onClose(); }
  useEffect(() => {
    alive.current = true;
    const invalidate = () => { erase(); setMetadata(undefined); setCopyState(''); latest.current.onClose(); };
    const events = ['zhangpu:session-expired', 'zhangpu:permissions-changed', 'zhangpu:access-denied', 'zhangpu:data-policy-changed'];
    events.forEach(event => window.addEventListener(event, invalidate));
    return () => { alive.current = false; erase(); events.forEach(event => window.removeEventListener(event, invalidate)); };
  }, []);
  useEffect(() => { if (identityKey !== openedFor.current) close(); }, [identityKey]);
  const current = (ticket: number, epoch: number) => alive.current && ticket === serial.current && epoch === currentIdentityEpoch() && epoch === openedEpoch.current && identityKey === openedFor.current;

  async function issue() {
    if (started.current || currentIdentityEpoch() !== openedEpoch.current || identityKey !== openedFor.current) return;
    started.current = true;
    const ticket = ++serial.current, epoch = currentIdentityEpoch();
    setPhase('busy');
    try {
      const receipt = await operation.execute();
      if (!current(ticket, epoch)) return;
      if (typeof receipt.secret !== 'string' || !receipt.secret) throw Error('invalid-receipt');
      // Deliberately project fields: a raw receipt must never enter list state or callbacks.
      secret.current = receipt.secret;
      setMetadata({ name: receipt.name, prefix: receipt.prefix, expiresAt: receipt.expiresAt, previousKeyExpiresAt: receipt.previousKeyExpiresAt });
      setPhase('issued');
      latest.current.onIssued();
    } catch {
      if (current(ticket, epoch)) { secret.current = ''; setPhase('uncertain'); }
    }
  }
  async function copy() {
    if (!secret.current || phase !== 'issued' || currentIdentityEpoch() !== openedEpoch.current || identityKey !== openedFor.current) return;
    const ticket = serial.current, epoch = currentIdentityEpoch();
    try { await navigator.clipboard.writeText(secret.current); if (current(ticket, epoch)) setCopyState('已复制，请保存到调用方服务器的密钥配置。'); }
    catch { if (current(ticket, epoch)) setCopyState('复制未完成，请手动选中并复制当前 Key。'); }
  }
  if (identityKey !== openedFor.current || currentIdentityEpoch() !== openedEpoch.current) return null;
  return <Modal open title={operation.action === 'rotate' ? '轮换应用 API Key' : '创建应用 API Key'} centered width={600} zIndex={1200} styles={{body:{maxHeight:'calc(100vh - 200px)',overflowY:'auto'}}} maskClosable={false} destroyOnHidden onCancel={close} footer={<Space><Button onClick={close}>{phase === 'issued' ? '已安全保存，关闭' : '关闭'}</Button>{phase === 'ready' && <Button type="primary" onClick={() => void issue()}>确认{operation.action === 'rotate' ? '轮换' : '创建'}</Button>}</Space>}>
    <p>应用：{operation.name}</p>
    {phase === 'ready' && <p>Key 用于其他平台的服务器调用，与模型平台的供应商密钥分开管理。创建成功后仅展示这一次。</p>}
    {phase === 'busy' && <Alert type="info" message="正在签发，请稍候…" description="关闭将丢弃本次回执。若已签发，可在 Key 列表核对并撤销。" />}
    {phase === 'uncertain' && <Alert type="warning" message="签发结果尚未确认" description="请关闭并刷新 Key 列表核对。此处不会重复签发；需要时撤销未保存的 Key 后重新创建。" />}
    {phase === 'issued' && metadata && <>
      <Alert type="warning" message="完整 Key 仅展示这一次" description="请保存到调用方服务器。关闭后不能再次查看，请勿放入浏览器代码或网址。" />
      <p>{metadata.name} · {metadata.prefix}</p>
      <label>完整 API Key<Input.Password aria-label="完整 API Key（仅本次）" readOnly autoComplete="off" spellCheck={false} visibilityToggle={false} value={secret.current} /></label>
      <Button onClick={() => void copy()}>复制完整 Key</Button>
      {copyState && <p role="status">{copyState}</p>}
      <p>新 Key 到期时间：{metadata.expiresAt || '无到期时间'}</p>
      {operation.action === 'rotate' && <p>旧 Key 失效时间：{metadata.previousKeyExpiresAt || '服务端未返回，请在列表核对'}</p>}
    </>}
  </Modal>;
}
