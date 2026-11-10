'use client';
import { useEffect, useState } from 'react';
import { Button, Input, Space, Table, Tabs, Tag, Typography } from 'antd';
import { currentIdentityEpoch, request, time } from '@/lib/api';
import { formatError, type FormattedError } from '@/lib/errors';
import { ErrorBanner, PageTitle } from './shared';

const categories = { all:'全部日志', error:'报错日志', login:'登录日志', operation:'操作日志' };
const sources: Record<string,string> = { request:'接口', browser:'页面', run:'任务', audit:'操作审计' };
type Category = keyof typeof categories;
type Log = { id:string; category:Exclude<Category,'all'>; source:string; action:string; actor?:string;
  status?:number; code?:string; request_id?:string; run_id?:string; created_at:string };
type Result = { items:Log[]; has_more:boolean };
const empty: Result = { items:[], has_more:false };

export default function WorkspaceLogs() {
  const [category,setCategory]=useState<Category>('all');
  const [page,setPage]=useState(1);
  const [reference,setReference]=useState('');
  const [refresh,setRefresh]=useState(0);
  const [result,setResult]=useState<Result>(empty);
  const [error,setError]=useState<FormattedError>();
  const [loading,setLoading]=useState(true);
  useEffect(()=>{
    const controller=new AbortController();
    const epoch=currentIdentityEpoch();
    const current=()=>!controller.signal.aborted&&epoch===currentIdentityEpoch();
    const query=new URLSearchParams({category,page:String(page),reference});
    setLoading(true);setError(undefined);setResult(empty);
    void request<Result>('/workspace-logs?'+query,{signal:controller.signal}).then(
      value=>{if(current())setResult(value);},
      failure=>{if(current())setError(formatError(failure));},
    ).finally(()=>{if(current())setLoading(false);});
    return()=>controller.abort();
  },[category,page,reference,refresh]);
  return <>
    <PageTitle eyebrow="ADMIN" title="系统日志" description="按日志类型查看当前工作空间的记录。可用查询编号、运行编号或错误代码检索。"/>
    <section className="panel">
      <Tabs activeKey={category} items={Object.entries(categories).map(([key,label])=>({key,label}))}
        onChange={key=>{setCategory(key as Category);setPage(1);}}/>
      <Space wrap>
        <Input.Search aria-label="查询编号或错误代码" placeholder="查询编号、运行编号或错误代码"
          allowClear enterButton="查询" maxLength={100}
          onSearch={value=>{setReference(value.trim());setPage(1);setRefresh(n=>n+1);}}/>
        <Button onClick={()=>setRefresh(n=>n+1)}>刷新</Button>
      </Space>
      <ErrorBanner error={error}/>
      <Table<Log> rowKey="id" loading={loading} dataSource={result.items} pagination={false}
        locale={{emptyText:error?'暂时无法读取日志':'暂无匹配日志'}} scroll={{x:900}}
        columns={[
          {title:'时间',dataIndex:'created_at',render:time},
          {title:'类型',key:'category',render:(_,row)=><Tag color={row.category==='error'||(row.status??0)>=400?'error':undefined}>{categories[row.category]}</Tag>},
          {title:'来源',dataIndex:'source',render:value=>sources[value]||value},
          {title:'事件',dataIndex:'action'},
          {title:'操作者',dataIndex:'actor',render:value=>value||'—'},
          {title:'HTTP 状态',dataIndex:'status',render:value=>value||'—'},
          {title:'错误代码',dataIndex:'code',render:value=>value||'—'},
          {title:'查询编号',key:'reference',render:(_,row)=>{
            const value=row.request_id||row.run_id||row.id;
            return <Typography.Text copyable={{text:value}} style={{overflowWrap:'anywhere'}}>{value}</Typography.Text>;
          }},
        ]}/>
      <Space wrap>
        <Button disabled={loading||page===1} onClick={()=>setPage(n=>n-1)}>上一页</Button>
        <span>第 {page} 页</span>
        <Button disabled={loading||!result.has_more} onClick={()=>setPage(n=>n+1)}>下一页</Button>
      </Space>
    </section>
  </>;
}
