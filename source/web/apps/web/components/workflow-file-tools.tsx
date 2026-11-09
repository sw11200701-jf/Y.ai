'use client';
import {useEffect,useRef} from 'react';
import {currentIdentityEpoch} from '@/lib/api';
import type {WorkflowDefinition} from '@/lib/workflow/types';
import type {WorkflowFileClient} from '@/lib/workflow-file-client';
import WorkflowFileImport from './workflow-file-import';
import WorkflowFileExport from './workflow-file-export';

type Props={mode:'import'|'export'|null;client:WorkflowFileClient;workflow?:WorkflowDefinition|null;savedWorkflow?:WorkflowDefinition|null;beforeCreate:()=>boolean|Promise<boolean>;onCreatingChange?:(value:boolean)=>void;onCreated:(workflow:WorkflowDefinition)=>void|Promise<void>;onClose:()=>void};
/** Owns the original File only while its inspection dialog is mounted. */
export default function WorkflowFileTools({mode,client,workflow,savedWorkflow,beforeCreate,onCreatingChange,onCreated,onClose}:Props){
 const selected=useRef<File|undefined>(undefined),generation=useRef(0),alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;generation.current++;selected.current=undefined;onCreatingChange?.(false);};},[]);
 useEffect(()=>{generation.current++;selected.current=undefined;},[mode]);
 const clear=()=>{generation.current++;selected.current=undefined;onClose();};
 useEffect(()=>{window.addEventListener('zhangpu:permissions-changed',clear);return()=>window.removeEventListener('zhangpu:permissions-changed',clear);},[onClose]);
 const current=(ticket:number,epoch:number)=>alive.current&&generation.current===ticket&&currentIdentityEpoch()===epoch;
 return <>
  {mode==='import'&&<WorkflowFileImport open maxBytes={client.maxBytes} onClose={clear} beforeCreate={beforeCreate}
   onPreview={async(file,signal)=>{selected.current=file;const ticket=generation.current,epoch=currentIdentityEpoch();const value=await client.preview(file,{},signal);if(!current(ticket,epoch)||selected.current!==file)throw new DOMException('预览已失效','AbortError');return value;}}
   onCheck={async(preview,bindings,signal)=>{const file=selected.current;if(!file)throw Error('请重新选择并检测文件。');const ticket=generation.current,epoch=currentIdentityEpoch();const value=await client.preview(file,bindings,signal);if(!current(ticket,epoch)||selected.current!==file)throw new DOMException('预览已失效','AbortError');if(value.inputHash!==preview.inputHash)throw Error('文件检查结果已变化，请重新检测文件。');return value;}}
   onCreate={async(preview,bindings,name,key)=>{const file=selected.current;if(!file)throw Error('请重新选择并检测文件。');onCreatingChange?.(true);try{return await client.confirm(file,preview,bindings,name,key);}finally{onCreatingChange?.(false);}}}
   onCreated={onCreated}/>}
  {mode==='export'&&workflow&&<WorkflowFileExport open onClose={clear} formats={client.formats}
   sources={[{key:'editor',label:'当前编辑稿（包含未保存修改）'},...(workflow.id&&savedWorkflow?[{key:'saved',label:'服务器保存版（检查时读取并固定来源摘要）'}]:[])]}
   models={[...exportModels(workflow,'editor'),...(savedWorkflow?exportModels(savedWorkflow,'saved'):[])]}
   onPrepare={(selection,signal)=>client.prepareExport(selection,workflow,signal,savedWorkflow||undefined)} onDownload={client.download}/>}
 </>;
}
function exportModels(workflow:WorkflowDefinition,sourceKey:string){const values=new Map<string,string>();const walk=(value:WorkflowDefinition)=>{for(const node of value.nodes){if(typeof node.config?.model_config_id==='string')values.set(node.config.model_config_id,node.ui?.label||node.id);if(node.config?.body?.nodes)walk(node.config.body);}};walk(workflow);return [...values].map(([id,label])=>({id,label,sourceKey}));}
