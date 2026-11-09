import {validateRecipeDraft,type SlicingWorkflowView} from './slicing-workflow';
import type {Json} from './api';
export type RecipeOptions={max_context_rounds:number;max_repair_rounds:number;max_relation_pairs:number;source_group_bytes:number;csv_variant?:'original'|'excel_safe'};
export type StagePrompts=Record<string,{system_prompt:string;user_prompt:string}>;
export type BuiltinDraft={model_execution_mode?:'auto'|'direct'|'model_default'|null;recipe_version?:string|null;stage_prompts?:StagePrompts|null;source_scope?:string[]|null;recipe_options?:RecipeOptions|null;model_config_id:string|null;model_config_version:string|null;prompt_override:string|null};
export type BuiltinView={configuration_errors?:string[];workflow?:SlicingWorkflowView;portable_recipe?:{recipe_version:string;stage_prompts:StagePrompts;recipe_options:RecipeOptions};id:string;name:string;builtin:true;deletable:false;contract_version:string;editable_fields:string[];fixed_tools:unknown[];input_schema:Json;output_schema:Json;default_prompt:string;configuration_scope:'current_workspace';execution_identity:'current_caller';configuration_status:'waiting_configuration'|'configured'|'model_unavailable';config:BuiltinDraft&{revision:number}};
const nullableString=(value:unknown)=>value===null||typeof value==='string';
export function builtinView(value:unknown):BuiltinView{
 const v=value as Json;
 if(!v||!['builtin_knowledge_chunker','builtin_document_chunker','builtin_table_chunker','builtin_presentation_chunker'].includes(v.id)||v.builtin!==true||v.deletable!==false||typeof v.name!=='string'||typeof v.contract_version!=='string'||!Array.isArray(v.editable_fields)||!['model_config_id','prompt_override'].every(k=>v.editable_fields.includes(k))||!Array.isArray(v.fixed_tools)||!v.input_schema||!v.output_schema||typeof v.default_prompt!=='string'||v.configuration_scope!=='current_workspace'||v.execution_identity!=='current_caller'||!['waiting_configuration','configured','model_unavailable'].includes(v.configuration_status)||!v.config||!Number.isSafeInteger(v.config.revision)||v.config.revision<0||!['model_config_id','model_config_version','prompt_override'].every(k=>nullableString(v.config[k])))throw Error('内置Agent目录格式不完整，请刷新或联系维护人员。');
 return v as BuiltinView;
}
const recipeKeys=['recipe_version','stage_prompts','source_scope','recipe_options'] as const;
const same=(a:unknown,b:unknown)=>JSON.stringify(a??null)===JSON.stringify(b??null);
function recipePatch(view:BuiltinView,draft:BuiltinDraft){return Object.fromEntries(recipeKeys.filter(k=>!same(draft[k],view.config[k])).map(k=>[k,draft[k]]));}
export function builtinDraft(view:BuiltinView):BuiltinDraft{return {model_execution_mode:view.config.model_execution_mode||'auto',model_config_id:view.config.model_config_id,model_config_version:view.config.model_config_version,prompt_override:view.config.prompt_override,...Object.fromEntries(recipeKeys.filter(k=>Object.hasOwn(view.config,k)).map(k=>[k,structuredClone(view.config[k])]))};}
export function modelChanged(view:BuiltinView,draft:BuiltinDraft){return draft.model_config_id!==view.config.model_config_id||draft.model_config_version!==view.config.model_config_version;}
export function builtinDirty(view:BuiltinView,draft:BuiltinDraft){return (draft.model_execution_mode||'auto')!==(view.config.model_execution_mode||'auto')||modelChanged(view,draft)||draft.prompt_override!==view.config.prompt_override||Object.keys(recipePatch(view,draft)).length>0;}
export function builtinPatch(view:BuiltinView,draft:BuiltinDraft){
 validateRecipeDraft(draft,view.workflow);
 if(draft.prompt_override!==null&&(!draft.prompt_override.trim()||Array.from(draft.prompt_override).length>8000))throw Error('任务提示须为非空白文本，最多8000字；也可选择恢复默认任务提示。');
 if(view.id==='builtin_knowledge_chunker'&&draft.recipe_version==='portable-slicing-1.0.0'&&!draft.source_scope?.length)throw Error('请明确选择允许处理的知识库范围。');
 return {expected_revision:view.config.revision,...((draft.model_execution_mode||'auto')!==(view.config.model_execution_mode||'auto')?{model_execution_mode:draft.model_execution_mode||'auto'}:{}),...recipePatch(view,draft),...(modelChanged(view,draft)?{model_config_id:draft.model_config_id}:{}),...(draft.prompt_override!==view.config.prompt_override?{prompt_override:draft.prompt_override}:{})};
}
export type BuiltinRebaseChoice='mine'|'latest';
export class BuiltinRebaseConflict extends Error{
 constructor(readonly fields:string[]){super(`以下配置双方都已修改，请明确选择保留哪一方：${fields.join('、')}`);this.name='BuiltinRebaseConflict';}
}
const objectValue=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
const optionLabels:Record<string,string>={max_context_rounds:'最多上下文回取轮数',max_repair_rounds:'每组最多返修轮数',max_relation_pairs:'最多逐对关系审核数',source_group_bytes:'每组完整来源字节预算',csv_variant:'CSV副本策略'};
const stageLabels:Record<string,string>={plan:'任务计划',slice:'知识切片',audit:'独立审核',repair:'有限返修',merge:'跨组归并',candidates:'关系候选',relation:'逐对关系审核',finalaudit:'最终独立审核'};
function rebaseLabel(path:string){const parts=path.split('.');return parts[0]==='recipe_options'?(optionLabels[parts[1]]||'阶段参数与默认值'):parts[0]==='stage_prompts'?(parts[1]?`${stageLabels[parts[1]]||parts[1]} · ${parts[2]==='system_prompt'?'System':parts[2]==='user_prompt'?'User':'阶段提示词'}`:'阶段提示词与默认值'):path;}
/** Three-way leaf merge. No result is returned for unresolved conflicts. */
export function rebaseBuiltinDraft(previous:BuiltinView,draft:BuiltinDraft,latest:BuiltinView,choice?:BuiltinRebaseChoice):BuiltinDraft{
 if(previous.id!==latest.id)throw Error('内置Agent引用已变化，请重新打开配置。');
 const conflicts:string[]=[],result=builtinDraft(latest);
 function atomic(base:unknown,mine:unknown,remote:unknown,path:string):unknown{
  if(same(mine,base))return structuredClone(remote);
  if(same(remote,base)||same(mine,remote))return structuredClone(mine);
  conflicts.push(rebaseLabel(path));return structuredClone(choice==='latest'?remote:mine);
 }
 function nested(base:unknown,mine:unknown,remote:unknown,path:string,defaults?:unknown):unknown{
  if(same(mine,base))return structuredClone(remote);
  if(same(remote,base)||same(mine,remote))return structuredClone(mine);
  // null is an explicit whole-group reset. It must not be treated as {} and
  // silently resurrect the other window's fields. Arrays remain atomic too.
  if(!objectValue(mine)||!objectValue(remote))return atomic(base,mine,remote,path);
  const before=objectValue(base)?base:objectValue(defaults)?defaults:{};
  return Object.fromEntries([...new Set([...Object.keys(remote),...Object.keys(mine),...Object.keys(before)])].map(key=>[key,nested(before[key],mine[key],remote[key],`${path}.${key}`)]));
 }
 const model=atomic({id:previous.config.model_config_id,version:previous.config.model_config_version},{id:draft.model_config_id,version:draft.model_config_version},{id:latest.config.model_config_id,version:latest.config.model_config_version},'模型及版本') as {id:string|null;version:string|null};
 result.model_config_id=model.id;result.model_config_version=model.version;
 result.model_execution_mode=atomic(previous.config.model_execution_mode||'auto',draft.model_execution_mode||'auto',latest.config.model_execution_mode||'auto','切片模型模式') as BuiltinDraft['model_execution_mode'];
 result.prompt_override=atomic(previous.config.prompt_override,draft.prompt_override,latest.config.prompt_override,'兼容方案任务提示') as string|null;
 const stableRecipe=same(previous.config.recipe_version,draft.recipe_version)&&same(previous.config.recipe_version,latest.config.recipe_version);
 if(!stableRecipe){
  // A recipe switch changes the meaning of its dependent fields. Resolve the
  // bundle explicitly rather than mixing prompts/options from two recipes.
  const bundle=(value:BuiltinDraft)=>Object.fromEntries(recipeKeys.map(key=>[key,value[key]]));
  Object.assign(result,atomic(bundle(previous.config),bundle(draft),bundle(latest.config),'切片方案及关联配置'));
 }else{
  result.recipe_options=nested(previous.config.recipe_options,draft.recipe_options,latest.config.recipe_options,'recipe_options',previous.portable_recipe?.recipe_options) as BuiltinDraft['recipe_options'];
  result.stage_prompts=nested(previous.config.stage_prompts,draft.stage_prompts,latest.config.stage_prompts,'stage_prompts',previous.portable_recipe?.stage_prompts) as BuiltinDraft['stage_prompts'];
  result.source_scope=atomic(previous.config.source_scope,draft.source_scope,latest.config.source_scope,'允许处理的知识库范围') as BuiltinDraft['source_scope'];
 }
 if(conflicts.length&&!choice)throw new BuiltinRebaseConflict([...new Set(conflicts)]);
 return result;
}
export const builtinStatus={waiting_configuration:'待配置模型',configured:'已配置',model_unavailable:'模型不可用或版本已变化'};

export type BuiltinDirectoryItem=Pick<BuiltinView,'id'|'name'|'configuration_status'|'configuration_scope'>;
export function builtinDirectoryItem(value:unknown):BuiltinDirectoryItem{const v=value as Json;if(!v||typeof v.id!=='string'||typeof v.name!=='string'||!['waiting_configuration','configured','model_unavailable'].includes(v.configuration_status)||v.configuration_scope!=='current_workspace')throw Error('切片配置目录格式不完整');return {id:v.id,name:v.name,configuration_status:v.configuration_status,configuration_scope:v.configuration_scope};}
