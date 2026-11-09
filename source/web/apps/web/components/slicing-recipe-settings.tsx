'use client';
import {useEffect,useRef,useState} from 'react';
import {currentIdentityEpoch,list,request} from '@/lib/api';
import {builtinPatch,builtinDirty,type BuiltinView,type BuiltinDraft,type RecipeOptions} from '@/lib/builtin-agents';
import {workflowView,validateRecipeDraft,type SlicingWorkflowView} from '@/lib/slicing-workflow';
import {LocalPromptEditor} from './workflow-studio/local-prompt-editor';

const titles:Record<string,string>={plan:'任务计划与上下文补读',slice:'语义切片',audit:'两轮独立审核',repair:'有限返修',merge:'跨组归并',candidates:'业务关系候选',relation:'逐对关系审核',finalaudit:'最终独立审核'};
const optionLabels={max_context_rounds:'每组上下文补读上限',max_repair_rounds:'每组语义返修上限',max_relation_pairs:'整项任务关系候选上限',source_group_bytes:'每组完整来源字节预算'};
export function SlicingRecipeSettings({view,draft,onChange,disabled}:{view:BuiltinView;draft:BuiltinDraft;onChange:(draft:BuiltinDraft)=>void;disabled:boolean}){
 const [bases,setBases]=useState<any[]>([]),[directoryError,setDirectoryError]=useState('');
 const [preview,setPreview]=useState<{key:string;value:SlicingWorkflowView}>(),[previewError,setPreviewError]=useState(''),[loading,setLoading]=useState(false);
 const serial=useRef(0),pending=useRef<AbortController|undefined>(undefined);
 const key=JSON.stringify([view.id,view.config.revision,draft]);const keyRef=useRef(key);keyRef.current=key;
 useEffect(()=>{let live=true;const abort=new AbortController();list('/knowledge-bases',{signal:abort.signal}).then(items=>{if(live)setBases(items);}).catch(()=>{if(live&&!abort.signal.aborted)setDirectoryError('知识库目录读取失败，已保存范围仍保留。');});return()=>{live=false;abort.abort();};},[]);
 useEffect(()=>{serial.current++;pending.current?.abort();setLoading(false);setPreviewError('');return()=>{serial.current++;pending.current?.abort();};},[key]);
 const defaults=view.portable_recipe;if(!defaults)return null;
 const selected=draft.recipe_version===defaults.recipe_version,prompts=draft.stage_prompts||defaults.stage_prompts,options=draft.recipe_options||defaults.recipe_options;
 let validation='';try{validateRecipeDraft(draft,view.workflow);}catch(e){validation=e instanceof Error?e.message:'节点配置无效';}
 const current=preview?.key===key?preview.value:!builtinDirty(view,draft)?view.workflow:undefined;
 const schema=view.workflow?.recipe_option_schema;
 async function readPreview(){
  if(disabled||loading||validation)return;
  const ticket=++serial.current,epoch=currentIdentityEpoch(),selectedKey=key;
  pending.current?.abort();const controller=new AbortController();pending.current=controller;setLoading(true);setPreviewError('');
  const valid=()=>!controller.signal.aborted&&ticket===serial.current&&epoch===currentIdentityEpoch()&&selectedKey===keyRef.current;
  try{const payload=builtinPatch(view,draft);const result=workflowView(await request(`/builtin-agents/${encodeURIComponent(view.id)}/workflow-preview`,{method:'POST',body:JSON.stringify(payload),signal:AbortSignal.any([controller.signal,AbortSignal.timeout(15000)])}));if(result.agent_id!==view.id||result.revision!==view.config.revision)throw Error('工作流预览版本不匹配，请重新读取配置。');if(valid())setPreview({key:selectedKey,value:result});}
  catch(e){if(valid())setPreviewError(e instanceof Error?e.message:'无法读取有效提示词，请重试。');}
  finally{if(valid())setLoading(false);}
 }
 return <fieldset disabled={disabled} style={{border:0,padding:0,margin:'24px 0'}}>
  <h3>切片工作流</h3>
  <select disabled={view.id!=='builtin_knowledge_chunker'} aria-label="切片方案" value={draft.recipe_version||'knowledge-chunker-v1'} onChange={e=>onChange({...draft,recipe_version:e.target.value})}>
   <option value="knowledge-chunker-v1">原文片段与概述（兼容已有配置）</option><option value={defaults.recipe_version}>语义知识 · 八阶段审核与最新导出</option>
  </select>
  {selected&&<>
   <p className="hint">来源准备 → 计划与补读 → 语义切片 → 两轮独立审核 ↔ 有限返修 → 跨组归并 → 关系候选与逐对审核 → 最终知识及来源审核 → 保存新草稿。发布后才用于检索。</p>
   {view.workflow?.strategy&&<p>{view.workflow.strategy.policy}</p>}
   <h4>允许处理的知识库</h4>
   {view.id!=='builtin_knowledge_chunker'&&<p className="hint">留空时仅处理本次明确选择且有完整权限的文档；选定知识库可进一步限制处理范围。</p>}
   {directoryError&&<p role="alert">{directoryError}</p>}
   {[...new Set([...bases.map(v=>v.id),...(draft.source_scope||[])])].map(id=><label key={id} style={{display:'block'}}><input type="checkbox" checked={draft.source_scope?.includes(id)||false} onChange={e=>onChange({...draft,source_scope:e.target.checked?[...(draft.source_scope||[]),id]:(draft.source_scope||[]).filter(k=>k!==id)})}/>{bases.find(v=>v.id===id)?.name||`${id}（已保存范围）`}</label>)}
   {!bases.length&&!draft.source_scope?.length&&<p>当前目录没有可见知识库；建立并授权知识库后才能选择资料处理。</p>}
   <details style={{margin:'20px 0'}}><summary>阶段参数与预算边界</summary>
    {(Object.keys(optionLabels) as (keyof typeof optionLabels)[]).map(name=><label key={name} style={{display:'block',margin:12}}>{optionLabels[name]}<input type="number" aria-label={name} value={options[name]??''} min={schema?.[name]?.minimum??(name==='source_group_bytes'?256:0)} max={schema?.[name]?.maximum??(name==='source_group_bytes'?262144:name==='max_relation_pairs'?512:name==='max_context_rounds'?8:2)} step={1} onChange={e=>onChange({...draft,recipe_options:{...options,[name]:e.target.value===''?undefined:Number(e.target.value)} as RecipeOptions})}/></label>)}
    <p>参数范围来自后台节点契约。关系候选超过上限会停止，保留完整问题；设为0不会跳过应有检查。完整请求、输出与全部返修调用还受本次任务总预算限制。</p>
    <p>语义返修和JSON/证据结构纠正分别计数；结构纠正最多两次，全部计入模型调用上限。来源字节预算是传输分组大小，不是最终知识的切分长度。</p>
   </details>
   {validation&&<p role="alert">{validation}</p>}
   <label style={{display:'block',margin:'16px 0'}}>CSV副本策略<select aria-label="CSV副本策略" value={options.csv_variant||'original'} onChange={e=>onChange({...draft,recipe_options:{...options,csv_variant:e.target.value as 'original'|'excel_safe'}})}><option value="original">原值（存在公式风险时拒绝CSV）</option><option value="excel_safe">Excel安全副本（记录所有前缀变更）</option></select></label>
   <h4>逐节点提示词与执行逻辑</h4>
   <p>下方可编辑业务规则。来源权限、审核结构、智能体策略与防止原文指令干扰的规则由后台固定附加。预览只核对配置，不调用模型、不保存配置。</p>
   <button type="button" disabled={disabled||loading||!!validation} onClick={()=>void readPreview()}>{loading?'正在核对有效提示词…':'预览本次修改的有效提示词'}</button>
   {previewError&&<p role="alert">{previewError}</p>}
   {!current&&builtinDirty(view,draft)&&<p role="status">配置已修改，请重新预览；旧预览不会作为当前结果展示。</p>}
   {Object.entries(prompts).map(([stage,prompt])=>{
    const node=view.workflow?.stages.find(v=>v.id===stage),effective=current?.stages.find(v=>v.id===stage);
    return <details key={stage} style={{margin:'12px 0'}}><summary>{titles[stage]||stage}</summary>
     {node&&<><p>{node.purpose}</p><p>读取：{node.inputs.join('、')}。产出：{node.outputs.join('、')}。</p><p>{node.execution}</p></>}
     <LocalPromptEditor title={`${titles[stage]} · System`} system names={[]} value={prompt.system_prompt} onChange={system_prompt=>onChange({...draft,stage_prompts:{...prompts,[stage]:{...prompt,system_prompt}}})}/>
     <LocalPromptEditor title={`${titles[stage]} · User`} names={['input_json']} value={prompt.user_prompt} onChange={user_prompt=>onChange({...draft,stage_prompts:{...prompts,[stage]:{...prompt,user_prompt}}})}/>
     {effective&&<details><summary>本节点实际有效基础提示词（只读）</summary><h5>System</h5><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{effective.effective_system_prompt}</pre><h5>User</h5><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{effective.effective_user_prompt}</pre></details>}
    </details>;
   })}
   {(current?.runtime_notes||view.workflow?.runtime_notes)?.map((note,i)=><p className="hint" key={i}>{note}</p>)}
   {current&&<p>{current.after.name}：{current.after.purpose}</p>}
  </>}
 </fieldset>;
}
