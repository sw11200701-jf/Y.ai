import type {Schema,VariableOption} from './types';

export function inputValueIssue(name:string, schema:Schema, value:any, required:boolean, variables:VariableOption[]):string {
  if(value===undefined) return required&&!Object.hasOwn(schema,'default')?`请设置输入“${name}”`:'';
  const types=Array.isArray(schema.type)?schema.type:schema.type?[schema.type]:[];
  if(value!==null&&typeof value==='object'&&typeof value.$ref==='string') {
    const chosen=variables.find(v=>v.ref===value.$ref);
    if(!chosen)return `输入“${name}”的引用不在当前合法来源中`;
    if(types.length&&chosen.type!=='any'&&!chosen.type.split(/[|,]/).every(t=>types.includes(t)))return `输入“${name}”的引用类型与声明不符`;
    return '';
  }
  const type=value===null?'null':Array.isArray(value)?'array':typeof value;
  if(types.length&&!types.includes(type)&&!(type==='number'&&Number.isInteger(value)&&types.includes('integer')))return `输入“${name}”的固定值类型与声明不符，原值已保留`;
  if(type==='number'&&!Number.isFinite(value))return `输入“${name}”必须为有限数值`;
  if(schema.format==='date-time'&&(type!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)||!Number.isFinite(Date.parse(value))))return `输入“${name}”须为含时区的日期时间`;
  return '';
}
