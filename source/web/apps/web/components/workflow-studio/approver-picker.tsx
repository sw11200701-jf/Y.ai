'use client';
import {useEffect,useRef,useState} from 'react';
import {currentIdentityEpoch} from '@/lib/api';
import {workflowApi} from '@/lib/workflow/api';
import {errorMessage} from '@/lib/errors';
type Person={id:string;display_name:string;is_self:boolean;role_name?:string};
const epoch=()=>typeof currentIdentityEpoch==='function'?currentIdentityEpoch():0;
export function ApproverPicker({value,allowSelf,onChange,onValidity}:{value:string[];allowSelf:boolean;onChange:(ids:string[])=>void;onValidity:(valid:boolean)=>void}){
 const [items,setItems]=useState<Person[]>([]),[error,setError]=useState(''),[loading,setLoading]=useState(true),[attempt,setAttempt]=useState(0);const valid=useRef(onValidity);valid.current=onValidity;
 useEffect(()=>{let live=true;const actor=epoch();setItems([]);setError('');setLoading(true);const clear=()=>{live=false;setItems([]);setLoading(false);setError('身份或权限已变化，请重新打开审批人选择');};const signals=['zhangpu:session-expired','zhangpu:access-denied','zhangpu:permissions-changed','zhangpu:identity-changed'];signals.forEach(signal=>window.addEventListener(signal,clear));void workflowApi.approvers().then(body=>{if(!live||actor!==epoch())return;if(!Array.isArray(body.items)||body.items.some(item=>!item.id||typeof item.display_name!=='string'||typeof item.is_self!=='boolean'))throw Error('审批人目录格式异常');setItems(body.items);}).catch(error=>{if(live&&actor===epoch()){setItems([]);setError(errorMessage(error));}}).finally(()=>{if(live&&actor===epoch())setLoading(false);});return()=>{live=false;signals.forEach(signal=>window.removeEventListener(signal,clear));};},[attempt]);
 const available=value.length>0&&value.every(id=>items.some(item=>item.id===id&&(!item.is_self||allowSelf)));
 useEffect(()=>{valid.current(!loading&&!error&&available);},[loading,error,available]);useEffect(()=>()=>valid.current(true),[]);
 return <section><h3>选择审批人</h3>{loading&&<p>正在读取当前可指定的审批人…</p>}{error&&<p role="alert">{error}</p>}{items.map(item=><label key={item.id}><input type="checkbox" checked={value.includes(item.id)} disabled={item.is_self&&!allowSelf||!value.includes(item.id)&&value.length>=16} onChange={event=>onChange(event.target.checked?[...value,item.id]:value.filter(id=>id!==item.id))}/>{item.display_name}{item.is_self?'（本人）':''}{item.role_name?` · ${item.role_name}`:''}</label>)}{value.filter(id=>!items.some(item=>item.id===id)).map((id,index)=><p key={id}>已保存审批人{index+1}当前不可选择<button onClick={()=>onChange(value.filter(other=>other!==id))}>移除此引用</button></p>)}{!loading&&!error&&!items.length&&<p>当前没有可指定的审批人，请检查人员状态和审批权限。</p>}<button onClick={()=>setAttempt(value=>value+1)}>刷新审批人</button><small>此目录只提供姓名选择，实际审批和恢复仍由后台重新检查权限。</small></section>;
}
