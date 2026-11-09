'use client';
import {useId,useState} from 'react';
import { groups, nodeGroup, nodeSubgroup, readinessReason, stateLabels } from '@/lib/workflow/adapter';
import { messageKinds } from '@/lib/workflow/messages';
import type { NodeDefinition, NodeRegistry } from '@/lib/workflow/types';
import styles from './studio.module.css';
export function NodeLibrary({ scope = 'root', catalog, connected, count, legacy, busy, search, onSearch, onRefresh, onAdd }: { scope?: 'root' | 'subflow'; catalog: NodeRegistry; connected: boolean; count: number; legacy: boolean; busy: boolean; search: string; onSearch: (v: string) => void; onRefresh: () => void; onAdd: (d: NodeDefinition) => void }) {
 const displayLabel=(definition:NodeDefinition)=>definition.type==='output'?'结束并返回结果':definition.type==='end'?'仅终止（旧版兼容）':definition.label;
 const [group,setGroup]=useState('触发'),[subgroup,setSubgroup]=useState('模型与智能体');const id=useId(),query=search.trim().toLowerCase();
 const entries=catalog.items.filter(d=>d.type!=='aggregate'&&`${displayLabel(d)} ${d.label} ${d.type} ${nodeGroup(d)} ${nodeSubgroup(d)}`.toLowerCase().includes(query));
 const messageEntries=scope==='root'?messageKinds:[];
 const missing=messageEntries.filter(kind=>!catalog.items.some(d=>d.type===`trigger_${kind.type}`)&&(!query||`${kind.label} 触发`.includes(query)));
 const actions=catalog.items.filter(d=>d.type!=='aggregate'&&nodeGroup(d)==='行为'),subgroups=[...new Set(actions.map(nodeSubgroup))],activeSubgroup=subgroups.includes(subgroup)?subgroup:subgroups[0];
 const entry = (definition: NodeDefinition) => <button key={definition.type} className={styles.libraryNode} draggable={!legacy && !busy && (definition.status !== 'unavailable'||definition.configurable===true)} disabled={legacy || busy || (definition.status === 'unavailable'&&definition.configurable!==true)} title={readinessReason(definition) || '拖动到画布，或点击添加'} onDragStart={event => { event.dataTransfer.setData('application/workflow-node', definition.type); event.dataTransfer.effectAllowed = 'copy'; }} onClick={() => onAdd(definition)}><span>◇</span><span><strong>{displayLabel(definition)}</strong><small>{stateLabels[definition.status]}{definition.status !== 'supported' && definition.reason ? ` · ${readinessReason(definition)}` : ''}</small></span><span>＋</span></button>;
 const visible=query?groups:[group];
 return <aside className={styles.library}><div className={styles.panelHeading}><strong>节点库</strong><button aria-label="刷新节点状态" onClick={onRefresh}>↻</button></div><div className={styles.librarySearch}><input aria-label="搜索节点" placeholder="跨分类搜索节点与能力" value={search} onChange={e=>onSearch(e.target.value)}/>{search&&<button aria-label="清空节点搜索" onClick={()=>onSearch('')}>×</button>}</div>
 <div role="tablist" aria-label="节点分类" className={styles.libraryGroups}>{groups.map(name=><button key={name} role="tab" aria-selected={!query&&group===name} aria-controls={`${id}-list`} onClick={()=>{setGroup(name);if(search)onSearch('');}}>{name}<small>{catalog.items.filter(d=>d.type!=='aggregate'&&nodeGroup(d)===name).length+(name==='触发'?messageEntries.filter(k=>!catalog.items.some(d=>d.type===`trigger_${k.type}`)).length:0)}</small></button>)}</div>
 {scope==='subflow'&&<p className={styles.hint}>子流程使用开始或用户输入节点，消息从外层传入。消息触发入口请在根画布配置。</p>}
 {!query&&group==='行为'&&<div className={styles.librarySubgroups} aria-label="行为分类">{subgroups.map(name=><button key={name} aria-pressed={activeSubgroup===name} onClick={()=>setSubgroup(name)}>{name}<small>{actions.filter(d=>nodeSubgroup(d)===name).length}</small></button>)}</div>}
 {query&&<p className={styles.searchSummary}>全分类搜索 · {entries.length+missing.length} 项</p>}
 <div id={`${id}-list`} role="region" aria-label={query?'节点搜索结果':`${group}节点`} tabIndex={0} className={styles.libraryScroll}>{visible.map(name=>{
 const definitions=entries.filter(d=>nodeGroup(d)===name&&(query||name!=='行为'||nodeSubgroup(d)===activeSubgroup));const pending=name==='触发'?missing:[];
 if(!definitions.length&&!pending.length)return null;
 return <section key={name}>{query&&<h3>{name}</h3>}{pending.map(kind=><button key={`pending-${kind.type}`} className={styles.libraryNode} disabled title="消息画布入口尚未由服务端注册，不能添加或执行"><span>{kind.glyph}</span><span><strong>{kind.label}</strong><small>待接入 · 消息触发路由未就绪</small></span></button>)}{definitions.map(entry)}</section>;
 })}{!(query?entries.length+missing.length:entries.filter(d=>nodeGroup(d)===group&&(group!=='行为'||nodeSubgroup(d)===activeSubgroup)).length+(group==='触发'?missing.length:0))&&<div className={styles.libraryEmpty}>{query?'没有匹配节点，试试其他关键词。':'当前分类暂无节点。'}</div>}</div>
 <small className={styles.libraryFoot}>拖动或点击添加 · {connected?'目录已连接':'目录未连接'}<br/>{count}/{catalog.limits.max_nodes||64} 节点</small></aside>;
}
