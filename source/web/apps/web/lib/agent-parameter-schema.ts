import type {Json} from './api';
export type AgentParameterType='string'|'integer'|'number'|'boolean'|'object'|'array'|'null';
export const agentParameterTypes:AgentParameterType[]=['string','integer','number','boolean','object','array','null'];
/** Unsupported constructs stay intact and read-only, never silently recompiled. */
export function parameterEditable(schema:Json):boolean{return !('$ref'in schema||'oneOf'in schema||'anyOf'in schema||'allOf'in schema)&&agentParameterTypes.includes(schema.type);}
export function parameterObjectEditable(schema:Json):boolean{return schema.type==='object'&&parameterEditable(schema)&&(!schema.properties||typeof schema.properties==='object'&&!Array.isArray(schema.properties));}
export function setParameter(schema:Json,key:string,next:Json):Json{return {...schema,properties:{...(schema.properties||{}),[key]:next}};}
export function removeParameter(schema:Json,key:string):Json{
 const properties={...(schema.properties||{})};delete properties[key];return {...schema,properties,...(Array.isArray(schema.required)?{required:schema.required.filter((name:string)=>name!==key)}:{})};
}
export function setParameterRequired(schema:Json,key:string,required:boolean):Json{
 const values=new Set<string>(schema.required||[]);required?values.add(key):values.delete(key);return {...schema,required:[...values]};
}
export function nextParameterKey(schema:Json):string{let n=1;while(Object.hasOwn(schema.properties||{},`parameter_${n}`))n++;return `parameter_${n}`;}
export function changeParameterType(schema:Json,type:AgentParameterType):Json{
 if(schema.type===type)return schema;
 // Keep safe annotations. Type-specific constraints are not silently discarded.
 const complex=Object.keys(schema).some(key=>!['type','title','description','default','properties','required','additionalProperties','items'].includes(key));
 if(complex)throw Error('此参数包含已有校验规则，请保留原类型或新建参数，避免丢失规则。');
 return {type,...(schema.title?{title:schema.title}:{}),...(schema.description?{description:schema.description}:{}),...(type==='object'?{properties:{},required:[],additionalProperties:false}:type==='array'?{items:{type:'string'}}:{})};
}
export function parameterInitialValue(schema:Json):unknown {
 if(Object.hasOwn(schema,'default'))return structuredClone(schema.default);
 if(schema.type==='object')return Object.fromEntries(Object.entries(schema.properties||{}).filter(([,field])=>Object.hasOwn(field as Json,'default')).map(([key,field])=>[key,parameterInitialValue(field as Json)]));
 return schema.type==='array'?[]:schema.type==='boolean'?false:schema.type==='null'?null:['integer','number'].includes(schema.type)?0:'';
}
export function parameterReferences(schema:Json,path:string[]=[],title=''):Array<{value:string;label:string;reference:string}>{
 if(path.length>6||schema.type!=='object')return [];
 return Object.entries(schema.properties||{}).flatMap(([key,field])=>{const next=field as Json,parts=[...path,key],label=[title,next.title||key].filter(Boolean).join(' / '),reference=JSON.stringify(parts);
  return [{value:reference,label,reference},...parameterReferences(next,parts,label)];});
}
