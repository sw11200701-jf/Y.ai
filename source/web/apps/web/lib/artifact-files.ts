import {request,readDownload,currentIdentityEpoch} from './api';
export const documentArtifactContract='generated-document-artifact-v1';
export const artifactMimes={docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',xlsx:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',pptx:'application/vnd.openxmlformats-officedocument.presentationml.presentation',png:'image/png',pdf:'application/pdf'};
export type FinanceArtifactPublic={artifact_kind?:never;id:string;run_id:string;dataset_id:string;type:'docx'|'xlsx'|'png'|'pdf';filename:string;sha256:string;size_bytes:number;created_at:string};
export type ArtifactSource={kind:string;resource_id:string;action:'export_detail'};
export type GeneratedDocumentArtifact={id:string;artifact_kind:'generated_document';contract_version:'generated-document-artifact-v1';run_id:string;execution_id:string;node_key:string;tool_id:'document.word.v1'|'document.excel.v1'|'document.powerpoint.v1';renderer_version:string;input_sha256:string;user_input_sha256:string;type:'docx'|'xlsx'|'pptx';filename:string;mime_type:string;sha256:string;size_bytes:number;created_at:string;data_sources:ArtifactSource[]};
export type RunArtifact=FinanceArtifactPublic|GeneratedDocumentArtifact;
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const text=(v:unknown,max=500):v is string=>typeof v==='string'&&v.length>0&&v.length<=max&&!/[\x00-\x1f\x7f]/.test(v);
const identifier=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,160}$/.test(v);
const hash=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const filename=(v:unknown):v is string=>text(v,240)&&!/[\\/]/.test(v);
function invalid():never{throw Error('运行文件信息不符合当前文件合同，请重新读取本次运行。');}
export function runArtifact(value:unknown,expectedRunId?:string):RunArtifact{
 if(!object(value))return invalid();const v=value,id=v.id,run=v.run_id,name=v.filename,sha=v.sha256,size=v.size_bytes,at=v.created_at,type=v.type;
 if(!identifier(id)||!identifier(run)||expectedRunId!==undefined&&run!==expectedRunId||!filename(name)||!hash(sha)||typeof size!=='number'||!Number.isSafeInteger(size)||size<0||!text(at)||!Number.isFinite(Date.parse(at)))return invalid();
 if(v.artifact_kind==='generated_document'){
  const fields=['id','artifact_kind','contract_version','run_id','execution_id','node_key','tool_id','renderer_version','input_sha256','user_input_sha256','type','filename','mime_type','sha256','size_bytes','created_at','data_sources'];
  if(Object.keys(v).sort().join(',')!==fields.sort().join(',')||v.contract_version!==documentArtifactContract||!identifier(v.execution_id)||!text(v.node_key,1000)||!text(v.renderer_version)||!hash(v.input_sha256)||!hash(v.user_input_sha256)||size<1||size>2097152||!Array.isArray(v.data_sources)||(type!=='docx'&&type!=='xlsx'&&type!=='pptx')||v.mime_type!==artifactMimes[type]||!name.toLowerCase().endsWith('.'+type))return invalid();
  const tool=v.tool_id;if(tool!=='document.word.v1'&&tool!=='document.excel.v1'&&tool!=='document.powerpoint.v1')return invalid();if(({ 'document.word.v1':'docx','document.excel.v1':'xlsx','document.powerpoint.v1':'pptx'} as const)[tool]!==type)return invalid();
  const sources=v.data_sources.map(s=>{if(!object(s)||!text(s.kind,100)||!text(s.resource_id,160)||s.action!=='export_detail')return invalid();return {kind:s.kind,resource_id:s.resource_id,action:'export_detail' as const};});
  return {id,artifact_kind:'generated_document',contract_version:documentArtifactContract,run_id:run,execution_id:v.execution_id,node_key:v.node_key,tool_id:tool,renderer_version:v.renderer_version,input_sha256:v.input_sha256,user_input_sha256:v.user_input_sha256,type,filename:name,mime_type:artifactMimes[type],sha256:sha,size_bytes:size,created_at:at,data_sources:sources};
 }
 if(v.artifact_kind!==undefined||!text(v.dataset_id,160)||(type!=='docx'&&type!=='xlsx'&&type!=='png'&&type!=='pdf'))return invalid();
 // The legacy internal path is neither used nor exposed; dataset and financial formats stay mandatory.
 return {id,run_id:run,dataset_id:v.dataset_id,type,filename:name,sha256:sha,size_bytes:size,created_at:at};
}
export function runArtifacts(items:unknown,runId?:string):RunArtifact[]{if(!Array.isArray(items))return invalid();const result=items.map(item=>runArtifact(item,runId));if(new Set(result.map(item=>item.id)).size!==result.length)return invalid();return result;}
export async function generatedDocumentArtifact(id:string,runId:string,signal?:AbortSignal):Promise<GeneratedDocumentArtifact>{if(!identifier(id)||!identifier(runId))return invalid();const value=runArtifact(await request('/document-artifacts/'+encodeURIComponent(id),{signal}),runId);if(value.artifact_kind!=='generated_document')return invalid();return value;}
export async function downloadRunArtifact(file:RunArtifact,signal:AbortSignal){
 const value=await readDownload('/artifacts/'+encodeURIComponent(file.id)+'/download',file.filename,signal);if(!filename(value.filename))throw Error('下载文件名称不符合要求，未保存文件。');
 if(file.artifact_kind==='generated_document'){
  if(value.filename!==file.filename||value.blob.size!==file.size_bytes||value.blob.type.split(';')[0].toLowerCase()!==file.mime_type)throw Error('下载文件的名称、类型或大小与登记信息不符，未保存文件。');
  const bytes=await value.blob.arrayBuffer(),digest=await crypto.subtle.digest('SHA-256',bytes),sha=Array.from(new Uint8Array(digest)).map(n=>n.toString(16).padStart(2,'0')).join('');if(sha!==file.sha256)throw Error('下载文件校验值不符，未保存文件。');
 }
 if(signal.aborted||value.identityEpoch!==currentIdentityEpoch())throw new DOMException('文件读取范围已变化','AbortError');
 const url=URL.createObjectURL(value.blob),anchor=document.createElement('a'),revoke=()=>URL.revokeObjectURL(url);signal.addEventListener('abort',revoke,{once:true});anchor.href=url;anchor.download=value.filename;anchor.click();setTimeout(()=>{signal.removeEventListener('abort',revoke);revoke();},1000);
}
