'use client';
import {useState} from 'react';
import {Alert,Button,Checkbox,Input,InputNumber,Select,Space} from 'antd';
import type {Json} from '@/lib/api';
import {agentParameterTypes,changeParameterType,nextParameterKey,parameterEditable,parameterObjectEditable,parameterInitialValue,removeParameter,setParameter,setParameterRequired} from '@/lib/agent-parameter-schema';
const labels={string:'文本',integer:'整数',number:'数字',boolean:'是/否',object:'对象',array:'列表',null:'空值'};
export default function AgentParameterEditor({value,onChange,readOnly=false,depth=0}:{value:Json;onChange:(value:Json)=>void;readOnly?:boolean;depth?:number}){
 const [error,setError]=useState('');
 if(!parameterObjectEditable(value))return <Alert type="info" message="现有参数定义已保留" description="该定义包含特殊结构，目前只读；不会转换或覆盖已有规则。"/>;
 const update=(key:string,field:Json)=>onChange(setParameter(value,key,field));
 return <section aria-label="智能体参数字段" style={{display:'grid',gap:12,minWidth:0}}>
  {error&&<Alert type="warning" message={error}/>}
  {Object.entries(value.properties||{}).map(([key,raw],index)=>{const field=raw as Json,editable=parameterEditable(field);return <section key={key} style={{padding:14,border:'1px solid #dce5ed',borderRadius:14,background:'rgba(255,255,255,.6)'}}>
   <Space wrap align="start"><label>业务名称<Input aria-label={`参数${index+1}业务名称`} value={field.title||key} disabled={readOnly} onChange={event=>update(key,{...field,title:event.target.value})}/></label><label>类型<Select aria-label={`参数${index+1}类型`} value={field.type} disabled={readOnly||!editable} options={agentParameterTypes.map(type=>({value:type,label:labels[type]}))} onChange={type=>{try{update(key,changeParameterType(field,type));setError('');}catch(error){setError((error as Error).message);}}}/></label><Checkbox disabled={readOnly} checked={(value.required||[]).includes(key)} onChange={event=>onChange(setParameterRequired(value,key,event.target.checked))}>必填</Checkbox><Button danger disabled={readOnly} onClick={()=>onChange(removeParameter(value,key))}>移除参数</Button></Space>
   <label style={{display:'block',marginTop:10}}>用途说明<Input disabled={readOnly} value={field.description||''} onChange={event=>update(key,{...field,description:event.target.value})}/></label>
   {!editable?<Alert type="info" message="此参数的特殊结构与规则保持原样"/>:<>
    {field.type==='object'&&depth<6&&<details><summary>配置对象中的参数</summary><AgentParameterEditor depth={depth+1} readOnly={readOnly} value={field} onChange={next=>update(key,next)}/></details>}
    {field.type==='array'&&<ArrayStructure value={field} onChange={next=>update(key,next)} readOnly={readOnly} depth={depth+1}/>}
    <Checkbox disabled={readOnly} checked={Object.hasOwn(field,'default')} onChange={event=>{const next={...field};if(event.target.checked)next.default=parameterInitialValue(field);else delete next.default;update(key,next);}}>设置默认值</Checkbox>
    {Object.hasOwn(field,'default')&&<ParameterValue schema={field} value={field.default} disabled={readOnly} onChange={next=>update(key,{...field,default:next})}/>}
   </>}
  </section>;})}
  {!Object.keys(value.properties||{}).length&&<p className="subtle">尚无参数，可按业务需要添加。</p>}
  <Button disabled={readOnly||depth>=6} onClick={()=>{const key=nextParameterKey(value);update(key,{type:'string',title:'新参数'});}}>添加参数</Button>
 </section>;
}
function ArrayStructure({value,onChange,readOnly,depth}:{value:Json;onChange:(value:Json)=>void;readOnly:boolean;depth:number}){
 const [error,setError]=useState('');if(depth>=6)return <span>更深层定义保留原样。</span>;
 const items=value.items||{type:'string'};
 return <details><summary>配置列表元素</summary>{error&&<Alert type="warning" message={error}/>}<Select aria-label="列表元素类型" disabled={readOnly||!parameterEditable(items)} value={items.type} options={agentParameterTypes.map(type=>({value:type,label:labels[type]}))} onChange={type=>{try{onChange({...value,items:changeParameterType(items,type)});setError('');}catch(error){setError((error as Error).message);}}}/>{items.type==='object'&&<AgentParameterEditor depth={depth} readOnly={readOnly} value={items} onChange={next=>onChange({...value,items:next})}/>} {items.type==='array'&&<ArrayStructure depth={depth+1} readOnly={readOnly} value={items} onChange={next=>onChange({...value,items:next})}/>}</details>;
}
export function ParameterValue({schema,value,onChange,disabled=false,depth=0}:{schema:Json;value:unknown;onChange:(value:unknown)=>void;disabled?:boolean;depth?:number}){
 if(depth>6)return <span>层级较深，默认值保持原样。</span>;
 if(!parameterEditable(schema))return <span>特殊类型的现有值已保留。</span>;
 if(schema.type==='null')return <span>空值（null）</span>;
 if(schema.type==='object')return <div style={{paddingLeft:12}}>{Object.entries(schema.properties||{}).map(([key,raw])=><label key={key} style={{display:'block'}}>{(raw as Json).title||key}<ParameterValue depth={depth+1} schema={raw as Json} value={(value as Json)?.[key]} disabled={disabled} onChange={next=>onChange({...((value&&typeof value==='object'&&!Array.isArray(value))?value:{}),[key]:next})}/></label>)}</div>;
 if(schema.type==='array')return <div>{(Array.isArray(value)?value:[]).map((item,index)=><Space key={index}><ParameterValue depth={depth+1} schema={schema.items||{type:'string'}} value={item} disabled={disabled} onChange={next=>onChange((value as unknown[]).map((old,n)=>n===index?next:old))}/><Button disabled={disabled} onClick={()=>onChange((value as unknown[]).filter((_,n)=>n!==index))}>移除此项</Button></Space>)}<Button disabled={disabled} onClick={()=>onChange([...(Array.isArray(value)?value:[]),parameterInitialValue(schema.items||{type:'string'})])}>添加列表项</Button></div>;
 if(schema.type==='boolean')return <Select aria-label="参数默认值" disabled={disabled} value={value as boolean} options={[{value:true,label:'是'},{value:false,label:'否'}]} onChange={onChange}/>;
 if(schema.type==='number'||schema.type==='integer')return <InputNumber aria-label="参数默认值" disabled={disabled} precision={schema.type==='integer'?0:undefined} value={value as number} onChange={onChange}/>;
 return <Input aria-label="参数默认值" disabled={disabled} value={typeof value==='string'?value:''} onChange={event=>onChange(event.target.value)}/>;
}
