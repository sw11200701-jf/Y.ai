'use client';
import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Table} from 'antd';
import {currentIdentityEpoch,request} from '@/lib/api';
import {formatError,type FormattedError} from '@/lib/errors';
import {ErrorBanner} from './shared';
type Preview={contract:string;document_id:string;run_id:string;read_only:boolean;complete_document:boolean;publish_allowed:boolean;completed_windows:number;total_windows:number;total_chunks:number;items:Record<string,unknown>[];offset:number;limit:number};
const events=['zhangpu:permissions-changed','zhangpu:data-policy-changed','zhangpu:access-denied','zhangpu:session-expired'];
export default function CommittedWindowPreview({documentId,runId,disabled=false}:{documentId:string;runId:string;disabled?:boolean}){
 const [value,setValue]=useState<Preview>(),[error,setError]=useState<string|FormattedError>(''),[loading,setLoading]=useState(false);
 const generation=useRef(0),alive=useRef(false),lock=useRef(false);
 useEffect(()=>{alive.current=true;const clear=()=>{generation.current++;lock.current=false;setValue(undefined);setError('');setLoading(false);};clear();events.forEach(e=>window.addEventListener(e,clear));return()=>{alive.current=false;generation.current++;events.forEach(e=>window.removeEventListener(e,clear));};},[documentId,runId]);
 async function read(offset=0){if(disabled||lock.current)return;lock.current=true;setLoading(true);setError('');const ticket=++generation.current,epoch=currentIdentityEpoch(),current=()=>alive.current&&generation.current===ticket&&currentIdentityEpoch()===epoch;
  try{const v=await request('/knowledge/documents/'+encodeURIComponent(documentId)+'/runs/'+encodeURIComponent(runId)+'/progress?offset='+offset+'&limit=20') as Preview;
   if(v.contract!=='committed-window-preview-v1'||v.document_id!==documentId||v.run_id!==runId||v.read_only!==true||v.complete_document!==false||v.publish_allowed!==false||!Array.isArray(v.items))throw Error('已完成分段核验未通过');
   if(current())setValue(v);
  }catch(e){if(current()){setValue(undefined);setError(formatError(e));}}finally{if(current()){lock.current=false;setLoading(false);}}
 }
 return <section aria-label="已完成分段"><Button disabled={disabled||loading} loading={loading} onClick={()=>void read()}>查看已完成分段</Button><ErrorBanner error={error}/>{value&&<><Alert type="info" message={`已校验 ${value.completed_windows}/${value.total_windows} 个原文窗口 · ${value.total_chunks} 个分段`} description="以下为已完成分段，只读预览。整项任务尚未完成，不能作为完整草稿发布。"/><Table size="small" loading={loading} rowKey={(_,i)=>String(value.offset+(i||0))} dataSource={value.items} columns={[{title:'主题',dataIndex:'topic'},{title:'概述',dataIndex:'summary'},{title:'原文',dataIndex:'text',render:v=><span style={{whiteSpace:'pre-wrap'}}>{String(v??'')}</span>}]} pagination={{current:Math.floor(value.offset/20)+1,pageSize:20,total:value.total_chunks,showSizeChanger:false,onChange:page=>void read((page-1)*20)}}/></>}</section>;
}
