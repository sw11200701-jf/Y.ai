import {request,type Json} from './api';
import type {FieldDefinition} from './field-schema';

export type CurlPreview={schema_version:'1.0';fingerprint:string;can_confirm:boolean;connection:{base_url:string;auth_type:'none'|'bearer'|'api_key';api_key_header?:string};endpoint:{method:'GET'|'POST';path:string;request_encoding:string;input_fields:FieldDefinition[]};credentials_required:boolean;response_schema_required:true};
export type CurlSelection={expected_preview_fingerprint:string;connection_id?:string;create_connection_name?:string;plugin_id?:string;expected_plugin_revision?:number;plugin_name?:string;endpoint_id:string;endpoint_name:string;tool_id:string;tool_name:string;tool_description:string;output_fields:FieldDefinition[];import_credentials:boolean};
export type CurlCreated={status:'draft';plugin:Json;connection:Json;endpoint_id:string};
const ownError=(message:string)=>Object.assign(new Error(message),{safeCurlImport:true});
function publicFields(value:unknown,depth=0):FieldDefinition[]{
 if(!Array.isArray(value)||value.length>128||depth>6)throw ownError('解析结果字段格式不完整，请重新解析。');
 return value.map(item=>{
  if(!item||typeof item!=='object'||typeof item.name!=='string'||item.name.length>64||!/^[A-Za-z_][A-Za-z0-9_-]*$/.test(item.name)||!['string','integer','number','boolean','object','array'].includes(item.type))throw ownError('解析结果字段格式不完整，请重新解析。');
  const field:FieldDefinition={name:item.name,type:item.type,required:item.required===true,...(['path','query','body','header'].includes(item.position)?{position:item.position}:{}),...(item.type==='object'?{children:publicFields(item.children||[],depth+1)}:{})};
  if(item.type==='array'){
   const entry={...item.item,name:'item'};field.item=publicFields([entry],depth+1)[0];field.item.name='';
  }
  // Request defaults and unknown server fields are never reflected in the preview.
  return field;
 });
}
function previewDto(value:Json):CurlPreview{
 let address:URL;try{address=new URL(value?.connection?.base_url);}catch{throw ownError('解析结果缺少有效服务地址，请重新解析。');}
 const endpoint=value?.endpoint;
 if(value.schema_version!=='1.0'||typeof value.fingerprint!=='string'||value.fingerprint.length!==64||!/^[a-f0-9]+$/.test(value.fingerprint)||!['http:','https:'].includes(address.protocol)||address.username||address.password||address.search||address.hash||!['none','bearer','api_key'].includes(value.connection.auth_type)||!['GET','POST'].includes(endpoint?.method)||typeof endpoint?.path!=='string'||!endpoint.path.startsWith('/')||endpoint.path.startsWith('//')||endpoint.path.length>2048||!['json','form_urlencoded','multipart'].includes(endpoint.request_encoding))throw ownError('解析结果不完整或不受支持，请改用手工配置。');
 return {schema_version:'1.0',fingerprint:value.fingerprint,can_confirm:value.can_confirm===true,connection:{base_url:address.href.replace(/\/$/,''),auth_type:value.connection.auth_type,...(typeof value.connection.api_key_header==='string'&&value.connection.api_key_header.length<=64&&/^[A-Za-z0-9_-]+$/.test(value.connection.api_key_header)?{api_key_header:value.connection.api_key_header}:{})},endpoint:{method:endpoint.method,path:endpoint.path,request_encoding:endpoint.request_encoding,input_fields:publicFields(endpoint.input_fields||[])},credentials_required:Array.isArray(value.credential_requirements)&&value.credential_requirements.length>0,response_schema_required:true};
}
async function post(path:string,body:unknown,signal?:AbortSignal){
 try{return await request(path,{method:'POST',body:JSON.stringify(body),...(signal?{signal}:{})});}
 catch(error){
  const value=error as {status?:number;code?:string;request_id?:string};
  const message=value.status===404?'当前服务尚未提供 cURL 导入入口，请更新服务后重试。':value.status===409?'插件草稿已变化，请关闭导入、刷新插件后重新确认。':value.status===401?'登录已失效，请重新登录。':value.status===403?'当前操作未获允许，请检查权限或安全验证后重试。':value.status===422?'命令或字段无法安全导入，请检查方法、引号、文件引用、鉴权位置及输出字段；也可改用手工配置。':'导入操作未完成，请检查服务后重试；创建结果未确认时，请先刷新列表核对。';
  throw Object.assign(ownError(message),{...(typeof value.status==='number'&&Number.isInteger(value.status)&&value.status>=400&&value.status<=599?{status:value.status}:{}),...(typeof value.code==='string'&&/^[A-Z][A-Z0-9_]{0,63}$/.test(value.code)&&!/[\r\n]/.test(value.code)?{code:value.code}:{}),...(typeof value.request_id==='string'&&value.request_id.length===32&&/^[a-fA-F0-9]+$/.test(value.request_id)?{request_id:value.request_id}:{})});
 }
}
export const pluginCurlClient={
 async preview(command:string,signal?:AbortSignal){if(!command.trim()||new TextEncoder().encode(command).length>32768)throw ownError('请粘贴不超过32 KiB的 cURL 命令。');return previewDto(await post('/plugins/curl-preview',{command},signal));},
 async confirm(command:string,selection:CurlSelection){const value=await post('/plugins/curl-confirm',{command,selection});if(value?.status!=='draft'||typeof value.plugin?.id!=='string'||!Array.isArray(value.plugin?.endpoints)||!Array.isArray(value.plugin?.tools)||typeof value.plugin.name!=='string'||value.plugin.status!=='draft'||!Number.isInteger(value.plugin.revision)||value.plugin.revision<1||typeof value.connection?.id!=='string'||typeof value.endpoint_id!=='string')throw ownError('创建响应未确认，请先刷新服务与插件列表核对；不要直接重复创建。');return {status:'draft',plugin:value.plugin,connection:value.connection,endpoint_id:value.endpoint_id} as CurlCreated;}
};
export function matchingCurlConnections(preview:CurlPreview,connections:Json[]){return connections.filter(connection=>{
 try{const url=new URL(connection.base_url),prefix=url.pathname.replace(/\/$/,'');return connection.enabled!==false&&url.origin===new URL(preview.connection.base_url).origin&&(!prefix||prefix==='/'||preview.endpoint.path.startsWith(prefix+'/'))&&(preview.connection.auth_type==='none'||connection.auth_type===preview.connection.auth_type&&connection.credential_configured===true&&(preview.connection.auth_type!=='api_key'||String(connection.api_key_header||'X-API-Key').toLowerCase()===String(preview.connection.api_key_header||'X-API-Key').toLowerCase()));}catch{return false;}
 });}
/** Infer only observed, representable shapes; keep sample values out of the field table. */
export function curlOutputFieldsFromSample(sample:string):FieldDefinition[]{
 const invalid=()=>ownError('样例需为64 KiB内的 JSON 对象；null、空列表或混合结构请手工指定字段类型。');
 if(new TextEncoder().encode(sample).length>65536)throw invalid();
 let root:unknown;try{root=JSON.parse(sample);}catch{throw invalid();}
 const field=(name:string,value:unknown,depth:number):FieldDefinition=>{
  if(depth>6||value===null)throw invalid();
  if(Array.isArray(value)){
   if(!value.length)throw invalid();const items=value.map(item=>field('',item,depth+1));
   if(items.every(item=>item.type==='integer'||item.type==='number'))return {name,type:'array',required:false,item:{name:'',type:items.some(item=>item.type==='number')?'number':'integer'}};
   if(items.some(item=>JSON.stringify(item)!==JSON.stringify(items[0])))throw invalid();return {name,type:'array',required:false,item:items[0]};
  }
  if(typeof value==='object')return {name,type:'object',required:false,children:fields(value as Record<string,unknown>,depth+1)};
  if(typeof value==='number'&&!Number.isFinite(value))throw invalid();
  const type=typeof value==='boolean'?'boolean':typeof value==='number'?(Number.isInteger(value)?'integer':'number'):'string';return {name,type,required:false,...(name?{source_path:name}:{})};
 };
 const fields=(value:Record<string,unknown>,depth:number):FieldDefinition[]=>{
  if(Object.keys(value).length>128)throw invalid();return Object.entries(value).map(([name,item])=>{if(name.length>64||!/^[A-Za-z_]/.test(name)||/[^A-Za-z0-9_-]/.test(name))throw invalid();return field(name,item,depth);});
 };
 if(!root||typeof root!=='object'||Array.isArray(root))throw invalid();return fields(root as Record<string,unknown>,0);
}
