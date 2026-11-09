import type {Json} from './api';
export function publishedApplicationVersion(agentId:string,items:Json[]){
 const version=items.filter(item=>item.agent_id===agentId&&item.status==='published'&&typeof item.id==='string'&&Number.isSafeInteger(item.version)&&item.version>0).sort((a,b)=>b.version-a.version)[0];
 if(!version||!version.config)throw Error('此应用尚无可用的已发布版本，请联系维护人员。');
 const schema=version.workflow_snapshot?.input_schema||version.config.input_schema||{type:'object',properties:{},additionalProperties:false};
 return {version,schema};
}
export function businessInputProblems(schema:Json,value:Json,title=''):string[]{
 if(schema.type!=='object')return ['当前应用输入定义不支持业务表单，请联系维护人员。'];
 return Object.entries(schema.properties||{}).flatMap(([key,raw])=>{const field=raw as Json,label=[title,field.title||key].filter(Boolean).join(' / '),v=value[key];
  if(v===undefined)return (schema.required||[]).includes(key)?[`请填写${label}`]:[];
  if(field.type==='object')return v&&typeof v==='object'&&!Array.isArray(v)?businessInputProblems(field,v,label):[`${label}需为对象`];
  if(field.type==='array')return !Array.isArray(v)?[`${label}需为列表`]:v.length<(field.minItems||0)?[`请补全${label}`]:[];
  if(field.type==='string')return typeof v!=='string'?[`${label}需为文本`]:v.length<(field.minLength||0)?[`请填写${label}`]:[];
  if(field.type==='integer')return Number.isSafeInteger(v)?[]:[`${label}需为整数`];
  if(field.type==='number')return typeof v==='number'&&Number.isFinite(v)?[]:[`${label}需为数字`];
  if(field.type==='boolean')return typeof v==='boolean'?[]:[`请选择${label}`];
  if(field.type==='null')return v===null?[]:[`${label}需为空值`];
  return [];
 });
}
