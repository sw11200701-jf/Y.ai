"use client";
import { useRef, useState } from 'react';
import { Button, List, Modal, Popconfirm } from 'antd';
import { currentIdentityEpoch, request, send, time } from '@/lib/api';
import { formatError, type FormattedError } from '@/lib/errors';
import { ErrorBanner } from './shared';

type AssetKind = 'workflow' | 'agent';
type ArchivedAsset = { id: string; kind: AssetKind; name: string; archived_at: string };
const base = '/agents/recycle-bin';
const resourceLabel = (kind: AssetKind) => kind === 'workflow' ? '工作流' : '智能体';

function change(kind: AssetKind, id: string, action: 'archive' | 'restore') {
  return send(base + '/' + kind + '/' + encodeURIComponent(id) + '/' + action);
}

export function ArchiveAssetButton({ kind, id, onArchive, disabled }: {
  kind: AssetKind; id: string; onArchive: () => Promise<void>; disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<FormattedError>();
  const pending = useRef(false);
  async function archive() {
    if (pending.current || disabled) return;
    const epoch = currentIdentityEpoch();
    pending.current = true;
    setBusy(true);
    setError(undefined);
    try {
      await change(kind, id, 'archive');
      if (epoch === currentIdentityEpoch()) await onArchive();
    } catch (cause) {
      if (epoch === currentIdentityEpoch()) setError(formatError(cause));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return <div className="asset-archive-actions">
    <Popconfirm title={'将此' + resourceLabel(kind) + '移入回收站？'} okText="移入回收站" cancelText="取消" onConfirm={archive}>
      <Button size="small" disabled={disabled} loading={busy}>移入回收站</Button>
    </Popconfirm>
    <ErrorBanner error={error}/>
  </div>;
}

export function AssetRecycleBin({ onRestore }: { onRestore: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState('');
  const [items, setItems] = useState<ArchivedAsset[]>([]);
  const [error, setError] = useState<FormattedError>();
  const requestId = useRef(0);
  const pending = useRef(false);
  async function run(key: string, item?: ArchivedAsset) {
    if (pending.current) return;
    const ticket = ++requestId.current, epoch = currentIdentityEpoch();
    const current = () => ticket === requestId.current && epoch === currentIdentityEpoch();
    pending.current = true;
    setBusy(key);
    setError(undefined);
    try {
      if (item) await change(item.kind, item.id, 'restore');
      const result = await request<{ items: ArchivedAsset[] }>(base);
      if (current()) {
        setItems(result.items);
        if (item) await onRestore();
      }
    } catch (cause) {
      if (current()) setError(formatError(cause));
    } finally {
      pending.current = false;
      setBusy('');
    }
  }
  return <>
    <Button onClick={() => { setItems([]); setOpen(true); void run('load'); }}>回收站</Button>
    <Modal title="工作流与智能体回收站" open={open} footer={null} closable={!busy} maskClosable={!busy} keyboard={!busy}
      onCancel={() => { requestId.current++; setItems([]); setError(undefined); setOpen(false); }}>
      <ErrorBanner error={error}/>
      <List dataSource={items} loading={busy==='load'} locale={{emptyText:'暂无可恢复的工作流或智能体'}}
        renderItem={item => <List.Item key={item.kind+item.id} actions={[
          <Button key="restore" disabled={Boolean(busy)} loading={busy===item.id} onClick={()=>void run(item.id,item)}>恢复</Button>,
        ]}><List.Item.Meta title={item.name} description={resourceLabel(item.kind)+' · '+time(item.archived_at)}/></List.Item>}/>
    </Modal>
  </>;
}
