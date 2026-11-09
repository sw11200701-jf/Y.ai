'use client';
import {useRef} from 'react';
import styles from './studio.module.css';

export function LocalPromptEditor({title,value,names,onChange,system=false}:{title:string;value:string;names:string[];onChange:(text:string)=>void;system?:boolean}){
 const input=useRef<HTMLTextAreaElement>(null);
 return <section className={styles.promptBlock}><div className={styles.promptHead}><strong>{title}</strong>{<select aria-label={`${title}插入变量`} value="" disabled={!names.length} onChange={e=>{
  const name=e.target.value;if(!names.includes(name))return;const start=input.current?.selectionStart??value.length,end=input.current?.selectionEnd??start,token=`{{${name}}}`;
  onChange(value.slice(0,start)+token+value.slice(end));requestAnimationFrame(()=>{input.current?.focus();input.current?.setSelectionRange(start+token.length,start+token.length);});
 }}><option value="">插入本节点输入</option>{names.map(name=><option key={name} value={name}>{name}</option>)}</select>}</div><textarea ref={input} aria-label={title} value={value} rows={system?4:7} maxLength={16000} onChange={e=>onChange(e.target.value)}/><small>{system?'定义角色与规则；原始文档资料应由用户消息传入。':'变量使用 {{输入参数名}}，仅引用上表中已声明的本节点输入。'}</small></section>;
}
