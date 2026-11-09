import type {BuiltinDraft,RecipeOptions} from './builtin-agents';
export const stageOrder=['plan','slice','audit','repair','merge','candidates','relation','finalaudit'] as const;
export type StageView={id:string;name:string;purpose:string;inputs:string[];outputs:string[];execution:string;effective_system_prompt:string;effective_user_prompt:string};
export type SlicingWorkflowView={contract:'slicing-workflow-view-v1';agent_id:string;revision:number;strategy?:{name:string;policy:string}|null;effective_recipe_options:RecipeOptions;recipe_option_schema:Record<string,{type:string;minimum?:number;maximum?:number;default?:unknown}>;stages:StageView[];before:{name:string;purpose:string};after:{name:string;purpose:string};runtime_notes:string[]};
export function workflowView(value:any):SlicingWorkflowView{
 if(value?.contract!=='slicing-workflow-view-v1'||typeof value.agent_id!=='string'||!Number.isSafeInteger(value.revision)||!Array.isArray(value.stages)||value.stages.map((v:any)=>v?.id).join(',')!==stageOrder.join(',')||value.stages.some((v:any)=>!['name','purpose','execution','effective_system_prompt','effective_user_prompt'].every(k=>typeof v[k]==='string')||!Array.isArray(v.inputs)||!Array.isArray(v.outputs))||!value.recipe_option_schema||!value.effective_recipe_options||!Array.isArray(value.runtime_notes)||typeof value.before?.purpose!=='string'||typeof value.after?.purpose!=='string')throw Error('工作流节点说明不完整，请重新读取配置。');
 return value;
}
export function validateRecipeDraft(draft:BuiltinDraft,view?:SlicingWorkflowView){
 if(draft.recipe_options){
  const bounds={max_context_rounds:[0,8],max_repair_rounds:[0,2],max_relation_pairs:[0,512],source_group_bytes:[256,262144]};
  for(const [key,fallback] of Object.entries(bounds)){
   const schema=view?.recipe_option_schema[key],min=schema?.minimum??fallback[0],max=schema?.maximum??fallback[1],value=draft.recipe_options[key as keyof RecipeOptions];
   if(!Number.isSafeInteger(value)||Number(value)<min||Number(value)>max)throw Error(`${key} 须为 ${min} 至 ${max} 的整数；未保存配置。`);
  }
  if(draft.recipe_options.csv_variant!==undefined&&!['original','excel_safe'].includes(draft.recipe_options.csv_variant))throw Error('CSV副本策略无效。');
 }
 if(draft.stage_prompts){
  if(Object.keys(draft.stage_prompts).sort().join(',')!==[...stageOrder].sort().join(','))throw Error('必须保留完整八个工作流节点的提示词。');
  for(const key of stageOrder){const p=draft.stage_prompts[key];if(!p||!['system_prompt','user_prompt'].every(k=>typeof p[k as keyof typeof p]==='string'&&p[k as keyof typeof p].trim()&&Array.from(p[k as keyof typeof p]).length<=16000)||!p.user_prompt.includes('{{input_json}}'))throw Error(`${key} 提示词须完整且在16000字内，User提示须保留 {{input_json}}。`);}
 }
}
