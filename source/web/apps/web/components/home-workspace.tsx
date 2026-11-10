'use client';
import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Select,Skeleton,Space,Tag} from 'antd';
import {HistoryOutlined,ReloadOutlined} from '@ant-design/icons';
import {currentIdentityEpoch,Json,list,reportClientError,request,send} from '@/lib/api';
import {formatError,errorMessage,type FormattedError} from '@/lib/errors';
import {canOpenPage,hasPermission} from '@/lib/permissions';
import {emptyMessage,toMessageEnvelope} from '@/lib/workflow/messages';
import {conversationChoices,chooseConversation,rememberedConversation,rememberConversation} from '@/lib/conversation-home';
import type {MessageDraft} from '@/lib/workflow/types';
import {useSession} from './auth-gate';
import ChatComposer from './chat-composer';
import {Artifacts,ErrorBanner,JsonView,PageTitle,Status} from './shared';
import {PresentationResult} from './workflow-studio/presentation-result';
export default function HomeWorkspace({onNavigate}:{onNavigate:(page:string)=>void}){
 const {session,permissionsChecking}=useSession();
 const scope=`${currentIdentityEpoch()}:${session.user.tenant_id}:${session.user.workspace_id}:${session.user.id}:${[...(session.user.permissions||[])].sort().join(',')}`;
 return <Conversation key={scope} user={session.user} permissionsChecking={permissionsChecking} onNavigate={onNavigate}/>;
}
function Conversation({user,permissionsChecking,onNavigate}:{user:Json;permissionsChecking:boolean;onNavigate:(page:string)=>void}){
 const can=(page:string)=>!permissionsChecking&&canOpenPage(user,page),identityKey=String(currentIdentityEpoch()),active=useRef(true);
 const [agents,setAgents]=useState<Json[]>([]),[loading,setLoading]=useState(true),[loadFailed,setLoadFailed]=useState(false),[attempt,setAttempt]=useState(0),[agentId,setAgentId]=useState<string>();
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
 useEffect(()=>{if(!runId||!['queued','running'].includes(status))return;let stale=false;const timer=setInterval(async()=>{try{const value=await request(`/runs/${runId}`);if(!stale&&identityKey===String(currentIdentityEpoch()))setRun(value);}catch(e){if(!stale&&identityKey===String(currentIdentityEpoch()))setError(formatError(e));}},1200);return()=>{stale=true;clearInterval(timer);};},[runId,status]);
 useEffect(()=>{if(!runId||status!=='succeeded')return;let stale=false;Promise.all([list(`/runs/${runId}/artifacts`),datasetId?request(`/datasets/${datasetId}`):Promise.resolve(undefined)]).then(([a,d])=>{if(!stale&&identityKey===String(currentIdentityEpoch())){setArtifacts(a);setDataset(d);}}).catch(e=>{if(!stale&&identityKey===String(currentIdentityEpoch()))setError(formatError(e));});return()=>{stale=true;};},[runId,status,datasetId]);
 async function execute(draft:MessageDraft,chosenId=agentId){const chosen=agents.find(value=>value.id===chosenId);if(!hasPermission(user,'resources.execute')||chosen?.access?.execute===false){setError('当前助手没有执行授权');return;}if(!active.current||identityKey!==String(currentIdentityEpoch())||!chosen||permissionsChecking||busy||run&&['queued','running','outcome_unknown'].includes(run.status))return;const epoch=currentIdentityEpoch(),current=()=>active.current&&epoch===currentIdentityEpoch();setLastAttempt({message:draft,agentId:chosen.id});setBusy(true);setError('');setRun(undefined);setDataset(undefined);setArtifacts([]);try{const value=await send('/runs',{agent_id:chosen.id,agent_version_id:chosen.conversation_version_id,message:toMessageEnvelope(draft)});if(current())setRun(value);}catch(e){if(current())setError(formatError(e));}finally{if(current())setBusy(false);}}
 return <><PageTitle eyebrow="CHAT" title="对话问答" description="直接提问，开始对话。" action={can('runs')&&<Button icon={<HistoryOutlined/>} onClick={()=>onNavigate('runs')}>对话记录</Button>}/>
  <section className="home-hero"><h2>有什么可以帮你？</h2><ErrorBanner error={error}/>
    {loading?<Skeleton active paragraph={{rows:2}}/>:loadFailed?<Button onClick={()=>setAttempt(value=>value+1)}>重新加载对话助手</Button>:<>
      {agents.length>0?<div className="chat-agent-picker"><Select aria-label="选择对话助手" placeholder="选择已发布的对话助手" value={agentId} disabled={busy||!!run&&['queued','running','outcome_unknown'].includes(run.status)} onChange={id=>{setAgentId(id);rememberConversation(user,id);}} style={{minWidth:240,maxWidth:'100%'}} options={agents.map(a=>({value:a.id,label:a.name}))}/></div>:<Alert type="info" message="尚无可用的对话助手" description="请在 AI 工作室发布一个已绑定模型的普通助手，或联系管理员。" action={can('agents')?<Button onClick={()=>onNavigate('agents')}>配置对话助手</Button>:undefined}/>}
      <ChatComposer key={identityKey} identityKey={identityKey} initialText={message.text} disabled={!hasPermission(user,'resources.execute')||!agents.some(a=>a.id===agentId&&a.access?.execute!==false)||permissionsChecking||!!run&&run.status==='outcome_unknown'} busy={busy||!!run&&['queued','running'].includes(run.status)} onSend={draft=>void execute(draft)}/>
      {lastAttempt&&error&&!run&&<p className="hint">请先核对对话记录，确认消息是否已提交，避免重复请求。</p>}
      {!agents.length&&<Button type="text" onClick={()=>setAttempt(value=>value+1)}>重新读取对话配置</Button>}
    </>}
  </section>
  {run&&<section className="panel">{lastAttempt&&<div className="chat-sent-message"><p>{lastAttempt.message.text||({image:'图片消息',audio:'语音消息',file:'文件消息',mixed:'附件消息'} as Json)[lastAttempt.message.type]}</p>{lastAttempt.message.attachments.map(a=><Tag key={a.id}>{a.filename}</Tag>)}</div>}<div className="panel-heading"><h2>回答</h2><Space><Status value={run.status}/>{can('runs')&&<Button size="small" onClick={()=>onNavigate('runs')}>查看记录</Button>}</Space></div>{run.error?<Alert type="error" message={errorMessage(run.error)}/>:run.status==='succeeded'?<>{run.presentations?.length?<PresentationResult items={run.presentations}/>:<p className="chat-answer" style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{typeof run.outputs?.result?.text==='string'?run.outputs.result.text:typeof run.outputs?.answer==='string'?run.outputs.answer:'未返回可显示的回答，请查看对话记录。'}</p>}{dataset&&<details><summary>结果数据</summary><JsonView value={dataset}/></details>}<Artifacts items={artifacts} onError={setError}/></>:<p className="subtle">任务已提交，等待真实执行结果…</p>}</section>}
 </>;
}
