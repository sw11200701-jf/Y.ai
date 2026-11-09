'use client';
import {useState} from 'react';
import {Button,Input,Space,Tag} from 'antd';
import {Json} from '@/lib/api';
import {JsonView} from './shared';
type Props={chunk:Json;sequence:number;canEdit:boolean;disabled:boolean;draft?:string;onDraft:(text:string)=>void;onSave:()=>void;onCancel:()=>void;onToggle:()=>void;onAdd?:()=>void;onDelete?:()=>void};
export default function CustomerChunkCard({chunk,sequence,canEdit,disabled,draft,onDraft,onSave,onCancel,onToggle,onAdd,onDelete}:Props){
 const [editing,setEditing]=useState(false),[expanded,setExpanded]=useState(false);
 const body=typeof chunk.display_text==='string'?chunk.display_text:String(chunk.text||'');
 const long=body.length>220||body.split('\n').length>6;
 return <article className="chunk-card" style={{padding:20}}>
  <div className="panel-heading"><h4 style={{margin:0,fontSize:16}}>{sequence}. {chunk.topic||'知识切片'}</h4><Space>{chunk.enabled===false&&<Tag>已停用</Tag>}{canEdit&&!editing&&<Button size="small" disabled={disabled} onClick={()=>setEditing(true)}>编辑</Button>}</Space></div>
  {chunk.summary&&<p className="subtle" style={{margin:'8px 0 12px'}}>{chunk.summary}</p>}
  {editing?<><Input.TextArea maxLength={32700} aria-label={`切片${sequence}正文`} autoSize={{minRows:5,maxRows:18}} disabled={!canEdit||disabled} value={draft??body} onChange={e=>onDraft(e.target.value)}/><Space style={{marginTop:12}}><Button type="primary" disabled={!canEdit||disabled||draft===undefined||!draft.trim()} onClick={onSave}>保存</Button><Button onClick={()=>{onCancel();setEditing(false);}}>取消</Button></Space></>:<><div style={{whiteSpace:'pre-wrap',lineHeight:1.8,...(!expanded&&long?{display:'-webkit-box',WebkitLineClamp:5,WebkitBoxOrient:'vertical' as const,overflow:'hidden'}:{})}}>{body}</div>{long&&<Button type="link" size="small" style={{padding:0,marginTop:4}} onClick={()=>setExpanded(v=>!v)}>{expanded?'收起':'展开全文'}</Button>}</>}
  <details style={{marginTop:12,fontSize:13}}><summary className="subtle">来源与操作</summary><p>{chunk.source_label||'来源定位见详情'}</p>{canEdit&&<Button size="small" disabled={disabled||editing} onClick={onToggle}>{chunk.enabled===false?'启用切片':'停用切片'}</Button>}{canEdit&&onAdd&&<Button size="small" disabled={disabled||editing} onClick={onAdd}>基于此来源新增知识</Button>}{canEdit&&onDelete&&<Button danger size="small" disabled={disabled||editing} onClick={onDelete}>删除知识</Button>}<details style={{marginTop:8}}><summary>技术详情</summary><JsonView value={{locator:chunk.locator,source_ref:chunk.source_ref,text_sha256:chunk.text_sha256,original_text:chunk.text}}/></details></details>
 </article>;
}
