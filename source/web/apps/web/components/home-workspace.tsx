'use client';
import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Select,Skeleton,Space,Tag} from 'antd';
import {DatabaseOutlined,HistoryOutlined,ReloadOutlined,RobotOutlined} from '@ant-design/icons';
import {currentIdentityEpoch,Json,list,reportClientError,request,send} from '@/lib/api';
import {formatError,errorMessage,type FormattedError} from '@/lib/errors';
import {canOpenPage} from '@/lib/permissions';
import {emptyMessage,toMessageEnvelope} from '@/lib/workflow/messages';
import {conversationChoices,chooseConversation,rememberedConversation,rememberConversation} from '@/lib/conversation-home';
import {inputDefaults} from '@/lib/workflow/debug-inputs';
import {businessInputProblems} from '@/lib/workbench-application';
import type {MessageDraft} from '@/lib/workflow/types';
import {useSession} from './auth-gate';
import ChatComposer from './chat-composer';
import {ParameterValue} from './agent-parameter-editor';
import {Artifacts,ErrorBanner,JsonView,Status} from './shared';
import {PresentationResult} from './workflow-studio/presentation-result';
export default function HomeWorkspace({onNavigate}:{onNavigate:(page:string)=>void}){
 const {session,permissionsChecking}=useSession();
 const scope=`${currentIdentityEpoch()}:${session.user.tenant_id}:${session.user.workspace_id}:${session.user.id}:${[...(session.user.permissions||[])].sort().join(',')}`;
 return <Conversation key={scope} user={session.user} permissionsChecking={permissionsChecking} onNavigate={onNavigate}/>;
}
function Conversation({user,permissionsChecking,onNavigate}:{user:Json;permissionsChecking:boolean;onNavigate:(page:string)=>void}){
 const can=(page:string)=>!permissionsChecking&&canOpenPage(user,page),identityKey=String(currentIdentityEpoch()),active=useRef(true);
 const [agents,setAgents]=useState<Json[]>([]),[loading,setLoading]=useState(true),[loadFailed,setLoadFailed]=useState(false),[attempt,setAttempt]=useState(0),[agentId,setAgentId]=useState<string>();
 const [inputs,setInputs]=useState<Json>({});
 const [error,setError]=useState<string|FormattedError>(''),[busy,setBusy]=useState(false),[lastAttempt,setLastAttempt]=useState<{message:MessageDraft;agentId:string}>(),[message]=useState<MessageDraft>(emptyMessage),[run,setRun]=useState<Json>(),[artifacts,setArtifacts]=useState<Json[]>([]),[dataset,setDataset]=useState<Json>();
 useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
 useEffect(()=>{
  if(permissionsChecking)return;
  const epoch=currentIdentityEpoch(),controller=new AbortController();
  const current=()=>!controller.signal.aborted&&epoch===currentIdentityEpoch();
  setLoading(true);setLoadFailed(false);setError('');setAgents([]);
  void list('/agents/conversations',{signal:AbortSignal.any([controller.signal,AbortSignal.timeout(15000)])}).then(values=>{
    if(!current())return;
    const choices=conversationChoices(values);setAgents(choices);
    setAgentId(previous=>{const chosen=chooseConversation(choices,previous||rememberedConversation(user));rememberConversation(user,chosen);return chosen;});
  },e=>{
    if(!current())return;
    const value=formatError(e);
    setLoadFailed(true);setError(value);
    const code=e?.name==='TimeoutError'?'CONVERSATION_LIST_TIMEOUT':e instanceof TypeError?'CONVERSATION_LIST_NETWORK':undefined;
    if(code)void reportClientError(code).then(reference=>{if(current()&&reference)setError({...value,request_id:reference});});
  }).finally(()=>{if(current())setLoading(false);});
  return()=>controller.abort();
 },[permissionsChecking,attempt]);
 const runId=run?.id,status=run?.status,datasetId=run?.dataset_id;
 const selected=agents.find(value=>value.id===agentId);
 const inputSchema=selected?.input_schema;
 useEffect(()=>{setInputs(inputDefaults(inputSchema||{},{}));},[agentId,inputSchema]);
 useEffect(()=>{if(!runId||!['queued','running'].includes(status))return;let stale=false;const timer=setInterval(async()=>{try{const value=await request(`/runs/${runId}`);if(!stale&&identityKey===String(currentIdentityEpoch()))setRun(value);}catch(e){if(!stale&&identityKey===String(currentIdentityEpoch()))setError(formatError(e));}},1200);return()=>{stale=true;clearInterval(timer);};},[runId,status]);
 useEffect(()=>{if(!runId||status!=='succeeded')return;let stale=false;Promise.all([list(`/runs/${runId}/artifacts`),datasetId?request(`/datasets/${datasetId}`):Promise.resolve(undefined)]).then(([a,d])=>{if(!stale&&identityKey===String(currentIdentityEpoch())){setArtifacts(a);setDataset(d);}}).catch(e=>{if(!stale&&identityKey===String(currentIdentityEpoch()))setError(formatError(e));});return()=>{stale=true;};},[runId,status,datasetId]);
 async function execute(draft:MessageDraft){
  const chosen=selected;
  if(!active.current||identityKey!==String(currentIdentityEpoch())||!chosen||permissionsChecking||busy||run&&['queued','running','outcome_unknown'].includes(run.status))return;
  if(chosen.access?.execute!==true){setError('当前资源没有使用权限');return;}
  const problem=inputSchema&&businessInputProblems(inputSchema,inputs)[0];
  if(problem){setError(problem);return;}
  const epoch=currentIdentityEpoch(),current=()=>active.current&&epoch===currentIdentityEpoch();
  const target=chosen.kind==='workflow'?{workflow_id:chosen.id}:{agent_id:chosen.id,agent_version_id:chosen.conversation_version_id};
  setLastAttempt({message:draft,agentId:chosen.id});setBusy(true);setError('');setRun(undefined);setDataset(undefined);setArtifacts([]);
  try{const value=await send('/runs',{...target,inputs,message:toMessageEnvelope(draft)});if(current())setRun(value);}
  catch(e){if(current())setError(formatError(e));}
  finally{if(current())setBusy(false);}
 }
 const choices=agents.length>0&&<div className="chat-composer-options">
   <Select aria-label="选择智能体或工作流" placeholder="选择已发布的智能体或工作流" value={agentId} disabled={busy||!!run&&['queued','running','outcome_unknown'].includes(run.status)} onChange={id=>{setAgentId(id);rememberConversation(user,id);}} options={agents.map(a=>({value:a.id,label:`${a.name} · ${a.kind==='workflow'?'工作流':a.workflow_agent?'带工作流的智能体':'智能体'}`}))}/>
   <Button type="text" icon={<ReloadOutlined/>} aria-label="刷新对话助手" disabled={busy||!!run&&['queued','running'].includes(run.status)} onClick={()=>setAttempt(value=>value+1)}/>
 </div>;
 const shortcuts=[
   {page:'data',label:'数据中心',icon:<DatabaseOutlined/>},
   {page:'agents',label:'AI 工作室',icon:<RobotOutlined/>},
 ].filter(({page})=>can(page));
 return <>
  <section className="home-hero conversation-hero">
    <span className="conversation-orbit" aria-hidden="true"><span/></span>
    <div className="conversation-heading">
      <div><h1>有什么可以帮你？</h1><p>选择智能体或已发布工作流，开始处理你的任务。</p></div>
      {can('runs')&&<Button icon={<HistoryOutlined/>} onClick={()=>onNavigate('runs')}>对话记录</Button>}
    </div>
    <ErrorBanner error={error}/>
    {loading?<Skeleton active paragraph={{rows:2}}/>:loadFailed?<Button onClick={()=>setAttempt(value=>value+1)}>重新加载对话助手</Button>:<>
      {!agents.length&&<Alert type="info" message="尚无可用的智能体或工作流" description="请在 AI 工作室发布，或请资源所有者将你加入公开范围。" action={can('agents')?<Button onClick={()=>onNavigate('agents')}>打开 AI 工作室</Button>:undefined}/>}
      {inputSchema?.properties&&Object.keys(inputSchema.properties).length>0&&<details open><summary>工作流参数</summary><Space wrap>{Object.entries(inputSchema.properties).map(([name,raw])=>{const field=raw as Json;return <label key={name}>{field.title||name}{inputSchema.required?.includes(name)?' *':''}<ParameterValue schema={field} value={inputs[name]} disabled={busy||!!run&&['queued','running'].includes(run.status)} onChange={value=>setInputs(previous=>({...previous,[name]:value}))}/></label>;})}</Space></details>}
      <ChatComposer options={choices} key={identityKey} identityKey={identityKey} initialText={message.text} disabled={selected?.access?.execute!==true||permissionsChecking||!!run&&run.status==='outcome_unknown'} busy={busy||!!run&&['queued','running'].includes(run.status)} onSend={draft=>void execute(draft)}/>
      {lastAttempt&&error&&!run&&<p className="hint">请先核对对话记录，确认消息是否已提交，避免重复请求。</p>}
      {!agents.length&&<Button type="text" onClick={()=>setAttempt(value=>value+1)}>重新读取对话配置</Button>}
    </>}
  </section>
  {!run&&shortcuts.length>0&&<section className="panel conversation-shortcuts">
    <h2>常用入口</h2>{shortcuts.map(({page,label,icon})=><Button key={page} icon={icon} onClick={()=>onNavigate(page)}>{label}</Button>)}
  </section>}
  {run&&<section className="panel">{lastAttempt&&<div className="chat-sent-message"><p>{lastAttempt.message.text||({image:'图片消息',audio:'语音消息',file:'文件消息',mixed:'附件消息'} as Json)[lastAttempt.message.type]}</p>{lastAttempt.message.attachments.map(a=><Tag key={a.id}>{a.filename}</Tag>)}</div>}<div className="panel-heading"><h2>回答</h2><Space><Status value={run.status}/>{can('runs')&&<Button size="small" onClick={()=>onNavigate('runs')}>查看记录</Button>}</Space></div>{run.error?<Alert type="error" message={errorMessage(run.error)}/>:run.status==='succeeded'?<>{run.presentations?.length?<PresentationResult items={run.presentations}/>:<p className="chat-answer" style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{typeof run.outputs?.result?.text==='string'?run.outputs.result.text:typeof run.outputs?.answer==='string'?run.outputs.answer:typeof run.outputs?.text==='string'?run.outputs.text:''}</p>}{!run.presentations?.length&&![run.outputs?.result?.text,run.outputs?.answer,run.outputs?.text].some(value=>typeof value==='string')&&<JsonView value={run.outputs||{}}/>}{dataset&&<details><summary>结果数据</summary><JsonView value={dataset}/></details>}<Artifacts items={artifacts} onError={setError}/></>:<p className="subtle">任务已提交，等待真实执行结果…</p>}</section>}
 </>;
}
