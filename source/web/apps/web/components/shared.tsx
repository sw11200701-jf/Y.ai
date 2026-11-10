'use client';
import { Alert, Tag, Typography } from 'antd';
import { formatError, type FormattedError } from '@/lib/errors';
import ArtifactFileCards from './artifact-file-cards';
export const statusNames: Record<string, string> = { queued: '等待执行', running: '执行中', resuming: '正在恢复', paused: '已暂停', waiting_approval: '等待审批', outcome_unknown: '调用结果待核查', succeeded: '已完成', failed: '执行失败', cancelled: '已取消', interrupted: '已中断', published: '已发布', active: '使用中', deleted: '已移入回收站', disabled: '已停用', draft: '草稿', valid: '校验通过', validated: '校验通过', invalid: '校验失败', preview: '待发布', committed: '已发布' };
export function Status({ value }: { value: string }) { return <Tag color={['succeeded', 'published', 'committed', 'valid', 'validated'].includes(value) ? 'success' : ['failed', 'invalid', 'interrupted'].includes(value) ? 'error' : ['running', 'queued'].includes(value) ? 'processing' : 'default'}>{statusNames[value] || value}</Tag>; }
export function SupportReference({ value }: { value?: string }) {
  return value ? <div><Typography.Text type="secondary" copyable={{text:value}}>查询编号 {value}</Typography.Text></div> : null;
}
export function ErrorBanner({ error }: { error?: string | FormattedError }) {
  const value=formatError(error);
  return value.message ? <Alert className="error-banner" type="error" showIcon message="操作未完成"
    description={<>{value.message}<SupportReference value={value.request_id}/></>} /> : null;
}
export function JsonView({ value }: { value: unknown }) { return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>; }
export function Artifacts({items,onError,runId}:{items:unknown[];onError:(message:string)=>void;runId?:string}){return <ArtifactFileCards items={items} runId={runId} onError={onError}/>;}
export function PageTitle({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) { return <div className="page-title"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>{action}</div>; }
