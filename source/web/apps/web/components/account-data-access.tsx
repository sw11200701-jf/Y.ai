'use client';
import {useEffect,useRef,useState} from 'react';
import {App,Drawer,Empty,Select} from 'antd';
import {Json,list,request} from '@/lib/api';
import {hasPermission} from '@/lib/permissions';
import {formatError,type FormattedError} from '@/lib/errors';
import {useSession} from './auth-gate';
import DataAccessPanel from './data-access-panel';
import {ErrorBanner} from './shared';
export default function AccountDataAccess({user,users,roles,onClose,enabled=true}:{user?:Json;users:Json[];roles:Json[];onClose:()=>void;enabled?:boolean}){
 const {session}=useSession();const {modal}=App.useApp();const [resources,setResources]=useState<Json[]>([]);const [resourcesFor,setResourcesFor]=useState<string>();const [selected,setSelected]=useState<string>();const [error,setError]=useState<string|FormattedError>('');const generation=useRef(0);const selectedRef=useRef(selected);selectedRef.current=selected;const dirty=useRef(false);const leavePending=useRef<Promise<boolean>|null>(null);const leaveDialog=useRef<{destroy:()=>void}|null>(null);const leaveResolve=useRef<((leave:boolean)=>void)|null>(null);
 async function canLeave(){const token=generation.current;if(!dirty.current)return true;if(!leavePending.current)leavePending.current=new Promise<boolean>(resolve=>{leaveResolve.current=resolve;leaveDialog.current=modal.confirm({title:'数据授权尚未保存',content:'离开将放弃当前资源的修改。',centered:true,okText:'放弃修改',cancelText:'继续编辑',onOk:()=>resolve(true),onCancel:()=>resolve(false)});}).finally(()=>{leavePending.current=null;leaveDialog.current=null;leaveResolve.current=null;});const leave=await leavePending.current;return leave&&token===generation.current;}
 useEffect(()=>{
   const userId=user?.id;setResourcesFor(undefined);setResources([]);setSelected(undefined);selectedRef.current=undefined;dirty.current=false;setError('');
   async function reload(preserve:boolean){const id=++generation.current;if(!userId)return;try{
     const existing=await list(`/users/${userId}/data-access`);const requests:Promise<Json[]>[]=[];
     if(hasPermission(session.user,'page.knowledge'))requests.push(list('/knowledge-bases').then(items=>items.map(r=>({...r,kind:'knowledge_base',resource_id:r.id}))));
     if(hasPermission(session.user,'page.database'))requests.push(list('/databases').then(items=>items.filter(r=>!r.restricted).map(r=>({...r,kind:'database',resource_id:r.id}))));
     const outcomes=await Promise.allSettled(requests);for(const outcome of outcomes)if(outcome.status==='rejected')throw outcome.reason;
     const candidates=outcomes.flatMap(r=>r.status==='fulfilled'?r.value:[]);const allowed=await Promise.allSettled(candidates.map(async r=>({...r,...await request(`/data-access/${r.kind}/${r.resource_id}`)})));
     for(const outcome of allowed)if(outcome.status==='rejected'&&outcome.reason?.status!==404)throw outcome.reason;
     if(id!==generation.current)return;const values=[...existing,...allowed.flatMap(r=>r.status==='fulfilled'?[r.value]:[])];const unique=[...new Map(values.map(r=>[`${r.kind}:${r.resource_id}`,r])).values()];
     const previous=selectedRef.current;setResourcesFor(userId);setResources(unique);
     if(preserve&&previous){if(!unique.some(r=>`${r.kind}:${r.resource_id}`===previous)){dirty.current=false;setSelected(undefined);setError('当前资源的管理授权已变化，请重新选择可管理的资源。');}}
     else setSelected(unique[0]?`${unique[0].kind}:${unique[0].resource_id}`:undefined);
   }catch(e){if(id===generation.current){if(preserve){setResources([]);setSelected(undefined);dirty.current=false;}setError(formatError(e));}}}
   void reload(false);const changed=()=>void reload(true);window.addEventListener('zhangpu:data-policy-changed',changed);return()=>{generation.current++;leaveResolve.current?.(false);leaveDialog.current?.destroy();window.removeEventListener('zhangpu:data-policy-changed',changed);};
 },[user?.id]);
 // Props change before effects clear the previous resource list (notably on close).
 const resource=user&&resourcesFor===user.id?resources.find(r=>`${r.kind}:${r.resource_id}`===selected):undefined;
 return <Drawer title={`${user?.display_name||user?.username||''} · 数据授权`} open={!!user} onClose={()=>void canLeave().then(leave=>{if(leave)onClose();})} maskClosable={false} width="min(1050px,calc(100vw - 16px))" destroyOnHidden><ErrorBanner error={error}/><p className="hint">这里只列出你本来可管理的资源，选择资源后编辑与资源设置页相同的授权。授予角色会影响该角色的所有实际成员。</p><Select aria-label="可管理的知识库或数据库" showSearch optionFilterProp="label" value={selected} style={{width:'100%',marginBottom:20}} placeholder="选择要授权的资源" options={resources.map(r=>({value:`${r.kind}:${r.resource_id}`,label:`${r.kind==='knowledge_base'?'知识库':r.kind==='database'?'数据库':'数据资源'} · ${r.name||r.resource_id}`}))} onChange={value=>void canLeave().then(leave=>{if(leave){dirty.current=false;setSelected(value);}})}/>{resource?<DataAccessPanel disabled={!enabled} key={selected} kind={resource.kind} resourceId={resource.resource_id} subjects={{users,roles}} defaultSubject={{subject_type:'user',subject_id:user!.id}} onDirtyChange={value=>{dirty.current=value;}}/>:<Empty description="没有可管理的数据资源"/>}</Drawer>;
}
