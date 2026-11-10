'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import {Alert,App,Button,Checkbox,Form,Input,InputNumber,Modal,Popconfirm,Select,Space,Switch,Table,Tag} from 'antd';
import {currentIdentityEpoch,Json,list,request,send} from '@/lib/api';
import {errorMessage} from '@/lib/errors';
import {canOpenPage} from '@/lib/permissions';
import {assertModelReasoningProfile,assertModelVisionProfile,parameterLabels,reasoningBlockedParameters} from '@/lib/model-catalog';
import ModelReasoningProfile from './model-reasoning-profile';
import ModelVisionProfile from './model-vision-profile';
import ModelReviewQueue from './model-review-queue';
import {menuLabels} from '@/lib/ui-labels';
import {trialPassed,trialReceipt,type TrialReceipt} from '@/lib/model-trial';
import {useSession} from './auth-gate';
import {PageTitle,SupportReference} from './shared';
import {useLeaveConfirmation} from './workflow-studio/use-leave-confirmation';
import styles from './model-management.module.css';

const modalLayers={management:1000,templates:1100,editor:1200,result:1300,confirmation:1500};
type CheckRecord={order:number;identityEpoch:number;status:'passed'|'failed'|'unconfirmed';at:string;providerId:string;providerRevision:number;modelId?:string;modelRevision?:number;modelName?:string};
const checkKey=(epoch:number,provider:Json,model?:Json)=>`${epoch}:${provider.id}:${provider.revision}${model?`:${model.id}:${model.revision}`:''}`;
const revisionKnown=(value:Json)=>Number.isInteger(value.revision)&&value.revision>0;
export default function ModelManagement(){
 const {session}=useSession(),{modal}=App.useApp();const manage=canOpenPage(session.user,'models'),reviewer=session.user.role==='super_admin';
 const [presets,setPresets]=useState<Json[]>([]),[presetsError,setPresetsError]=useState(''),[presetsLoading,setPresetsLoading]=useState(false),[templatesOpen,setTemplatesOpen]=useState(false);
 const [providers,setProviders]=useState<Json[]>([]),[selected,setSelected]=useState<Json>(),[models,setModels]=useState<Json[]>([]);
 const [connectionChecks,setConnectionChecks]=useState<Record<string,CheckRecord>>({}),[inferenceChecks,setInferenceChecks]=useState<Record<string,CheckRecord>>({});const policyEpoch=useRef(0);
 const [managementOpen,setManagementOpen]=useState(false);const managementOpenRef=useRef(false),providerButtons=useRef<Record<string,HTMLButtonElement|null>>({});
 const [providersLoading,setProvidersLoading]=useState(true),[loading,setLoading]=useState(false),[providersError,setProvidersError]=useState(''),[modelsError,setModelsError]=useState('');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[errorTitle,setErrorTitle]=useState('操作未完成'),[failure,setFailure]=useState<{status?:number;code?:string;request_id?:string}>(),[notice,setNotice]=useState('');
 const [testTarget,setTestTarget]=useState<{kind:'connection'|'inference';providerId:string;providerName:string;modelId?:string;modelName?:string;epoch:number;selection:number;ticket:number}>();
 const [trialText,setTrialText]=useState(''),[trialLimit,setTrialLimit]=useState<number|null>(512),[trial,setTrial]=useState<TrialReceipt>(),[trialSent,setTrialSent]=useState(false);
 const [editing,setEditing]=useState<{kind:'provider'|'model';value?:Json}>(),[dirty,setDirty]=useState(false);const [form]=Form.useForm();
 const sequence=useRef(0),providerSequence=useRef(0),presetSequence=useRef(0),actionSequence=useRef(0),alive=useRef(false),selectedRef=useRef<Json|undefined>(undefined),lock=useRef(false),dirtyRef=useRef(false);dirtyRef.current=dirty;
 const [confirming,setConfirming]=useState(false);const confirmationGeneration=useRef(0),confirmationReturnFocus=useRef<HTMLElement|null>(null),managerContent=useRef<HTMLElement|null>(null),childReturnFocus=useRef<HTMLElement|null>(null),creationReturnFocus=useRef<HTMLElement|null>(null);
 const overlayState=useRef({editing:false,result:false,templates:false,confirming:false});overlayState.current={editing:!!editing,result:!!testTarget,templates:templatesOpen,confirming};
 const portalContainer=useCallback(()=>document.body,[]);
 const authType=Form.useWatch('auth_type',form),presetId=Form.useWatch('preset',form);
 const [profileSupported,setProfileSupported]=useState(false),actualModel=Form.useWatch('model_name',form),profile=Form.useWatch(['capabilities','reasoning_profile'],form),declaredParameters=Form.useWatch(['capabilities','parameters'],form);
 const [visionSupported,setVisionSupported]=useState(false);
 const valid=(epoch:number)=>alive.current&&epoch===currentIdentityEpoch();
 const {guard,dismiss}=useLeaveConfirmation({dirty:()=>dirtyRef.current,blocked:()=>lock.current,container:()=>document.body,show:options=>{confirmationGeneration.current++;const active=document.activeElement;confirmationReturnFocus.current=active&&typeof (active as HTMLElement).focus==='function'?active as HTMLElement:null;setConfirming(true);return modal.confirm({...options,title:'模型配置尚未保存',zIndex:modalLayers.confirmation,keyboard:true,focusTriggerAfterClose:false});},onError:()=>{setFailure(undefined);setError('暂时无法显示离开提示，请稍后重试。');}});
 const checkLeave=useCallback(async()=>{const epoch=currentIdentityEpoch(),decision=guard();if(typeof decision==='boolean')return decision;const generation=confirmationGeneration.current;try{return await decision;}finally{if(valid(epoch)&&generation===confirmationGeneration.current){setConfirming(false);if(confirmationReturnFocus.current?.isConnected)confirmationReturnFocus.current.focus({preventScroll:true});}}},[guard]);
 useEffect(()=>{const before=(event:Event)=>(event as CustomEvent).detail?.waitUntil?.(checkLeave());window.addEventListener('zhangpu:before-navigation',before);return()=>{window.removeEventListener('zhangpu:before-navigation',before);};},[checkLeave]);
 useEffect(()=>{
  alive.current=true;
  const expireChecks=()=>{policyEpoch.current++;setConnectionChecks({});setInferenceChecks({});setTestTarget(undefined);setTrialText('');setTrial(undefined);setTrialSent(false);setNotice('');setError('');setFailure(undefined);};
  const clear=()=>{expireChecks();confirmationGeneration.current++;setConfirming(false);confirmationReturnFocus.current=null;childReturnFocus.current=null;creationReturnFocus.current=null;dismiss();managementOpenRef.current=false;setManagementOpen(false);sequence.current++;providerSequence.current++;presetSequence.current++;actionSequence.current++;lock.current=false;setBusy(false);setLoading(false);setProvidersLoading(false);setPresetsLoading(false);selectedRef.current=undefined;setSelected(undefined);setProviders([]);setModels([]);setPresets([]);setEditing(undefined);setTemplatesOpen(false);setTestTarget(undefined);setDirty(false);form.resetFields();setNotice('');setError('');setFailure(undefined);setProvidersError('');setModelsError('');setPresetsError('');};
  const policyEvents=['zhangpu:data-policy-changed','zhangpu:permissions-changed'];policyEvents.forEach(name=>window.addEventListener(name,expireChecks));
  const events=['zhangpu:access-denied','zhangpu:session-expired'];events.forEach(name=>window.addEventListener(name,clear));
  void load();
  return()=>{alive.current=false;sequence.current++;providerSequence.current++;presetSequence.current++;form.resetFields();events.forEach(name=>window.removeEventListener(name,clear));policyEvents.forEach(name=>window.removeEventListener(name,expireChecks));};
 },[form,dismiss]);
 async function choose(provider:Json){
  const id=++sequence.current,epoch=currentIdentityEpoch();setTestTarget(undefined);selectedRef.current=provider;setSelected(provider);setModels([]);setLoading(true);setModelsError('');setError('');setFailure(undefined);setNotice('');
  try{const items=await list(`/model-providers/${encodeURIComponent(provider.id)}/models`,{signal:AbortSignal.timeout(15000)});if(valid(epoch)&&id===sequence.current)setModels(items);}
  catch(e){if(valid(epoch)&&id===sequence.current)setModelsError(errorMessage(e));}
  finally{if(valid(epoch)&&id===sequence.current)setLoading(false);}
 }
 async function load(refreshModels=false){
  const id=++providerSequence.current,epoch=currentIdentityEpoch();setProvidersLoading(true);setProvidersError('');
  try{const items=await list('/model-providers',{signal:AbortSignal.timeout(15000)});if(!valid(epoch)||id!==providerSequence.current)return;
   setProviders(items);const current=selectedRef.current;const provider=items.find(p=>p.id===current?.id);
   if(provider){if(managementOpenRef.current&&refreshModels)await choose(provider);else{selectedRef.current=provider;setSelected(provider);}}
   else{sequence.current++;managementOpenRef.current=false;setManagementOpen(false);setTestTarget(undefined);selectedRef.current=undefined;setSelected(undefined);setModels([]);setModelsError('');setLoading(false);}
  }catch(e){if(valid(epoch)&&id===providerSequence.current)setProvidersError(errorMessage(e));}
  finally{if(valid(epoch)&&id===providerSequence.current)setProvidersLoading(false);}
 }
 async function loadPresets(){const id=++presetSequence.current,epoch=currentIdentityEpoch();setPresetsLoading(true);setPresetsError('');
  try{const items=await list('/model-provider-presets',{signal:AbortSignal.timeout(15000)});if(valid(epoch)&&id===presetSequence.current)setPresets(items);}
  catch(e){if(valid(epoch)&&id===presetSequence.current)setPresetsError(errorMessage(e));}
  finally{if(valid(epoch)&&id===presetSequence.current)setPresetsLoading(false);}
 }
 async function selectProvider(provider:Json){if(lock.current)return;const epoch=currentIdentityEpoch();if(await checkLeave()!==true||!valid(epoch))return;setEditing(undefined);setDirty(false);form.resetFields();managementOpenRef.current=true;setManagementOpen(true);await choose(provider);}
 async function closeManagement(){
  if(editing||testTarget||templatesOpen)return;const epoch=currentIdentityEpoch();if(await checkLeave()!==true||!valid(epoch))return;
  sequence.current++;managementOpenRef.current=false;setManagementOpen(false);setModels([]);setModelsError('');setLoading(false);setError('');setFailure(undefined);setNotice('');
 }
 function captureManagerFocus(){const active=document.activeElement;childReturnFocus.current=active&&managerContent.current?.contains(active)?active as HTMLElement:null;}
 function restoreInnerFocus(){
  const state=overlayState.current;if(!alive.current||state.editing||state.result||state.templates||state.confirming)return;
  if(managementOpenRef.current){const previous=childReturnFocus.current;if(previous?.isConnected&&managerContent.current?.contains(previous))previous.focus({preventScroll:true});else managerContent.current?.focus({preventScroll:true});}
  else if(creationReturnFocus.current?.isConnected)creationReturnFocus.current.focus({preventScroll:true});
 }
 function addProvider(){const active=document.activeElement;creationReturnFocus.current=active&&typeof (active as HTMLElement).focus==='function'?active as HTMLElement:null;setError('');setFailure(undefined);setNotice('');setTemplatesOpen(true);void loadPresets();}
 function edit(kind:'provider'|'model',value?:Json,preset?:Json){if(lock.current)return;captureManagerFocus();setTestTarget(undefined);setTemplatesOpen(false);setError('');setFailure(undefined);setNotice('');setDirty(false);setEditing({kind,value});form.resetFields();
  form.setFieldsValue({visibility:value?.visibility||(reviewer?'public':'private'),...(kind==='provider'?{name:value?.name||preset?.name||'',protocol:'openai_compatible',preset:value?.preset||preset?.id||'custom',base_url:value?.base_url||preset?.base_url||'',auth_type:value?.auth_type||preset?.auth_type||'api_key',timeout_seconds:value?.timeout_seconds??20,enabled:value?.enabled??true,allow_private_network:value?.allow_private_network??false,api_key:'',clear_api_key:false,retain_api_key:false}:{name:value?.name||'',model_name:value?.model_name||'',enabled:value?.enabled??true,context_window:value?.context_window??131072,max_output_tokens:value?.max_output_tokens??16384,capabilities:value?.capabilities||{parameters:['temperature','max_tokens'],tools:false,json_object:false,json_schema:false,reasoning_summary:false,reasoning_explanation:false}})});
 }
 async function act(fn:(current:()=>boolean)=>Promise<void>,title='操作未完成'){
  if(lock.current)return;lock.current=true;setBusy(true);setError('');setErrorTitle(title);setNotice('');setFailure(undefined);const action=++actionSequence.current,epoch=currentIdentityEpoch(),id=sequence.current,policy=policyEpoch.current;const current=()=>valid(epoch)&&id===sequence.current&&action===actionSequence.current&&policy===policyEpoch.current;
  try{await fn(current);}catch(e){if(current()){const details=e as {status?:number;code?:string;request_id?:string};const policyTest=details?.status===403&&details?.code==='CONNECTION_BLOCKED'&&(title==='测试推理未完成'||title==='连接测试未完成');setError(policyTest?'连接被网络安全策略阻止。请核对服务地址和代理或 DNS 设置后重试；配置已保留。':errorMessage(e));setFailure({status:details?.status,code:details?.code,request_id:details?.request_id});}}finally{if(action===actionSequence.current){lock.current=false;if(valid(epoch))setBusy(false);}}
 }
 function recordCheck(kind:'connection'|'inference',provider:Json,model:Json|undefined,status:CheckRecord['status']){
  if(!revisionKnown(provider)||model&&!revisionKnown(model))return;
  const value:CheckRecord={order:actionSequence.current,identityEpoch:currentIdentityEpoch(),status,at:new Date().toISOString(),providerId:provider.id,providerRevision:provider.revision,...(model?{modelId:model.id,modelRevision:model.revision,modelName:model.name}:{})};
  (kind==='inference'?setInferenceChecks:setConnectionChecks)(items=>({...items,[checkKey(currentIdentityEpoch(),provider,model)]:value}));
 }
 function expireProviderChecks(providerId:string){
  setConnectionChecks(items=>Object.fromEntries(Object.entries(items).filter(([,value])=>value.providerId!==providerId)));
  setInferenceChecks(items=>Object.fromEntries(Object.entries(items).filter(([,value])=>value.providerId!==providerId)));
 }
 function checkView(value?:CheckRecord){return value?<><Tag>{value.status==='passed'?'已通过':value.status==='failed'?'失败':'未确认'}</Tag><small className={styles.checkTime}><time dateTime={value.at}>{new Date(value.at).toLocaleString('zh-CN',{hour12:false})}</time></small></>:<span className="subtle">暂无本页记录</span>;}
 function prepareTrial(modelId:string){
  const provider=selectedRef.current,model=models.find(item=>item.id===modelId);if(!manage||lock.current||editing||templatesOpen||!provider||!model)return;
  captureManagerFocus();setError('');setFailure(undefined);setNotice('');setTrialText('');setTrial(undefined);setTrialSent(false);setTrialLimit(Math.min(512,model.max_output_tokens||512));
  setTestTarget({kind:'inference',providerId:provider.id,providerName:provider.name,modelId:model.id,modelName:model.name,epoch:currentIdentityEpoch(),selection:sequence.current,ticket:actionSequence.current});
 }
 async function test(kind:'connection'|'inference',modelId?:string){
  const provider=selectedRef.current,model=kind==='inference'?models.find(item=>item.id===modelId):undefined;
  if(!manage||lock.current||editing||templatesOpen||!provider||kind==='inference'&&!model)return;
  if(kind==='inference'&&(!trialText.trim()||trialText.length>8000||!Number.isInteger(trialLimit)||trialLimit!<1||trialLimit!>Math.min(393216,model!.max_output_tokens)))return;
  setTrial(undefined);setTrialSent(kind==='inference');
  if(!testTarget)captureManagerFocus();setTestTarget({kind,providerId:provider.id,providerName:provider.name,modelId:model?.id,modelName:model?.name,epoch:currentIdentityEpoch(),selection:sequence.current,ticket:actionSequence.current+1});
  await act(async current=>{
   try{
    const result=await send(kind==='inference'?`/models/${encodeURIComponent(model!.id)}/test-prompt`:`/model-providers/${encodeURIComponent(provider.id)}/test-connection`,kind==='inference'?{text:trialText,max_output_tokens:trialLimit}:{});
    if(!current())return;
    recordCheck(kind,provider,model,(kind==='inference'?trialPassed(result):result.connected===true)?'passed':'unconfirmed');
    if(kind==='inference')setTrial(trialReceipt(result));
    setNotice(kind==='inference'?(trialPassed(result)?'请求已发送，模型已返回有效响应；回答质量未评判。':'尚未取得完整的发送与有效响应回执。'):('本次仅检查平台连接，未发起推理。'+(result.connected===true?(result.model_listing_supported===false?'地址有响应，但未提供模型列表。':result.model_listing_supported===true?'平台模型列表地址已响应。':'平台地址已响应。'):'连接结果尚未确认。')));
   }catch(e){if(current()){recordCheck(kind,provider,model,'failed');if(kind==='inference')setTrial(trialReceipt((e as {details?:Json}).details||{}));}throw e;}
  },kind==='inference'?'测试推理未完成':'连接测试未完成');
 }
 async function save(){if(!manage||!editing)return;const target=editing,parent=selectedRef.current;await act(async current=>{
  const value=await form.validateFields();if(!current())return;let path:string,body:Json;
  if(target.kind==='provider'){path=target.value?`/model-providers/${encodeURIComponent(target.value.id)}`:'/model-providers';body={name:value.name,protocol:'openai_compatible',preset:value.preset,base_url:value.base_url,auth_type:value.auth_type,timeout_seconds:value.timeout_seconds,...(reviewer?{allow_private_network:!!value.allow_private_network}:{}),enabled:!!value.enabled,...(value.api_key?{api_key:value.api_key}:{}),clear_api_key:!!value.clear_api_key,retain_api_key:!!value.retain_api_key};}
  else{if(!parent)throw Error('请先选择平台。');if(value.capabilities?.reasoning_profile!=null)assertModelReasoningProfile(value.capabilities.reasoning_profile,value.model_name,profileSupported,value.capabilities.parameters||[],value.capabilities.tools===true);if(value.capabilities?.vision_profile!=null)assertModelVisionProfile(value.capabilities.vision_profile,value.model_name,visionSupported,value.capabilities.reasoning_profile);path=target.value?`/models/${encodeURIComponent(target.value.id)}`:`/model-providers/${encodeURIComponent(parent.id)}/models`;body={name:value.name,model_name:value.model_name,purpose:'chat',enabled:!!value.enabled,context_window:value.context_window,max_output_tokens:value.max_output_tokens,capabilities:value.capabilities};if(body.max_output_tokens>=body.context_window)throw Error('最大输出长度必须小于上下文上限。');}
  if(!target.value)body.visibility=reviewer?'public':value.visibility;
  if(target.value)body.expected_revision=target.value.revision;
  const result=await send(path,body,target.value?'PATCH':'POST');if(!current())return;
  if(target.kind==='provider')expireProviderChecks(result.id);else setInferenceChecks(items=>Object.fromEntries(Object.entries(items).filter(([,value])=>value.modelId!==result.id)));
  setEditing(undefined);setDirty(false);form.resetFields();providerSequence.current++;setProvidersLoading(false);
  if(target.kind==='provider'){
   managementOpenRef.current=true;setManagementOpen(true);
   // The authoritative create response selects the new card before any optional list reread.
   setProviders(items=>target.value?items.map(p=>p.id===result.id?result:p):[result,...items.filter(p=>p.id!==result.id)]);void choose(result);
  }else if(parent)void choose(parent);
  window.dispatchEvent(new Event('zhangpu:model-catalog-changed'));setNotice(result.review_status==='pending'?'已提交超级管理员审批。':'配置已保存，可以测试连接和推理。');
 },'配置未保存');}
 function sharing(value:Json){
  const label=(scope:string)=>scope==='public'?'公开':'私有';
  return <><Tag>{value.approved?label(value.visibility):'未生效'}</Tag>
   {value.review_status==='pending'&&<Tag color="processing">待审批 · {label(value.requested_visibility)}</Tag>}
   {value.review_status==='rejected'&&<Tag color="error">申请未通过</Tag>}</>;
 }
 function visibilityAction(kind:'provider'|'model',value:Json){
  const desired=value.visibility==='public'?'private':'public';
  if(!value.can_manage||value.review_status==='pending'||reviewer&&desired==='private')return null;
  return <Popconfirm title={`申请改为${desired==='public'?'公开':'私有'}？`} description="批准前保留当前可用范围。"
   onConfirm={()=>act(async current=>{
    const route=kind==='provider'?'/model-providers/':'/models/';
    await send(route+encodeURIComponent(value.id),{expected_revision:value.revision,visibility:desired},'PATCH');
    if(!current())return;
    await load(true);if(current())setNotice('可用范围变更已提交审批。');
   },'可用范围申请未提交')}>
   <Button size="small" disabled={busy}>{desired==='public'?'申请公开':'申请转为私有'}</Button>
  </Popconfirm>;
 }
 const sharingField=!editing?.value&&<Form.Item name="visibility" label="可用范围" rules={[{required:true}]}
   extra={reviewer?'超级管理员发布公开配置。':'提交后由超级管理员审批。'}>
  <Select disabled={reviewer} options={[
   ...(!reviewer?[{value:'private',label:'私有 · 仅自己使用'}]:[]),
   {value:'public',label:'公开 · 当前工作空间',disabled:editing?.kind==='model'&&selected?.visibility!=='public'&&selected?.requested_visibility!=='public'},
  ]}/>
 </Form.Item>;
 const recentInference=selected&&Object.values(inferenceChecks).filter(value=>value.identityEpoch===currentIdentityEpoch()&&value.providerId===selected.id&&value.providerRevision===selected.revision&&models.some(model=>model.id===value.modelId&&model.revision===value.modelRevision)).sort((a,b)=>b.order-a.order)[0];
 const shownInference=testTarget?.modelId&&selected?inferenceChecks[checkKey(currentIdentityEpoch(),selected,models.find(model=>model.id===testTarget.modelId)||{})]:recentInference;
 const providerFields=<><Form.Item name="name" label="平台名称" rules={[{required:true,whitespace:true,max:100,message:'请输入平台名称（最多100字）'}]}><Input/></Form.Item><Form.Item name="preset" label="协议预设"><Select loading={presetsLoading} options={[...(!presets.some(p=>p.id===presetId)?[{value:presetId||'custom',label:!presetId||presetId==='custom'?'自定义 OpenAI-compatible':'已保存平台类型'}]:[]),...presets.map(p=>({value:p.id,label:p.name}))]} onChange={value=>{const preset=presets.find(p=>p.id===value);if(preset)form.setFieldsValue({protocol:'openai_compatible',base_url:preset.base_url,auth_type:preset.auth_type});}}/></Form.Item><Form.Item name="base_url" label="服务URL" rules={[{required:true,message:'请输入服务URL'}]} extra="支持根地址、/v1、/api/v3或/compatible-mode/v1；不要填写/chat/completions。地址可按业务空间和地域修改。"><Input placeholder="https://your-service.example/v1" autoComplete="off"/></Form.Item><Form.Item name="auth_type" label="认证"><Select options={[{value:'api_key',label:'API Key'},{value:'none',label:'无需认证'}]}/></Form.Item>
 {editing?.value?.has_key&&<p className="hint">已保存认证凭据，不回显明文。密钥留空保持原值。</p>}{authType==='api_key'&&<Form.Item name="api_key" label="新API Key" extra="只有填写新值才替换已存密钥。"><Input.Password autoComplete="new-password"/></Form.Item>}
 {editing?.value?.has_key&&<><Form.Item name="clear_api_key" valuePropName="checked"><Checkbox>明确清除已存密钥</Checkbox></Form.Item><Form.Item name="retain_api_key" valuePropName="checked"><Checkbox>修改服务URL后，明确允许向新地址发送已存密钥</Checkbox></Form.Item></>}
 <div className="field-grid"><Form.Item name="timeout_seconds" label="超时（秒）"><InputNumber min={0.1} max={300} step={0.1}/></Form.Item><Form.Item name="enabled" valuePropName="checked" label="启用平台"><Switch/></Form.Item></div>{reviewer&&<Form.Item name="allow_private_network" valuePropName="checked"><Checkbox>明确允许访问本机或私有网络服务</Checkbox></Form.Item>}</>;
 const modelFields=<><Form.Item name="name" label="模型显示名" rules={[{required:true,whitespace:true,max:100}]}><Input/></Form.Item><Form.Item name="model_name" label="服务实际model名称" rules={[{required:true,whitespace:true,max:200}]} extra="使用服务要求的模型名称；普通使用者只从目录选择显示名。"><Input/></Form.Item><div className="field-grid"><Form.Item name="context_window" label="上下文上限"><InputNumber min={512} max={1048576}/></Form.Item><Form.Item name="max_output_tokens" label="最大输出长度"><InputNumber min={1} max={393216}/></Form.Item></div><Form.Item name={['capabilities','parameters']} label="支持的生成参数"><Checkbox.Group options={Object.entries(parameterLabels).map(([value,label])=>({value,label}))}/></Form.Item><Form.Item name={['capabilities','reasoning_profile']}><ModelReasoningProfile active={editing?.kind==='model'} modelId={actualModel} parameters={declaredParameters||[]} onSupported={setProfileSupported} onRemoveUnsupported={()=>{form.setFieldValue(['capabilities','parameters'],(declaredParameters||[]).filter((key:string)=>!reasoningBlockedParameters(profile).includes(key)));setDirty(true);}}/></Form.Item><Form.Item name={['capabilities','vision_profile']}><ModelVisionProfile active={editing?.kind==='model'} modelId={actualModel} reasoning={profile} onSupported={setVisionSupported}/></Form.Item><div className="model-capability-switches">{[['tools','智能体工具调用'],['json_object','JSON对象输出'],['json_schema','JSON Schema结构化输出'],['reasoning_summary','服务提供公开思考摘要'],['reasoning_explanation','服务提供公开推理说明']].map(([key,label])=><Form.Item key={key} name={['capabilities',key]} valuePropName="checked" label={label}><Switch/></Form.Item>)}</div><Form.Item name="enabled" valuePropName="checked" label="启用模型"><Switch/></Form.Item><p className="hint">使用授权与管理权限独立；能力声明不代表连接或真实推理已通过。思考摘要或推理说明仅在服务明确支持时开启，结果按实际返回展示。</p></>;
 return <div className={styles.page}>
 <PageTitle eyebrow="MODELS" title={menuLabels.models} description="添加自己的平台和模型，或使用工作空间共享的模型。" action={manage&&<Button type="primary" onClick={addProvider} disabled={busy}>添加平台</Button>}/>
 {reviewer&&<ModelReviewQueue onChange={()=>{void load(true);window.dispatchEvent(new Event('zhangpu:model-catalog-changed'));}}/>}<section className={styles.providers} aria-label="已创建的平台" aria-busy={providersLoading}>
  <div className={styles.sectionHeading}><div><h2>已创建的平台 <span>{providers.length}</span></h2><p>点击整张卡片，打开平台管理窗口。</p></div><Button onClick={()=>void load(true)} loading={providersLoading} disabled={busy}>刷新平台</Button></div>
  {providersError&&<Alert type="error" message="平台读取失败" description={providersError} action={<Button onClick={()=>void load(true)} loading={providersLoading}>重试读取平台</Button>}/>}
  <div className={styles.cards}>{providers.map(p=><button key={p.id} type="button" ref={element=>{providerButtons.current[p.id]=element;}} aria-haspopup="dialog" aria-expanded={managementOpen&&selected?.id===p.id} aria-label={`选择平台：${p.name}`} aria-pressed={selected?.id===p.id} className={`${styles.card} ${selected?.id===p.id?styles.selected:''}`} disabled={busy} onClick={()=>void selectProvider(p)}>
   <span className={styles.cardTop}><span className={styles.monogram} aria-hidden="true">{String(p.name||'M').slice(0,1)}</span><span className={styles.selection}>{selected?.id===p.id?'✓ 已选择 · 管理平台 →':'管理平台 →'}</span></span>
   <strong>{p.name}</strong><span>{sharing(p)}</span><span className={styles.endpoint}>{p.base_url}</span><span className={styles.cardBottom}><span>{p.enabled?'已启用':'已停用'}</span><span>{p.auth_type==='none'?'无需认证':p.has_key?'已配置 Key':'待配置 Key'}</span></span>
  </button>)}</div>
  {!providers.length&&!providersLoading&&!providersError&&<div className={styles.empty}><h3>添加第一个模型平台</h3><p>选择平台模板或填写自定义地址，再配置认证与模型。</p>{manage&&<Button onClick={addProvider}>添加平台</Button>}</div>}
  {providersLoading&&!providers.length&&<p role="status" className="subtle">正在读取平台…</p>}
 </section>
 <Modal zIndex={modalLayers.management} getContainer={portalContainer} focusTriggerAfterClose={false} keyboard={!editing&&!testTarget&&!templatesOpen&&!confirming} closable={!busy&&!editing&&!testTarget&&!templatesOpen&&!confirming} width="min(1120px, calc(100vw - 32px))" centered className={styles.managementModal} title={selected?`管理平台 · ${selected.name}`:'管理模型平台'} open={managementOpen&&!!selected} footer={<Button disabled={busy||!!editing||!!testTarget||templatesOpen||confirming} onClick={()=>void closeManagement()}>关闭管理</Button>} maskClosable={false} styles={{body:{maxHeight:'72vh',overflowY:'auto'}}} onCancel={()=>void closeManagement()} afterClose={()=>{if(alive.current&&!managementOpenRef.current&&!overlayState.current.editing&&!overlayState.current.result&&!overlayState.current.templates&&!overlayState.current.confirming)providerButtons.current[selectedRef.current?.id||'']?.focus();}}>
 <section ref={managerContent} tabIndex={-1} className={styles.managementContent} aria-label="平台模型与设置">
 {notice&&<Alert type="info" message={notice}/>}
 {selected?<>
  <div className={styles.detailHeading}><div><span className={styles.eyebrow}>当前平台</span><h2>{selected.name}</h2><p className={styles.detailEndpoint}>{selected.base_url}</p><Space wrap>{sharing(selected)}<Tag>{selected.enabled?'平台已启用':'平台已停用'}</Tag><Tag>{selected.auth_type==='none'?'无需认证':selected.has_key?'已配置认证':'尚未配置认证'}</Tag></Space></div>
   {selected.can_manage&&<Space wrap>{visibilityAction('provider',selected)}<Button disabled={busy} onClick={()=>edit('provider',selected)}>平台设置</Button><Button disabled={busy||!selected.approved} onClick={()=>void test('connection')}>测试连接</Button><Button type="primary" disabled={busy} onClick={()=>edit('model')}>添加模型</Button><Popconfirm title="归档此平台？有子模型或引用时服务会拒绝。" disabled={busy} onConfirm={()=>act(async current=>{await request(`/model-providers/${encodeURIComponent(selected.id)}?expected_revision=${selected.revision}`,{method:'DELETE'});if(!current())return;sequence.current++;managementOpenRef.current=false;setManagementOpen(false);selectedRef.current=undefined;setSelected(undefined);setModels([]);setProviders(items=>items.filter(p=>p.id!==selected.id));await load();window.dispatchEvent(new Event('zhangpu:model-catalog-changed'));})}><Button disabled={busy} danger>归档平台</Button></Popconfirm></Space>}
  </div>
  {busy&&<p role="status" className="subtle">正在处理，请稍候…</p>}
  {error&&<Alert type="error" message={errorTitle} description={<>{error}<SupportReference value={failure?.request_id}/></>}/>}
  <div className={styles.checkSummary}><strong>平台连接检查</strong>{selected&&checkView(connectionChecks[checkKey(currentIdentityEpoch(),selected)])}<p>以下为本页会话记录，仅对应当前配置；刷新页面后不保留。</p></div>
  <div className={styles.modelHeading}><h3>平台下的模型 <span>{models.length}</span></h3><p>连接测试检查平台服务；测试推理由你填写内容并主动发送到所选模型。</p></div>
  {modelsError&&<Alert type="error" message="模型读取失败" description={modelsError} action={<Button onClick={()=>void choose(selected)} loading={loading}>重试读取模型</Button>}/>}
  <Table<Json> loading={loading} rowKey="id" dataSource={models} pagination={false} scroll={{x:900}} locale={{emptyText:modelsError?'读取失败，请重试':loading?'正在读取模型…':<div className={styles.modelEmpty}><h3>此平台还没有模型</h3><p>添加模型后，可设置工作空间使用范围并测试推理。</p>{selected.can_manage&&<Button type="primary" disabled={busy} onClick={()=>edit('model')}>添加第一个模型</Button>}</div>}} columns={[
   {title:'模型',render:(_,m)=><><strong>{m.name}</strong><small className="model-meta">{m.model_name}</small></>},
   {title:'状态与能力',render:(_,m)=><Space wrap><Tag>{m.enabled?'启用':'停用'}</Tag>{sharing(m)}{m.capabilities?.json_schema&&<Tag>结构化输出</Tag>}</Space>},
   {title:'最近推理',render:(_,m)=>checkView(inferenceChecks[checkKey(currentIdentityEpoch(),selected,m)])},
   {title:'上下文 / 最大输出',render:(_,m)=>`${m.context_window} / ${m.max_output_tokens}`},
   {title:'操作',render:(_,m)=>m.can_manage?<Space wrap>{visibilityAction('model',m)}<Button size="small" disabled={busy} onClick={()=>edit('model',m)}>编辑</Button><Button size="small" disabled={busy||!selected.approved||!m.approved} onClick={()=>prepareTrial(m.id)}>测试推理</Button><Popconfirm title="归档此模型？已有引用时服务会拒绝。" disabled={busy} onConfirm={()=>act(async current=>{await request(`/models/${encodeURIComponent(m.id)}?expected_revision=${m.revision}`,{method:'DELETE'});if(!current())return;await choose(selected);window.dispatchEvent(new Event('zhangpu:model-catalog-changed'));})}><Button size="small" disabled={busy} danger>归档</Button></Popconfirm></Space>:<span>只读</span>}
  ]}/>
 </>:<div className={styles.empty}><p>{providers.length?'选择一个平台查看模型与设置。':'创建平台后，可在这里管理模型与测试推理。'}</p>{error&&<Alert type="error" message={errorTitle} description={<>{error}<SupportReference value={failure?.request_id}/></>}/>}</div>}
 </section>
 </Modal>
 {manage&&<div className={styles.addFromTemplate} aria-label="底部添加平台入口"><span>需要新增模型平台？</span><Button type="link" disabled={busy} onClick={addProvider}>从模板添加平台</Button></div>}
 <Modal zIndex={modalLayers.result} getContainer={portalContainer} focusTriggerAfterClose={false} afterClose={restoreInnerFocus} keyboard={!confirming} maskClosable={false} styles={{body:{maxHeight:'calc(100vh - 200px)',overflowY:'auto'}}} width={680} title={testTarget?.kind==='inference'?`测试推理 · ${testTarget.modelName}`:'平台连接测试'} open={!!testTarget&&testTarget.epoch===currentIdentityEpoch()&&testTarget.selection===sequence.current&&testTarget.ticket===actionSequence.current&&testTarget.providerId===selected?.id} onCancel={()=>setTestTarget(undefined)} footer={<Space><Button onClick={()=>setTestTarget(undefined)}>关闭</Button><Button type="primary" disabled={busy||!manage||testTarget?.kind==='inference'&&(!trialText.trim()||trialText.length>8000||!Number.isInteger(trialLimit)||trialLimit!<1||trialLimit!>Math.min(393216,models.find(m=>m.id===testTarget.modelId)?.max_output_tokens||4096))} onClick={()=>testTarget&&void test(testTarget.kind,testTarget.modelId)}>{testTarget?.kind==='inference'?'发送测试内容':'重新测试'}</Button></Space>}>
  <p className="subtle">{testTarget?.providerName}{testTarget?.modelName&&` · ${testTarget.modelName}`}</p>
  {testTarget?.kind==='inference'&&<><label>测试内容<Input.TextArea aria-label="模型测试内容" value={trialText} maxLength={8000} showCount rows={5} disabled={busy} onChange={event=>setTrialText(event.target.value)}/></label><label>最大输出 Token<InputNumber aria-label="试答最大输出 Token" min={1} max={Math.min(393216,models.find(m=>m.id===testTarget.modelId)?.max_output_tokens||4096)} value={trialLimit} disabled={busy} onChange={setTrialLimit}/></label><p className="subtle">点击“发送测试内容”会使用此模型已配置的认证发送以上内容。成功表示请求已送达且取得有效响应，回答内容由你判断。</p></>}
  <div className={styles.checkSummary}><strong>{shownInference?.modelName?`最近推理 · ${shownInference.modelName}`:'当前模型最近推理'}</strong>{checkView(shownInference)}<p>仅本页会话记录；平台连接检查不会重新运行或覆盖模型推理验证。</p></div>
  <div aria-live="polite" aria-busy={busy}>{busy?<p role="status">{testTarget?.kind==='inference'?'正在发送测试内容并等待模型响应…':'正在测试平台连接，请稍候…'}</p>:error?<Alert type="error" message={errorTitle} description={<>{error}<SupportReference value={failure?.request_id}/></>}/>:notice?<Alert type="info" message={notice}/>:<p>填写内容后点击发送，查看本次结果。</p>}</div>
  {testTarget?.kind==='inference'&&trialSent&&<section aria-label="本次模型试答结果"><p>请求发送：{trial?.sent===true?'已发送':trial?.sent===false?'未发送':'待服务端确认'} · 响应接收：{trial?.received===true?'已接收':trial?.received===false?'未接收':'待服务端确认'} · 耗时：{trial?.elapsed!==undefined?`${trial.elapsed} ms`:'尚无服务端计时'}</p>{trial?.truncated&&<Alert type="warning" message="回复达到输出上限，以下为截断内容"/>}{trial?.state==='empty'&&<Alert type="warning" message="模型已响应，但没有返回可见回答"/>}{trial?.state==='tool_request'&&<Alert type="warning" message="模型返回工具请求，本次试答不执行工具"/>}{trial?.answer!==undefined&&<div><h3>模型实际回答</h3><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',fontFamily:'inherit'}}>{trial.answer||'（空回答）'}</pre></div>}</section>}
 </Modal>
 <Modal zIndex={modalLayers.templates} getContainer={portalContainer} focusTriggerAfterClose={false} afterClose={restoreInnerFocus} keyboard={!editing&&!confirming} styles={{body:{maxHeight:'calc(100vh - 200px)',overflowY:'auto'}}} width={760} title="添加平台 · 选择模板" open={templatesOpen} footer={null} onCancel={()=>setTemplatesOpen(false)}>
  <p className="subtle">模板只预填设置，保存后才创建平台；不包含密钥、模型或使用授权。</p>
  {presetsError&&<Alert type="error" message="平台模板读取失败" description={presetsError} action={<Button onClick={()=>void loadPresets()} loading={presetsLoading}>重试读取模板</Button>}/>}
  {presetsLoading&&<p role="status">正在读取模板…</p>}
  <div className={styles.templates}>{presets.filter(p=>p.id!=='custom').map(p=><article key={p.id}><h3>{p.name}</h3><p>{p.description}</p>{(p.endpoint_notes?.length>0||p.documentation_url)&&<details><summary>地址与使用说明</summary>{p.endpoint_notes?.map((note:string,index:number)=><p key={index}>{note}</p>)}{p.documentation_url&&<a href={p.documentation_url} target="_blank" rel="noopener noreferrer">官方说明 ↗</a>}</details>}<Button onClick={()=>edit('provider',undefined,p)}>使用模板</Button></article>)}<article><h3>自定义平台</h3><p>使用兼容 OpenAI 的服务地址，自行填写平台设置。</p><Button onClick={()=>edit('provider')}>自定义配置</Button></article></div>
 </Modal>
 <Modal zIndex={modalLayers.editor} getContainer={portalContainer} focusTriggerAfterClose={false} afterClose={restoreInnerFocus} keyboard={!confirming&&!busy} closable={!confirming&&!busy} cancelButtonProps={{disabled:busy||confirming}} centered styles={{body:{maxHeight:'calc(100vh - 200px)',overflowY:'auto'}}} forceRender width={620} title={editing?`${editing.value?'编辑':'添加'}${editing.kind==='provider'?'模型平台':'模型'}`:''} open={!!editing} confirmLoading={busy} okButtonProps={{disabled:!manage}} maskClosable={false} onCancel={async()=>{const epoch=currentIdentityEpoch();if(await checkLeave()===true&&valid(epoch)){setEditing(undefined);setDirty(false);form.resetFields();}}} onOk={()=>void save()}>
  <Form form={form} layout="vertical" onValuesChange={()=>setDirty(true)}>{sharingField}{editing?.value&&!reviewer&&<p className="hint">保存修改后，配置需要重新审批才能使用。</p>}{editing?.kind==='provider'?providerFields:modelFields}</Form>{error&&<Alert type="error" message={errorTitle} description={<>{error}<SupportReference value={failure?.request_id}/></>}/>}
 </Modal>
 </div>;
}
