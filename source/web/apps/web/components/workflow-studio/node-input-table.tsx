'use client';
import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {Dropdown} from 'antd';
import type {ObjectValue,Schema,VariableOption} from '@/lib/workflow/types';
import {inputValueIssue} from '@/lib/workflow/input-values';
import {typeLabel} from '@/lib/workflow/labels';
import {ValueField} from './schema-fields';
import {SchemaDefinition} from './schema-definition';
import styles from './studio.module.css';

const types=['string','number','integer','boolean','object','array','null','datetime'];
const safe=(name:string)=>/^[A-Za-z_\u3400-\u9fff][A-Za-z_0-9\u3400-\u9fff]*$/.test(name)&&!['__proto__','prototype','constructor','system','user'].includes(name);
/** Names remain local drafts until valid; type edits never coerce saved values. */
export function NodeInputTable({schema,value,variables,onChange,onValidity,definitionsOnly=false,label='输入'}:{label?:string;schema:Schema;value:ObjectValue;variables:VariableOption[];onChange:(schema:Schema,value:ObjectValue)=>void;onValidity:(ok:boolean)=>void;definitionsOnly?:boolean}){
 const properties=schema.properties||{},[names,setNames]=useState<Record<string,string>>({}),[invalid,setInvalid]=useState<Record<string,boolean>>({});
 const callbacks=useRef({onValidity});callbacks.current={onValidity};const drafts=useRef<Record<string,{fixed?:any;reference?:any}>>({});
 const nameIssue=(name:string,raw:string)=>!safe(raw)||raw!==name&&Object.hasOwn(properties,raw);
 const valueIssues=definitionsOnly?[]:Object.entries(properties).map(([name,field])=>inputValueIssue(name,field,value[name],!!schema.required?.includes(name),variables)).filter(Boolean);
 const bad=!!valueIssues.length||Object.entries(names).some(([name,raw])=>Object.hasOwn(properties,name)&&raw!==name)||Object.entries(invalid).some(([key,v])=>v&&(Object.hasOwn(properties,key)||key==='__advanced'));
 useLayoutEffect(()=>{callbacks.current.onValidity(!bad);},[bad,JSON.stringify(names),JSON.stringify(properties)]);
 useEffect(()=>()=>callbacks.current.onValidity(true),[]);
 const validity=(name:string)=>(ok:boolean)=>setInvalid(old=>old[name]===!ok?old:{...old,[name]:!ok});
 const update=(name:string,next:any)=>{const values={...value};if(next===undefined)delete values[name];else values[name]=next;onChange(schema,values);};
 if(schema.type&&schema.type!=='object'||Array.isArray(properties)||typeof value?.$ref==='string')return <p role="alert">已有完整对象来源或高级输入结构已保留，请在高级兼容中编辑。</p>;
 return <div className={styles.nodeInputTable} data-definitions-only={definitionsOnly}><table><thead><tr><th>参数名</th><th>类型</th>{!definitionsOnly&&<><th>取值方式</th><th>值</th></>}<th>选项</th><th>操作</th></tr></thead><tbody>{Object.entries(properties).map(([name,field])=>{
  const raw=names[name]??name,reference=value[name]!==null&&typeof value[name]==='object'&&typeof value[name]?.$ref==='string';
  const type=field.type==='string'&&field.format==='date-time'?'datetime':typeof field.type==='string'?field.type:'__advanced';
  const choices=variables.filter(v=>!field.type||v.type==='any'||v.type.split(/[|,]/).every(t=>(Array.isArray(field.type)?field.type:[field.type]).includes(t)));
  return <tr key={name}><td data-label="参数名"><input aria-label={`${label}字段${name}名称`} value={raw} aria-invalid={nameIssue(name,raw)} onChange={e=>setNames(old=>({...old,[name]:e.target.value}))} onBlur={()=>{
   if(nameIssue(name,raw))return; if(raw===name){setNames(old=>{const next={...old};delete next[name];return next;});return;}
   const next={...value};if(Object.hasOwn(next,name)){next[raw]=next[name];delete next[name];}
   onChange({...schema,properties:Object.fromEntries(Object.entries(properties).map(([key,v])=>[key===name?raw:key,v])),required:schema.required?.map(key=>key===name?raw:key)},next);
  }}/>{nameIssue(name,raw)&&<small role="alert">名称无效、重复或属于保留名称</small>}</td>
  <td data-label="类型"><select aria-label={`${label}字段${name}类型`} value={type} onChange={e=>onChange({...schema,properties:{...properties,[name]:{...field,type:e.target.value==='datetime'?'string':e.target.value,...(e.target.value==='datetime'?{format:'date-time'}:type==='datetime'?{format:undefined}:{}),...(e.target.value==='array'&&!field.items?{items:{}}:{})}}},value)}>{type==='__advanced'&&<option value="__advanced">已有高级类型</option>}{types.map(t=><option key={t} value={t}>{t==='datetime'?'日期时间':typeLabel(t)}</option>)}</select></td>
  {!definitionsOnly&&<><td data-label="取值方式"><select aria-label={`${label}字段${name}取值方式`} value={reference?'reference':'literal'} onChange={e=>{const cache=drafts.current[name]||{};if(reference)cache.reference=value[name];else cache.fixed=value[name];drafts.current[name]=cache;update(name,e.target.value==='reference'?cache.reference??{$ref:''}:cache.fixed);}}><option value="reference">引用</option><option value="literal">固定值</option></select></td><td data-label="值"><ValueField compact name={name} schema={field} value={value[name]} variables={choices} allowReferences={false} required={!!schema.required?.includes(name)} onValidity={validity(name)} onChange={v=>update(name,v)}/></td></>}
  <td data-label="选项"><Dropdown trigger={['click']} menu={{items:[{key:'required',label:`${schema.required?.includes(name)?'✓ ':''}必填`,onClick:()=>onChange({...schema,required:schema.required?.includes(name)?(schema.required||[]).filter(key=>key!==name):[...new Set([...(schema.required||[]),name])]},value)},...(!definitionsOnly&&reference?[{key:'optional',label:`${value[name]?.optional?'✓ ':''}分支跳过时允许为空`,onClick:()=>update(name,{$ref:value[name]?.$ref||'',optional:!value[name]?.optional})}]:[])]}}><button type="button" aria-label={`${label}字段${name}选项`}>⋯</button></Dropdown></td>
  <td data-label="操作"><button aria-label={`删除${label}${name}`} onClick={()=>{const next={...properties},values={...value};delete next[name];delete values[name];onChange({...schema,properties:next,required:schema.required?.filter(key=>key!==name)},values);}}>删除</button></td></tr>;
 })}</tbody></table>{valueIssues.map(issue=><p role="alert" key={issue}>{issue}</p>)}{!Object.keys(properties).length&&<p className={styles.hint}>尚未添加字段。</p>}<button onClick={()=>{let index=1;while(Object.hasOwn(properties,`input_${index}`))index++;onChange({...schema,type:'object',properties:{...properties,[`input_${index}`]:{type:'string'}}},definitionsOnly?value:{...value,[`input_${index}`]:{$ref:''}});}}>＋ 添加{label}</button>
 <details className={styles.details}><summary>嵌套结构、默认值与高级约束</summary><SchemaDefinition allowNullable label={label} value={schema} onChange={next=>onChange(next,value)} onValidity={validity('__advanced')}/></details></div>;
}
