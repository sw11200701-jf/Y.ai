'use client';
import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, InputNumber, Select, Space, Table } from 'antd';
import { currentIdentityEpoch } from '@/lib/api';
import type { ApplicationPreviewBody, ApplicationRuntimePolicy, ApplicationSource } from '@/lib/application-api-client';
import styles from './application-api-view.module.css';

export type ApplicationPublishSourceOption = { id: string; kind: 'agent'|'workflow'; name: string; versions: {id:string;label:string}[] };
export type ApplicationReleasePreview = { sourceSha256:string; canPublish:boolean; blockers:string[]; dependencies:{name:string;statusLabel:string}[] };
const initialPolicy:ApplicationRuntimePolicy={timeout_seconds:120,token_reservation:8192,expose_trace:false,allow_resume:false,max_file_bytes:10485760,allowed_file_types:[],max_streams:2,stream_lease_seconds:30};
export default function ApplicationApiPublishForm({ kind, sources, preselectedId, busy, onPreview, onPublish, onCancel, onDirtyChange }: {
  kind:'agent'|'workflow'; sources:ApplicationPublishSourceOption[]; preselectedId?:string; busy:boolean;
  onPreview:(body:ApplicationPreviewBody)=>Promise<ApplicationReleasePreview>;
  onPublish:(body:ApplicationPreviewBody&{expected_source_sha256:string})=>void;
  onCancel:()=>void;
  onDirtyChange?:()=>void;
}) {
  const [sourceId,setSourceId]=useState(sources.some(source=>source.id===preselectedId&&source.kind===kind)?preselectedId:undefined);
  const [versionId,setVersionId]=useState<string>();
  const [policy,setPolicy]=useState(initialPolicy);
  const [preview,setPreview]=useState<ApplicationReleasePreview>();
  const [error,setError]=useState('');
  const [checking,setChecking]=useState(false);
  const generation=useRef(0);
  useEffect(()=>()=>{generation.current++;},[]);
  const source=sources.find(item=>item.id===sourceId&&item.kind===kind);
  function changePolicy(patch:Partial<ApplicationRuntimePolicy>){onDirtyChange?.();generation.current++;setPreview(undefined);setPolicy(old=>({...old,...patch}));}
  function body():ApplicationPreviewBody|undefined {
    if(!source||(kind==='agent'&&!source.versions.some(version=>version.id===versionId))){setError(kind==='agent'?'请选择智能体及其已发布版本。':'请选择一个已保存的工作流。');return;}
    const selected:ApplicationSource={kind,id:source.id,...(kind==='agent'?{version_id:versionId}:{})};
    return {source:selected,runtime_policy:{...policy,allowed_file_types:[...policy.allowed_file_types]}};
  }
  async function check(){
    if(checking||busy)return;const value=body();if(!value)return;
    const ticket=++generation.current,epoch=currentIdentityEpoch();setChecking(true);setError('');setPreview(undefined);
    try{const result=await onPreview(value);if(ticket===generation.current&&epoch===currentIdentityEpoch())setPreview(result);}
    catch{if(ticket===generation.current&&epoch===currentIdentityEpoch())setError('发布检查未完成，请核对源与当前资源权限后重试。');}
    finally{if(ticket===generation.current)setChecking(false);}
  }
  function publish(){const value=body();if(value&&preview?.canPublish&&/^[a-f0-9]{64}$/.test(preview.sourceSha256))onPublish({...value,expected_source_sha256:preview.sourceSha256});}
  const disabled=busy||checking;
  const number=(key:'timeout_seconds'|'token_reservation'|'max_file_bytes'|'max_streams'|'stream_lease_seconds',label:string,min:number,max:number)=><label className={styles.field}>{label}<InputNumber aria-label={label} min={min} max={max} precision={0} value={policy[key]} disabled={disabled} onChange={value=>changePolicy({[key]:value??min})}/></label>;
  return <div className={styles.form}>
    <p className={styles.muted}>先检查，再明确发布。此操作冻结保存源与依赖，不调用模型。发布后的源草稿变化不会修改该应用版本。</p>
    {error&&<Alert type="error" message={error}/>}
    <label className={styles.field}>选择{kind==='agent'?'智能体':'工作流'}<Select aria-label="API 发布源" value={sourceId} disabled={disabled} options={sources.filter(item=>item.kind===kind).map(item=>({value:item.id,label:item.name}))} placeholder="选择当前有权访问的保存源" onChange={id=>{onDirtyChange?.();generation.current++;setSourceId(id);setVersionId(undefined);setPreview(undefined);setError('');}}/></label>
    {kind==='agent'&&<label className={styles.field}>已发布智能体版本<Select aria-label="已发布智能体版本" value={versionId} disabled={disabled||!source} options={source?.versions.map(version=>({value:version.id,label:version.label}))||[]} onChange={value=>{onDirtyChange?.();generation.current++;setVersionId(value);setPreview(undefined);}} placeholder="选择固定版本"/>{source&&!source.versions.length&&<small>当前智能体尚无可用发布版本。请先回 AI 工作室发布，再刷新选择。</small>}</label>}
    <details><summary>运行与文件策略</summary><div className={styles.limits}>{number('timeout_seconds','最长运行秒数',1,600)}{number('token_reservation','每次调用令牌预留',1,1000000)}{number('max_file_bytes','单文件字节上限',1,10485760)}{number('max_streams','同时事件订阅数',1,10)}{number('stream_lease_seconds','订阅租约秒数',10,120)}</div><div className={styles.scopes}>
      <Checkbox disabled={disabled} checked={policy.expose_trace} onChange={event=>changePolicy({expose_trace:event.target.checked})}>允许授权调用方读取脱敏运行摘要</Checkbox>
      <Checkbox disabled={disabled} checked={policy.allow_resume} onChange={event=>changePolicy({allow_resume:event.target.checked})}>允许正常可恢复运行在重新核权与审批后继续</Checkbox>
      <p>允许上传的文件类型（默认关闭）</p><Space wrap>{(['text','image','audio','pdf','docx','xlsx','csv'] as const).map(type=><Checkbox key={type} disabled={disabled} checked={policy.allowed_file_types.includes(type)} onChange={event=>changePolicy({allowed_file_types:event.target.checked?[...policy.allowed_file_types,type]:policy.allowed_file_types.filter(item=>item!==type)})}>{{text:'文本',image:'图片',audio:'音频',pdf:'PDF',docx:'Word',xlsx:'Excel',csv:'CSV'}[type]}</Checkbox>)}</Space>
    </div></details>
    {preview&&<><Alert type={preview.canPublish?'success':'warning'} message={preview.canPublish?'检查完成，可确认冻结此版本':'当前不能发布'} description={preview.blockers.join('；')||'源与依赖已通过服务端检查。'}/><Table rowKey="name" pagination={false} size="small" dataSource={preview.dependencies} columns={[{title:'固定依赖',dataIndex:'name'},{title:'状态',dataIndex:'statusLabel'}]}/></>}
    <Space wrap><Button disabled={busy} onClick={()=>{generation.current++;onCancel();}}>取消</Button><Button disabled={disabled} loading={checking} onClick={()=>void check()}>检查发布条件</Button><Button type="primary" disabled={disabled||!preview?.canPublish} loading={busy} onClick={publish}>确认发布不可变版本</Button></Space>
  </div>;
}
