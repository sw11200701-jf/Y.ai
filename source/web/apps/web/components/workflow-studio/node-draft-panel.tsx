'use client';
import { useEffect, useRef, useState, type ComponentProps } from 'react';
import { commitNodeDraft, nodeSettingsChange, type NodeSettingsChange } from '@/lib/workflow/node-draft';
import type { WorkflowDefinition } from '@/lib/workflow/types';
import { NodeSettings } from './node-settings';
import styles from './studio.module.css';

type Settings = ComponentProps<typeof NodeSettings>;
export function NodeDraftPanel({ onCommit, onCancel, onDirtyChange, saveScopeNote, ...props }: Omit<Settings, 'onChange' | 'onWorkflowChange' | 'onValidity'> & { onCommit: (value: WorkflowDefinition,change:NodeSettingsChange) => void | Promise<void>; onCancel: () => void; onDirtyChange?: (dirty: boolean) => void; saveScopeNote?:string }) {
  const [baseline] = useState(() => JSON.parse(JSON.stringify(props.workflow)) as WorkflowDefinition);
  const validRef=useRef(true),issuesRef=useRef<string[]>([]),errorKind=useRef<'validation'|'commit'|null>(null);const [validationIssues,setValidationIssues]=useState<string[]>([]);const [saved,setSaved]=useState(false);const [saving,setSaving]=useState(false); const pending=useRef(false); const [draft, setDraft] = useState(baseline); const [valid, setValid] = useState(true); const [edited, setEdited] = useState(false); const [error, setError] = useState('');
  const latest = useRef(props.workflow); latest.current = props.workflow;
  const mounted = useRef(true); useEffect(() => {mounted.current=true;return()=>{mounted.current=false;};},[]);
  const node = draft.nodes.find(node => node.id === props.node.id)!;
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  const unsaved = dirty || edited && !valid;
  const notify = useRef(onDirtyChange); notify.current = onDirtyChange;
  useEffect(() => { notify.current?.(unsaved); }, [unsaved]); useEffect(() => () => notify.current?.(false), []);
  return <section onChangeCapture={() => setEdited(true)}><div className={styles.nodeSaveBar}><button disabled={saving} onClick={async () => { if(pending.current)return;if(!validRef.current){errorKind.current='validation';setError(issuesRef.current.join('；')||'请检查当前节点标记的必填字段与格式');return;}pending.current=true;setSaving(true);errorKind.current=null;setError('');try { const next = commitNodeDraft(latest.current, baseline, draft, node.id); await onCommit(next,nodeSettingsChange(baseline,draft,node.id)); if(mounted.current){setSaved(true);onCancel();} } catch (error) { errorKind.current='commit';if(mounted.current)setError([...(Array.isArray((error as any).details) ? (error as any).details : []).map((d:any)=>`${(d.loc||[]).join('.')}: ${d.message||d.type||'字段无效'}`),(error as Error).message].filter(Boolean).join('；')); } finally {pending.current=false;if(mounted.current)setSaving(false);} }}>{saving?'保存中…':saved?'已保存':'保存节点'}</button><button disabled={saving} onClick={onCancel}>取消</button>{unsaved&&<span role="status" className={styles.hint}>未保存</span>}</div><label className={styles.field}><span>节点名称</span><input disabled={saving} aria-label="节点草稿名称" value={node.ui?.label || ''} onChange={event => setDraft(value => ({ ...value, nodes: value.nodes.map(item => item.id === node.id ? { ...item, ui: { ...item.ui, label: event.target.value } } : item) }))} /></label>
    <fieldset className={styles.nodeDraftFields} disabled={saving}><NodeSettings {...props} onIssues={items=>{issuesRef.current=items;setValidationIssues(items);}} node={node} workflow={draft} onChange={patch => setDraft(value => ({ ...value, nodes: value.nodes.map(item => item.id === node.id ? { ...item, ...patch } : item) }))} onWorkflowChange={setDraft} onValidity={value=>{validRef.current=value;setValid(value);if(value&&errorKind.current==='validation'){errorKind.current=null;setError('');}}} /></fieldset>{(error||validationIssues.length>0)&&<div role="alert" className={styles.errorText}>{error||validationIssues.join("；")}</div>}
  {saveScopeNote&&<details className={styles.details}><summary>保存说明</summary><p>{saveScopeNote}</p></details>}</section>;
}
