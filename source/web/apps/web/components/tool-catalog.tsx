'use client';
import {useEffect,useState} from 'react';
import {Modal,Button,Input,Table} from 'antd';
import {currentIdentityEpoch} from '@/lib/api';
import {loadToolCatalog,type CatalogTool} from '@/lib/tool-catalog';
import {formatError,type FormattedError} from '@/lib/errors';
import {ErrorBanner} from './shared';
import ToolCatalogDetails from './tool-catalog-details';
export default function ToolCatalog({open,onClose}:{open:boolean;onClose:()=>void}){
 const [items,setItems]=useState<CatalogTool[]>([]),[query,setQuery]=useState(''),[selected,setSelected]=useState<CatalogTool>(),[loading,setLoading]=useState(false),[error,setError]=useState<string|FormattedError>(''),[attempt,setAttempt]=useState(0);
 useEffect(()=>{let alive=true;const epoch=currentIdentityEpoch();setItems([]);setSelected(undefined);setError('');if(open){setLoading(true);void loadToolCatalog().then(value=>{if(alive&&epoch===currentIdentityEpoch())setItems(value);},e=>{if(alive&&epoch===currentIdentityEpoch())setError(formatError(e));}).finally(()=>{if(alive&&epoch===currentIdentityEpoch())setLoading(false);});}const clear=()=>{alive=false;setItems([]);setSelected(undefined);onClose();};const events=['zhangpu:permissions-changed','zhangpu:data-policy-changed','zhangpu:session-expired','zhangpu:access-denied'];events.forEach(event=>window.addEventListener(event,clear));return()=>{alive=false;events.forEach(event=>window.removeEventListener(event,clear));};},[open,attempt]);
 return <Modal open={open} title="工具目录" onCancel={onClose} footer={<Button onClick={onClose}>关闭</Button>} width={900}><ErrorBanner error={error}/><p>查看已发布工具的字段、固定连接与权限要求；读取目录不会调用工具。</p><Input aria-label="搜索工具" value={query} onChange={e=>setQuery(e.target.value)}/><Button onClick={()=>setAttempt(value=>value+1)}>刷新工具目录</Button><Table rowKey={item=>JSON.stringify(item.binding)} loading={loading} dataSource={items.filter(item=>`${item.name} ${item.description}`.includes(query))} columns={[{title:'工具',dataIndex:'name'},{title:'方法',dataIndex:'http_method'},{title:'查看',render:(_,item)=><Button onClick={()=>setSelected(item)}>字段与权限</Button>}]}/>{selected&&<ToolCatalogDetails value={selected}/>}</Modal>;
}
