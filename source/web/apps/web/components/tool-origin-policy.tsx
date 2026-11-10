"use client";
import { useRef, useState } from 'react';
import { Button, Checkbox, Empty, Modal, Space } from 'antd';
import { currentIdentityEpoch, request, send } from '@/lib/api';
import { formatError, type FormattedError } from '@/lib/errors';
import { ErrorBanner } from './shared';

type OriginPolicy = { deployment_origins: string[]; origins: string[]; revision: number };
const path = '/connections/origin-policy';

export default function ToolOriginPolicy() {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [policy, setPolicy] = useState<OriginPolicy>();
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<FormattedError>();
  const pending = useRef(false);

  async function readOrSave(save = false) {
    if (pending.current || save && !policy) return;
    const epoch = currentIdentityEpoch();
    pending.current = true;
    setBusy(true);
    setError(undefined);
    try {
      const value = save && policy ? await send<OriginPolicy>(path, { origins: selected, expected_revision: policy.revision }, 'PUT')
        : await request<OriginPolicy>(path);
      if (epoch === currentIdentityEpoch()) {
        setPolicy(value);
        setSelected(value.origins);
      }
    } catch (cause) {
      if (epoch === currentIdentityEpoch()) setError(formatError(cause));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  const changed = Boolean(policy && JSON.stringify([...selected].sort()) !== JSON.stringify(policy.origins));
  return <>
    <Button onClick={() => { setOpen(true); setPolicy(undefined); setSelected([]); void readOrSave(); }}>允许的服务地址</Button>
    <Modal title="当前空间的服务地址白名单" open={open} onCancel={()=>setOpen(false)}
      maskClosable={!busy} closable={!busy} keyboard={!busy}
      footer={<Space><Button disabled={busy} onClick={()=>void readOrSave()}>刷新</Button><Button disabled={busy} onClick={()=>setOpen(false)}>关闭</Button>
        <Button type="primary" loading={busy} disabled={!changed} onClick={()=>void readOrSave(true)}>保存</Button></Space>}>
      <p>可启用部署允许范围内的地址。保存后，新建连接和后续工具调用按最新清单校验。</p>
      <ErrorBanner error={error}/>
      {policy && (policy.deployment_origins.length ?
        <Checkbox.Group aria-label="允许的服务源地址" style={{ display: 'grid', gap: 8 }} disabled={busy}
          options={policy.deployment_origins} value={selected} onChange={setSelected}/>
        : <Empty description="部署尚未允许任何工具服务地址"/>)}
    </Modal>
  </>;
}
