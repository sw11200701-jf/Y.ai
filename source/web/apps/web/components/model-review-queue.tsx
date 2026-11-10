'use client';
import { useEffect, useState } from 'react';
import { Button, Popconfirm, Space, Table, Tag } from 'antd';
import { currentIdentityEpoch, request, send } from '@/lib/api';
import { formatError, type FormattedError } from '@/lib/errors';
import { ErrorBanner } from './shared';

type Review = { id:string; kind:'provider'|'model'; name:string; owner_id:string; revision:number;
  requested_visibility:'public'|'private'; endpoint?:string; model_name?:string; provider_id?:string };

export default function ModelReviewQueue({onChange}:{onChange:()=>void}) {
  const [items,setItems]=useState<Review[]>([]);
  const [reload,setReload]=useState(0);
  const [loading,setLoading]=useState(true);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<FormattedError>();
  useEffect(()=>{
    const controller=new AbortController();
    const epoch=currentIdentityEpoch();
    setLoading(true);setError(undefined);setItems([]);
    const active=()=>!controller.signal.aborted&&epoch===currentIdentityEpoch();
    void request<{items:Review[]}>('/model-catalog/reviews',{signal:controller.signal}).then(
      value=>{if(active())setItems(value.items);},failure=>{if(active())setError(formatError(failure));},
    ).finally(()=>{if(active())setLoading(false);});
    return()=>controller.abort();
  },[reload]);
  async function decide(item:Review,decision:'approve'|'reject'){
    if(busy)return;
    const epoch=currentIdentityEpoch();
    setBusy(true);setError(undefined);
    try{
      const path=item.kind==='provider'?'/model-providers/':'/models/';
      await send(path+encodeURIComponent(item.id)+'/review',{expected_revision:item.revision,decision});
      if(epoch===currentIdentityEpoch()){setItems(rows=>rows.filter(row=>row.id!==item.id));onChange();}
    }catch(failure){if(epoch===currentIdentityEpoch())setError(formatError(failure));}
    finally{if(epoch===currentIdentityEpoch())setBusy(false);}
  }
  return <section className="panel">
    <div className="panel-heading"><h2>模型与平台审批</h2>
      <Button disabled={busy} onClick={()=>setReload(value=>value+1)}>刷新申请</Button></div>
    <p className="hint">有新平台和模型时先审批平台。公开配置面向当前工作空间，私有配置仅供提交人使用。</p>
    <ErrorBanner error={error}/>
    <Table<Review> rowKey="id" dataSource={items} loading={loading} size="small" scroll={{x:720}}
      locale={{emptyText:'没有待审批的申请'}}
      columns={[
        {title:'申请',render:(_,row)=><>{row.kind==='provider'?'平台':'模型'} · {row.name}</>},
        {title:'配置',render:(_,row)=>row.endpoint||row.model_name||'—'},
        {title:'申请范围',render:(_,row)=><Tag>{row.requested_visibility==='public'?'公开':'私有'}</Tag>},
        {title:'提交人',dataIndex:'owner_id'},
        {title:'操作',render:(_,row)=><Space>
          <Popconfirm title="批准此申请？" onConfirm={()=>decide(row,'approve')}><Button disabled={busy} type="primary" size="small">批准</Button></Popconfirm>
          <Popconfirm title="驳回此申请？" onConfirm={()=>decide(row,'reject')}><Button disabled={busy} size="small">驳回</Button></Popconfirm>
        </Space>},
      ]}/>
  </section>;
}
