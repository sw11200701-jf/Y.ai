'use client';
import type { NodeRegistry, NodeTrace, StudioEvent, StudioRun, WorkflowDefinition } from '@/lib/workflow/types';
import {orderedRunNodes} from '@/lib/workflow/run-order';
import { eventBelongsToNode } from '@/lib/workflow/run-events';
import { stateLabels } from '@/lib/workflow/adapter';
import { publicReasoningDisplay } from '@/lib/workflow/public-reasoning';
import { PresentationResult } from './presentation-result';
import { TypedValue } from './typed-value';
import { RetrievalResult } from './retrieval-result';
import styles from './studio.module.css';

const modelTypes = new Set(['llm', 'agent', 'parameter_extractor', 'question_classifier']);
const hasValue = (value: unknown) => value !== undefined && value !== null && (typeof value !== 'object' || Object.keys(value).length > 0);
function Reasoning({ trace }: { trace: NodeTrace }) {
  const value = publicReasoningDisplay(trace.reasoning);
  return <details className={styles.details} data-reasoning-status={value.status}><summary>{value.title} · {value.statusLabel}</summary><p className={styles.hint}>{value.message}</p>{value.source && <small>来源：{value.sourceLabel} · {value.source}</small>}{value.profileLabel && <p className={styles.hint}>适配：{value.profileLabel} · 模型版本：{value.revision}<br />{value.verification}</p>}{value.text !== undefined && <div className={styles.typedText}>{value.text}</div>}</details>;
}
/** Consumes only the same-run, ACL-checked projection. Never derives final output from events. */
export function RunResults({ run, workflow, registry, events, traces, selected, onSelect, traceError }: { run: StudioRun | null; workflow: WorkflowDefinition; registry?: NodeRegistry; events: StudioEvent[]; traces: NodeTrace[]; selected: string; onSelect: (id: string) => void; traceError?: string }) {
  const nodeName = (id: string) => { const node = workflow.nodes.find(node => node.id === id); return node?.ui?.label || registry?.items.find(definition => definition.type === node?.type)?.label || id; };
  const fieldLabels=Object.assign({},...workflow.nodes.filter(node=>selected?node.id===selected:['output','answer'].includes(node.type)).map(node=>node.ui?.field_labels||{}));
  const trace = traces.find(item => item.node_id === selected);
  const presentations = Array.isArray(run?.presentations) ? run.presentations.filter(item => item?.kind === 'json' && Object.hasOwn(item, 'json') || typeof item?.text === 'string' || item?.table && Array.isArray(item.table.rows) || Array.isArray(item?.files) && item.files.length > 0) : [];
  const output = selected ? trace && Object.hasOwn(trace, 'outputs') ? trace.outputs : run?.variable_pool?.nodes?.[selected] : run?.outputs;
  const finalMissing = Boolean(run && !run.view_unavailable && run.status === 'succeeded' && !hasValue(run.outputs) && presentations.length === 0);
  const states = new Map<string, string>();
  for (const event of events) if (event.node_id && event.type.startsWith('node.')) states.set(event.node_id, event.status || event.type.replace(/^node\./, ''));
  for (const item of traces) states.set(item.node_id, item.status);
  const table = output && typeof output === 'object' && Array.isArray((output as any).rows) && Array.isArray((output as any).columns) ? output as Record<string, any> : null;
  const represented = new Set(presentations.flatMap(item => item.kind === 'json' && item.json && typeof item.json === 'object' ? Object.keys(item.json) : ['text', 'table', 'files'].filter(key => Object.hasOwn(item, key))));
  const extra = run?.outputs && typeof run.outputs === 'object' ? Object.fromEntries(Object.entries(run.outputs).filter(([key]) => !represented.has(key))) : null;
  const filtered = selected ? events.filter(event => eventBelongsToNode(event, selected)) : events;
  const knowledge = trace?.node_type === 'knowledge_retrieval';
  return <div className={styles.runResults}>
    <nav className={styles.runNodeList} aria-label="本次节点执行进度"><button aria-pressed={!selected} onClick={() => onSelect('')}>本次最终输出</button>{orderedRunNodes(workflow,events,traces).filter(item => run?.execution_identity?.kind !== 'node_test' || item.id === run.execution_identity.node_id).map(item => <button key={item.id} data-node-id={item.id} aria-pressed={selected === item.id} onClick={() => onSelect(item.id)} data-state={states.get(item.id) || 'pending'}><span className={styles.statusDot} /><span>{nodeName(item.id)}</span><small>{stateLabels[states.get(item.id) || 'idle'] || '等待执行'}</small></button>)}</nav>
    <div className={styles.runOutput} aria-live="polite">
      <h3>{selected ? `${nodeName(selected)} · 本次节点结果` : '本次最终输出'}</h3>
      {traceError && <p role="alert" className={styles.errorText}>{traceError}</p>}
      {finalMissing && !selected && <div role="alert" className={styles.warning}>服务端标记本次执行成功，但没有返回最终输出。请检查输出节点的字段映射；本次没有可交付结果。</div>}
      {selected && <section><h4>实际输入</h4>{trace && Object.hasOwn(trace, 'inputs') ? <TypedValue value={trace.inputs} /> : <p className={styles.hint}>尚未提供本次节点的实际输入。</p>}</section>}
      {trace?.error && <div role="alert" className={styles.alert}>{trace.error.message || '节点执行失败，请查看错误编号并重试。'}<small>{trace.error.code}{trace.error.request_id ? ` · 请求编号 ${trace.error.request_id}` : ''}</small></div>}
      {selected && <h4>实际输出</h4>}
      {knowledge ? <RetrievalResult value={(trace?.outputs as any)?.retrieval} /> : !selected && presentations.length ? <><PresentationResult key={run?.id} items={presentations} />{extra && Object.keys(extra).length > 0 && <TypedValue value={extra} fieldLabels={fieldLabels} />}</> : table ? <><PresentationResult items={[{ node_id: selected, table }]} /><details className={styles.details}><summary>数据版本与来源</summary><TypedValue value={Object.fromEntries(Object.entries(table).filter(([key]) => !['rows', 'columns'].includes(key)))} /></details></> : output !== undefined ? <TypedValue value={output} fieldLabels={fieldLabels} /> : <p className={styles.hint}>{trace?.status === 'skipped' || states.get(selected) === 'skipped' ? '分支未命中，节点已跳过，没有输出。' : run ? '本次执行尚未返回可用输出。' : '填写试运行输入后开始执行，结果会显示在这里。'}</p>}
      {trace && modelTypes.has(trace.node_type) && <Reasoning trace={trace} />}
      {!selected && traces.filter(item => modelTypes.has(item.node_type)).map(item => <section key={item.node_id}><small>{nodeName(item.node_id)}</small><Reasoning trace={item} /></section>)}
      {run?.artifact_ids?.length ? <p className={styles.hint}>{run.artifact_ids.length} 个真实产物，可在运行记录中下载。</p> : null}
      <details className={styles.details}><summary>技术详情 · 输入输出、耗时与执行事件</summary>{trace && <><p>请求编号：{trace.request_id || '未提供'}</p><p>开始：{trace.started_at || '未提供'} · 完成：{trace.completed_at || '未提供'}</p><pre>{JSON.stringify({ inputs: trace.inputs, outputs: knowledge ? { retrieval: (trace.outputs as any)?.retrieval } : trace.outputs, technical: trace.technical }, null, 2)}</pre></>}{!selected && <pre>{JSON.stringify(run?.outputs, null, 2)}</pre>}<ol className={styles.events}>{filtered.map(event => <li key={event.sequence} data-state={event.status || event.type.split('.').pop()}><div><strong>{event.node_id || '流程'} · {stateLabels[event.status || ''] || '状态更新'}</strong><small>{event.sequence} · {event.type}</small></div></li>)}</ol></details>
    </div>
  </div>;
}
