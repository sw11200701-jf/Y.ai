import type {Schema} from './types';
/** Immediate scalar feedback; full workflow/JSON Schema validation remains on the server. */
export function scalarIssue(schema:Schema,value:unknown,required=false):string{
 if(value===undefined)return required?'请填写此必填项':'';
 if(value&&typeof value==='object'&&'$ref' in value)return '';
 if(schema.enum&&!schema.enum.some(v=>JSON.stringify(v)===JSON.stringify(value)))return '请选择有效选项';
 const types=Array.isArray(schema.type)?schema.type:[schema.type];
 if(value===null)return types.includes('null')||!schema.type?'':'此字段不能使用空值';
 if(types.includes('integer')&&!types.includes('number')&&typeof value==='number'){
  if(!Number.isSafeInteger(value))return '请填写可准确表示的整数';
 }else if(types.includes('number')&&typeof value==='number'){
  if(!Number.isFinite(value))return '请填写有限数值';
 }else if(schema.type&&typeof value!=='object'&&!types.includes(typeof value))return '内容类型与字段要求不一致';
 if(typeof value==='number'){
  if(schema.minimum!==undefined&&value<schema.minimum)return `不得小于 ${schema.minimum}`;
  if(schema.maximum!==undefined&&value>schema.maximum)return `不得大于 ${schema.maximum}`;
 }
 if(typeof value==='string'){
  const length=Array.from(value).length;
  if(schema.minLength!==undefined&&length<schema.minLength)return `至少填写 ${schema.minLength} 个字符`;
  if(schema.maxLength!==undefined&&length>schema.maxLength)return `最多填写 ${schema.maxLength} 个字符`;
 }
 return '';
}
