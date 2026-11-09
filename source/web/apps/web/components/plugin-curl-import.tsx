'use client';
import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Checkbox,Input,Modal,Select,Space,Table,Tag} from 'antd';
import {currentIdentityEpoch,type Json} from '@/lib/api';
import {validateFields,type FieldDefinition} from '@/lib/field-schema';
import {matchingCurlConnections,pluginCurlClient,curlOutputFieldsFromSample,type CurlCreated,type CurlPreview,type CurlSelection} from '@/lib/plugin-curl-client';
import {FieldEditor} from './field-editor';
import styles from './plugin-curl-import.module.css';

export type CurlImportTarget={id:string;revision:number;name:string;connection_id?:string};
type Props={open:boolean;connections:Json[];target?:CurlImportTarget;preferredConnectionId?:string;onClose:(refresh?:boolean)=>void;onCreated:(value:CurlCreated)=>void};
const blankNames=()=>{const suffix=crypto.randomUUID().slice(0,8);return {plugin_name:'',create_connection_name:'',endpoint_id:`endpoint_${suffix}`,endpoint_name:'',tool_id:`tool_${suffix}`,tool_name:'',tool_description:''};};
function inputRows(fields:FieldDefinition[],prefix='',position?:FieldDefinition['position']):FieldDefinition[]{return fields.flatMap(field=>{
 const name=prefix?`${prefix}.${field.name}`:field.name,at=field.position||position;const {children,item,...row}=field;
 return [{...row,name,position:at},...(children?inputRows(children,name,at):[]),...(item?.children?inputRows(item.children,`${name}[]`,at):[])];
});}
export default function PluginCurlImport(props:Props){
 const latest=useRef(props);latest.current=props;const mounted=useRef(false),serial=useRef(0),previewAbort=useRef<AbortController|undefined>(undefined),busyRef=useRef('');
 const [command,setCommand]=useState(''),[sample,setSample]=useState(''),[preview,setPreview]=useState<CurlPreview>(),[busy,setBusy]=useState(''),[error,setError]=useState(''),[reference,setReference]=useState(''),[unknownCreation,setUnknownCreation]=useState(false);
 const [names,setNames]=useState(blankNames),[connectionMode,setConnectionMode]=useState<'existing'|'new'>('new'),[connectionId,setConnectionId]=useState<string>(),[importCredentials,setImportCredentials]=useState(false),[outputFields,setOutputFields]=useState<FieldDefinition[]>([]),[emptyOutput,setEmptyOutput]=useState(false),[sampleNotice,setSampleNotice]=useState('');
 function clearSensitive(){serial.current++;previewAbort.current?.abort();setCommand('');setSample('');setPreview(undefined);setOutputFields([]);setEmptyOutput(false);setImportCredentials(false);setSampleNotice('');setError('');setReference('');setUnknownCreation(false);setNames(blankNames());setConnectionId(undefined);}
 useEffect(()=>{const before=(event:Event)=>{if(latest.current.open&&busyRef.current==='confirm'){(event as CustomEvent).detail?.waitUntil?.(false);event.preventDefault();}};window.addEventListener('zhangpu:before-navigation',before);return()=>window.removeEventListener('zhangpu:before-navigation',before);},[]);
 useEffect(()=>{mounted.current=true;const invalidated=()=>{clearSensitive();latest.current.onClose();};const events=['zhangpu:access-denied','zhangpu:session-expired','zhangpu:permissions-changed','zhangpu:data-policy-changed'];events.forEach(name=>window.addEventListener(name,invalidated));return()=>{mounted.current=false;serial.current++;previewAbort.current?.abort();events.forEach(name=>window.removeEventListener(name,invalidated));};},[]);
 useEffect(()=>{clearSensitive();busyRef.current='';setBusy('');setNames(blankNames());setConnectionMode(props.target?.connection_id||props.preferredConnectionId?'existing':'new');setConnectionId(props.target?.connection_id||props.preferredConnectionId);},[props.open,props.target?.id,props.target?.revision,props.preferredConnectionId]);
 const matches=preview?matchingCurlConnections(preview,props.connections):[];
 const current=(ticket:number,epoch:number)=>mounted.current&&latest.current.open&&ticket===serial.current&&epoch===currentIdentityEpoch();
 function close(refresh=false){if(busyRef.current==='confirm')return;clearSensitive();latest.current.onClose(refresh);}
 function changeCommand(value:string){serial.current++;previewAbort.current?.abort();setCommand(value);setSample('');setSampleNotice('');setPreview(undefined);setOutputFields([]);setEmptyOutput(false);setImportCredentials(false);setError('');setReference('');setUnknownCreation(false);busyRef.current='';setBusy('');}
 function report(failure:unknown){const value=failure as {safeCurlImport?:boolean;message?:string;code?:string;request_id?:string};setError(value.safeCurlImport===true?value.message||'操作未完成，请重新核对配置。':'操作未完成，请重新核对配置。');setReference(value.safeCurlImport===true?[value.code,value.request_id].filter(Boolean).join(' · '):'');}
 async function parse(){
  if(busyRef.current)return;const ticket=++serial.current,epoch=currentIdentityEpoch();busyRef.current='preview';setBusy('preview');setError('');setReference('');setPreview(undefined);previewAbort.current?.abort();const controller=new AbortController();previewAbort.current=controller;
  try{const result=await pluginCurlClient.preview(command,AbortSignal.any([controller.signal,AbortSignal.timeout(15000)]));if(!current(ticket,epoch))return;setPreview(result);setImportCredentials(false);
   const compatible=matchingCurlConnections(result,latest.current.connections),preferred=latest.current.target?.connection_id||latest.current.preferredConnectionId;
   const match=compatible.find(item=>item.id===preferred)||(!latest.current.target?.connection_id?compatible[0]:undefined);setConnectionId(match?.id);setConnectionMode(match?'existing':latest.current.target?.connection_id?'existing':'new');
   setNames(value=>({...value,create_connection_name:value.create_connection_name||new URL(result.connection.base_url).hostname,plugin_name:value.plugin_name||'导入的插件',endpoint_name:value.endpoint_name||'导入的接口',tool_name:value.tool_name||'导入的接口工具'}));
  }catch(e){if(current(ticket,epoch))report(e);}finally{if(current(ticket,epoch)){busyRef.current='';setBusy('');}}
 }
 function inferOutput(){
  setSampleNotice('');try{const fields=curlOutputFieldsFromSample(sample);setOutputFields(fields);setEmptyOutput(false);setSample('');setSampleNotice(fields.length?'已生成输出字段，请核对类型与响应路径。':'样例为空对象；请手工补字段，或明确选择无输出字段。');}catch{setSampleNotice('样例需为64 KiB内的 JSON 对象；null、空列表或混合结构请手工指定字段类型。');}
 }
 async function confirm(){
  if(busyRef.current||!preview||!preview.can_confirm||unknownCreation)return;
  setError('');setReference('');
  const idPattern=/^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
  if(!idPattern.test(names.endpoint_id)||!idPattern.test(names.tool_id)||[names.endpoint_name,names.tool_name,names.tool_description,props.target?'existing':names.plugin_name,connectionMode==='new'?names.create_connection_name:'existing'].some(value=>!value.trim())){setError('请补全服务、插件、接口与工具名称及工具说明；标识需以字母开头。');return;}
  if(connectionMode==='existing'&&!matches.some(item=>item.id===connectionId)){setError('请选择同地址、路径与认证匹配的可用服务；已有插件不能隐式改绑服务。');return;}
  if(connectionMode==='new'&&preview.credentials_required&&!importCredentials){setError('命令含认证。请明确选择安全保存到新服务，或复用已经配置认证的服务。');return;}
  if(!outputFields.length&&!emptyOutput){setError('请补充输出字段，或明确确认此接口没有输出字段。');return;}
  try{validateFields(outputFields);}catch{setError('输出字段不完整，请检查字段名、类型与嵌套结构。');return;}
  const selection:CurlSelection={expected_preview_fingerprint:preview.fingerprint,...(connectionMode==='existing'?{connection_id:connectionId}:{create_connection_name:names.create_connection_name.trim()}),...(props.target?{plugin_id:props.target.id,expected_plugin_revision:props.target.revision}:{plugin_name:names.plugin_name.trim()}),endpoint_id:names.endpoint_id,endpoint_name:names.endpoint_name.trim(),tool_id:names.tool_id,tool_name:names.tool_name.trim(),tool_description:names.tool_description.trim(),output_fields:outputFields,import_credentials:connectionMode==='new'&&preview.credentials_required&&importCredentials};
  const ticket=++serial.current,epoch=currentIdentityEpoch();busyRef.current='confirm';setBusy('confirm');
  try{const result=await pluginCurlClient.confirm(command,selection);if(!current(ticket,epoch))return;clearSensitive();busyRef.current='';setBusy('');latest.current.onClose();latest.current.onCreated(result);}
  catch(e){if(current(ticket,epoch)){report(e);const status=(e as {status?:number}).status;setUnknownCreation(status===undefined||status>=500||status===409);}}
  finally{if(current(ticket,epoch)){busyRef.current='';setBusy('');}}
 }
 const field=(key:keyof ReturnType<typeof blankNames>,label:string,maxLength=100)=><label className={styles.field}>{label}<Input aria-label={label} maxLength={maxLength} disabled={!!busy} value={names[key]} onChange={e=>setNames(value=>({...value,[key]:e.target.value}))}/></label>;
 return <Modal title={props.target?`从 cURL 添加接口 · ${props.target.name}`:'从 cURL 导入插件'} open={props.open} width="min(1080px, calc(100vw - 24px))" maskClosable={false} destroyOnHidden onCancel={()=>close()} footer={<Space><Button disabled={busy==='confirm'} onClick={()=>close(unknownCreation)}>{unknownCreation?'关闭并刷新列表核对':'取消'}</Button><Button type="primary" disabled={!preview?.can_confirm||unknownCreation||!!busy} loading={busy==='confirm'} onClick={()=>void confirm()}>确认创建草稿</Button></Space>}>
  <p className="subtle">只解析命令与创建草稿，不会发送接口请求。支持原样粘贴多行命令；认证内容始终遮盖，关闭或账号变化后清空。</p>
  <label className={styles.field}>粘贴 cURL 命令<Input.Password aria-label="cURL 命令（遮盖）" value={command} visibilityToggle={false} autoComplete="off" spellCheck={false} disabled={busy==='confirm'||unknownCreation} onPaste={e=>{e.preventDefault();changeCommand(e.clipboardData.getData('text/plain'));}} onChange={e=>changeCommand(e.target.value)}/></label>
  <Space className={styles.commandActions}><Button disabled={!command||!!busy||unknownCreation} loading={busy==='preview'} onClick={()=>void parse()}>解析预览</Button><Button disabled={!!busy||unknownCreation} onClick={()=>changeCommand('')}>清空命令</Button></Space>
  {error&&<Alert type="error" message={error} description={reference||undefined}/>}
  {preview&&<>
   <section className={styles.preview}><h3>已识别的请求</h3><p><strong>服务地址：</strong>{preview.connection.base_url}</p><p><Tag>{preview.endpoint.method}</Tag><code>{preview.endpoint.path}</code><Tag>{{json:'JSON 正文',form_urlencoded:'表单编码',multipart:'文本表单'}[preview.endpoint.request_encoding]}</Tag></p><p>认证：{preview.connection.auth_type==='none'?'无需认证':preview.connection.auth_type==='bearer'?'Bearer Token（内容遮盖）':'API Key（内容遮盖）'}</p>
    <Table<FieldDefinition> rowKey="name" pagination={false} dataSource={inputRows(preview.endpoint.input_fields)} size="small" columns={[{title:'输入字段',dataIndex:'name'},{title:'位置',render:(_,value)=>({path:'路径',query:'查询参数',header:'请求头',body:'正文'}[value.position||'body'])},{title:'类型',render:(_,value)=>({string:'文本',integer:'整数',number:'数值',boolean:'布尔',object:'对象',array:'列表'}[value.type])},{title:'要求',render:(_,value)=>value.required?'必填':'可选'}]}/><p className="hint">请求值不在预览中回显。创建后可在接口编辑器补充或调整输入字段。</p>
   </section>
   <div className={styles.grid}>
    <section><h3>服务与插件</h3><label className={styles.field}>服务选择<Select aria-label="服务选择" value={connectionMode} disabled={!!busy||!!props.target?.connection_id} options={[{value:'existing',label:'复用已有服务'},{value:'new',label:'新建服务'}]} onChange={value=>{setConnectionMode(value);setImportCredentials(false);}}/></label>
     {connectionMode==='existing'?<label className={styles.field}>可用服务<Select aria-label="可用服务" value={connectionId} disabled={!!busy||!!props.target?.connection_id} placeholder="选择地址与认证匹配的服务" options={matches.map(item=>({value:item.id,label:item.name}))} onChange={setConnectionId}/><small>复用服务将使用其已配置认证；需要更新凭据时，请在服务设置中操作。</small>{!matches.length&&<small>没有匹配的可用服务。请先在服务设置核对地址、路径与认证。</small>}</label>:<>{field('create_connection_name','新服务名称')}{preview.credentials_required&&<Checkbox disabled={!!busy} checked={importCredentials} onChange={e=>setImportCredentials(e.target.checked)}>明确将命令中的认证安全保存到新服务</Checkbox>}</>}
     {props.target?<p>追加到现有插件草稿：<strong>{props.target.name}</strong>；保持原发布版本。</p>:field('plugin_name','新插件名称')}
    </section>
    <section><h3>接口与工具</h3>{field('endpoint_name','接口名称')}{field('endpoint_id','接口标识',64)}{field('tool_name','工具名称')}{field('tool_id','工具标识',64)}{field('tool_description','工具说明',2000)}</section>
   </div>
   <section className={styles.preview}><h3>明确输出字段</h3><p className="subtle">命令不包含响应信息，不能据此推断输出。请填写字段，或粘贴你已有的响应 JSON 样例。</p><label className={styles.field}>响应 JSON 样例<Input.Password aria-label="响应 JSON 样例（遮盖）" value={sample} visibilityToggle={false} autoComplete="off" spellCheck={false} disabled={!!busy} onPaste={e=>{e.preventDefault();setSample(e.clipboardData.getData('text/plain'));}} onChange={e=>setSample(e.target.value)}/></label><Button disabled={!sample||!!busy} onClick={inferOutput}>从样例提取字段</Button>{sampleNotice&&<p role="status">{sampleNotice}</p>}<FieldEditor output value={outputFields} onChange={fields=>{if(!busy){setOutputFields(fields);setEmptyOutput(false);}}}/><Checkbox disabled={!!busy||outputFields.length>0} checked={emptyOutput} onChange={e=>setEmptyOutput(e.target.checked)}>已确认此接口没有输出字段</Checkbox></section>
  </>}
 </Modal>;
}
