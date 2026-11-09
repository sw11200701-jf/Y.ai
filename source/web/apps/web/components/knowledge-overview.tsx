'use client';
import {useEffect,useState} from 'react';
import {Collapse,Empty,Tag} from 'antd';
import {Json,request} from '@/lib/api';
import {formatError,type FormattedError} from '@/lib/errors';
import {ErrorBanner} from './shared';
export default function KnowledgeOverview({libraryId}:{libraryId:string}){const [data,setData]=useState<Json>();const [error,setError]=useState<string|FormattedError>('');useEffect(()=>{let stale=false;setData(undefined);setError('');void request(`/knowledge-bases/${libraryId}/overview`).then(value=>{if(!stale)setData(value);}).catch(e=>{if(!stale)setError(formatError(e));});return()=>{stale=true;};},[libraryId]);return <Collapse className="knowledge-overview" items={[{key:'overview',label:'知识概述',children:<><ErrorBanner error={error}/>{data&&(data.items?.length?data.items.map((item:Json)=><article className="search-hit" key={item.document_id}><strong>{item.document_name}</strong><p>{item.overview?.summary||'尚未填写概述'}</p><Tag>{item.chunk_count} 个授权切片</Tag>{['topic','subject','period'].map(key=>item.overview?.[key]?<Tag key={key}>{item.overview[key]}</Tag>:null)}</article>):<Empty description="当前没有可展示概述的已发布文档"/>)}<p className="hint">仅展示服务端权限筛选后的发布内容；概述不能代替原文与切片来源。</p></>}]} />;}
