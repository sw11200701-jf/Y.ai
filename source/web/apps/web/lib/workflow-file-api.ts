import {currentIdentityEpoch,request} from './api';
import {bindingProblems,type FileBindings,type FileDiagnostic,type WorkflowFilePreview} from './workflow-file-view';
import type {WorkflowDefinition} from './workflow/types';
import type {WorkflowFileClient} from './workflow-file-client';
import type {FileExportPrepared} from '../components/workflow-file-export';

type Diagnostic={severity:string;code:string;path:string;message:string};
type Slot={key:string;kind:string;path:string;target_fields?:string[];required_fields?:string[]};
type Preview={format:string;source_version:string;source_hash:string;converter_version:string;preview_token:string;expires_at:number;workflow:WorkflowDefinition|null;diagnostics:Diagnostic[];bindings:Slot[];can_confirm:boolean};
type Choice={id:string;label:string;value:Record<string,unknown>};
type CatalogResource={kind:string;resource_id:string;name:string;agent_id?:string;table_id?:string;database_id?:string;knowledge_base_id?:string;kb_id?:string;source_id?:string;status?:string};
type Catalog={binding_protocol:string;models:{id:string;name:string;provider_name:string;config_version:string}[];tools:{plugin_version_id:string;tool_id:string;connection_id?:string|null;name:string}[];resources:CatalogResource[]};
type BindingPart={slot:Slot;field?:string;choices:Choice[]};
type ImportProof={token:string;hash:string;expires:number;epoch:number;policy:number;parts:Map<string,BindingPart>};
const previews=new WeakMap<WorkflowFilePreview,ImportProof>();
const downloads=new WeakMap<FileExportPrepared,{blob:Blob;epoch:number;policy:number}>();
let policyRevision=0;
const policyEvents=['zhangpu:session-expired','zhangpu:access-denied','zhangpu:data-policy-changed','zhangpu:permissions-changed'];
if(typeof window!=='undefined')policyEvents.forEach(event=>window.addEventListener(event,()=>{policyRevision++;}));
const formatLabels:Record<string,string>={'native-json':'Yai JSON','native-yaml':'Yai YAML','dify-yaml':'Dify DSL YAML',unknown:'未识别格式'};
const nodeTypeLabels:Record<string,string>={start:'开始',input:'输入',end:'结束',answer:'回复',output:'输出',llm:'大语言模型',template:'模板转换',condition:'条件分支',agent:'智能体',plugin:'工具',http:'HTTP服务',knowledge_retrieval:'知识检索',document_extractor:'文档提取',database_query:'数据库查询',iteration:'迭代',loop:'循环'};
const kindLabels:Record<string,string>={model:'模型配置',plugin:'工具与连接',knowledge:'知识资料',document:'文件资料',database:'数据库',table:'数据表版本',agent:'智能体版本',workflow:'工作流',connection:'连接',batch:'数据批次'};
const validText=(value:unknown):value is string=>typeof value==='string'&&value.length>0;
function fail():never {throw Error('工作流文件接口返回不完整，请重试检查。');}
function diagnostics(items:Diagnostic[],mode:'import'|'export',format?:string):FileDiagnostic[]{return items.map(item=>{
 if(!validText(item.code)||!validText(item.message)||typeof item.path!=='string'||!['unsupported','lossy','requires_binding','warning'].includes(item.severity))return fail();
 const binding=item.severity==='requires_binding';
 const blocking=item.severity==='unsupported'||item.severity==='lossy'||binding&&(mode==='import'?!['MODEL_REBIND','RESOURCE_REBIND'].includes(item.code):format==='dify-yaml');
 return {severity:blocking?'blocking':binding?'warning':'info',code:item.code,message:item.message,field:item.path,nodeId:item.path.match(/nodes\[(\d+)\]/)?.[1]!==undefined?`节点 ${Number(item.path.match(/nodes\[(\d+)\]/)![1])+1}`:undefined};
});}
function choicesFor(slot:Slot,catalog:Catalog):Choice[]{
 const make=(label:string,value:Record<string,unknown>)=>({id:JSON.stringify(value),label,value});
 if(slot.kind==='model')return catalog.models.filter(item=>validText(item.id)&&validText(item.name)&&validText(item.config_version)).map(item=>make(`${item.name} · ${item.provider_name||'可用模型'} · ${item.config_version}`,{model_config_id:item.id,model_config_version:item.config_version}));
 if(slot.kind==='plugin')return catalog.tools.filter(item=>validText(item.plugin_version_id)&&validText(item.tool_id)).map(item=>make(item.name||'可用工具组合',{binding:{plugin_version_id:item.plugin_version_id,tool_id:item.tool_id,...(item.connection_id?{connection_id:item.connection_id}:{})}}));
 if(slot.kind==='agent')return catalog.resources.filter(item=>item.kind==='agent_version'&&item.status==='published').flatMap(version=>{const parent=catalog.resources.find(item=>item.kind==='agent'&&item.resource_id===version.agent_id);return parent?[make(`${parent.name} · 固定版本 ${version.name||version.resource_id}`,{agent_id:parent.resource_id,agent_version_id:version.resource_id})]:[];});
 if(slot.kind==='database')return catalog.resources.filter(item=>item.kind==='data_table_version').flatMap(version=>{const table=catalog.resources.find(item=>item.kind==='data_table'&&item.resource_id===version.table_id),database=catalog.resources.find(item=>item.kind==='database'&&item.resource_id===table?.database_id);if(!table||!database)return [];const all:Record<string,string>={database_id:database.resource_id,table_id:table.resource_id,table_version_id:version.resource_id};return [make(`${database.name} / ${table.name} · ${version.name||version.resource_id}`,Object.fromEntries((slot.target_fields||[]).map(field=>[field,all[field]])))];});
 const field=slot.kind==='document'?'file_id':slot.kind==='batch'?'batch_id':'';
 const kind=slot.kind==='document'?'document':slot.kind==='batch'?'import_batch':'';
 return field?catalog.resources.filter(item=>item.kind===kind&&validText(item.resource_id)).map(item=>make(item.name||kindLabels[slot.kind],{[field]:item.resource_id})):[];
}
function multipart(file:File){const body=new FormData();body.append('file',file,file.name);return body;}
function completeWorkflow(value:unknown):value is WorkflowDefinition {const item=value as WorkflowDefinition;return !!item&&validText(item.id)&&validText(item.name)&&Array.isArray(item.nodes)&&Array.isArray(item.edges);}
export const workflowFileClient:WorkflowFileClient={
 maxBytes:2*1024*1024,
 formats:[{key:'native-json',label:formatLabels['native-json'],versions:[]},{key:'native-yaml',label:formatLabels['native-yaml'],versions:[]},{key:'dify-yaml',label:'Dify DSL YAML（支持子集）',versions:['0.4.0']}],
 async preview(file,selected,signal){
  const epoch=currentIdentityEpoch(),policy=policyRevision;
  const wire=await request<Preview>('/workflows/file-preview',{method:'POST',body:multipart(file),signal});
  if(!validText(wire.preview_token)||!validText(wire.source_hash)||!Array.isArray(wire.diagnostics)||!Array.isArray(wire.bindings)||!Number.isFinite(wire.expires_at))return fail();
  const parts=new Map<string,BindingPart>();
  if(wire.bindings.length){const catalog=await request<Catalog>('/workflows/file-resources',{signal});if(catalog.binding_protocol!=='workflow-file-bindings-v1'||!Array.isArray(catalog.models)||!Array.isArray(catalog.tools)||!Array.isArray(catalog.resources))return fail();
   for(const slot of wire.bindings){if(!validText(slot.key)||!validText(slot.kind)||typeof slot.path!=='string'||!Array.isArray(slot.target_fields)||!Array.isArray(slot.required_fields)||parts.has(slot.key))return fail();
    if(slot.kind==='knowledge')for(const field of slot.target_fields){const key=`${slot.key}:${field}`;const kinds:Record<string,string[]>={file_ids:['document'],knowledge_base_ids:['knowledge_base'],version_ids:['knowledge_version','knowledge_source_version']};
     const baseIds=selected[`${slot.key}:knowledge_base_ids`];
     const choices=catalog.resources.filter(item=>kinds[field]?.includes(item.kind)).filter(item=>{if(field!=='version_ids'||!Array.isArray(baseIds)||!baseIds.length)return true;const source=catalog.resources.find(source=>source.kind==='knowledge_source'&&source.resource_id===item.source_id);const parent=item.knowledge_base_id||item.kb_id||source?.knowledge_base_id||source?.kb_id;return !!parent&&baseIds.includes(parent);}).map(item=>({id:item.resource_id,label:item.name||'可用资料',value:{id:item.resource_id}}));
     parts.set(key,{slot,field,choices});
    }else parts.set(slot.key,{slot,choices:choicesFor(slot,catalog)});
   }}
  const report=diagnostics(wire.diagnostics,'import');
  const fieldLabels:Record<string,string>={file_ids:'文档',knowledge_base_ids:'知识库',version_ids:'固定知识版本'};
  for(const part of parts.values())if(!part.choices.length)report.push({severity:'blocking',code:'NO_AUTHORIZED_BINDING',message:`${kindLabels[part.slot.kind]||'资源'}尚无可用的完整授权选择，不能创建草稿。`,field:part.slot.path});
  const bindings=[...parts].map(([key,part])=>({key,kind:part.slot.kind,label:`${kindLabels[part.slot.kind]||'资源映射'}${part.field?' · '+(fieldLabels[part.field]||part.field):''} · ${part.slot.path}`,required:!part.field||!!part.slot.required_fields?.includes(part.field),multiple:!!part.field,choices:part.choices.map(item=>({id:item.id,label:item.label}))}));
  const workflow=wire.workflow;
  const result:WorkflowFilePreview={token:wire.preview_token,inputHash:wire.source_hash,formatLabel:formatLabels[wire.format]||'未识别格式',versionLabel:wire.source_version||'未知版本',name:workflow?.name||'导入的工作流',nodeCount:workflow?.nodes?.length||0,edgeCount:workflow?.edges?.length||0,nodes:(workflow?.nodes||[]).map((node,index)=>({id:`节点 ${index+1}`,label:node.ui?.label||node.title||`节点 ${index+1}`,typeLabel:nodeTypeLabels[node.type]||node.type})),diagnostics:report,bindings,canCreate:!!workflow&&!report.some(item=>item.severity==='blocking')&&(wire.can_confirm||wire.bindings.length>0)};
  if(Object.keys(selected).length&&bindingProblems(bindings,selected).length)result.canCreate=false;
  if(epoch!==currentIdentityEpoch()||policy!==policyRevision)throw new DOMException('身份或权限已变化','AbortError');
  previews.set(result,{token:wire.preview_token,hash:wire.source_hash,expires:wire.expires_at,epoch,policy,parts});return result;
 },
 async confirm(file,preview,bindings,name,key){
  const proof=previews.get(preview);if(!proof||proof.epoch!==currentIdentityEpoch()||proof.policy!==policyRevision||proof.expires*1000<=Date.now())throw Error('预览已失效，请重新检查资源映射后确认。');
  if(!preview.canCreate||preview.diagnostics.some(item=>item.severity==='blocking')||bindingProblems(preview.bindings,bindings).length||Object.keys(bindings).some(key=>!proof.parts.has(key)))throw Error('资源映射不完整或不在当前受权目录中，请重新检查。');
  const targets=new Map<string,{key:string;kind:string;target:Record<string,unknown>}>();
  for(const [key,part] of proof.parts){const value=bindings[key];let target=targets.get(part.slot.key);if(!target){target={key:part.slot.key,kind:part.slot.kind,target:{}};targets.set(part.slot.key,target);}
   if(part.field){if(!Array.isArray(value)||value.some(id=>!part.choices.some(choice=>choice.id===id)))throw Error('资源选择已失效，请重新检查。');target.target[part.field]=value;}
   else{const choice=part.choices.find(item=>item.id===value);if(!choice)throw Error('资源选择已失效，请重新检查。');Object.assign(target.target,choice.value);}
  }
  const resolutions=[...targets.values()];
  const body=multipart(file);body.append('preview_token',proof.token);body.append('source_hash',proof.hash);body.append('idempotency_key',key);body.append('binding_resolutions',JSON.stringify(resolutions));body.append('name',name);
  try{const result=await request<{workflow:WorkflowDefinition}>('/workflows/file-confirm',{method:'POST',body});if(!completeWorkflow(result.workflow))return fail();return result.workflow;}
  catch(error){const value=error as {details?:{diagnostics?:Diagnostic[]};fileDiagnostics?:FileDiagnostic[]};if(Array.isArray(value.details?.diagnostics))value.fileDiagnostics=diagnostics(value.details!.diagnostics!,'import');throw error;}
 },
 async prepareExport(selection,editor,signal,saved){
  if(!['editor','saved'].includes(selection.sourceKey)||!formatLabels[selection.format])throw Error('请选择支持的来源和文件格式。');
  if(selection.sourceKey==='saved'&&(!editor.id||!saved?.id||editor.id!==saved.id))throw Error('保存稿尚未读取，请重新打开工作流后导出。');
  const epoch=currentIdentityEpoch(),policy=policyRevision;
  const mappings=Object.fromEntries(Object.entries(selection.modelBindings||{}).map(([id,value])=>{if(!value.provider.trim()||!value.name.trim())throw Error('请补全Dify目标provider标识和模型名称。');return [id,{provider:value.provider.trim(),name:value.name.trim(),mode:'chat'}];}));
  const body=JSON.parse(JSON.stringify({source:selection.sourceKey,...(selection.sourceKey==='saved'?{workflow_id:saved!.id}:{workflow:editor}),format:selection.format,target_version:'0.4.0',...(selection.format==='dify-yaml'?{model_bindings:mappings}:{})}));
  type Report={source:string;source_hash:string;format:string;target_version:string;diagnostics:Diagnostic[];blocked:boolean};
  type ExportResponse={report:Report;file:null|{filename:string;media_type:string;content_base64:string;sha256:string;size:number}};
  // Ask the server for its actual selected-source digest; do not reproduce Python's
  // canonical JSON in JavaScript. The bytes request must still match this digest.
  const checked=await request<{export_report:Report}>('/workflows/file-export',{method:'POST',body:JSON.stringify({...body,report_only:true}),signal});
  if(!checked.export_report||!/^[a-f0-9]{64}$/.test(checked.export_report.source_hash)||checked.export_report.source!==selection.sourceKey)return fail();
  const response:ExportResponse=checked.export_report.blocked?{report:checked.export_report,file:null}:await request<ExportResponse>('/workflows/file-export',{method:'POST',body:JSON.stringify({...body,expected_sha256:checked.export_report.source_hash}),signal});
  if(!response.report||!Array.isArray(response.report.diagnostics)||typeof response.report.blocked!=='boolean'||response.report.source_hash!==checked.export_report.source_hash||response.report.source!==selection.sourceKey||response.report.format!==selection.format)return fail();
  const report=diagnostics(response.report.diagnostics,'export',selection.format);
  const result:FileExportPrepared={filename:response.file?.filename||'暂无可下载文件',sourceLabel:`${selection.sourceKey==='editor'?'当前编辑稿快照':'服务器保存版（检查时读取）'} · 来源SHA256 ${response.report.source_hash}`,formatLabel:`${formatLabels[response.report.format]||'未知格式'} · ${response.report.target_version}`,diagnostics:report,canDownload:!response.report.blocked&&!!response.file&&!report.some(item=>item.severity==='blocking'),downloadData:undefined};
  if(result.canDownload){const file=response.file!;if(!/^workflow-export\.(json|yaml)$/.test(file.filename)||!['application/json','application/yaml','text/yaml'].includes(file.media_type)||!Number.isSafeInteger(file.size)||file.size<0||typeof file.content_base64!=='string'||!/^[a-f0-9]{64}$/.test(file.sha256))return fail();
   const bytes=Uint8Array.from(atob(file.content_base64),char=>char.charCodeAt(0));if(bytes.byteLength!==file.size)return fail();const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(value=>value.toString(16).padStart(2,'0')).join('');if(hash!==file.sha256)throw Error('导出文件校验未通过，请重新检查后下载。');
   if(epoch!==currentIdentityEpoch()||policy!==policyRevision||signal.aborted)throw new DOMException('导出已失效','AbortError');
   result.previewText=new TextDecoder('utf-8',{fatal:true}).decode(bytes);downloads.set(result,{blob:new Blob([bytes],{type:file.media_type}),epoch,policy});
  }return result;
 },
 download(prepared){const proof=downloads.get(prepared);if(!proof||proof.epoch!==currentIdentityEpoch()||proof.policy!==policyRevision||!prepared.canDownload)throw Error('文件检查已失效，请重新检查后下载。');const url=URL.createObjectURL(proof.blob);const link=document.createElement('a');link.href=url;link.download=prepared.filename;
  let timer:ReturnType<typeof setTimeout>|undefined;const revoke=()=>{if(timer)clearTimeout(timer);URL.revokeObjectURL(url);policyEvents.forEach(event=>window.removeEventListener(event,revoke));};policyEvents.forEach(event=>window.addEventListener(event,revoke));try{link.click();}finally{timer=setTimeout(revoke,1000);}
 }
};
