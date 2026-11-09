'use client';
import {useEffect,useState} from 'react';
import {Alert,Button,Input,Select,Space,Tag} from 'antd';
import {currentIdentityEpoch} from '@/lib/api';
import {visionProfileContract,visionProfileIssues,type VisionProfile} from '@/lib/model-catalog';
export default function ModelVisionProfile({active,value,onChange,modelId,reasoning,onSupported}:{active:boolean;value?:unknown;onChange?:(value:VisionProfile|null)=>void;modelId?:string;reasoning?:unknown;onSupported:(supported:boolean)=>void}){
 const [state,setState]=useState<'loading'|'supported'|'unsupported'>('loading');
 useEffect(()=>{let stale=false;const epoch=currentIdentityEpoch(),abort=new AbortController();onSupported(false);if(!active)return;setState('loading');const events=['zhangpu:access-denied','zhangpu:session-expired','zhangpu:permissions-changed','zhangpu:data-policy-changed'],invalidate=()=>{stale=true;abort.abort();setState('unsupported');onSupported(false);};events.forEach(name=>window.addEventListener(name,invalidate));void(async()=>{try{const response=await fetch('/openapi.json',{cache:'no-store',credentials:'same-origin',signal:AbortSignal.any([abort.signal,AbortSignal.timeout(15000)])});if(!response.ok)throw Error();const supported=visionProfileContract(await response.json());if(!stale&&epoch===currentIdentityEpoch()){setState(supported?'supported':'unsupported');onSupported(supported);}}catch{if(!stale&&epoch===currentIdentityEpoch()){setState('unsupported');onSupported(false);}}})();return()=>{stale=true;abort.abort();events.forEach(name=>window.removeEventListener(name,invalidate));};},[active,onSupported]);
 if(!active)return null;
 const p=value as VisionProfile|undefined,supported=state==='supported',recognized=value==null||typeof value==='object'&&!Array.isArray(value)&&p?.profile_id==='openai_image_url_v1'&&p.protocol==='openai_chat_completions'&&p.input_source==='current_run_message_attachment'&&p.token_reservation==='tiles512_2048_plus1024_v1'&&typeof p.model_id==='string'&&typeof p.model_revision==='string';
 if(!recognized)return <Alert type="warning" message="已有图片输入声明暂不能识别，原值已保留" action={<Button onClick={()=>onChange?.(null)}>明确移除此图片声明</Button>}/>;
 const issues=p?visionProfileIssues(p,modelId,reasoning):[];
 return <section aria-label="模型图片输入版本适配" style={{display:'grid',gap:12}}><h3>图片输入版本适配</h3><p>按实际模型版本文档明确声明。平台品牌、思考摘要或公开推理能力都不能证明图片理解能力。</p>{!supported&&<Alert type="info" message={state==='loading'?'正在确认服务端图片适配配置字段…':'当前服务尚未声明图片适配配置字段；原值保留，暂不能新增声明'}/>}
  <Select aria-label="图片输入适配类型" value={p?.profile_id||''} disabled={!supported||!modelId?.trim()} options={[{value:'',label:'未配置图片版本适配'},{value:'openai_image_url_v1',label:'兼容Chat图片输入 · 本次运行消息'}]} onChange={id=>{if(!supported)return;onChange?.(id?{profile_id:'openai_image_url_v1',protocol:'openai_chat_completions',model_id:modelId||'',model_revision:'',input_source:'current_run_message_attachment',token_reservation:'tiles512_2048_plus1024_v1'}:null);}}/>
  {!p?<Tag>图片输入 · 未适配</Tag>:<><Space wrap><Tag>管理员声明 · 尚非视觉质量验证</Tag><Tag>仅本次运行已受理消息图片</Tag></Space><p>兼容协议：Chat Completions；绑定实际model：{p.model_id}</p>{p.model_id!==modelId&&<Alert type="warning" message="实际model已变化，图片声明不再匹配" action={<Button disabled={!supported||!modelId?.trim()} onClick={()=>onChange?.({...p,model_id:modelId||'',model_revision:''})}>明确重绑图片model并重新填写版本</Button>}/>}
   <label>图片模型具体版本<Input aria-label="图片模型具体版本" value={p.model_revision} disabled={!supported} maxLength={200} placeholder="填写明确版本；与公开推理联用须同一model和版本" onChange={e=>onChange?.({...p,model_revision:e.target.value})}/></label>{issues.length>0&&<Alert type="warning" message="图片适配声明尚需明确调整" description={issues.join(' ')}/>}
   <p className="subtle">本批用于LLM节点的本次消息图片：PNG/JPEG/WebP，最多4张，单张2 MiB、合计4 MiB，像素与边长由服务器重验。图片占用额外上下文预约，预约不是供应商实测用量。此处不上传图片、不调用模型。</p><p className="subtle">与公开推理联用必须同一实际model和具体版本；当前DeepSeek公开推理适配未开放图片联用。声明不会自动为智能体或其它节点启用图片。</p>
  </>}
 </section>;
}
