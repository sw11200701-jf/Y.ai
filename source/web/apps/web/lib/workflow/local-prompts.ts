import type {ObjectValue, Schema} from './types';

export function promptInputSchema(value:ObjectValue, saved?:Schema):Schema {
  const properties={...saved?.properties};
  for(const [name,item] of Object.entries(value)) if(!Object.hasOwn(properties,name)) properties[name]={type:item===null?'null':Array.isArray(item)?'array':typeof item==='object'&&item?.$ref?undefined:typeof item};
  return {...saved,type:'object',properties,additionalProperties:false};
}

export function localPromptIssues(text:string, names:string[], system=false):string[] {
  const issues:string[]=[];
  const remainder=text.replace(/\{\{([\s\S]*?)\}\}/g,(_,raw:string)=>{
    const name=raw.trim();
    if(!/^[A-Za-z_\u3400-\u9fff][A-Za-z_0-9\u3400-\u9fff]*(?:\.(?:[A-Za-z_\u3400-\u9fff][A-Za-z_0-9\u3400-\u9fff]*|[0-9]+))*$/.test(name)) issues.push(`变量标记“${raw}”只能使用输入名称及字段路径`);
    else if(!names.includes(name.split('.')[0])) issues.push(`提示词输入“${name}”未声明或已删除`);
    return '';
  });
  if(remainder.includes('{{')||remainder.includes('}}')||remainder.includes('{%')) issues.push('提示词变量标记不完整或包含不支持的表达式');
  return [...new Set(issues)];
}
