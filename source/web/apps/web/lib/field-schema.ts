import { Json } from './api';
export type FieldDefinition = { name:string; display_name?:string; type:'string'|'integer'|'number'|'boolean'|'object'|'array'; required?:boolean; default?:any; description?:string; position?:'path'|'query'|'body'|'header'; children?:FieldDefinition[]; item?:FieldDefinition; source_path?:string; secret?:boolean };
export function schemaFields(schema:Json={},position?:FieldDefinition['position']):FieldDefinition[] {
  return Object.entries(schema.properties||{}).map(([name,s])=>{const v=s as Json;const type=(v.type||'string') as FieldDefinition['type'];return {name,type,required:(schema.required||[]).includes(name),description:v.description,display_name:v.title,...('default'in v?{default:v.default}:{}),...(position?{position}:{}),...(type==='object'?{children:schemaFields(v)}:{}),...(type==='array'?{item:schemaItem(v.items||{type:'string'})}:{})};});
}
function schemaItem(s:Json):FieldDefinition {const type=s.type||'string';return {name:'',type,...(type==='object'?{children:schemaFields(s)}:{}),...(type==='array'?{item:schemaItem(s.items||{type:'string'})}:{})};}
export function compileFields(fields:FieldDefinition[]):Json {
  const properties=Object.fromEntries(fields.filter(f=>!f.secret).map(f=>[f.name,fieldSchema(f)]));
  return {type:'object',properties,required:fields.filter(f=>f.required&&!f.secret).map(f=>f.name),additionalProperties:false};
}
function fieldSchema(f:FieldDefinition):Json {return {...(f.type==='object'?compileFields(f.children||[]):{type:f.type}),...(f.type==='array'?{items:fieldSchema(f.item||{name:'',type:'string'})}:{}),...(f.description?{description:f.description}:{}),...('default'in f?{default:f.default}:{})};}
export function validateFields(fields:FieldDefinition[],depth=0):void {
  if(depth>6)throw Error('字段最多嵌套6层'); const names=new Set<string>();
  fields.forEach(f=>{if(!f.name.trim())throw Error('请填写字段名');if(names.has(f.name))throw Error(`字段名重复：${f.name}`);names.add(f.name);if(f.type==='object')validateFields(f.children||[],depth+1);if(f.type==='array'&&f.item?.type==='object')validateFields(f.item.children||[],depth+1);});
}
export function parseDefault(raw:string,type:FieldDefinition['type']) {if(type==='string')return raw;const value=JSON.parse(raw);if(type==='number'&&typeof value==='number'&&Number.isFinite(value)||type==='integer'&&Number.isInteger(value)||type==='boolean'&&typeof value==='boolean'||type==='array'&&Array.isArray(value)||type==='object'&&value!==null&&typeof value==='object'&&!Array.isArray(value))return value;throw Error('默认值与字段类型不符');}
export function extractResponseFields(value:unknown,prefix='',depth=0):FieldDefinition[] {
  if(!value||typeof value!=='object'||Array.isArray(value)||depth>=5)return [];
  return Object.entries(value).slice(0,40).map(([name,v])=>{const type:FieldDefinition['type']=Array.isArray(v)?'array':v!==null&&typeof v==='object'?'object':typeof v==='boolean'?'boolean':typeof v==='number'?(Number.isInteger(v)?'integer':'number'):'string';const source_path=prefix?`${prefix}.${name}`:name;return {name,type,required:false,source_path,...(type==='object'?{children:extractResponseFields(v,'',depth+1)}:{}),...(type==='array'?{item:{name:'',type:typeof (v as any[])?.[0]==='object'?'object':typeof (v as any[])?.[0]==='number'?'number':'string',...(typeof (v as any[])?.[0]==='object'?{children:extractResponseFields((v as any[])[0],'',depth+1)}:{})} as FieldDefinition}:{})};});
}
