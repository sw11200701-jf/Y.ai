'use client';
import {useEffect,useMemo,useRef,useState} from 'react';
import {Alert,Button,Collapse,Empty,Space,Tag} from 'antd';
import {currentIdentityEpoch,time} from '@/lib/api';
import {errorMessage} from '@/lib/errors';
import {downloadRunArtifact,runArtifacts,type RunArtifact} from '@/lib/artifact-files';
const policyEvents=['zhangpu:permissions-changed','zhangpu:data-policy-changed','zhangpu:session-expired','zhangpu:access-denied'];
export default function ArtifactFileCards({items,runId,onError}:{items:unknown[];runId?:string;onError:(message:string)=>void}){
 const [busy,setBusy]=useState<string[]>([]),[blocked,setBlocked]=useState(false);const alive=useRef(false),generation=useRef(0),pending=useRef(new Map<string,AbortController>()),report=useRef(onError);report.current=onError;
 const parsed=useMemo(()=>{try{return {files:runArtifacts(items,runId),error:''};}catch(e){return {files:[] as RunArtifact[],error:errorMessage(e)};}},[items,runId]);
 const signature=JSON.stringify(parsed.files);
 useEffect(()=>{if(parsed.error&&!blocked)report.current(parsed.error);},[parsed.error,blocked]);
 useEffect(()=>{alive.current=true;const invalidate=()=>{generation.current++;pending.current.forEach(c=>c.abort());pending.current.clear();setBusy([]);setBlocked(true);};policyEvents.forEach(name=>window.addEventListener(name,invalidate));return()=>{alive.current=false;generation.current++;pending.current.forEach(c=>c.abort());pending.current.clear();policyEvents.forEach(name=>window.removeEventListener(name,invalidate));};},[]);
 useEffect(()=>{generation.current++;pending.current.forEach(c=>c.abort());pending.current.clear();setBusy([]);},[signature,runId]);
 async function download(file:RunArtifact){if(blocked||pending.current.has(file.id))return;const controller=new AbortController(),epoch=currentIdentityEpoch(),ticket=generation.current;pending.current.set(file.id,controller);setBusy(v=>[...v,file.id]);try{await downloadRunArtifact(file,controller.signal);}catch(e){if(alive.current&&!controller.signal.aborted&&ticket===generation.current&&epoch===currentIdentityEpoch())report.current(errorMessage(e));}finally{if(pending.current.get(file.id)===controller){pending.current.delete(file.id);if(alive.current)setBusy(v=>v.filter(id=>id!==file.id));}}}
 if(blocked)return <Alert type="info" message="权限或会话已变化，请重新打开运行以读取文件信息"/>;
 if(parsed.error)return <Alert type="warning" message="本次运行文件信息尚未确认" description={parsed.error}/>;
 if(!parsed.files.length)return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次运行尚无文件"/>;
 return <Space wrap align="start">{parsed.files.map(file=><article key={file.id} aria-label={`运行文件：${file.filename}`} style={{display:'grid',gap:10,border:'1px solid #dce5ed',borderRadius:12,padding:16,width:360,maxWidth:'100%',overflowWrap:'anywhere'}}><Space wrap><Tag>{file.type.toUpperCase()}</Tag><Tag>{file.artifact_kind==='generated_document'?'生成文档':'财务报告'}</Tag></Space><strong>{file.filename}</strong><span>{file.size_bytes} bytes · {time(file.created_at)}</span>{file.artifact_kind==='generated_document'?<span>关联来源：{file.data_sources.length} 项 · 下载按当前权限核验</span>:<span>数据集：{file.dataset_id}</span>}<Button loading={busy.includes(file.id)} onClick={()=>void download(file)}>下载 {file.type.toUpperCase()}</Button><Collapse ghost size="small" items={[{key:'integrity',label:'文件校验与来源',children:<div><p style={{wordBreak:'break-all'}}>SHA256：{file.sha256}</p>{file.artifact_kind==='generated_document'&&<><p>MIME：{file.mime_type}</p><p>渲染版本：{file.renderer_version}</p><p>运行：{file.run_id}</p><p style={{wordBreak:'break-all'}}>内容校验：{file.input_sha256}</p>{file.data_sources.map((s,i)=><p key={i}>来源 {i+1}：{s.kind} · {s.resource_id}</p>)}</>}</div>}]} /></article>)}</Space>;
}
