'use client';
import {useRef,useState} from 'react';
import {Button,Modal,Popconfirm,Table} from 'antd';
import {list,request,send,type Json,time} from '@/lib/api';
import {useDataActions} from '@/lib/use-data-actions';
import {formatError,type FormattedError} from '@/lib/errors';
import {ErrorBanner} from './shared';

export function KnowledgeRemove({kind,id,onRemoved,onError}:{kind:'knowledge_base'|'knowledge_document'|'knowledge_dataset';id:string;onRemoved:()=>Promise<void>;onError:(e:FormattedError)=>void}){
 const actions=useDataActions(kind,id);const [busy,setBusy]=useState(false);const lock=useRef(false);
 const path=kind==='knowledge_base'?`/knowledge-bases/${id}`:`/knowledge/${kind==='knowledge_document'?'documents':'datasets'}/${id}`;
 async function remove(){if(lock.current)return;lock.current=true;setBusy(true);try{await request(path,{method:'DELETE'});await onRemoved();}catch(e){onError(formatError(e));}finally{lock.current=false;setBusy(false);}}
 return <Popconfirm title={kind==='knowledge_base'?'删除此知识库及其访问入口？':'删除此文件？'} description="移入回收站，可恢复。历史运行记录保留。" okText="删除" cancelText="取消" onConfirm={remove}><Button danger size="small" disabled={!actions.includes('maintain')||busy} loading={busy}>删除{kind==='knowledge_base'?'知识库':''}</Button></Popconfirm>;
}

export function KnowledgeRecycleBin({libraryId,onChanged}:{libraryId?:string;onChanged:()=>Promise<void>}){
 const [open,setOpen]=useState(false),[rows,setRows]=useState<Json[]>([]),[loading,setLoading]=useState(false),[busy,setBusy]=useState('');const [error,setError]=useState<FormattedError|string>('');const lock=useRef(false);
 async function load(){setLoading(true);try{setRows(await list('/knowledge/recycle-bin'+(libraryId?'?kb_id='+encodeURIComponent(libraryId):'')));}finally{setLoading(false);}}
 async function show(){setOpen(true);setError('');try{await load();}catch(e){setError(formatError(e));}}
 async function restore(row:Json){if(lock.current)return;lock.current=true;setBusy(row.id);setError('');try{const path=row.kind==='knowledge_base'?`/knowledge-bases/${row.id}/restore`:`/knowledge/${row.kind==='knowledge_document'?'documents':'datasets'}/${row.id}/restore`;await send(path);await load();await onChanged();}catch(e){setError(formatError(e));}finally{lock.current=false;setBusy('');}}
 return <><Button onClick={()=>void show()}>回收站</Button><Modal title={libraryId?'文件回收站':'知识库回收站'} open={open} onCancel={()=>setOpen(false)} footer={<Button onClick={()=>setOpen(false)}>关闭</Button>}><ErrorBanner error={error}/><Table<Json> rowKey="id" loading={loading} dataSource={rows} columns={[{title:'名称',render:(_,r)=>r.filename||r.name||r.id},{title:'删除时间',render:(_,r)=>time(r.deletion?.deleted_at||r.updated_at)},{title:'操作',render:(_,r)=><Button disabled={!!busy} loading={busy===r.id} onClick={()=>void restore(r)}>恢复</Button>}]}/></Modal></>;
}
