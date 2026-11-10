'use client';

import {useEffect, useState} from 'react';
import {Button, Modal, Radio, Select, Space, Tag} from 'antd';
import {request} from '@/lib/api';
import {formatError, type FormattedError} from '@/lib/errors';
import {ErrorBanner} from './shared';

export type Visibility = 'public' | 'partial' | 'private';
export type Audience = {
  visibility: Visibility;
  user_ids: string[];
  role_ids: string[];
  expected_revision: number;
};
export type Publication = {
  status: 'draft' | 'published' | 'api_published';
  visibility: Visibility | 'legacy';
  revision: number;
  version?: number;
  user_ids?: string[];
  role_ids?: string[];
};

const labels = {public: '空间公开', partial: '部分公开', private: '私有'};

export function PublicationBadges({value}: {value?: Publication}) {
  const published = value?.status === 'published';
  const state = published ? (value.version ? `已发布 v${value.version}` : '已发布') : value?.status === 'api_published' ? 'API 已发布' : '未发布';
  const visibility = value?.visibility;
  const scope = !visibility || visibility === 'legacy' ? (value?.status === 'draft' || !value ? '未设置公开范围' : '沿用原授权') : labels[visibility];
  return <Space size={2} wrap><Tag color={published ? 'green' : undefined}>{state}</Tag><Tag color={visibility === 'public' ? 'blue' : visibility === 'partial' ? 'purple' : undefined}>{scope}</Tag></Space>;
}

type Directory = {users: {id:string; name:string}[]; roles: {id:string; name:string}[]};

export function PublicationDialog({kind, value, onClose, onPublish}: {
  kind: 'agent' | 'workflow';
  value?: Publication;
  onClose: () => void;
  onPublish: (audience: Audience) => Promise<boolean | void>;
}) {
  const [visibility, setVisibility] = useState<Visibility>(value?.visibility && value.visibility !== 'legacy' ? value.visibility : 'private');
  const [userIds, setUserIds] = useState(value?.user_ids || []);
  const [roleIds, setRoleIds] = useState(value?.role_ids || []);
  const [directory, setDirectory] = useState<Directory>();
  const [error, setError] = useState<FormattedError>();
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (visibility !== 'partial') return;
    const controller = new AbortController();
    setLoading(true);
    setError(undefined);
    void request<Directory>('/agents/publication-audience', {signal:controller.signal}).then(data => {
      if (!controller.signal.aborted) setDirectory(data);
    }).catch(error => {
      if (!controller.signal.aborted) setError(formatError(error));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [visibility, attempt]);
  const partial = visibility === 'partial';
  const submit = async () => {
    setBusy(true);
    setError(undefined);
    try {
      if (await onPublish({visibility, user_ids: partial ? userIds : [], role_ids: partial ? roleIds : [],
        expected_revision: value?.revision || 0}) !== false) onClose();
    } catch (error) {
      setError(formatError(error));
    } finally {
      setBusy(false);
    }
  };
  return <Modal open title={kind === 'agent' ? '发布智能体' : '发布工作流'} centered okText="确认发布" cancelText="取消"
    confirmLoading={busy} cancelButtonProps={{disabled:busy}} closable={!busy} keyboard={!busy} maskClosable={false}
    onCancel={onClose} onOk={submit} okButtonProps={{disabled:partial && (loading || !directory || !(userIds.length || roleIds.length))}}>
    <p>发布会保存当前版本。工作台只运行已发布内容。</p>
    <Radio.Group aria-label="公开范围" value={visibility} disabled={busy} onChange={event => setVisibility(event.target.value)}>
      <Space direction="vertical">
        <Radio value="public">空间公开 · 当前空间成员可使用</Radio>
        <Radio value="partial">部分公开 · 指定成员或角色可使用</Radio>
        <Radio value="private">私有 · 仅创建者可使用</Radio>
      </Space>
    </Radio.Group>
    {partial && <Space direction="vertical" style={{width:'100%', marginTop:16}}>
      <Select mode="multiple" aria-label="选择空间成员" placeholder="选择成员" value={userIds} onChange={setUserIds}
        optionFilterProp="label" loading={loading} disabled={busy || !directory} style={{width:'100%'}}
        options={directory?.users.map(user => ({value:user.id, label:user.name}))} />
      <Select mode="multiple" aria-label="选择空间角色" placeholder="选择角色" value={roleIds} onChange={setRoleIds}
        optionFilterProp="label" loading={loading} disabled={busy || !directory} style={{width:'100%'}}
        options={directory?.roles.map(role => ({value:role.id, label:role.name}))} />
      {!loading && !directory && <Button onClick={() => setAttempt(value => value + 1)}>重新读取成员和角色</Button>}
    </Space>}
    <ErrorBanner error={error || ''}/>
  </Modal>;
}
