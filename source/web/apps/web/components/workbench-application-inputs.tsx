'use client';
import {Alert,Input,Select,Tag} from 'antd';
import type {Json} from '@/lib/api';
import {ParameterValue} from './agent-parameter-editor';
export default function WorkbenchApplicationInputs({schema,value,onChange,batches,disabled=false}:{schema:Json;value:Json;onChange:(value:Json)=>void;batches:Json[];disabled?:boolean}){
 if(schema.type!=='object')return <Alert type="warning" message="此应用输入定义暂不能显示，请联系维护人员。"/>;
 return <section aria-label="应用业务参数" style={{display:'grid',gap:16}}>{Object.entries(schema.properties||{}).map(([key,raw])=>{const field=raw as Json,change=(next:unknown)=>onChange({...value,[key]:next}),batch=batches.find(b=>b.id===value[key]);
  return <div key={key}><label style={{display:'grid',gap:6}}>{field.title||key}{(schema.required||[]).includes(key)?' *':''}
   {key==='batch_id'&&field.type==='string'?<Select aria-label="选择授权资料批次" value={value[key]} disabled={disabled} placeholder="选择已提交或已发布的财务资料" options={batches.map(b=>({value:b.id,label:`${b.filename||b.name||b.id}${b.synthetic===true?' · 业务演示样例':''}`}))} onChange={change}/>:key==='periods'&&field.type==='array'&&field.items?.type==='string'?<Select aria-label="分析年度" mode="tags" value={value[key]||[]} disabled={disabled} placeholder="输入四位分析年度" onChange={change}/>:field.type==='string'?<Input aria-label={field.title||key} value={value[key]??''} disabled={disabled} onChange={event=>change(event.target.value)}/>:<ParameterValue schema={field} value={value[key]} onChange={change} disabled={disabled}/>}
  </label>{field.description&&<p className="subtle">{field.description}</p>}{batch&&<Tag color={batch.synthetic===true?'orange':undefined}>{batch.synthetic===true?'业务演示样例':batch.synthetic===false?'业务资料':'资料性质未标记'}</Tag>}{key==='batch_id'&&!batches.length&&<p className="subtle">当前没有可用的已发布资料批次，请联系资料维护人员。</p>}</div>;
 })}</section>;
}
