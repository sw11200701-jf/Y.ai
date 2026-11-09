 'use client';
import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import type {Schema} from '@/lib/workflow/types';
import {typeLabel} from '@/lib/workflow/labels';
import {ValueField} from './schema-fields';
import {literalDefault} from '@/lib/workflow/node-config';
import styles from './studio.module.css';
const types=['string','number','integer','boolean','object','array','null'];
const safeName=(name:string)=>/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)&&!['__proto__','prototype','constructor','system','user'].includes(name);
/** Uncommitted names and nested editor errors belong to the enclosing draft. */
export function SchemaDefinition({value,onChange,label='业务输入',depth=0,allowNullable=false,onValidity,onIssues}:{value:Schema;onChange:(value:Schema)=>void;label?:string;depth?:number;allowNullable?:boolean;onValidity?:(valid:boolean)=>void;onIssues?:(issues:string[])=>void}){
 const [names,setNames]=useState<Record<string,string>>({}),[invalid,setInvalid]=useState<Record<string,boolean>>({});
 const callbacks=useRef({onValidity,onIssues});callbacks.current={onValidity,onIssues};const properties=value.properties||{};
 const nameError=(name:string,raw:string)=>!safeName(raw.trim())||raw.trim()!==name&&Object.hasOwn(properties,raw.trim())?'字段名须以字母或下划线开头，且不能重复或使用系统保留名称。':'';
 function report(drafts=names,children=invalid){
  const messages=Object.entries(drafts).filter(([name,raw])=>Object.hasOwn(properties,name)&&raw!==name).map(([name,raw])=>`${label}字段 ${name}：${nameError(name,raw)||'名称尚未确认，请完成编辑后保存。'}`);
  for(const [key,bad] of Object.entries(children))if(bad&&Object.hasOwn(properties,key.split(':')[0]))messages.push(`${label}字段 ${key.split(':')[0]}：请完成标记的字段定义或默认值。`);
  callbacks.current.onValidity?.(!messages.length);callbacks.current.onIssues?.(messages);
 }
 function draftName(name:string,raw:string){const next={...names,[name]:raw};setNames(next);report(next);}
 function childValidity(key:string){return(valid:boolean)=>setInvalid(old=>old[key]===!valid?old:{...old,[key]:!valid});}
 useLayoutEffect(()=>{const liveNames=Object.fromEntries(Object.entries(names).filter(([name])=>Object.hasOwn(properties,name))),liveInvalid=Object.fromEntries(Object.entries(invalid).filter(([key])=>Object.hasOwn(properties,key.split(':')[0])));if(JSON.stringify(liveNames)!==JSON.stringify(names))setNames(liveNames);if(JSON.stringify(liveInvalid)!==JSON.stringify(invalid))setInvalid(liveInvalid);report(liveNames,liveInvalid);},[JSON.stringify(names),JSON.stringify(invalid),JSON.stringify(properties)]);
 useEffect(()=>()=>{callbacks.current.onValidity?.(true);callbacks.current.onIssues?.([]);},[]);
 const update=(key:string,next:Schema)=>onChange({...value,properties:{...properties,[key]:next}});
 if(value.type&&value.type!=='object'||typeof properties!=='object'||Array.isArray(properties))return <p className={styles.hint}>已有的高级结构已保留，请在高级设置中编辑。</p>;
 return <div className={styles.schemaDefinition}>{Object.entries(properties).map(([name,schema])=>{
  if(!schema||typeof schema!=='object'||Array.isArray(schema))return <p key={name} className={styles.hint}>{name} 使用高级结构，原设置已保留。</p>;
  const raw=names[name]??name,issue=raw!==name?nameError(name,raw):'';
  return <section className={styles.fieldList} key={name}><div className={styles.fieldTitle}><strong>{schema.title||name}</strong><button className={styles.linkButton} onClick={()=>{const next={...properties};delete next[name];onChange({...value,properties:next,required:value.required?.filter(key=>key!==name)});}}>移除字段</button></div>
   <label className={styles.field}><span>标题</span><input aria-label={`${label}字段${name}显示标题`} value={schema.title||''} placeholder="填写用户看得懂的标题" onChange={e=>update(name,{...schema,title:e.target.value})}/></label>
   <label className={styles.field}><span>字段名称</span><input aria-label={`${label}字段${name}名称`} value={raw} aria-invalid={!!issue} onChange={e=>draftName(name,e.target.value)} onBlur={e=>{
    const entered=e.target.value,next=entered.trim();if(nameError(name,entered)){draftName(name,entered);return;}
    if(next===name){const remaining={...names};delete remaining[name];setNames(remaining);report(remaining);onChange(value);return;}
    // Keep the attempted name until the parent accepts the schema change.
    draftName(name,entered);const renamed=Object.fromEntries(Object.entries(properties).map(([key,field])=>[key===name?next:key,field]));onChange({...value,properties:renamed,required:value.required?.map(key=>key===name?next:key)});
   }}/>{issue&&<small className={styles.errorText} role="alert">{issue}</small>}</label>
   <label className={styles.field}><span>类型</span><select aria-label={`${label}字段${name}类型`} value={Array.isArray(schema.type)&&allowNullable?schema.type.find(t=>t!=='null')||'null':Array.isArray(schema.type)?'__advanced':schema.type||'string'} onChange={e=>update(name,{...schema,type:allowNullable&&Array.isArray(schema.type)&&schema.type.includes('null')?[e.target.value,'null']:e.target.value,...(e.target.value==='array'&&!schema.items?{items:{type:'string'}}:{})})}>{Array.isArray(schema.type)&&<option value="__advanced">已保留多类型声明</option>}{types.map(type=><option key={type} value={type}>{typeLabel(type)}</option>)}</select></label>
   {allowNullable&&<label className={styles.check}><input type="checkbox" checked={Array.isArray(schema.type)&&schema.type.includes('null')} onChange={e=>{const base=Array.isArray(schema.type)?schema.type.filter(t=>t!=='null')[0]||'string':schema.type||'string';update(name,{...schema,type:e.target.checked?[base,'null']:base});}}/>允许空值（未命中的分支）</label>}
   <label className={styles.check}><input type="checkbox" checked={!!value.required?.includes(name)} onChange={e=>onChange({...value,required:e.target.checked?[...new Set([...(value.required||[]),name])]:(value.required||[]).filter(key=>key!==name)})}/>必填</label>
   <details className={styles.details}><summary>默认值与填写说明</summary><label className={styles.field}><span>填写说明</span><input aria-label={`${label}字段${name}说明`} value={schema.description||''} onChange={e=>update(name,{...schema,description:e.target.value})}/></label><label className={styles.check}><input type="checkbox" checked={Object.hasOwn(schema,'default')} onChange={e=>{const next={...schema};if(e.target.checked)next.default=literalDefault(String(schema.type||'string'));else delete next.default;update(name,next);}}/>使用默认值</label>
    {Object.hasOwn(schema,'default')&&<ValueField name="default" schema={schema} value={schema.default} onChange={next=>update(name,{...schema,default:next})} variables={[]} required={false} allowReferences={false} onValidity={childValidity(`${name}:default`)}/>}
   </details>
   {schema.type==='object'&&depth<3&&<details className={styles.details}><summary>对象内的字段</summary><SchemaDefinition allowNullable={allowNullable} label={`${label}.${name}`} value={schema} depth={depth+1} onChange={next=>update(name,next)} onValidity={childValidity(`${name}:object`)}/></details>}
   {schema.type==='array'&&<label className={styles.field}><span>列表项类型</span><select value={typeof schema.items?.type==='string'?schema.items.type:'string'} onChange={e=>update(name,{...schema,items:{...schema.items,type:e.target.value}})}>{types.map(type=><option key={type} value={type}>{typeLabel(type)}</option>)}</select></label>}
   {schema.type==='array'&&schema.items?.type==='object'&&depth<3&&<details className={styles.details}><summary>每项有哪些字段</summary><SchemaDefinition allowNullable={allowNullable} label={`${label}.${name}列表项`} value={schema.items} depth={depth+1} onChange={items=>update(name,{...schema,items})} onValidity={childValidity(`${name}:items`)}/></details>}
  </section>;
 })}<button onClick={()=>{let i=1;while(Object.hasOwn(properties,`field_${i}`))i++;onChange({...value,type:value.type||'object',properties:{...properties,[`field_${i}`]:{type:'string',title:''}}});}}>＋ 添加{label}字段</button><p className={styles.hint}>所有嵌套结构、默认值和高级约束会保留。系统与当前用户身份由后台提供，不在这里重新定义。</p></div>;
}
