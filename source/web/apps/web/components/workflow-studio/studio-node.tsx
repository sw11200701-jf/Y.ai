'use client';
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { isEntryType, nodeTone, readinessReason, stateLabels } from '@/lib/workflow/adapter';
import { enumLabel } from '@/lib/workflow/labels';
import type { NodeDefinition, WorkflowNode } from '@/lib/workflow/types';
import styles from './studio.module.css';
export function portLabel(node: WorkflowNode | undefined, port: string) {
  if (node?.type !== 'question_classifier') return enumLabel(port);
  const classes = node.config.classes;
  const choice = Array.isArray(classes) ? classes.find(item => item?.id === port) : classes?.[port];
  return typeof choice === 'string' ? choice : choice?.label || enumLabel(port);
}
export type StudioNodeData = { node: WorkflowNode; definition?: NodeDefinition; ports: string[]; runStatus?: string; issue?: string; [key: string]: unknown };
export type FlowNode = Node<StudioNodeData, 'studio'>;
const glyphs: Record<string, string> = { start: '▶', input: '↳', answer: '↗', end: '■', output: '↗', llm: '✦', agent: '◎', if_else: '⑂', iteration: '↻', loop: '⟳', query: '▤', calculate: '∑', chart: '▥', report: '▧', plugin: '⚙', http: '⇄', template: 'T', aggregate: '⋈', list: '☷' };
export function StudioNode({ data, selected }: NodeProps<FlowNode>) {
  const { node, definition: def, ports, runStatus, issue } = data;
  const status = runStatus || 'idle';
  return <div className={`${styles.node} ${selected ? styles.nodeSelected : ''}`} data-tone={nodeTone(def)} data-state={status} data-invalid={Boolean(issue)}>
    {!isEntryType(node.type, def) && <Handle id="default" type="target" position={Position.Left} className={styles.handle} />}
    <div className={styles.nodeHeading}><span className={styles.nodeGlyph}>{glyphs[node.type] || '◇'}</span><strong>{node.ui?.label || def?.label || node.type}</strong><span className={styles.statusDot} /></div>
    <small className={styles.nodeId} title={node.id}>{def?.label || '处理步骤'}</small><div className={styles.nodeFoot}><span title={issue || (def ? readinessReason(def) : '')}>{issue ? '配置待修正' : def?.status === 'supported' ? '能力可用' : stateLabels[def?.status || 'unavailable']}</span><span>{stateLabels[status] || '待执行'}</span></div>
    {ports.map((port, index) => <div key={port}><Handle id={port} type="source" position={Position.Right} style={{ top: `${((index + 1) / (ports.length + 1)) * 100}%` }} className={styles.handle} />{port !== 'default' && <span className={styles.portLabel} style={{ top: `${((index + 1) / (ports.length + 1)) * 100}%` }}>{portLabel(node, port)}</span>}</div>)}
  </div>;
}
