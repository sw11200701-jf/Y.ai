'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import {Alert,App,Button,Modal,Select,Space,Tag} from 'antd';
import {currentIdentityEpoch} from '@/lib/api';
import {formatError,type FormattedError} from '@/lib/errors';
import {hasPermission} from '@/lib/permissions';
import {documentNames,documentProblems,emptyDocuments,type DocumentToolId} from '@/lib/document-content';
import {useSession} from './auth-gate';
import {useLeaveConfirmation} from './workflow-studio/use-leave-confirmation';
import {ErrorBanner} from './shared';
import DocumentContentFields from './document-content-fields';
import DocumentGenerationRun from './document-generation-run';
import {deterministicDocumentCatalog,type DeterministicDefinition} from '@/lib/document-deterministic';
const policyEvents=['zhangpu:access-denied','zhangpu:session-expired','zhangpu:permissions-changed','zhangpu:data-policy-changed'];
export default function DocumentGeneratorPanel({open,onClose}:{open:boolean;onClose:()=>void}){
 const {session,permissionsChecking}=useSession(),{modal}=App.useApp(),permitted=['page.plugins','page.ai','page.workbench'].some(k=>hasPermission(session.user,k));
 const [definitions,setDefinitions]=useState<DeterministicDefinition[]>([]),[selected,setSelected]=useState<DocumentToolId>(),[drafts,setDrafts]=useState(emptyDocuments),[dirty,setDirty]=useState(false),[loading,setLoading]=useState(false),[error,setError]=useState<string|FormattedError>(''),[issues,setIssues]=useState<string[]>([]),[notice,setNotice]=useState(''),[confirming,setConfirming]=useState(false),[working,setWorking]=useState(false);
 const alive=useRef(false),generation=useRef(0),reads=useRef(0),catalogAbort=useRef<AbortController|undefined>(undefined),dirtyRef=useRef(false),latest=useRef({open,onClose});dirtyRef.current=dirty;latest.current={open,onClose};
 const portal=useCallback(()=>document.body,[]),{guard,dismiss}=useLeaveConfirmation({dirty:()=>dirtyRef.current,blocked:()=>false,container:portal,show:options=>{setConfirming(true);return modal.confirm({...options,title:'文档内容尚未保存',content:<><p>这些填写内容只保留在本次表单。离开会清空本次填写，已提交的生成任务会继续，可在运行记录查看。</p><span hidden>{options.content}</span></>,zIndex:1500,focusTriggerAfterClose:false});},onError:()=>setError('暂时无法显示离开提示，请稍后重试。')});
 const checkLeave=useCallback(()=>{const epoch=currentIdentityEpoch(),ticket=generation.current,value=guard();if(typeof value==='boolean')return value;return value.finally(()=>{if(alive.current&&epoch===currentIdentityEpoch()&&ticket===generation.current)setConfirming(false);});},[guard]);
 function clear(){generation.current++;reads.current++;catalogAbort.current?.abort();dismiss();setDefinitions([]);setSelected(undefined);setDrafts(emptyDocuments());setDirty(false);dirtyRef.current=false;setLoading(false);setError('');setIssues([]);setNotice('');setConfirming(false);setWorking(false);}
 async function load(){catalogAbort.current?.abort();const controller=new AbortController();catalogAbort.current=controller;const epoch=currentIdentityEpoch(),ticket=++reads.current,policy=generation.current;setLoading(true);setError('');try{const items=await deterministicDocumentCatalog(controller.signal);if(alive.current&&epoch===currentIdentityEpoch()&&policy===generation.current&&ticket===reads.current){setDefinitions(items);setSelected(old=>items.some(d=>d.id===old)?old:items[0]?.id);}}catch(e){if(alive.current&&epoch===currentIdentityEpoch()&&policy===generation.current&&ticket===reads.current)setError(formatError(e));}finally{if(alive.current&&policy===generation.current&&ticket===reads.current)setLoading(false);}}
 useEffect(()=>{alive.current=true;clear();if(open&&permitted&&!permissionsChecking)void load();const invalidate=()=>{clear();latest.current.onClose();};policyEvents.forEach(name=>window.addEventListener(name,invalidate));return()=>{alive.current=false;generation.current++;reads.current++;catalogAbort.current?.abort();dismiss();policyEvents.forEach(name=>window.removeEventListener(name,invalidate));};},[open,permitted,permissionsChecking,session.user.id,dismiss]);
 useEffect(()=>{const before=(event:Event)=>{if(!latest.current.open)return;const wait=(event as CustomEvent).detail?.waitUntil;if(wait)wait(checkLeave());else if(dirtyRef.current)event.preventDefault();};window.addEventListener('zhangpu:before-navigation',before);return()=>window.removeEventListener('zhangpu:before-navigation',before);},[checkLeave]);
 useEffect(()=>{if(!open||!dirty)return;const before=(e:BeforeUnloadEvent)=>{e.preventDefault();e.returnValue='';};window.addEventListener('beforeunload',before);return()=>window.removeEventListener('beforeunload',before);},[open,dirty]);
 async function close(){if(confirming)return;const epoch=currentIdentityEpoch(),ticket=generation.current;if(await checkLeave()===true&&alive.current&&ticket===generation.current&&epoch===currentIdentityEpoch())latest.current.onClose();}
 const definition=definitions.find(d=>d.id===selected);
 if(!permitted||permissionsChecking)return null;
 return <Modal key={`documents-${generation.current}`} destroyOnHidden title="Word / Excel / PPT 内容表单" open={open} zIndex={1000} width="min(1160px,calc(100vw - 24px))" getContainer={portal} focusTriggerAfterClose={false} maskClosable={false} keyboard={!confirming} closable={!confirming} styles={{body:{maxHeight:'calc(100vh - 220px)',overflowY:'auto'}}} onCancel={()=>void close()} footer={<Space wrap><Button disabled={confirming} onClick={()=>void close()}>关闭内容表单</Button><Button disabled={!definition||loading||confirming} onClick={()=>{if(!selected)return;const problems=documentProblems(selected,drafts);setIssues(problems);setNotice(problems.length?'':'内容预检通过；本次检查未生成文件，最终版面以实际文件为准。');}}>检查内容</Button></Space>}>
  <p>填写章节、工作表或幻灯片内容。本次填写在关闭后清空。检查内容不会创建任务，明确点击生成后才会产生文件。</p>
  <ErrorBanner error={error}/>{error&&<Button disabled={loading} onClick={()=>void load()}>重试文档工具目录</Button>}{loading&&<p role="status">正在读取文档工具与内容格式…</p>}{!loading&&!error&&!definitions.length&&<p>当前没有有权使用的文档生成工具。</p>}
  {definition&&selected&&<><Select aria-label="文档格式" style={{width:'100%',margin:'16px 0'}} disabled={confirming||loading||working} value={selected} options={definitions.map(d=>({value:d.id,label:documentNames[d.id]}))} onChange={id=>{setSelected(id);setIssues([]);setNotice('');}}/><p>{definition.description}</p><Space wrap><Tag>固定格式 v{definition.version}</Tag><Tag>输入最多256 KiB</Tag><Tag>全文最多64000字</Tag><Tag>文件上限2 MiB</Tag><Tag>{dirty?'本次内容有修改':'尚未填写'}</Tag></Space><p className="subtle">按结构化内容生成；本表单不接受现有文件或模板上传、图片、任意格式互转或远程地址。Excel按文本处理公式字符；PPT采用保守版面预检，密集内容请拆页。</p>
   <DocumentContentFields id={selected} value={drafts} disabled={confirming||loading||working} onChange={value=>{setDrafts(value);setDirty(true);setIssues([]);setNotice('');}}/>
  <DocumentGenerationRun definition={definition} drafts={drafts} disabled={confirming||loading||!['page.ai','page.workbench'].some(k=>hasPermission(session.user,k))} onActivityChange={setWorking} onProblems={setIssues}/>{!['page.ai','page.workbench'].some(k=>hasPermission(session.user,k))&&<p>生成任务需要当前 AI 工作室或工作台权限。</p>}
  </>}{issues.length>0&&<Alert type="warning" message="请调整这些内容" description={<ul>{issues.slice(0,40).map((value,i)=><li key={i}>{value}</li>)}{issues.length>40&&<li>另有{issues.length-40}项，请分批调整后重新检查。</li>}</ul>}/>} {notice&&<Alert type="success" message={notice}/>}
 </Modal>;
}
