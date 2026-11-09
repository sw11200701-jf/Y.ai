'use client';
import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Input,InputNumber,Popover,Select,Slider,Switch} from 'antd';
import ModelPicker from '../model-picker';
import {modelIssues,modelParameterAllowed,parameterLabels,parameterRange,type ModelChoice} from '@/lib/model-catalog';
import type {MessageDraft,ObjectValue,VariableOption,WorkflowDefinition,WorkflowNode} from '@/lib/workflow/types';
import {JsonEditor,ValueField} from './schema-fields';
import styles from './studio.module.css';
import { SchemaDefinition } from './schema-definition';
import {NodeInputTable} from './node-input-table';
import {localPromptIssues,promptInputSchema} from '@/lib/workflow/local-prompts';
import { VisionInputs } from './vision-inputs';
import { visionInputIssues, visionSources } from '@/lib/workflow/vision-input';

const isObject=(value:any):value is ObjectValue=>Boolean(value)&&typeof value==='object'&&!Array.isArray(value);


function Prompt({title,value,variables,bindings,onInsert,onChange}:{title:string;value:string;variables:VariableOption[];bindings:ObjectValue;onInsert:(ref:VariableOption)=>string;onChange:(text:string)=>void}){
 const input=useRef<HTMLTextAreaElement>(null);const tokens=[...value.matchAll(/\{\{\s*([\w.]+)\s*\}\}/g)].map(m=>m[1]);
 return <section className={styles.promptBlock}><div className={styles.promptHead}><strong>{title}</strong><Select aria-label={`${title}插入变量`} size="small" value={undefined} placeholder="插入本节点输入" disabled={Boolean(bindings?.$ref)||!variables.length} options={variables.map(v=>({value:v.ref,label:v.label}))} showSearch optionFilterProp="label" onChange={ref=>{const variable=variables.find(v=>v.ref===ref);if(!variable)return;const alias=onInsert(variable),token=`{{${alias}}}`,start=input.current?.selectionStart??value.length,end=input.current?.selectionEnd??start;onChange(value.slice(0,start)+token+value.slice(end));}}/></div>
 <textarea ref={input} aria-label={title} rows={4} maxLength={16000} value={value} placeholder={title.startsWith('System')?'填写固定角色、回答要求和边界。输入内容请放入User。':'输入任务，可插入本节点已声明的输入。'} onChange={e=>onChange(e.target.value)}/>
 {tokens.length>0&&<div className={styles.promptTokens}>{[...new Set(tokens)].map(token=>{const binding=bindings?.[token.split('.')[0]];return <span key={token} className={styles.promptToken} title={binding?.$ref?'已绑定上游变量':'请检查变量字典'}>⌁ {variables.find(v=>v.ref===binding?.$ref)?.label||token}</span>;})}</div>}
 {!variables.length&&<small>请先在输入表声明参数，再插入提示词。</small>}
 </section>;
}

export function StructuredOutput({value,onChange}:{value?:ObjectValue;onChange:(value:ObjectValue)=>void}){
 const schema=value?.schema??{type:'object',properties:{},required:[]};
 return <SchemaDefinition label="输出" value={schema} onChange={schema=>onChange({...value,schema})}/>;
}

export default function LlmEditor({node,variables,onChange,onValidity,canManageModels=false,onManage,workflow,currentMessage,visionContract=false}:{workflow?:WorkflowDefinition;currentMessage?:MessageDraft;visionContract?:boolean;node:WorkflowNode;variables:VariableOption[];onChange:(fields:ObjectValue)=>void;onValidity:(valid:boolean)=>void;canManageModels?:boolean;onManage?:()=>void}){
 const config=node.config||{},inputs=node.inputs||{};const [model,setModel]=useState<ModelChoice>();const [modelChanged,setModelChanged]=useState(false);const [advancedValid,setAdvancedValid]=useState({config:true,inputs:true,bindings:true});const latest=useRef({config,inputs});latest.current={config,inputs};const validity=useRef(onValidity);validity.current=onValidity;
 const formatDrafts=useRef<Record<string,ObjectValue>>({});
 const patchConfig=(patch:ObjectValue)=>onChange({config:{...latest.current.config,...patch}});
 function chooseFormat(type:string){
  const saved=latest.current.config.response_format;if(isObject(saved)&&typeof saved.type==='string')formatDrafts.current[saved.type]=saved;
  patchConfig({response_format:formatDrafts.current[type]||(type==='json_schema'?{type,name:'result',schema:{type:'object',properties:{},required:[]}}:{type})});
 }
 const removeParam=(key:string)=>{const parameters={...config.model_parameters};delete parameters[key];patchConfig({model_parameters:parameters});};
 const setParam=(key:string,value:any)=>patchConfig({model_parameters:{...config.model_parameters,[key]:value}});
 const invalidBindings=inputs.variables!==undefined&&!isObject(inputs.variables);const bindings=invalidBindings?{$ref:'invalid-configuration'}:inputs.variables||{};const messages=Array.isArray(config.prompt_messages)?config.prompt_messages:undefined;
 const resolvedModel=model&&model.id===config.model_config_id&&(!config.model_config_version||model.config_version===config.model_config_version)?model:undefined;
 const issues=modelIssues(resolvedModel,{model_parameters:config.model_parameters,context_budget:config.context_budget,bindings:config.bindings,response_format:config.response_format});
 issues.push(...visionInputIssues(config,inputs,resolvedModel,visionContract,visionSources(workflow,variables),currentMessage));
 if(invalidBindings)issues.push('已保存变量字典格式异常，请在高级兼容中处理；原值已保留。');
 if(config.prompt_messages!==undefined&&!messages)issues.push('已保存角色消息格式异常，请在高级兼容中处理。');
 if(messages&&(!messages.length||messages.length>16||!messages.some((m:any)=>m?.role==='user')||messages.some((m:any,i:number)=>!['system','user','assistant'].includes(m?.role)||m.role==='system'&&i!==0||typeof m?.content!=='string'||!m.content.length||m.content.length>16000)))issues.push('角色消息需包含User，System仅可位于首条；每条消息需1至16000字。');
 if(inputs.prompt!==undefined&&typeof inputs.prompt!=='string'&&(!isObject(inputs.prompt)||typeof inputs.prompt.$ref!=='string'))issues.push('已保存User输入格式需在高级兼容中明确处理。');
 if(!config.model_config_id)issues.push('请选择可用模型。');
 if(!messages&&!inputs.prompt)issues.push('请填写User用户提示词或绑定上游输入。');
 if(config.system_prompt!==undefined&&typeof config.system_prompt!=='string')issues.push('旧系统提示词格式需要在高级兼容中明确处理。');
 if(messages?.some((m:any)=>typeof m?.content!=='string'))issues.push('已保存消息内容不是文本，请在高级兼容中明确处理。');
 if(messages&&config.system_prompt!==undefined)issues.push('消息提示词与旧系统提示词同时存在，请在高级兼容中明确保留一种模式。');
 if(messages&&inputs.prompt)issues.push('消息提示词模式保留了旧用户输入，请在高级兼容中明确处理，避免内容被忽略。');
 const texts=messages?messages.map((m:any)=>m?.content):[config.system_prompt||'',typeof inputs.prompt==='string'?inputs.prompt:''];
 if(!bindings.$ref)for(const text of texts)if(typeof text==='string')for(const match of text.matchAll(/\{\{\s*([\w.]+)\s*\}\}/g))if(!Object.hasOwn(bindings,match[1].split('.')[0]))issues.push(`提示词变量${match[1]}尚未绑定。`);
 for(const [i,text] of texts.entries())if(typeof text==='string')issues.push(...localPromptIssues(text,Object.keys(bindings),messages?messages[i]?.role==='system':i===0));
 const issueKey=issues.join('\n');
 useEffect(()=>{validity.current(advancedValid.config&&advancedValid.inputs&&advancedValid.bindings&&!issueKey);},[advancedValid,issueKey]);
 useEffect(()=>()=>validity.current(true),[]);
 function insert(variable:VariableOption){return variable.ref;}
 const localInputs=Object.keys(bindings).filter(name=>name!=='$ref').map(name=>({ref:name,label:name,type:'any'}));
 function promptChange(index:number,text:string){if(messages){patchConfig({prompt_contract:'local_inputs_v1',prompt_messages:messages.map((m:any,i:number)=>i===index?{...m,content:text}:m)});}else if(index===0)patchConfig({prompt_contract:'local_inputs_v1',system_prompt:text});else onChange({config:{...latest.current.config,prompt_contract:'local_inputs_v1'},inputs:{...latest.current.inputs,prompt:text}});}
 const parameterControls=<div className={styles.parameterPanel}><strong>模型参数</strong><p>只发送已开启且受当前模型支持的参数。切换模型不会静默移除旧值。</p>{Object.entries(parameterLabels).map(([key,label])=>{const supported=modelParameterAllowed(resolvedModel,key),value=config.model_parameters?.[key],range=model?parameterRange(key,model):undefined;return <div className={styles.parameterItem} key={key}><div className={styles.promptHead}><span>{label}</span><Switch size="small" checked={value!==undefined} disabled={!supported} onChange={checked=>checked?setParam(key,key==='stop'?[]:key==='temperature'?0.2:key==='top_p'?1:key==='max_tokens'?Math.min(1000,model!.max_output_tokens):0):removeParam(key)}/></div>
 {key==='stop'?<Select mode="tags" aria-label="停止词" disabled={!supported||value===undefined} value={Array.isArray(value)?value:[]} options={[]} maxCount={4} tokenSeparators={['\n']} onChange={next=>setParam(key,next)}/>:<div className={styles.parameterRow}>{key!=='seed'&&<Slider aria-label={`${label}滑块`} disabled={!supported||value===undefined} {...range} value={typeof value==='number'?value:range?.min} onChange={next=>setParam(key,next)}/>}<InputNumber aria-label={label} disabled={!supported||value===undefined} {...range} value={value} onChange={next=>next!==null&&setParam(key,next)}/></div>}
 {!supported&&<small>{model?'当前模型或版本公开推理声明未放行此参数。':'选择可用模型后查看支持参数。'}{value!==undefined&&<Button type="link" size="small" onClick={()=>removeParam(key)}>明确移除旧参数</Button>}</small>}</div>;})}</div>;
 return <div className={styles.llmEditor}><section className={styles.editorSection}><div className={styles.promptHead}><h3>模型</h3><Popover trigger="click" placement="leftTop" content={parameterControls}><Button size="small">参数 ⚙</Button></Popover></div>
 <ModelPicker value={config.model_config_id} version={config.model_config_version} onResolved={setModel} canManage={canManageModels} onManage={onManage} onChange={next=>{setModelChanged(true);patchConfig({model_config_id:next.id,model_config_version:next.config_version});}}/>
 {modelChanged&&<small>模型已切换，原参数和提示词已保留；请检查新模型的范围。</small>}
 {issues.length>0&&<Alert type="warning" message="配置需要明确处理" description={[...new Set(issues)].join(' ')}/>}
 </section>{!invalidBindings&&!bindings.$ref&&<section className={styles.editorSection}><h3>输入</h3><NodeInputTable schema={promptInputSchema(bindings,config.prompt_input_schema)} value={bindings} variables={variables} onValidity={valid=>setAdvancedValid(current=>current.bindings===valid?current:{...current,bindings:valid})} onChange={(schema,variables)=>onChange({config:{...latest.current.config,prompt_contract:'local_inputs_v1',prompt_input_schema:schema},inputs:{...latest.current.inputs,variables}})}/></section>}<section className={styles.editorSection}><h3>提示词</h3>{messages?messages.map((message:any,index:number)=><Prompt key={index} title={message?.role==='system'?'System 系统提示词':message?.role==='user'?'User 用户提示词':'Assistant 示例回复'} value={typeof message?.content==='string'?message.content:''} variables={localInputs} bindings={bindings} onInsert={insert} onChange={text=>promptChange(index,text)}/>):<><Prompt title="System 系统提示词" value={typeof config.system_prompt==='string'?config.system_prompt:''} variables={localInputs} bindings={bindings} onInsert={insert} onChange={text=>promptChange(0,text)}/>{inputs.prompt&&typeof inputs.prompt==='object'?<div className={styles.promptBlock}><strong>User 用户提示词</strong><span className={styles.promptToken}>⌁ {variables.find(v=>v.ref===inputs.prompt.$ref)?.label||'已保存的上游引用'}</span><Button size="small" onClick={()=>onChange({inputs:{...inputs,prompt:''}})}>明确改为文本提示词</Button></div>:<Prompt title="User 用户提示词" value={typeof inputs.prompt==='string'?inputs.prompt:''} variables={localInputs} bindings={bindings} onInsert={insert} onChange={text=>promptChange(1,text)}/>}</>}</section>
 <details className={styles.details}><summary>图片、上下文与预算</summary><VisionInputs config={config} inputs={inputs} model={resolvedModel} contract={visionContract} workflow={workflow} variables={variables} message={currentMessage} onConfig={patchConfig} onInputs={inputs=>onChange({inputs})}/><section className={styles.editorSection}><h3>上下文来源</h3>{inputs.context!==undefined&&!inputs.context?.$ref&&<small>已有固定上下文已保留；选用下方来源将明确替换它。</small>}<Select aria-label="上下文来源" allowClear placeholder="不附加上下文" value={inputs.context?.$ref} options={[...(inputs.context?.$ref&&!variables.some(v=>v.ref===inputs.context.$ref)?[{value:inputs.context.$ref,label:'已保存来源（检查连线后可用）'}]:[]),...variables.map(v=>({value:v.ref,label:v.label}))]} onChange={ref=>{const next={...inputs};if(ref)next.context={$ref:ref,...(variables.find(v=>v.ref===ref)?.optional?{optional:true}:{})};else delete next.context;onChange({inputs:next});}}/>
 <label className={styles.parameterRow}>上下文预算<InputNumber aria-label="上下文预算" min={512} max={model?.context_window||32768} value={config.context_budget??8192} onChange={value=>value!==null&&patchConfig({context_budget:value})}/></label></section></details>
 <section className={styles.editorSection}><h3>输出格式</h3><small>切换格式会保留本次编辑草稿，切回可恢复；保存只提交当前格式。</small><Select aria-label="输出格式" value={config.response_format?.type||'text'} options={[{value:'text',label:'文本'},{value:'json_object',label:'JSON对象',disabled:!model?.capabilities.json_object},{value:'json_schema',label:'结构化字段',disabled:!model?.capabilities.json_schema}]} onChange={chooseFormat}/>{!model?.capabilities.json_schema&&<small>结构化字段需要模型明确支持JSON Schema；已保存配置不会被自动替换。</small>}{config.response_format?.type==='json_schema'&&<StructuredOutput value={config.response_format} onChange={response_format=>patchConfig({response_format})}/>}</section>
 <details className={styles.details}><summary>高级兼容与原始结构</summary><JsonEditor label="模型节点配置" value={config} onChange={config=>onChange({config})} onValidity={valid=>setAdvancedValid(current=>current.config===valid?current:{...current,config:valid})}/><JsonEditor label="模型节点输入" value={inputs} onChange={inputs=>onChange({inputs})} onValidity={valid=>setAdvancedValid(current=>current.inputs===valid?current:{...current,inputs:valid})}/></details></div>;
}
