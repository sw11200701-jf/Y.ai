'use client';
import { useState } from 'react';
import { Alert, Select } from 'antd';
import type { ApplicationApiKeyFormValue, ApplicationApiKeyPolicyView } from '@/lib/application-api-presentation';
import ApplicationApiKeyForm from './application-api-key-form';
import styles from './application-api-view.module.css';

export type ApplicationClientVersionOption = {id:string;label:string;policy:ApplicationApiKeyPolicyView;externalUserModes:('client'|'required')[];canCreate:boolean;blockers:string[]};
export default function ApplicationApiClientForm({ versions, defaultVersionId, onSubmit, onCancel, onDirtyChange }: {
  versions:ApplicationClientVersionOption[];defaultVersionId?:string;
  onSubmit:(versionId:string,userMode:'client'|'required',value:ApplicationApiKeyFormValue)=>void;
  onCancel:()=>void;
  onDirtyChange?:()=>void;
}) {
  const [versionId,setVersionId]=useState(versions.some(version=>version.id===defaultVersionId)?defaultVersionId:versions[0]?.id);
  const [userMode,setUserMode]=useState<'client'|'required'>('required');
  const version=versions.find(item=>item.id===versionId);
  const selectedMode=version?.externalUserModes.includes(userMode)?userMode:version?.externalUserModes[0];
  return <div className={styles.form}>
    <label className={styles.field}>固定应用版本<Select aria-label="调用方固定版本" value={versionId} options={versions.map(item=>({value:item.id,label:item.label}))} onChange={value=>{onDirtyChange?.();setVersionId(value);}}/></label>
    <label className={styles.field}>外部用户分区<Select aria-label="外部用户分区策略" value={selectedMode} options={(version?.externalUserModes||[]).map(value=>({value,label:value==='required'?'每次必须指定外部用户':'默认使用调用方分区'}))} onChange={value=>{onDirtyChange?.();setUserMode(value);}}/><small className={styles.muted}>同一用户的运行、会话和文件在调用方与固定版本内隔离；外部标识不能赋予内部账号权限。</small></label>
    {version?version.canCreate&&selectedMode?<ApplicationApiKeyForm key={version.id} onDirtyChange={onDirtyChange} policy={version.policy} onCancel={onCancel} submitLabel="保存调用方配置" onSubmit={value=>onSubmit(version.id,selectedMode,value)}/>:<Alert type="warning" message="此版本当前不能创建调用方" description={version.blockers.join('；')||'服务端未开放可授予范围。'}/>:<p>没有当前可授权的发布版本，请先发布并刷新。</p>}
  </div>;
}
