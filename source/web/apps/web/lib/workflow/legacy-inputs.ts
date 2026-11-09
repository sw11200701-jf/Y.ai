import type {ObjectValue,Schema,VariableOption,WorkflowNode} from './types';

// A view adapter only. Merely opening an old graph never adds declarations or
// changes its field contract, service mappings, prompt roles or output envelope.
export function legacyInputSchema(node:WorkflowNode, service:Schema, variables:VariableOption[]):Schema {
 if(node.input_schema)return node.input_schema;
 const infer=(value:any):Schema=>{
  if(value!==null&&typeof value==='object'&&typeof value.$ref==='string'){
   const type=variables.find(v=>v.ref===value.$ref)?.type;
   return !type||type==='any'?{}:{type:type.includes('|')?type.split('|'):type};
  }
  return value===undefined?{}:{type:value===null?'null':Array.isArray(value)?'array':typeof value==='object'?'object':typeof value};
 };
 const withoutDefaults=(schema:Schema):Schema=>{
  const next={...schema};delete next.default;
  if(next.properties)next.properties=Object.fromEntries(Object.entries(next.properties).map(([key,s])=>[key,withoutDefaults(s)]));
  if(next.items)next.items=withoutDefaults(next.items);
  if(next.$defs)next.$defs=Object.fromEntries(Object.entries(next.$defs).map(([key,s])=>[key,withoutDefaults(s as Schema)]));
  return next;
 };
 const names=[...new Set([...Object.keys(node.inputs||{}),...(service.required||[])])];
 return {...withoutDefaults(service),type:'object',properties:Object.fromEntries(names.map(name=>[name,
  service.properties?.[name]?withoutDefaults(service.properties[name]):infer(node.inputs?.[name])])),
  required:(service.required||[]).filter(name=>names.includes(name)),additionalProperties:true};
}

export function legacyInputPatch(node:WorkflowNode, before:Schema, next:Schema, values:ObjectValue, service:Schema):{patch?:Partial<WorkflowNode>;issue?:string} {
 const changed=JSON.stringify(before)!==JSON.stringify(next);
 if(!changed)return {patch:{inputs:values}};
 for(const key of service.required||[])if(!Object.hasOwn(next.properties||{},key)||!next.required?.includes(key))
  return {issue:`“${key}”是此节点的必要参数，请保留名称及必填设置。`};
 const visit=(value:any,key:string):boolean=>{
  if(typeof value==='string')return [...value.matchAll(/\{\{\s*([^}]+?)\s*\}\}/g)].some(match=>match[1].trim().split('.')[0]===key);
  if(!value||typeof value!=='object')return false;
  if(typeof value.$ref==='string'&&(value.$ref===`fields.${key}`||value.$ref.startsWith(`fields.${key}.`)))return true;
  return Object.values(value).some(v=>visit(v,key));
 };
 for(const key of Object.keys(before.properties||{}))if(!Object.hasOwn(next.properties||{},key)&&visit(node.config,key))
  return {issue:`“${key}”仍用于提示词或参数映射，请先明确修改对应配置，再改名或删除。`};
 // The user explicitly edited a field definition. Save its real declaration;
 // no business_v1 marker or input/output mapping is inferred or rewritten.
 return {patch:{input_schema:next,inputs:values}};
}
