'use client';
import {useEffect,useState} from 'react';
import {Alert,Button,Input,Select,Space,Tag} from 'antd';
import {currentIdentityEpoch} from '@/lib/api';
import {reasoningBlockedParameters,reasoningProfileContract,reasoningProfileIssues,reasoningProfileNames,parameterLabels,type ReasoningProfile} from '@/lib/model-catalog';
export default function ModelReasoningProfile({active,value,onChange,modelId,onSupported,parameters,onRemoveUnsupported}:{active:boolean;value?:unknown;onChange?:(value:ReasoningProfile|null)=>void;modelId?:string;onSupported:(supported:boolean)=>void;parameters:string[];onRemoveUnsupported:()=>void}){
 const [state,setState]=useState<'loading'|'supported'|'unsupported'>('loading');
 useEffect(()=>{let stale=false;const epoch=currentIdentityEpoch(),abort=new AbortController();onSupported(false);if(!active)return;setState('loading');const events=['zhangpu:access-denied','zhangpu:session-expired','zhangpu:permissions-changed','zhangpu:data-policy-changed'],invalidate=()=>{stale=true;abort.abort();setState('unsupported');onSupported(false);};events.forEach(name=>window.addEventListener(name,invalidate));void(async()=>{try{const response=await fetch('/openapi.json',{cache:'no-store',credentials:'same-origin',signal:AbortSignal.any([abort.signal,AbortSignal.timeout(15000)])});if(!response.ok)throw Error();const supported=reasoningProfileContract(await response.json());if(!stale&&epoch===currentIdentityEpoch()){setState(supported?'supported':'unsupported');onSupported(supported);}}catch{if(!stale&&epoch===currentIdentityEpoch()){setState('unsupported');onSupported(false);}}})();return()=>{stale=true;abort.abort();events.forEach(name=>window.removeEventListener(name,invalidate));};},[active,onSupported]);
 if(!active)return null;
 const p=value as ReasoningProfile|undefined,recognized=value==null||typeof value==='object'&&!Array.isArray(value)&&p&&Object.hasOwn(reasoningProfileNames,p.profile_id)&&p.protocol==='openai_chat_completions',supported=state==='supported',blocked=reasoningBlockedParameters(p).filter(k=>parameters.includes(k));
 if(!recognized)return <Alert type="warning" message="已有公开推理声明暂不能识别，原值已保留" action={<Button onClick={()=>onChange?.(null)}>明确移除此声明</Button>}/>;
 const update=(changes:Partial<ReasoningProfile>)=>p&&onChange?.({...p,...changes});
 const controls=p?.profile_id.startsWith('aliyun_')?[{value:'enable_thinking',label:'显式启用思考开关'}]:[{value:'thinking_type',label:'显式思考模式开关'}];
 const modes=[{value:'provider_default',label:'沿用该服务版本默认模式'},...(p?.request_control!=='none'?[{value:'enabled',label:'明确开启'},{value:'disabled',label:'明确关闭'},...(p?.profile_id.startsWith('ark_')?[{value:'auto',label:'自动（仅该版本明确支持时）'}]:[])]:[])];
 const issues=p?reasoningProfileIssues(p,modelId):[];
 return <section aria-label="模型公开推理版本适配" style={{display:'grid',gap:12}}><h3>公开推理版本适配</h3><p>按实际模型版本文档选择，不能从平台名称自动判断。声明不代表已完成供应商实测。</p>{!supported&&<Alert type="info" message={state==='loading'?'正在确认服务端适配配置字段…':'当前服务尚未声明此配置字段；原值保留，暂不能新增声明'}/>}
  <Select<string> aria-label="公开推理适配类型" value={p?.profile_id||''} disabled={!supported||!modelId?.trim()} options={[{value:'',label:'未配置版本适配'},...Object.entries(reasoningProfileNames).map(([value,label])=>({value,label}))]} onChange={id=>{if(!supported)return;if(!id){onChange?.(null);return;}onChange?.({profile_id:id as ReasoningProfile['profile_id'],protocol:'openai_chat_completions',model_id:modelId||'',model_revision:'',transport:'json',request_control:'none',mode:'provider_default'});}}/>
  {!p?<Tag>公开推理 · 未适配</Tag>:<><Space wrap><Tag>管理员声明 · 尚非供应商实测认证</Tag><Tag>{p.profile_id==='ark_public_summary_v1'?'供应商公开摘要':'供应商公开推理内容'}</Tag></Space><p>协议：Chat Completions兼容；绑定实际model：{p.model_id}</p>{p.model_id!==modelId&&<Alert type="warning" message="实际model已变化，原适配声明不再匹配" action={<Button disabled={!supported||!modelId?.trim()} onClick={()=>update({model_id:modelId||'',model_revision:''})}>明确绑定当前model并重新填写版本</Button>}/>}
   <label>供应商具体模型版本<Input aria-label="公开推理具体模型版本" disabled={!supported} value={p.model_revision} maxLength={200} placeholder="填写该部署对应的具体版本，不能使用latest等别名" onChange={event=>update({model_revision:event.target.value})}/></label>
   <label>服务响应方式<Select aria-label="公开推理响应方式" disabled={!supported} value={p.transport} options={[{value:'json',label:'JSON完整响应'},{value:'sse',label:'SSE流式响应（按完整回复展示）'}]} onChange={transport=>update({transport})}/></label>
   <label>该版本支持的请求开关<Select aria-label="公开推理请求开关" disabled={!supported} value={p.request_control} options={[{value:'none',label:'不发送开关参数'},...controls]} onChange={request_control=>update({request_control,mode:'provider_default'})}/></label>
   <label>思考模式<Select aria-label="公开推理模式" disabled={!supported||p.request_control==='none'} value={p.mode} options={modes} onChange={mode=>update({mode})}/></label>
   {issues.length>0&&<Alert type="warning" message="适配声明尚需明确调整" description={issues.join(' ')}/>}{blocked.length>0&&<Alert type="warning" message="支持的生成参数声明与此适配不兼容" description={`请定位“支持的生成参数”：${blocked.map(k=>parameterLabels[k]).join('、')}。原值未删除。`} action={<Button onClick={onRemoveUnsupported}>明确移除这些参数声明</Button>}/>}
   <p className="subtle">本批适配不支持工具多轮续传。关闭时不显示可读推理内容；未配置版本适配与供应商本次未返回文字是不同状态。</p>
  </>}
 </section>;
}
