'use client';
import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Select,Tag} from 'antd';
import {currentIdentityEpoch} from '@/lib/api';
import {errorMessage} from '@/lib/errors';
import {modelCatalog,reasoningProfileNames,type ModelChoice} from '@/lib/model-catalog';
export default function ModelPicker({value,version,onChange,onResolved,canManage=false,onManage,disabled=false}:{value?:string;version?:string;onChange:(model:ModelChoice)=>void;onResolved?:(model:ModelChoice|undefined)=>void;canManage?:boolean;onManage?:()=>void;disabled?:boolean}){
 const [items,setItems]=useState<ModelChoice[]>([]),[loading,setLoading]=useState(true),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);const generation=useRef(0);const resolve=useRef(onResolved);resolve.current=onResolved;
 useEffect(()=>{const changed=()=>setAttempt(n=>n+1);window.addEventListener('zhangpu:model-catalog-changed',changed);return()=>window.removeEventListener('zhangpu:model-catalog-changed',changed);},[]);
 useEffect(()=>{const id=++generation.current,epoch=currentIdentityEpoch();setLoading(true);setError('');void modelCatalog().then(data=>{if(id===generation.current&&epoch===currentIdentityEpoch())setItems(data);}).catch(e=>{if(id===generation.current&&epoch===currentIdentityEpoch()){setItems([]);setError(errorMessage(e));}}).finally(()=>{if(id===generation.current&&epoch===currentIdentityEpoch())setLoading(false);});return()=>{generation.current++;};},[attempt]);
 const selected=items.find(m=>m.id===value);
 useEffect(()=>{resolve.current?.(loading||error?undefined:selected);},[selected,loading,error]);
 const groups=[...new Set(items.map(m=>m.provider_name))].map(name=>({label:name,options:items.filter(m=>m.provider_name===name).map(m=>({value:m.id,label:`${name} · ${m.name}`}))}));
 return <div className="model-picker"><Select aria-label="选择模型" showSearch optionFilterProp="label" loading={loading} disabled={disabled||loading||Boolean(error)} value={value||undefined} placeholder="搜索平台或模型" options={[...(value&&!selected?[{label:'已保存模型（当前不可用，引用保留）',value}]:[]),...groups]} onChange={id=>{const m=items.find(m=>m.id===id);if(m)onChange(m);}} style={{width:'100%'}}/>
 {loading?<small>正在读取可用模型…</small>:error?<Alert type="error" message="模型目录加载失败" description={error} action={<Button size="small" onClick={()=>setAttempt(n=>n+1)}>重试</Button>}/>:!items.length?<div className="model-empty">尚未配置可用模型。{canManage&&onManage?<Button type="link" size="small" onClick={onManage}>前往模型管理</Button>:<span>请联系模型管理员。</span>}</div>:null}
 {!loading&&!error&&value&&!selected&&<Alert type="warning" message="已保存模型已停用、移除或当前账号无权使用；原引用与参数已保留。"/>}
 {selected&&<div className="model-capabilities"><Tag>{selected.source==='legacy_file'?'旧配置兼容':'文本对话'}</Tag>{selected.capabilities.json_schema&&<Tag>结构化输出</Tag>}{selected.capabilities.reasoning_profile?<Tag>{reasoningProfileNames[selected.capabilities.reasoning_profile.profile_id]} · {selected.capabilities.reasoning_profile.model_revision} · 管理员声明</Tag>:<Tag>公开推理 · 未配置版本适配</Tag>}{selected.capabilities.vision_profile?<Tag>本次消息图片 · {selected.capabilities.vision_profile.model_revision} · 管理员声明</Tag>:<Tag>图片输入 · 未配置版本适配</Tag>}<small>上下文{selected.context_window} · 最大输出{selected.max_output_tokens}</small></div>}
 {selected&&version&&version!==selected.config_version&&<Alert type="warning" message="模型配置版本已变化，已保存版本保留。" action={<Button size="small" disabled={disabled} onClick={()=>onChange(selected)}>明确使用当前版本</Button>}/>}
 </div>;
}
