'use client';
import { Alert, Collapse, Tag } from 'antd';
import { formatError, type FormattedError } from '@/lib/errors';
import ArtifactFileCards from './artifact-file-cards';
export const statusNames: Record<string, string> = { queued: '等待执行', running: '执行中', succeeded: '已完成', failed: '执行失败', cancelled: '已取消', interrupted: '已中断', published: '已发布', draft: '草稿', valid: '校验通过', validated: '校验通过', invalid: '校验失败', preview: '待发布', committed: '已发布' };
export function Status({ value }: { value: string }) { return <Tag color={['succeeded', 'published', 'committed', 'valid', 'validated'].includes(value) ? 'success' : ['failed', 'invalid', 'interrupted'].includes(value) ? 'error' : ['running', 'queued'].includes(value) ? 'processing' : 'default'}>{statusNames[value] || value}</Tag>; }
export function ErrorBanner({ error }: { error?: string | FormattedError }) { const value=formatError(error);return value.message ? <Alert className="error-banner" type="error" showIcon message="操作未完成" description={<>{value.message}{value.code&&<Collapse ghost size="small" items={[{key:'technical',label:'技术详情',children:<div>错误码：{value.code}{value.request_id&&<div>请求编号：{value.request_id}</div>}</div>}]} />}</>} /> : null; }
export function JsonView({ value }: { value: unknown }) { return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>; }
export function Artifacts({items,onError,runId}:{items:unknown[];onError:(message:string)=>void;runId?:string}){return <ArtifactFileCards items={items} runId={runId} onError={onError}/>;}
export function PageTitle({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) { return <div className="page-title"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>{action}</div>; }
