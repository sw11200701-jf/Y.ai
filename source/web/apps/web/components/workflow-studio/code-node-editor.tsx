'use client';
import {useEffect,useState} from 'react';
import type {ObjectValue,Schema,VariableOption,WorkflowNode} from '@/lib/workflow/types';
import {NodeInputTable} from './node-input-table';
import {SchemaDefinition} from './schema-definition';
import {JsonEditor} from './schema-fields';
import {promptInputSchema} from '@/lib/workflow/local-prompts';
import styles from './studio.module.css';

export function CodeNodeEditor({node,variables,onChange,onValidity}:{node:WorkflowNode;variables:VariableOption[];onChange:(p:Partial<WorkflowNode>)=>void;onValidity:(valid:boolean)=>void}){
 const config=node.config||{},[invalid,setInvalid]=useState<Record<string,boolean>>({});
 const check=(key:string)=>(ok:boolean)=>setInvalid(old=>old[key]===!ok?old:{...old,[key]:!ok});
 useEffect(()=>{onValidity(!Object.values(invalid).some(Boolean));},[JSON.stringify(invalid)]);
 useEffect(()=>()=>onValidity(true),[]);
 const codeKey=Object.hasOwn(config,'code')?'code':Object.hasOwn(config,'source')?'source':'code';
 return <div className={styles.nodeSettings}><section><h3>输入</h3><NodeInputTable schema={node.input_schema||promptInputSchema(node.inputs||{})} value={node.inputs||{}} variables={variables} onValidity={check('inputs')} onChange={(input_schema:Schema,inputs:ObjectValue)=>onChange({input_schema,inputs})}/></section><section><h3>代码</h3><label className={styles.field}>语言<select aria-label="代码语言" value={config.language||''} onChange={e=>onChange({config:{...config,language:e.target.value}})}><option value="">选择语言</option>{config.language&&!['python','javascript'].includes(config.language)&&<option value={config.language}>已保存：{config.language}</option>}<option value="python">Python</option><option value="javascript">JavaScript</option></select></label><textarea className={styles.codeEditor} aria-label="代码编辑器" spellCheck={false} rows={18} value={config[codeKey]??''} onChange={e=>onChange({config:{...config,[codeKey]:e.target.value}})}/><p role="status">代码可保存。当前环境尚未提供合格的隔离执行器，运行时会明确阻止执行。</p></section><section><h3>输出</h3><NodeInputTable label="输出" definitionsOnly schema={node.output_schema||{type:'object',properties:{},additionalProperties:false}} value={{}} variables={[]} onChange={output_schema=>onChange({output_schema})} onValidity={check('outputs')}/></section><details className={styles.details}><summary>高级兼容配置</summary><JsonEditor label="代码节点配置" value={config} onChange={config=>onChange({config})} onValidity={check('config')}/></details></div>;
}
