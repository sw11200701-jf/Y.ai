'use client';
import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Empty,Input,Space,Tag} from 'antd';
import {currentIdentityEpoch,request,send,time} from '@/lib/api';
import {errorMessage} from '@/lib/errors';
import {PageTitle,ErrorBanner} from './shared';
import {useSession} from './auth-gate';

type Item={id:string;run_id:string;node_id:string;title:string;status:string;expires_at:string;note?:string|null;can_approve:boolean;run_status:string};
const statuses:Record<string,string>={pending:'待审批',approved:'已同意',rejected:'已拒绝',expired:'已过期',cancelled:'已取消'};
export default function ApprovalInbox(){
 const {session,permissionsChecking}=useSession();
 const allowed=Array.isArray(session.user.permissions)&&session.user.permissions.includes('runs.approve')&&!permissionsChecking;
 const [items,setItems]=useState<Item[]>([]),[error,setError]=useState(''),[loading,setLoading]=useState(false),[busy,setBusy]=useState('');
 const [notes,setNotes]=useState<Record<string,string>>({});
 const alive=useRef(true),generation=useRef(0),reads=useRef(0),pending=useRef(false);
 const clear=()=>{generation.current++;reads.current++;setItems([]);setNotes({});setError('');setBusy('');};
 async function load(){
  if(!allowed)return;const ticket=++reads.current,version=generation.current,epoch=currentIdentityEpoch();setLoading(true);
  const current=()=>alive.current&&ticket===reads.current&&version===generation.current&&epoch===currentIdentityEpoch();
  try{
   const value=await request<{items:Item[];limit:number}>('/approvals?status=pending');
   if(!Array.isArray(value.items)||value.items.some(item=>!item||typeof item.id!=='string'||typeof item.run_id!=='string'||typeof item.title!=='string'||typeof item.can_approve!=='boolean'))throw Error('审批资料格式异常，请刷新');
   if(current()){setItems(value.items);setError('');}
  }catch(e){if(current()){setItems([]);setNotes({});setError(errorMessage(e));}}
  finally{if(current())setLoading(false);}
 }
 useEffect(()=>{
  alive.current=true;clear();if(allowed)void load();
  const reset=()=>{clear();setLoading(false);};
  const leave=(event:Event)=>{if(pending.current)event.preventDefault();};
  window.addEventListener('zhangpu:permissions-changed',reset);window.addEventListener('zhangpu:data-policy-changed',reset);window.addEventListener('zhangpu:session-expired',reset);window.addEventListener('zhangpu:before-navigation',leave);
  const timer=setInterval(()=>{if(!pending.current&&allowed)void load();},10000);
  return()=>{alive.current=false;generation.current++;reads.current++;clearInterval(timer);window.removeEventListener('zhangpu:permissions-changed',reset);window.removeEventListener('zhangpu:data-policy-changed',reset);window.removeEventListener('zhangpu:session-expired',reset);window.removeEventListener('zhangpu:before-navigation',leave);};
 },[session.user.id,allowed]);
 async function decide(item:Item,decision:'approve'|'reject'){
  if(pending.current||!allowed||item.can_approve!==true||item.status!=='pending')return;
  pending.current=true;reads.current++;setBusy(item.id);setError('');
  const version=generation.current,epoch=currentIdentityEpoch();
  const current=()=>alive.current&&version===generation.current&&epoch===currentIdentityEpoch();
  try{
   const result=await send<{id:string;run_id:string}>(`/runs/${encodeURIComponent(item.run_id)}/approvals/${encodeURIComponent(item.id)}/decision`,{decision,note:(notes[item.id]||'').trim()});
   if(result.id!==item.id||result.run_id!==item.run_id)throw Error('审批记录标识不一致，请刷新确认');
   if(current()){setNotes(value=>({...value,[item.id]:''}));await load();}
  }catch(e){if(current()){setItems([]);setNotes({});setError(errorMessage(e)+' 请刷新核对处理结果；系统不会自动重复提交。');}}
  finally{pending.current=false;if(current())setBusy('');}
 }
 return <><PageTitle eyebrow="APPROVALS" title="待我审批" description="逐项处理指定给当前账号的审批。全部同意后，由发起人在运行记录中继续。" action={<Button loading={loading} disabled={Boolean(busy)||!allowed} onClick={()=>void load()}>刷新审批</Button>}/><ErrorBanner error={error}/>{!allowed?<Alert type="info" message="当前账号没有审批指定运行的权限"/>:items.length?<div className="two-column equal">{items.map(item=><section className="panel" key={item.id}><div className="panel-heading"><h2>{item.title}</h2><Tag>{statuses[item.status]||'状态待确认'}</Tag></div><p className="subtle">运行 {item.run_id}<br/>节点 {item.node_id}<br/>有效至 {time(item.expires_at)}</p>{item.note&&<p>处理说明：{item.note}</p>}{item.can_approve===true&&item.status==='pending'?<><Input.TextArea aria-label={`审批说明：${item.title}`} value={notes[item.id]||''} maxLength={500} rows={2} disabled={Boolean(busy)} onChange={event=>setNotes(value=>({...value,[item.id]:event.target.value}))}/><Space style={{marginTop:12}}><Button type="primary" loading={busy===item.id} disabled={Boolean(busy)} onClick={()=>void decide(item,'approve')}>同意</Button><Button danger disabled={Boolean(busy)} onClick={()=>void decide(item,'reject')}>拒绝</Button></Space></>:item.status==='pending'?<p className="hint">本项当前不可处理，请刷新检查运行状态或有效期。</p>:null}</section>)}</div>:<section className="panel"><Empty description={loading?'正在读取审批':'暂无当前账号的审批事项'}/></section>}</>;
}
