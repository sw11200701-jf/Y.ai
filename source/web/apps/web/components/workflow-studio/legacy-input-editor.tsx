'use client';
import {useEffect,useState} from 'react';
import type {Schema,VariableOption,WorkflowNode} from '@/lib/workflow/types';
import {legacyInputPatch,legacyInputSchema} from '@/lib/workflow/legacy-inputs';
import {NodeInputTable} from './node-input-table';

export function LegacyInputEditor({node,service,variables,onChange,onValidity}:{node:WorkflowNode;service:Schema;variables:VariableOption[];onChange:(patch:Partial<WorkflowNode>)=>void;onValidity:(valid:boolean)=>void}){
 const [issue,setIssue]=useState(''),[valid,setValid]=useState(true);
 const schema=legacyInputSchema(node,service,variables);
 useEffect(()=>{onValidity(valid&&!issue);},[valid,issue,onValidity]);
 return <section><h3>输入</h3><NodeInputTable schema={schema} value={node.inputs||{}} variables={variables} onValidity={setValid} onChange={(next,values)=>{
  const result=legacyInputPatch(node,schema,next,values,service);setIssue(result.issue||'');if(result.patch)onChange(result.patch);
 }}/>{issue&&<p role="alert">{issue}</p>}</section>;
}
