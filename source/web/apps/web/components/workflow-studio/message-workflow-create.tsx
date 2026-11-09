'use client';
import { useState } from 'react';
import { messageKinds } from '@/lib/workflow/messages';
import { newMessageWorkflow } from '@/lib/workflow/message-workflow';
import type { MessageKind, NodeRegistry, WorkflowDefinition } from '@/lib/workflow/types';
export function MessageWorkflowCreate({registry,disabled,onCreate}:{registry:NodeRegistry|null;disabled?:boolean;onCreate:(workflow:WorkflowDefinition)=>void}) {
 const [kind,setKind]=useState<MessageKind>('text'),[error,setError]=useState('');
 const available=(type:MessageKind)=>registry?.items.some(node=>node.type===`trigger_${type}`&&node.status==='supported');
 const output=registry?.items.some(node=>node.type==='output'&&node.status!=='unavailable');
 return <div><label>消息入口 <select aria-label="新消息工作流类型" disabled={disabled||!registry} value={kind} onChange={event=>{setKind(event.target.value as MessageKind);setError('');}}>{messageKinds.map(item=><option key={item.type} value={item.type} disabled={!available(item.type)}>{item.label}{!available(item.type)?'（未开放）':''}</option>)}</select></label><button disabled={disabled||!available(kind)||!output} onClick={()=>{try{if(!registry)throw Error('能力目录尚未连接');onCreate(newMessageWorkflow(registry,kind));}catch(error){setError((error as Error).message);}}}>新建消息工作流</button>{error&&<p role="alert">{error}</p>}</div>;
}
