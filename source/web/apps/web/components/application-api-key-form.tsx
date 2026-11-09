'use client';
import { useState } from 'react';
import { Alert, Button, Checkbox, Input, InputNumber, Space } from 'antd';
import { ApplicationApiKeyFormValue, ApplicationApiKeyPolicyView, validateApplicationKeyForm } from '@/lib/application-api-presentation';
import styles from './application-api-view.module.css';

/** The host mounts a new form when app/client/identity/policy changes. */
export default function ApplicationApiKeyForm({ policy, onSubmit, onCancel, onDirtyChange, submitLabel = '核对并创建 Key' }: {
  policy: ApplicationApiKeyPolicyView;
  onSubmit: (value: ApplicationApiKeyFormValue) => void;
  onCancel: () => void;
  submitLabel?: string;
  onDirtyChange?:()=>void;
}) {
  const [value, setValue] = useState<ApplicationApiKeyFormValue>(() => ({ name: '', expiresAt: policy.defaultExpiresAt, scopes: policy.defaultScopes.filter(scope => policy.scopes.some(option => option.value === scope)), quotas: { ...policy.defaults }, resourceConstraints: { resources: (policy.initialResourceConstraints?.resources||policy.requiredResources||[]).map(resource=>({...resource,actions:[...resource.actions]})), allowed_entities: [...(policy.initialResourceConstraints?.allowed_entities||[])] } }));
  const [error, setError] = useState('');
  const [invalidExpiry, setInvalidExpiry] = useState(false);
  function changeValue(change:(old:ApplicationApiKeyFormValue)=>ApplicationApiKeyFormValue){onDirtyChange?.();setValue(change);}
  function submit() {
    const error = invalidExpiry ? '请填写有效的到期时间。' : validateApplicationKeyForm(value, policy);
    setError(error || ''); if (error) return;
    onSubmit({ ...value, name: value.name.trim(), scopes: [...value.scopes], quotas: { ...value.quotas } });
  }
  function limit(key: keyof ApplicationApiKeyFormValue['quotas'], label: string) {
    const maximum = policy.maximum[key]; if (maximum === undefined) return null;
    return <label className={styles.field}>{label}<InputNumber aria-label={label} min={1} max={maximum} precision={0} value={value.quotas[key]} onChange={amount => changeValue(old => ({ ...old, quotas: { ...old.quotas, [key]: amount ?? 0 } }))} /><small className={styles.muted}>调用方允许上限：{maximum}</small></label>;
  }
  return <div className={styles.form}>
    {error && <Alert type="error" message={error} />}
    <label className={styles.field}>名称与用途<Input aria-label="Key 名称与用途" maxLength={100} value={value.name} placeholder="例如：财务平台的月报集成" onChange={event => changeValue(old => ({ ...old, name: event.target.value }))} /></label>
    <label className={styles.field}>到期时间<input aria-label="Key 到期时间" type="datetime-local" defaultValue={policy.defaultExpiresAt ? localDateTime(policy.defaultExpiresAt) : ''} onChange={event => {
      const date = event.target.value ? new Date(event.target.value) : null;
      const invalid = !!date && !Number.isFinite(date.getTime()); setInvalidExpiry(invalid);
      changeValue(old => ({ ...old, expiresAt: invalid || !date ? null : date.toISOString() }));
    }} /><small className={styles.muted}>按当前设备时区填写；留空表示不额外缩短调用方的有效期。</small></label>
    <section className={styles.scopes}><strong>允许的功能</strong>{policy.scopes.map(scope => <Checkbox key={scope.value} checked={value.scopes.includes(scope.value)} onChange={event => changeValue(old => ({ ...old, scopes: event.target.checked ? [...old.scopes, scope.value] : old.scopes.filter(item => item !== scope.value) }))}>{scope.label}{scope.description && <small className={styles.muted}> · {scope.description}</small>}</Checkbox>)}{!policy.scopes.length && <p>当前调用方没有可授予的功能，请先调整调用方配置。</p>}</section>
    <section className={styles.scopes}><strong>数据与资源范围</strong><p className={styles.muted}>仅能在调用方与发布版本允许的范围内收紧，不能借外部用户标识扩大内部权限。未选择表示没有对应数据授权。</p>
      {policy.resourceConstraints?.resources.map((resource, index) => <section key={`${resource.kind}:${resource.resource_id}:${resource.version_id || ''}`}><p>{policy.resourceSummary[index] || `${resource.kind} · ${resource.resource_id}`}</p><Space wrap>{resource.actions.map(action => <Checkbox key={action} checked={!!value.resourceConstraints?.resources.some(selected => selected.kind === resource.kind && selected.resource_id === resource.resource_id && selected.version_id === resource.version_id && selected.actions.includes(action))} onChange={event => changeValue(old => {
        const all = old.resourceConstraints?.resources || [], match = all.find(selected => selected.kind === resource.kind && selected.resource_id === resource.resource_id && selected.version_id === resource.version_id);
        const actions = event.target.checked ? [...new Set([...(match?.actions || []), action])] : (match?.actions || []).filter(item => item !== action);
        const resources = all.filter(selected => selected !== match); if (actions.length) resources.push({ ...resource, actions });
        return { ...old, resourceConstraints: { allowed_entities: old.resourceConstraints?.allowed_entities || [], resources } };
      })}>{{ use: '使用', read: '读取', download_original: '下载原文件', export: '导出' }[action]}</Checkbox>)}</Space></section>)}
      {policy.resourceConstraints?.allowed_entities.map(entity => <Checkbox key={entity} checked={value.resourceConstraints?.allowed_entities.includes(entity)} onChange={event => changeValue(old => ({ ...old, resourceConstraints: { resources: old.resourceConstraints?.resources || [], allowed_entities: event.target.checked ? [...(old.resourceConstraints?.allowed_entities || []), entity] : (old.resourceConstraints?.allowed_entities || []).filter(item => item !== entity) } }))}>主体：{entity}</Checkbox>)}
      {!policy.resourceConstraints?.resources.length && !policy.resourceConstraints?.allowed_entities.length && <p>未授予数据与资源范围。</p>}
    </section>
    <details><summary>高级限额</summary><p className={styles.muted}>默认值来自调用方配置；应用、调用方与 Key 同时限制时取最严格值。</p><div className={styles.limits}>{limit('requestsPerMinute', '每分钟请求数')}{limit('concurrency', '同时运行数')}{limit('dailyCalls', '每日新调用数')}{limit('tokenBudget', '令牌预算')}</div></details>
    <Space><Button onClick={onCancel}>取消</Button><Button type="primary" disabled={!policy.scopes.length} onClick={submit}>{submitLabel}</Button></Space>
  </div>;
}
function localDateTime(value: string) {
  const date = new Date(value); if (!Number.isFinite(date.getTime())) return '';
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}T${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}
