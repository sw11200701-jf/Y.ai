'use client';
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { isEntryType, nodeTone, readinessReason, stateLabels } from '@/lib/workflow/adapter';
import { enumLabel } from '@/lib/workflow/labels';
import type { NodeDefinition, WorkflowNode } from '@/lib/workflow/types';
import styles from './studio.module.css';
export type StudioNodeData = { node: WorkflowNode; definition?: NodeDefinition; ports: string[]; runStatus?: string; [key: string]: unknown };
export type FlowNode = Node<StudioNodeData, 'studio'>;
const glyphs: Record<string, string> = { start: '▶', input: '↳', answer: '↗', end: '■', output: '↗', llm: '✦', agent: '◎', if_else: '⑂', iteration: '↻', loop: '⟳', query: '▤', calculate: '∑', chart: '▥', report: '▧', plugin: '⚙', http: '⇄', template: 'T', aggregate: '⋈', list: '☷' };
export function StudioNode({ data, selected }: NodeProps<FlowNode>) {
  const { node, definition: def, ports, runStatus } = data;
  const status = runStatus || 'idle';
  const portLabel=(port:string)=>{const classes=node.config.classes;if(node.type!=='question_classifier')return enumLabel(port);const choice=Array.isArray(classes)?classes.find(item=>item&&typeof item==='object'&&item.id===port):classes?.[port];return typeof choice==='string'?choice:choice?.label||enumLabel(port);};
  return <div className={`${styles.node} ${selected ? styles.nodeSelected : ''}`} data-tone={nodeTone(def)} data-state={status}>
    {!isEntryType(node.type, def) && <Handle id="default" type="target" position={Position.Left} className={styles.handle} />}
    <div className={styles.nodeHeading}><span className={styles.nodeGlyph}>{glyphs[node.type] || '◇'}</span><strong>{node.ui?.label || def?.label || node.type}</strong><span className={styles.statusDot} /></div>
    <small className={styles.nodeId} title={node.id}>{def?.label || '处理步骤'}</small><div className={styles.nodeFoot}><span title={def ? readinessReason(def) : ''}>{stateLabels[def?.status || 'unavailable']}</span><span>{stateLabels[status] || '待执行'}</span></div>
    {ports.map((port, index) => <div key={port}><Handle id={port} type="source" position={Position.Right} style={{ top: `${((index + 1) / (ports.length + 1)) * 100}%` }} className={styles.handle} />{port !== 'default' && <span className={styles.portLabel} style={{ top: `${((index + 1) / (ports.length + 1)) * 100}%` }}>{portLabel(port)}</span>}</div>)}
  </div>;
}
