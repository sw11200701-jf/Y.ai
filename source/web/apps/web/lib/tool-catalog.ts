import {request,type Json,type Binding} from './api';
export type CatalogField={name:string;title?:string;description?:string;type:string;required:boolean;position?:string;children?:CatalogField[];item?:CatalogField};
export type CatalogTool={binding:Binding;version:number;name:string;description:string;http_method:string;request_encoding:string;read_only:boolean;input_schema:Json;output_schema:Json;input_fields?:CatalogField[];output_fields?:CatalogField[];fixed_connection:{id:string;name:string};timeout_seconds_effective:number;max_output_bytes:number;required_permissions_any:string[];resource_access:string[]};
const invalid=()=>{throw Error('工具目录响应不完整，请刷新后重试。');};
const object=(value:unknown):value is Json=>!!value&&typeof value==='object'&&!Array.isArray(value);
const strings=(value:unknown):value is string[]=>Array.isArray(value)&&value.every(s=>typeof s==='string');
function catalogFields(value:unknown,depth=0):CatalogField[]|undefined{
 if(value===null||value===undefined)return undefined;
 if(!Array.isArray(value)||depth>6)return invalid();
 return value.filter(v=>object(v)&&v.secret!==true).map(v=>{if(typeof v.name!=='string'||!['string','integer','number','boolean','object','array','null'].includes(v.type))return invalid();return {name:v.name,type:v.type,required:v.required===true,...(typeof v.display_name==='string'?{title:v.display_name}:{}),...(typeof v.description==='string'?{description:v.description}:{}),...(typeof v.position==='string'?{position:v.position}:{}),...(v.type==='object'?{children:catalogFields(v.children||[],depth+1)}:{}),...(v.type==='array'&&object(v.item)?{item:catalogFields([{...v.item,name:''}],depth+1)?.[0]}:{})};});
}
export function catalogTools(value:unknown):CatalogTool[]{
 if(!object(value)||value.metadata_only!==true||value.unconfigured_tools_included!==false||!Array.isArray(value.items))return invalid();
 return value.items.map((v:Json)=>{if(!object(v)||!object(v.binding)||!['plugin_version_id','tool_id','connection_id'].every(k=>typeof v.binding[k]==='string')||!Number.isInteger(v.version)||typeof v.name!=='string'||typeof v.description!=='string'||v.call_method!=='published_http'||v.network_request_sent!==false||!['GET','POST'].includes(v.http_method)||typeof v.request_encoding!=='string'||typeof v.read_only!=='boolean'||!object(v.input_schema)||!object(v.output_schema)||!object(v.fixed_connection)||!['id','name'].every(k=>typeof v.fixed_connection[k]==='string')||!Number.isFinite(v.timeout_seconds_effective)||v.timeout_seconds_effective<=0||v.timeout_seconds_effective>30||!Number.isSafeInteger(v.max_output_bytes)||v.max_output_bytes<1||!strings(v.required_permissions_any)||!strings(v.resource_access))return invalid();
  return {binding:{plugin_version_id:v.binding.plugin_version_id,tool_id:v.binding.tool_id,connection_id:v.binding.connection_id},version:v.version,name:v.name,description:v.description,http_method:v.http_method,request_encoding:v.request_encoding,read_only:v.read_only,input_schema:v.input_schema,output_schema:v.output_schema,input_fields:catalogFields(v.input_fields),output_fields:catalogFields(v.output_fields),fixed_connection:{id:v.fixed_connection.id,name:v.fixed_connection.name},timeout_seconds_effective:v.timeout_seconds_effective,max_output_bytes:v.max_output_bytes,required_permissions_any:v.required_permissions_any,resource_access:v.resource_access};});
}
const signal=()=>AbortSignal.timeout(20000),root='/plugins/skills';

export async function loadToolCatalog(){return catalogTools(await request('/tools/catalog',{signal:AbortSignal.timeout(15000)}));}
