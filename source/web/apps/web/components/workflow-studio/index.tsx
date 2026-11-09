'use client';
import {UserVariables} from './user-variables';
import {redefineVariables} from '@/lib/workflow/variable-definitions';
import {timedLoad,loadClock,recordLoad} from '@/lib/load-timing';
import { menuLabels } from '@/lib/ui-labels';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Background, Controls, MiniMap, ReactFlow, ReactFlowProvider, useReactFlow, type Connection, type Edge, type NodeChange, type EdgeChange } from '@xyflow/react';
import registrySnapshot from '../../../../contracts/node-registry.json';
import { App, Input, Modal } from 'antd';
import { useLeaveConfirmation } from './use-leave-confirmation';
import { request,currentIdentityEpoch } from '@/lib/api';
import { workflowApi, loadResources } from '@/lib/workflow/api';
import { availableVariables, clone, configuredInputSchema, connectionError, convertLegacy, edgeKey, entryConflict, groups, isEntryType, isLegacy, newAgentWorkflow, newNode, newWorkflow, nodeGroup, nodeSubgroup, outputPorts, positionOf, readinessReason, stateLabels } from '@/lib/workflow/adapter';
import type { ApprovalDecision, CheckedTestTarget, DebugTarget, RunApproval, MessageDraft, NodeDefinition, NodeRegistry, NodeTrace, ObjectValue, StudioEvent, StudioResources, StudioRun, TestVariables, VariableOption, WorkflowDefinition, WorkflowNode, WorkflowStudioProps } from '@/lib/workflow/types';
import { orderedRunEvents, eventNodeStates, topLevelProgress, isPendingRun, runPollDelay, canRunAction, appendEventPage, consistentProjection } from '@/lib/workflow/run-events';
import { emptyMessage, messageKinds, toMessageEnvelope } from '@/lib/workflow/messages';
import { inputDefaults } from '@/lib/workflow/debug-inputs';
import { checkDescription, matchesTestIdentity, scopeWorkflow } from '@/lib/workflow/scoped-debug';
import MessageComposer from './message-composer';
import { MessageWorkflowCreate } from './message-workflow-create';
import { newMessageWorkflow, initialWorkflowMessage, workflowMessageEnvelope, workflowTestInputLayout } from '@/lib/workflow/message-workflow';
import { NodeLibrary } from './node-library';
import { LinkedWorkflow } from './linked-workflow';
import { JsonEditor, SchemaFields } from './schema-fields';
import { StudioNode, type FlowNode } from './studio-node';
import { errorMessage } from '@/lib/errors';
import { typeLabel } from '@/lib/workflow/labels';
import { CanvasWorkspace } from './canvas-workspace';
import { CanvasViewport, canvasFitOptions } from './canvas-viewport';
import { KnowledgePicker } from './knowledge-picker';
import { DatabaseQuery } from './database-query';
import { RunStatus } from './run-status';
import LlmEditor from './llm-editor';
import { RunApprovals } from './run-approvals';
import { ApprovalInbox } from './approval-inbox';
import { RunPanel } from './run-panel';
import { RunResults } from './run-results';
import { SchemaDefinition } from './schema-definition';
import { NodeTestDialog } from './node-test-dialog';
import { SubflowEditor } from './subflow-editor';
import { NodeDraftPanel } from './node-draft-panel';
import {mergeSavedNode,nodeSettingsRequest,nodeStructureRequest,withWorkflowLayout,type NodeSettingsChange} from '@/lib/workflow/node-draft';
import { LoopCondition } from './loop-condition';
import { ConditionEditor } from './condition-editor';
import { OutputOptions } from './output-options';
import WorkflowFileTools from '../workflow-file-tools';
import {workflowFileClient} from '@/lib/workflow-file-api';
import styles from './studio.module.css';

const nodeTypes = { studio: StudioNode };
const emptyResources: StudioResources = { agents: [], agentVersions: [], models: [], documents: [], plugins: [], versions: [], connections: [], imports: [], warnings: [] };
const pendingRegistry = { ...registrySnapshot, items: registrySnapshot.items.map(d => ({ ...d, status: 'unavailable', reason: '实时注册表未连接，仅供浏览节点目录' })) } as unknown as NodeRegistry;
const messageOf = errorMessage;
const accountEpoch = () => typeof currentIdentityEpoch === 'function' ? currentIdentityEpoch() : 0;
function Studio({ fileClient=workflowFileClient,onWorkflowImported,createOnOpen=false, initialMessageKind, initialAgent, onNavigate, onOpenApplicationApi, parentGuardsNavigation = false, canManageModels = false, fullPage = false, onBack, onLeaveGuardChange, embedded = false, agentId, agentName, workflowId, identityKey, publishedVersion, onWorkflowChange, onDirtyChange, onPublish }: WorkflowStudioProps) {
  const renderStarted=loadClock();const enteredAt=useRef(renderStarted);useEffect(()=>recordLoad('render_commit',renderStarted));
  const {modal}=App.useApp(); const modalRef=useRef(modal);modalRef.current=modal;
  const [fileMode,setFileMode]=useState<'import'|'export'|null>(null);const fileCreating=useRef(false);
  const [saveOpen,setSaveOpen]=useState(false);const [saveName,setSaveName]=useState('');
  const actionPending = useRef(false); const savePending = useRef(false); const navigationPending = useRef(false);
  const successDialogs = useRef(new Set<{ destroy: () => void }>());
  const closeSuccessDialogs = useCallback(() => { const dialogs = [...successDialogs.current]; successDialogs.current.clear(); dialogs.forEach(dialog => dialog.destroy()); }, []);
  useEffect(() => closeSuccessDialogs, [closeSuccessDialogs]);
  const [nodeDraftDirty, setNodeDraftDirty] = useState(false); const [inspectorOpen, setInspectorOpen] = useState(false); const [linkedAttempt, setLinkedAttempt] = useState(0);
  const showInspector = (next: 'node' | 'workflow') => { setTab(next); setInspectorOpen(true); };
  const listRequest = useRef(0);
  const [items, setItems] = useState<WorkflowDefinition[]>([]); const [workflow, setWorkflow] = useState<WorkflowDefinition | null>(null);
  useEffect(()=>{if(workflow)recordLoad('editable',enteredAt.current);},[Boolean(workflow)]);
  const [baseline, setBaseline] = useState(''); const [registry, setRegistry] = useState<NodeRegistry | null>(null); const [registryError, setRegistryError] = useState('');
  const [resources, setResources] = useState(emptyResources);const [resourcesLoading,setResourcesLoading]=useState(true); const [loading, setLoading] = useState(true); const [busy, setBusy] = useState('');
  const [readonlyVariables, setReadonlyVariables] = useState<VariableOption[]>([]);
  const [variableError, setVariableError] = useState('');const [customUserVariables,setCustomUserVariables]=useState<ObjectValue[]>([]);
  useEffect(()=>{let live=true;const epoch=currentIdentityEpoch();request<{items:ObjectValue[]}>('/user-variables').then(r=>{if(live&&epoch===currentIdentityEpoch())setCustomUserVariables(r.items);},()=>{});return()=>{live=false;};},[]);
  const [notice, setNotice] = useState(''); const [error, setError] = useState(''); const [search, setSearch] = useState(''); const [listSearch, setListSearch] = useState('');
  const [selected, setSelected] = useState<string[]>([]); const [selectedEdges, setSelectedEdges] = useState<string[]>([]); const [session, setSession] = useState(0);
  const [measurements, setMeasurements] = useState<Record<string, { width: number; height: number }>>({});
  const [tab, setTab] = useState<'node' | 'workflow'>('node'); const [run, setRun] = useState<StudioRun | null>(null); const [events, setEvents] = useState<StudioEvent[]>([]);
  const runControlPending = useRef(false); const runStateVersion = useRef(0); const [runRefresh, setRunRefresh] = useState(0);
  const [approvals, setApprovals] = useState<RunApproval[]>([]); const [approvalOpen, setApprovalOpen] = useState(false);
  const [traces, setTraces] = useState<NodeTrace[]>([]); const [traceError, setTraceError] = useState('');
  const [runWorkflow, setRunWorkflow] = useState<WorkflowDefinition | null>(null);
  const [testNode, setTestNode] = useState<WorkflowNode | null>(null); const [subflowId, setSubflowId] = useState('');
  const [testScopePath, setTestScopePath] = useState<string[]>([]); const expectedTarget = useRef<DebugTarget | null>(null);
  const [runTab, setRunTab] = useState<'inputs' | 'results'>('inputs');
  const [runError, setRunError] = useState(''); const [resultOpen, setResultOpen] = useState(false); const [resultNode, setResultNode] = useState('');
  const [testInputs, setTestInputs] = useState<ObjectValue>({}); const [query, setQuery] = useState<ObjectValue>({}); const [prompt, setPrompt] = useState('');
  const [invalid, setInvalid] = useState<Record<string, boolean>>({}); const [fullScreen, setFullScreen] = useState(false);
  const [message, setMessage] = useState<MessageDraft>(emptyMessage); const [uploading, setUploading] = useState(false);
  const [agent, setAgent] = useState<ObjectValue | null>(null); const [linkedId, setLinkedId] = useState<string | null>(null); const [bindSelection, setBindSelection] = useState('');
  const [linkedLoading, setLinkedLoading] = useState(embedded); const linkedRequest = useRef(0);
  const alive = useRef(true); const resourceTicket = useRef(0);
  const canvas = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition, fitView } = useReactFlow<FlowNode>(); const root = useRef<HTMLDivElement>(null); const serial = useRef(0); const latest = useRef(workflow); latest.current = workflow;
  const dirty = Boolean(workflow && (JSON.stringify(workflow) !== baseline || Object.values(invalid).some(Boolean) || saveOpen && saveName !== workflow.name)); const legacy = workflow ? isLegacy(workflow) : false;
  const versionLabel = typeof publishedVersion === 'object' ? publishedVersion.version : publishedVersion;
  const canPublish = Boolean(embedded && agentId && onPublish && workflow?.id && workflowId === workflow.id && linkedId === workflow.id);
  const catalog = registry || pendingRegistry;
  const valid = (key: string) => (ok: boolean) => setInvalid(prev => prev[key] === !ok ? prev : { ...prev, [key]: !ok });
  const hasInvalid = Object.values(invalid).some(Boolean);
  const refreshRegistry = useCallback(async () => { const epoch = accountEpoch(); try { const value = await timedLoad('registry',()=>workflowApi.registry()); if (!alive.current || epoch !== accountEpoch()) return; if (!Array.isArray(value.items) || !String(value.version).startsWith('2.')) throw new Error('后端未返回有效v2节点注册表'); setRegistry(value); setRegistryError(''); } catch (e) { if (alive.current && epoch === accountEpoch()) { setRegistry(null); setRegistryError(messageOf(e)); } } }, []);
  const refreshVariables = useCallback(async () => {
    const epoch = accountEpoch();
    try { const value = await workflowApi.variableCatalog(); if (!alive.current || epoch !== accountEpoch()) return;
      if (!Array.isArray(value.items)) throw new Error('只读变量目录格式异常');
      setReadonlyVariables(value.items.filter(item => item.readonly === true && item.source === 'server' && /^(system\.(run_id|tenant_id|workspace_id)|user\.(id|role|permissions))$/.test(item.ref)).map(item => ({ ref: item.ref, label: item.label, type: typeof item.schema?.type === 'string' ? item.schema.type : 'any', readonly: true, group: 'system' })));
      setVariableError('');
    } catch { if (alive.current && epoch === accountEpoch()) { setReadonlyVariables([]); setVariableError('系统与当前用户变量目录暂不可用，请刷新节点目录后重试。'); } }
  }, []);
  const refreshList = useCallback(async () => { const epoch = accountEpoch(), ticket = ++listRequest.current; const value = await workflowApi.list(); if (alive.current && epoch === accountEpoch() && ticket === listRequest.current) setItems(value.items); }, []);
  const refreshResources = useCallback(async () => { const epoch = accountEpoch(); const ticket = ++resourceTicket.current; setResources(emptyResources); setResourcesLoading(true);try{const value=await timedLoad('resources',()=>loadResources());if(alive.current&&epoch===accountEpoch()&&ticket===resourceTicket.current)setResources(value);}finally{if(alive.current&&epoch===accountEpoch()&&ticket===resourceTicket.current)setResourcesLoading(false);} }, []);
  useEffect(() => { let current = true; alive.current = true; const epoch = accountEpoch(); void refreshVariables(); void refreshRegistry().finally(()=>{if(current&&epoch===accountEpoch())setLoading(false);});void refreshList().catch(e=>{if(current&&epoch===accountEpoch())setError(messageOf(e));});void refreshResources().catch(e=>{if(current&&epoch===accountEpoch())setError(messageOf(e));}); return () => { current = false; alive.current = false; serial.current++; }; }, [refreshList, refreshRegistry, refreshResources, refreshVariables]);
  useEffect(() => { if (!dirty) return; const prevent = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; }; window.addEventListener('beforeunload', prevent); return () => window.removeEventListener('beforeunload', prevent); }, [dirty]);
  useEffect(() => { const change = () => setFullScreen(document.fullscreenElement === root.current); document.addEventListener('fullscreenchange', change); return () => document.removeEventListener('fullscreenchange', change); }, []);
  const open = (value: WorkflowDefinition | null, saved = true, fresh = false) => { dismissLeave(); closeSuccessDialogs(); actionPending.current = false; savePending.current = false; runControlPending.current = false; setInspectorOpen(false); serial.current++; setSession(s => s + 1); setWorkflow(value ? clone(value) : null); setTestInputs(inputDefaults(value?.input_schema || {}, {})); setMessage(initialWorkflowMessage(value)); setUploading(false); setBaseline(saved || fresh ? JSON.stringify(value) : ''); setSelected([]); setSelectedEdges([]); setMeasurements({}); setInvalid({}); setRun(null); setRunWorkflow(null); setTestNode(null); setTestScopePath([]); expectedTarget.current = null; setApprovalOpen(false); setSubflowId(''); setTraces([]); setTraceError(''); setApprovals([]); setEvents([]); setRunError(''); setResultNode(''); setResultOpen(false); setError(''); setNotice(''); setSaveOpen(false); setTab('node'); };
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => {
    if (!embedded) return;
    if (workflowId && linkedId === workflowId && latest.current?.id === workflowId) return;
    const token = ++serial.current; const requestId = ++linkedRequest.current; const epoch = accountEpoch(); setWorkflow(null); setBaseline(''); setAgent(null); setLinkedId(null); setRun(null); setEvents([]); setMessage(emptyMessage()); setSelected([]); setInvalid({});
    if (!agentId) { setLoading(false); setLinkedLoading(false); return; }
    setLinkedLoading(true);
    void (async () => { try { const owner=initialAgent&&linkedAttempt===0?initialAgent:await timedLoad('owner',()=>workflowApi.agent(agentId)); if (!alive.current || token !== serial.current || epoch !== accountEpoch()) return;
      if (workflowId && owner.main_workflow_id && workflowId !== owner.main_workflow_id) throw new Error('智能体关联已变化，请重新打开工作室');
      if (owner.agent_type === 'simple') { setAgent(owner); return; }
      setAgent(owner); const id = owner.main_workflow_id || workflowId; setLinkedId(id || null);
      if (id) { const loaded=await timedLoad('workflow',()=>workflowApi.get(id)); if (alive.current && token === serial.current && epoch === accountEpoch()) open(loaded); }
    } catch (e) { if (alive.current && token === serial.current && epoch === accountEpoch()) setError(messageOf(e)); }
    finally { if (alive.current && epoch === accountEpoch() && requestId === linkedRequest.current) setLinkedLoading(false); } })();
  }, [embedded, agentId, workflowId, linkedAttempt]);
  useEffect(()=>{
    if(embedded||!workflowId&&!createOnOpen||workflowId&&latest.current?.id===workflowId||createOnOpen&&latest.current)return;
    let live=true;const epoch=accountEpoch();setLinkedLoading(true);setError('');
    if(createOnOpen){if(initialMessageKind&&!registry){setLinkedLoading(!registryError);setError(registryError?`消息入口目录尚未就绪：${registryError}`:'');return;}try{open(initialMessageKind?newMessageWorkflow(registry!,initialMessageKind):newWorkflow(),false,true);}catch(error){setError(messageOf(error));}setLinkedLoading(false);return;}
    void timedLoad('workflow',()=>workflowApi.get(workflowId!)).then(value=>{if(live&&epoch===accountEpoch())open(value);}).catch(e=>{if(live&&epoch===accountEpoch())setError(messageOf(e));}).finally(()=>{if(live&&epoch===accountEpoch())setLinkedLoading(false);});
    return()=>{live=false;};
  },[embedded,workflowId,createOnOpen,initialMessageKind,linkedAttempt,registry,registryError]);
  useEffect(() => { const clear = () => { open(null); setItems([]); setResources(emptyResources); setRegistry(null); setReadonlyVariables([]); setVariableError(''); setAgent(null); setLinkedId(null); setMessage(emptyMessage()); setUploading(false); setBusy(''); setLoading(false); }; window.addEventListener('zhangpu:session-expired', clear); return () => window.removeEventListener('zhangpu:session-expired', clear); }, []);
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  const interaction = useRef({ busy, saveOpen }); interaction.current = { busy, saveOpen };
  const { guard: canLeave, dismiss: dismissLeave } = useLeaveConfirmation({ dirty: () => dirtyRef.current, blocked: () => Boolean(fileCreating.current || interaction.current.busy || interaction.current.saveOpen || actionPending.current || runControlPending.current), container: () => root.current || document.body, show: options => modalRef.current.confirm(options), onError: () => setError('暂时无法显示离开提示，请稍后重试') });
  const leaveTo = async (navigate: () => void | Promise<void>) => {
    if (navigationPending.current) return; navigationPending.current = true;
    try { if (await canLeave() && alive.current) await navigate(); } catch (error) { if (alive.current) setError(messageOf(error)); }
    finally { navigationPending.current = false; }
  };
  const navigatePage = async (page: string) => {
    if (!onNavigate) return;
    if (!parentGuardsNavigation) { await leaveTo(() => onNavigate(page)); return; }
    if (navigationPending.current) return; navigationPending.current = true;
    try { await onNavigate(page); } catch (error) { if (alive.current) setError(messageOf(error)); }
    finally { navigationPending.current = false; }
  };
  const applicationAssetId = embedded ? agentId : workflow?.id;
  const openApplicationApi = async () => {
    if (!onOpenApplicationApi || !applicationAssetId || busy || uploading || saveOpen || fileCreating.current || savePending.current || actionPending.current || runControlPending.current || navigationPending.current) return;
    navigationPending.current = true; const token = serial.current; const epoch = accountEpoch();
    const current = () => alive.current && token === serial.current && epoch === accountEpoch();
    try {
      if (!parentGuardsNavigation && !await canLeave()) return;
      if (current()) await onOpenApplicationApi({ kind: embedded ? 'agent' : 'workflow', id: applicationAssetId });
    } catch (error) { if (current()) setError(messageOf(error)); }
    finally { navigationPending.current = false; }
  };
  useEffect(() => { onLeaveGuardChange?.(canLeave); return () => onLeaveGuardChange?.(null); }, [canLeave, onLeaveGuardChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
  const readOnly=workflow?.access?.edit===false||agent?.access?.edit===false;const executeAllowed=workflow?.access?.execute!==false&&agent?.access?.execute!==false;
  const mutate = (update: (w: WorkflowDefinition) => WorkflowDefinition) => { if (!readOnly&&!busy && !actionPending.current && !runControlPending.current) setWorkflow(w => w ? update(w) : w); };
  const node = workflow?.nodes.find(n => n.id === selected[0]); const definition = catalog.items.find(d => d.type === node?.type);
  const variables = useMemo(() => workflow && node ? availableVariables(workflow, node.id, registry, readonlyVariables,customUserVariables) : [], [workflow, node, registry, readonlyVariables,customUserVariables]);
  const nodeStates = useMemo(() => eventNodeStates(events), [events]);
  const runScopePath = run?.execution_identity?.kind === 'node_test' ? run.execution_identity.scope_path || [] : [];
  const runViewWorkflow = useMemo(() => { const graph = runWorkflow || workflow; if (!graph) return null; try { return scopeWorkflow(graph, runScopePath); } catch { return null; } }, [workflow, runWorkflow, JSON.stringify(runScopePath)]);
  const progress = useMemo(() => runViewWorkflow ? topLevelProgress(run?.execution_identity?.kind === 'node_test' ? { ...runViewWorkflow, nodes: runViewWorkflow.nodes.filter(node => node.id === run.execution_identity.node_id) } : runViewWorkflow, nodeStates) : null, [runViewWorkflow, nodeStates, run?.execution_identity]);
  const flowNodes: FlowNode[] = useMemo(() => workflow?.nodes.map((n, i) => ({ id: n.id, type: 'studio', position: positionOf(n, i), selected: selected.includes(n.id), measured: measurements[n.id], data: { node: n, definition: catalog.items.find(d => d.type === n.type), ports: outputPorts(n, catalog.items.find(d => d.type === n.type)), runStatus: runScopePath.length ? undefined : nodeStates[n.id] } })) || [], [workflow, selected, catalog, nodeStates, measurements, JSON.stringify(runScopePath)]);
  const flowEdges: Edge[] = useMemo(() => workflow?.edges.map(e => ({ id: edgeKey(e), source: e.source, target: e.target, sourceHandle: e.source_port || 'default', targetHandle: e.target_port || 'default', selected: selectedEdges.includes(edgeKey(e)), label: e.source_port && e.source_port !== 'default' ? e.source_port : undefined, style: { stroke: 'var(--primary, #718ba9)', strokeWidth: 1.7 } })) || [], [workflow, selectedEdges]);
  const nodesChange = (changes: NodeChange<FlowNode>[]) => {
    // Controlled React Flow nodes must retain ResizeObserver measurements across edits.
    // Otherwise re-creating data objects drops measured dimensions and blanks the minimap.
    const dimensions = changes.filter(c => c.type === 'dimensions');
    if (dimensions.length) setMeasurements(prev => { let next = prev; dimensions.forEach(c => { if (c.type === 'dimensions' && c.dimensions && (prev[c.id]?.width !== c.dimensions.width || prev[c.id]?.height !== c.dimensions.height)) { if (next === prev) next = { ...prev }; next[c.id] = c.dimensions; } }); return next; });
    const selection = changes.filter(c => c.type === 'select');
    if (selection.some(change => change.type === 'select' && change.selected)) showInspector('node');
    if (selection.length) setSelected(prev => { const next = new Set(prev); selection.forEach(c => { if (c.type === 'select') c.selected ? next.add(c.id) : next.delete(c.id); }); return [...next]; });
    if (busy) return;
    const removed = new Set(changes.filter(c => c.type === 'remove').map(c => c.id));
    const positions = changes.filter(c => c.type === 'position');
    if (removed.size || positions.length) mutate(w => ({ ...w, nodes: w.nodes.filter(n => !removed.has(n.id)).map(n => { const change = positions.find(c => c.id === n.id); return change?.type === 'position' && change.position ? { ...n, ui: { ...n.ui, position: change.position } } : n; }), edges: w.edges.filter(e => !removed.has(e.source) && !removed.has(e.target)) }));
  };
  const edgesChange = (changes: EdgeChange[]) => {
    const removed = new Set(changes.filter(c => c.type === 'remove').map(c => c.id));
    const selection = changes.filter(c => c.type === 'select');
    if (selection.length) setSelectedEdges(prev => { const next = new Set(prev); selection.forEach(c => { if (c.type === 'select') c.selected ? next.add(c.id) : next.delete(c.id); }); return [...next]; });
    if (removed.size) mutate(w => ({ ...w, edges: w.edges.filter(e => !removed.has(edgeKey(e))) }));
  };
  const connect = (c: Connection) => { if (!workflow || busy) return; const edge = { source: c.source, target: c.target, source_port: c.sourceHandle || 'default', target_port: c.targetHandle || 'default' }; const why = connectionError(workflow, edge, registry); if (why) { setError(why); return; } setError(''); mutate(w => ({ ...w, edges: [...w.edges, edge] })); };
  const add = (def: NodeDefinition, position?: { x: number; y: number }) => { if (!workflow || busy || legacy || def.status === 'unavailable'&&def.configurable!==true) return; if (workflow.nodes.length >= (registry?.limits.max_nodes || 64)) { setError('已达到节点数量上限'); return; } const entryError = entryConflict(workflow, def); if (entryError) { setError(entryError); return; } const bounds = canvas.current?.getBoundingClientRect(); const next = newNode(def, position || screenToFlowPosition({ x: (bounds?.left || 0) + (canvas.current?.clientWidth || 600) / 2, y: (bounds?.top || 0) + (canvas.current?.clientHeight || 400) / 2 })); mutate(w => ({ ...w, nodes: [...w.nodes, next] })); setSelected([next.id]); showInspector('node'); };
  const removeSelected = () => { const ids = new Set(selected); mutate(w => ({ ...w, nodes: w.nodes.filter(n => !ids.has(n.id)), edges: w.edges.filter(e => !ids.has(e.source) && !ids.has(e.target) && !selectedEdges.includes(edgeKey(e))) })); setSelected([]); setSelectedEdges([]); };
  const duplicate = () => { if (!workflow || !node || busy) return; if (isEntryType(node.type, definition)) { setError('消息触发或兼容入口不能直接复制，请从节点库选择另一种触发类型'); return; } const copy = clone(node); copy.id = `${node.type}_${crypto.randomUUID().replace(/-/g, '').slice(0, 10)}`; const p = positionOf(node, 0); copy.ui = { ...copy.ui, position: { x: p.x + 45, y: p.y + 120 }, label: (copy.ui?.label || definition?.label || node.type) + ' 副本' }; mutate(w => ({ ...w, nodes: [...w.nodes, copy] })); setSelected([copy.id]); showInspector('node'); };
  const updateNode = (fields: ObjectValue) => mutate(w => ({ ...w, nodes: w.nodes.map(n => n.id === node?.id ? { ...n, ...fields } : n) }));
  const action = async (kind: 'save' | 'validate' | 'test' | 'publish', name?:string) => {
    if((kind==='save'||kind==='publish')&&readOnly){setError('当前资源为只读权限');return;}
    if(kind==='test'&&(!executeAllowed||readOnly)){setError('当前资源没有草稿执行授权');return;}
    if(nodeDraftDirty){setError('请先保存节点配置或取消编辑，再操作整个工作流');return;}
    if (!workflow || busy || uploading || actionPending.current || runControlPending.current || kind === 'test' && isPendingRun(run)) return false; if (hasInvalid) { setError('请先修正尚未完成的输入'); return false; }
    actionPending.current = true;
    const snapshot = clone(workflow);if(name!==undefined)snapshot.name=name.trim(); const token = serial.current; const epoch = accountEpoch();
    const current = () => alive.current && token === serial.current && epoch === accountEpoch();
    setBusy(kind); setError(''); setNotice('');
    if (kind === 'test') { expectedTarget.current = null; setRunTab('results'); runStateVersion.current++; setRunWorkflow(snapshot); setTraces([]); setTraceError(''); setRun(null); setApprovals([]); setEvents([]); setRunError(''); setResultOpen(true); setResultNode(''); }
    try {
      if (kind === 'publish' && !canPublish) throw new Error('请先保存并确认智能体已关联此画布，再发布版本');
      const envelope = kind === 'test' ? workflowMessageEnvelope(snapshot,message,embedded) : undefined;
      if(kind!=='save')await workflowApi.validate(snapshot);
      if (!current()) return;
      if (kind === 'save' || embedded && kind === 'publish') {
        const saved = await workflowApi.save(snapshot); if (!current()) return;
        setWorkflow(saved); setBaseline(JSON.stringify(saved));
        if (embedded) { if (!agentId || !saved.id) throw new Error('缺少智能体或画布标识'); if (linkedId !== saved.id) { await workflowApi.bind(agentId, saved.id); if (!current()) return; setLinkedId(saved.id); } }
        setNotice(embedded ? '画布已保存并关联此智能体' : '已保存到服务器');
        if (embedded && saved.id && saved.id !== workflowId) onWorkflowChange?.(saved.id);
        if(!embedded){if(saved.id)onWorkflowChange?.(saved.id);void refreshList().catch(e=>setError(messageOf(e)));}
      }
      if (kind === 'validate' && current()) setNotice('服务端校验通过');
      if (kind === 'publish') { if (!embedded || !agentId || !onPublish) throw new Error('请从智能体工作室发布配置与画布快照'); const published = await onPublish(); if (current()) setNotice(published && typeof published === 'object' && ('version' in published || 'id' in published) ? '工作流及关联配置快照已发布' : '发布操作已交由智能体工作室处理，请查看版本状态'); }
      if (kind === 'test') { if (!legacy && !registry) throw new Error('能力目录未连接，不能运行v2流程'); const unavailable = snapshot.nodes.filter(n => registry?.items.find(d => d.type === n.type)?.status === 'unavailable'); if (unavailable.length) throw new Error('包含未开放节点：' + unavailable.map(n => n.id).join('、')); if (!current()) return;
        const runInputs = inputDefaults(snapshot.input_schema || {}, testInputs);
        const created = legacy ? await workflowApi.run(snapshot, runInputs, query, prompt) : await workflowApi.debugRun({ workflow: snapshot, ...(embedded && agentId ? { agent_id: agentId } : {}), revision: `studio-${session}-${Date.now()}`, inputs: runInputs, query, prompt, ...(envelope ? { message: envelope } : {}) });
        if (current()) { setRun(created); setNotice('已提交本次草稿测试；使用点击时的画布快照'); }
      }
      return current();
    } catch (e) { if (current()) { setError(messageOf(e)); if (kind === 'test') setRunError(messageOf(e)); } return false; }
    finally { if (current()) { actionPending.current = false; setBusy(''); } }
  };
  const persistNodeConfiguration = async (next:WorkflowDefinition,change?:NodeSettingsChange):Promise<WorkflowDefinition> => {
    if(readOnly)throw Error('当前资源为只读权限');
    if(actionPending.current||busy)throw Error('当前正在保存或运行，请稍后重试');
    const token=serial.current,epoch=accountEpoch();const current=()=>alive.current&&token===serial.current&&epoch===accountEpoch();
    actionPending.current=true;setBusy('node-save');
    try { if(next.id&&change&&!change.expected_graph_sha256)throw Error('缺少保存版本，请重新打开工作流');
      const stored=JSON.parse(baseline||JSON.stringify(next)) as WorkflowDefinition;
      let storedNode=false;try{storedNode=!!change&&!!scopeWorkflow(stored,change.scope_path).nodes.find(item=>item.id===change.node_id);}catch{}
      const patchOnly=!!next.id&&!!change&&storedNode;
      const whole=nodeStructureRequest(next,stored,change);
      const saved=next.id&&change?await workflowApi.saveNodeSettings(next.id,{...nodeSettingsRequest(stored,change),...(!storedNode?{structure:whole}:{})}):change?await workflowApi.createNodeDraft(whole,change.node_id,change.scope_path):await workflowApi.save(whole);if(!current())throw Error('工作室或身份已变化，请重新读取保存状态');
      if(!saved.id)throw Error('保存未返回工作流标识，请重新读取确认');
      setWorkflow(previous=>previous&&patchOnly&&change?mergeSavedNode(previous,saved,change):withWorkflowLayout(saved,previous||next));
      if(embedded&&agentId&&linkedId!==saved.id){await workflowApi.bind(agentId,saved.id);if(!current())throw Error('工作室已变化，请重新打开');setLinkedId(saved.id);}
      setBaseline(JSON.stringify(saved));setNotice('节点配置已保存到服务器');onWorkflowChange?.(saved.id);return saved;
    } finally {if(current()){actionPending.current=false;setBusy('');}}
  };
  const saveWorkflow = async () => {
    if (savePending.current || actionPending.current || busy || !workflow) return;
    if (!saveName.trim()) { setError('请输入工作流名称'); return; }
    savePending.current = true; const token = serial.current; const epoch = accountEpoch();
    try {
      if (await action('save', saveName) && alive.current && token === serial.current && epoch === accountEpoch()) {
        setSaveOpen(false); setNotice('');
        let dialog: { destroy: () => void } | undefined;
        dialog = modalRef.current.success({ title: '工作流已保存', content: '可以继续编辑，或测试当前工作流。', centered: true, okText: '完成', getContainer: () => root.current || document.body, afterClose: () => { if (dialog) successDialogs.current.delete(dialog); } });
        if (dialog) successDialogs.current.add(dialog);
      }
    } catch { if (alive.current && token === serial.current && epoch === accountEpoch()) setNotice('工作流已保存，暂时无法显示确认提示'); }
    finally { if (alive.current && token === serial.current && epoch === accountEpoch()) savePending.current = false; }
  };
  const testNodeReason = nodeDraftDirty ? '请先保存节点配置或取消编辑，再测试当前节点' : !node ? '请先选择节点' : !registry ? '节点能力目录未连接' : definition?.status === 'unavailable' ? readinessReason(definition) : legacy ? '请先转换为 v2 副本再测试节点' : Object.entries(invalid).some(([key, value]) => value && key.endsWith(`-${node.id}`)) ? '请先修正当前节点配置' : (definition?.config_schema.required || []).some(key => node.config?.[key] === undefined || node.config?.[key] === '') ? '请先填写当前节点的必填配置' : (definition?.input_schema.required || []).some(key => node.inputs?.[key] === undefined || node.inputs?.[key]?.$ref === '') ? '请先填写当前节点的必填输入或变量引用' : '';
  const runSingleNode = async (inputs: ObjectValue, pools: TestVariables, checked: CheckedTestTarget) => {
    if (!workflow || !testNode || nodeDraftDirty || busy || actionPending.current || runControlPending.current || isPendingRun(run)) return;
    if (!checked || JSON.stringify(checked.workflow) !== JSON.stringify(workflow)) throw new Error('草稿已变化，请重新检查测试目标');
    checkDescription(checked.description, workflow, testNode.id, testScopePath);
    actionPending.current = true; runStateVersion.current++;
    const snapshot = clone(checked.workflow); const nodeId = testNode.id; const path = [...testScopePath]; const token = serial.current; const epoch = accountEpoch();
    const current = () => alive.current && token === serial.current && epoch === accountEpoch();
    expectedTarget.current = null; setBusy('node-test'); setRunTab('results'); setRun(null); setRunWorkflow(snapshot); setTraces([]); setTraceError(''); setEvents([]); setApprovals([]); setRunError(''); setResultOpen(true); setResultNode(nodeId);
    try {
      const envelope=workflowMessageEnvelope(snapshot,message,embedded);
      const created = await workflowApi.debugRun({ workflow: snapshot, ...(envelope?{message:envelope}:{}), ...(embedded && agentId ? { agent_id: agentId } : {}), revision: `studio-node-${session}-${Date.now()}`, inputs, query, prompt, node_test: { node_id: nodeId, scope_path: path, variables: pools } });
      if (!current()) return;
      if (!matchesTestIdentity(created.execution_identity, checked.description)) throw new Error('运行返回的测试目标不一致，已忽略此结果，请查看运行记录');
      expectedTarget.current = checked.description;
      setRun(created); setTestNode(null); setSubflowId(''); setNotice('已提交当前作用域节点测试，外围、上游和兄弟节点不会执行');
    } catch (error) { if (current()) { setRunError(messageOf(error)); throw new Error(messageOf(error)); } }
    finally { if (current()) { actionPending.current = false; setBusy(''); } }
  };
  const attachExisting = async (copy: boolean) => {
    if (!agentId || !bindSelection || busy) return; const epoch = accountEpoch(); const token = serial.current; setBusy('bind'); setError('');
    try { let chosen = await workflowApi.get(bindSelection); if (!alive.current || epoch !== accountEpoch() || token !== serial.current) return;
      if (copy) { const source = chosen.id; chosen = clone(chosen); delete chosen.id; chosen.name += ' · 智能体副本'; chosen.ui = { ...chosen.ui, copied_from: source }; chosen = await workflowApi.save(chosen); }
      if (!chosen.id || !alive.current || epoch !== accountEpoch() || token !== serial.current) return;
      await workflowApi.bind(agentId, chosen.id); if (!alive.current || epoch !== accountEpoch() || token !== serial.current) return;
      setLinkedId(chosen.id); open(chosen); onWorkflowChange?.(chosen.id);
    } catch (e) { if (alive.current && epoch === accountEpoch() && token === serial.current) setError(messageOf(e)); }
    finally { if (alive.current && epoch === accountEpoch()) setBusy(''); }
  };
  const controlRun = async (kind: 'cancel' | 'resume') => {
    if (!run || busy || runControlPending.current || !canRunAction(run, kind)) return;
    runControlPending.current = true;
    const id = run.id; const token = serial.current; const epoch = accountEpoch(); runStateVersion.current++;
    const current = () => alive.current && token === serial.current && epoch === accountEpoch();
    setBusy(kind); setRunError('');
    try { const value = await workflowApi[kind](id); if (value.id !== id) throw new Error('运行标识不一致，请刷新确认；没有创建替代运行'); if (current()) { if (expectedTarget.current && !matchesTestIdentity(value.execution_identity, expectedTarget.current)) throw new Error('恢复或取消返回的测试作用域不一致，请重新读取'); setRun(value); } }
    catch (error) { if (current()) { setRunError(messageOf(error)); setTraces([]); setEvents([]); setApprovals([]); setRun(previous => previous ? { id: previous.id, status: previous.status, engine: previous.engine, execution_identity: previous.execution_identity, view_unavailable: true } : null); } }
    finally { if (current()) { runControlPending.current = false; setBusy(''); setRunRefresh(value => value + 1); } }
  };
  const decideApproval = async (approvalId: string, decision: ApprovalDecision, note: string) => {
    const pending = approvals.filter(item => item.status === 'pending');
    if (!run || busy || runControlPending.current || run.capabilities?.can_approve !== true || run.capabilities.is_terminal || pending.length !== 1 || pending[0].id !== approvalId) return;
    runControlPending.current = true; runStateVersion.current++;
    const id = run.id; const token = serial.current; const epoch = accountEpoch();
    const current = () => alive.current && token === serial.current && epoch === accountEpoch();
    setBusy('decision'); setRunError('');
    try {
      const result = await workflowApi.approvalDecision(id, approvalId, decision, note);
      if (result.id !== approvalId || result.run_id !== id) throw new Error('审批记录标识不一致，请重新读取状态');
      if (current()) { setApprovals(items => items.map(item => item.id === result.id ? result : item)); setNotice('审批结果已记录'); }
    } catch (error) { if (current()) { setRunError(messageOf(error)); setTraces([]); setEvents([]); setApprovals([]); setRun(previous => previous ? { id: previous.id, status: previous.status, engine: previous.engine, execution_identity: previous.execution_identity, view_unavailable: true } : null); } }
    finally { if (current()) { runControlPending.current = false; setBusy(''); setRunRefresh(value => value + 1); } }
  };
  // Separate run polling from editor mutations. Refresh terminal POST responses too; no stale results survive a new run/session.
  const runId = run?.id;
  useEffect(() => {
    if (!runId) return;
    let live = true; let timer: ReturnType<typeof setTimeout>; let cursor = 0; let history: StudioEvent[] = [];
    const token = serial.current; const epoch = accountEpoch();
    const current = (revision: number) => live && token === serial.current && epoch === accountEpoch() && revision === runStateVersion.current;
    const checkState = (state: import('@/lib/workflow/types').RunExecutionState) => {
      if (state.run_id !== runId || !Number.isSafeInteger(state.last_sequence) || state.last_sequence < 0 || !['is_terminal', 'is_active', 'can_cancel', 'can_resume', 'can_approve', 'can_reconcile'].every(key => typeof state.capabilities?.[key as keyof typeof state.capabilities] === 'boolean')) throw new Error('运行状态或操作权限不完整，请重新读取');
      return state;
    };
    const poll = async () => {
      const revision = runStateVersion.current;
      try {
        const detail = await workflowApi.runDetail(runId); if (!current(revision)) return;
        if (detail.id !== runId) throw new Error('运行标识不一致，已忽略此响应');
        if (expectedTarget.current && !matchesTestIdentity(detail.execution_identity, expectedTarget.current)) throw new Error('运行测试作用域不一致，已隐藏结果，请重新读取');
        let state = detail.engine?.name === 'langgraph' ? checkState(await workflowApi.executionState(runId)) : null;
        if (state && !consistentProjection(detail, state)) throw new Error('运行操作权限正在更新，请刷新后重试');
        if (!current(revision)) return;
        const log = await workflowApi.events(runId, state ? cursor : undefined); if (!current(revision)) return;
        if (state) {
          let page = appendEventPage(history, log, runId, cursor);
          if (page.gap || page.after < state.last_sequence) {
            state = checkState(await workflowApi.executionState(runId)); if (!current(revision)) return;
            if (!consistentProjection(detail, state)) throw new Error('运行操作权限正在更新，请刷新后重试');
            const full = await workflowApi.events(runId, 0); if (!current(revision)) return;
            page = appendEventPage([], full, runId, 0);
            if (page.gap || page.after < state.last_sequence) throw new Error('运行事件记录不连续，请刷新或稍后重试');
          }
          history = page.items; cursor = page.after;
        } else history = orderedRunEvents(log.items, runId);
        let nodeTraces: NodeTrace[] = [];
        if (detail.execution_identity?.kind === 'draft' || detail.execution_identity?.kind === 'node_test') {
          let after = 0; let more = true; let pages = 0;
          const byNode = new Map<string, NodeTrace>();
          while (more) {
            const page = await workflowApi.trace(runId, after); if (!current(revision)) return;
            if (page.run_id !== runId || !Array.isArray(page.items) || !Number.isSafeInteger(page.last_sequence) || page.last_sequence < after || page.has_more && page.last_sequence <= after) throw new Error('本次节点详情分页不完整，请刷新后重试');
            page.items.forEach(item => { if (typeof item.node_id === 'string' && typeof item.node_type === 'string' && typeof item.status === 'string') byNode.set(item.node_id, item); });
            after = page.last_sequence; more = page.has_more;
            if (++pages > 256) throw new Error('本次节点详情超过显示上限，请在运行记录中查看');
          }
          nodeTraces = [...byNode.values()];
        }
        const view = state ? { ...detail, status: state.status, engine: state.engine || detail.engine, capabilities: state.capabilities } : detail;
        if (!current(revision)) return;
        setRun(view); setTraces(nodeTraces); setTraceError(''); setEvents(history); setApprovals((state?.approvals || []).filter(item => item.run_id === runId && typeof item.id === 'string' && typeof item.title === 'string' && typeof item.status === 'string')); setRunError('');
        const delay = runPollDelay(view); if (delay) timer = setTimeout(poll, delay);
      } catch (error) {
        if (current(revision)) { setRunError('运行状态读取失败：' + messageOf(error)); setTraces([]); setEvents([]); setApprovals([]); setRun(previous => previous ? { id: previous.id, status: previous.status, engine: previous.engine, execution_identity: previous.execution_identity, view_unavailable: true } : null); timer = setTimeout(poll, 8000); }
      }
    };
    void poll(); return () => { live = false; clearTimeout(timer); };
  }, [runId, runRefresh]);
  if (embedded && agent?.agent_type === 'simple') return <div className={styles.linkedEmpty}><p>简单智能体使用配置表单，无需编排画布。</p>{onBack && <button onClick={onBack}>返回智能体</button>}</div>;
  return <div ref={root} className={`${styles.studio} ${workflow ? styles.editing : ''} ${embedded ? styles.embedded : ''} ${fullPage ? styles.fullPage : ''}`}>
    <ApprovalInbox open={approvalOpen} container={() => root.current || document.body} onClose={() => setApprovalOpen(false)} onDecided={id => { if (id === run?.id) { runStateVersion.current++; setRunRefresh(value => value + 1); } }} />
    {fileClient&&<WorkflowFileTools mode={fileMode} client={fileClient} onCreatingChange={value=>{fileCreating.current=value;}} workflow={workflow} savedWorkflow={baseline?JSON.parse(baseline):null} beforeCreate={canLeave} onClose={()=>setFileMode(null)} onCreated={async value=>{if(!value.id)throw Error('服务端尚未返回新工作流标识，请重试确认。');setFileMode(null);listRequest.current++;setItems(items=>[value,...items.filter(item=>item.id!==value.id)]);if(onWorkflowImported){if(!embedded)open(value);await onWorkflowImported(value);}else if(!embedded){open(value);onWorkflowChange?.(value.id);await refreshList();}else throw Error('无法打开独立草稿，请返回AI工作室查看。');}}/>}
    {!workflow ? !embedded&&(workflowId||createOnOpen)?<LinkedWorkflow unavailable={!linkedLoading} loading={linkedLoading} busy={Boolean(busy)} error={error} agentName="工作流" onBack={onBack} onRetry={()=>{setLinkedAttempt(n=>n+1);if(initialMessageKind){setRegistryError('');void refreshRegistry();}}} items={[]} value="" onChange={()=>{}} onCreate={()=>{}} onBind={()=>{}} onCopy={()=>{}}/>:embedded ? <LinkedWorkflow unavailable={!loading && !linkedLoading && (!agent || Boolean(linkedId))} onRetry={() => setLinkedAttempt(value => value + 1)} onBack={onBack ? () => leaveTo(onBack) : undefined} agentName={agentName || agent?.name || "智能体编排"} loading={loading || linkedLoading} busy={Boolean(busy)} error={error} items={items} value={bindSelection} onChange={setBindSelection} onCreate={() => { if (!registry) { setError("能力目录尚未连接"); return; } let next; try { next = newAgentWorkflow(registry); } catch (e) { setError(messageOf(e)); return; } next.name = `${agentName || agent?.name || "智能体"} · 工作流`; open(next, false, true); }} onBind={() => attachExisting(false)} onCopy={() => attachExisting(true)} /> : <><header className={styles.listHeader}><div><span className={styles.eyebrow}>可视化编排</span><h1>工作流工作室</h1><p>把消息、知识与处理步骤连接成可追溯的流程。</p></div><button onClick={() => setApprovalOpen(true)}>我的审批</button>{fileClient&&<button onClick={()=>setFileMode('import')} disabled={loading||Boolean(busy)}>导入工作流</button>}<button className={styles.primary} onClick={() => open(newWorkflow(), false, true)} disabled={loading || Boolean(busy)}>＋ 新建工作流</button><MessageWorkflowCreate registry={registry} disabled={loading || Boolean(busy)} onCreate={value=>open(value,false,true)}/></header><div className={styles.listToolbar}><input aria-label="搜索工作流" placeholder="搜索工作流名称" value={listSearch} onChange={e => setListSearch(e.target.value)} /><button onClick={() => { setLoading(true); refreshList().catch(e => setError(messageOf(e))).finally(() => setLoading(false)); }}>刷新列表</button><span>{items.length} 个流程</span></div>{error && <div role="alert" className={styles.alert}>{error}</div>}{registryError && <div className={styles.warning}>实时节点注册表尚未就绪：{registryError} <button onClick={refreshRegistry}>重试注册表</button></div>}<div className={styles.workflowGrid}>{items.filter(w => w.name.toLowerCase().includes(listSearch.toLowerCase())).map(w => <button key={w.id} className={styles.workflowCard} onClick={async () => { if (!w.id) return; setBusy('open'); try { open(await workflowApi.get(w.id)); } catch (e) { setError(messageOf(e)); } finally { setBusy(''); } }} disabled={w.access?.read===false||Boolean(busy)} title={w.access?.read===false?'没有读取权限':undefined}><span className={styles.cardIcon}>⑂</span><strong>{w.name}</strong><span>{isLegacy(w) ? 'v1 兼容流程' : 'v2 可视化流程'}</span><small>{w.metadata_only?'仅目录可见':`${w.nodes?.length||0} 节点 · ${w.edges?.length||0} 连线`}</small><span className={styles.cardLink}>打开画布 ↗</span></button>)}{!loading && !items.length && <div className={styles.empty}>还没有工作流。新建后添加节点、连接变量，再进行真实测试。</div>}{loading && <div className={styles.empty}>正在读取服务器流程…</div>}</div></>
      : <><header className={styles.toolbar}>{onBack && <button aria-label={`返回${menuLabels.agents}`} disabled={Boolean(busy)} onClick={() => leaveTo(onBack)}>← 返回</button>}{!embedded&&!(onBack&&(createOnOpen||workflowId)) && <button title="返回流程列表" aria-label="返回流程列表" disabled={Boolean(busy)} onClick={() => leaveTo(() => open(null))}>←</button>}<div className={styles.workflowTitle}>{embedded && <strong className={styles.agentTitle}>工作流</strong>}<input aria-label="工作流名称" value={workflow.name} disabled={readOnly||Boolean(busy)} onChange={e => mutate(w => ({ ...w, name: e.target.value }))} /><small aria-live="polite">{busy ? '正在处理…' : !workflow.id ? '未保存草稿' :readOnly?'只读权限': dirty ? '有未保存更改' : '已保存'}{legacy ? ' · 兼容流程' : ''}{embedded && ` · ${linkedId ? '已关联工作流' : '尚未保存'} · ${versionLabel ? `已发布 v${versionLabel}` : '草稿'}`}</small></div><div className={styles.toolbarActions}>{onOpenApplicationApi && <button disabled={!applicationAssetId || Boolean(busy) || uploading || saveOpen} title={!applicationAssetId ? embedded ? "请先保存智能体，再打开 API 接入" : "请先保存工作流，再打开 API 接入" : "打开 API 接入，管理发布版本与调用凭据"} onClick={openApplicationApi}>API调用</button>}<button onClick={() => setApprovalOpen(true)}>我的审批</button>{fileClient&&<><button disabled={Boolean(busy)} onClick={()=>setFileMode('import')}>导入</button><button disabled={Boolean(busy)||hasInvalid} onClick={()=>setFileMode('export')}>导出</button></>}<button onClick={async () => { try { if (document.fullscreenElement && typeof document.exitFullscreen === 'function') await document.exitFullscreen(); else if (typeof root.current?.requestFullscreen === 'function') await root.current.requestFullscreen(); else setError('当前浏览器不支持全屏，请使用工作流独立页面'); } catch (error) { setError(messageOf(error)); } }} title="全屏工作室">{fullScreen ? '退出全屏' : '全屏'}</button><button disabled={Boolean(busy) || hasInvalid || nodeDraftDirty} onClick={() => action('validate')}>校验</button><button disabled={readOnly||Boolean(busy) || hasInvalid || nodeDraftDirty} onClick={() => {setSaveName(workflow.name);setSaveOpen(true);setError('');}}>保存</button><button className={styles.primary} disabled={readOnly||!executeAllowed||Boolean(busy) || uploading || hasInvalid || nodeDraftDirty || isPendingRun(run) || (!legacy && !registry)} onClick={() => { setResultOpen(true); setRunTab('inputs'); }}>{"▷ 试运行"}</button><button disabled={readOnly||(embedded?!canPublish:!onOpenApplicationApi||!applicationAssetId)||Boolean(busy) || hasInvalid || nodeDraftDirty} title={embedded ? "发布当前配置与工作流快照" : "配置已发布 API 版本"} onClick={() => embedded?action("publish"):openApplicationApi()}>{embedded ? "发布工作流" : "发布 API"}</button></div></header>
      <Modal title="保存工作流" open={saveOpen} centered okText="确认保存" cancelText="取消" confirmLoading={busy === 'save'} getContainer={() => root.current || document.body} destroyOnHidden maskClosable={!busy} keyboard={!busy} closable={!busy} cancelButtonProps={{ disabled: Boolean(busy) }} okButtonProps={{ disabled: Boolean(busy) || !saveName.trim() }} onCancel={() => { if (!busy && !savePending.current) setSaveOpen(false); }} onOk={saveWorkflow}><label>工作流名称<Input aria-label="保存工作流名称" value={saveName} maxLength={100} onChange={e=>setSaveName(e.target.value)}/></label><p className={styles.hint}>{embedded?'保存当前工作流的节点、连线与配置。':'保存当前节点、连线与配置。'}</p>{error&&<p role="alert" className={styles.errorText}>{error}</p>}</Modal>
      {(error || notice || registryError || legacy || resourcesLoading) && <div className={styles.banners}>{resourcesLoading&&<div className={styles.loadingResources} role="status">画布已打开，资源目录正在加载；可继续编辑节点。</div>}{error && <div className={styles.alert} role="alert">{error}</div>}{notice && <div className={styles.notice} role="status">{notice}</div>}{registryError && <div className={styles.warning}>注册表未连接，v2执行不可用：{registryError} <button onClick={refreshRegistry}>重试</button></div>}{legacy && <div className={styles.warning}>旧版流程保留原始配置和执行方式。<button disabled={!registry || Boolean(busy)} onClick={() => leaveTo(() => { if (registry) open(convertLegacy(workflow, registry), false); })}>转换为v2副本</button></div>}</div>}
      <CanvasWorkspace inspectorOpen={inspectorOpen} onInspectorOpenChange={open=>{setInspectorOpen(open);if(!open)setSelected([]);}} library={<NodeLibrary catalog={catalog} connected={Boolean(registry)} count={workflow.nodes.length} legacy={legacy} busy={Boolean(busy)} search={search} onSearch={setSearch} onRefresh={() => { refreshRegistry(); refreshVariables(); refreshResources().catch(e => setError(messageOf(e))); }} onAdd={add} />} inspector={<aside className={styles.inspector}><div className={styles.tabs}>{(['node', 'workflow'] as const).map(t => <button key={t} className={tab === t ? styles.activeTab : ''} onClick={() => showInspector(t)}>{({ node: '节点属性', workflow: '流程与变量', test: '测试输入' })[t]}</button>)}</div><fieldset disabled={Boolean(busy)} className={styles.inspectorBody} tabIndex={0} aria-label="属性与测试内容">
          {tab === 'node' && (node ? <div key={node.id}><h2>{node.ui?.label || definition?.label || "节点属性"}</h2><details className={styles.details}><summary>节点技术标识</summary><code>{node.id} · {node.type}</code></details><details className={styles.details}><summary>节点测试与能力</summary><div className={styles.readiness} data-state={definition?.status}>{node.type==='llm'&&definition?.status==='needs_configuration'?<>模型以可用目录为准，配置仍需校验与实际运行。{canManageModels&&onNavigate&&<small><button type="button" className={styles.linkButton} onClick={()=>navigatePage('models')}>模型管理 ↗</button></small>}</>:<>{stateLabels[definition?.status || 'unavailable']}{definition?.reason && <small>{readinessReason(definition)}</small>}</>}</div><button disabled={readOnly||!executeAllowed||Boolean(testNodeReason) || Boolean(busy) || isPendingRun(run)} title={testNodeReason || '仅测试当前节点'} onClick={() => { setTestScopePath([]); setTestNode(clone(node)); }}>▷ 测试此节点</button>{testNodeReason && <p className={styles.hint}>{testNodeReason}</p>}</details><fieldset disabled={readOnly} style={{border:0,padding:0}}><NodeDraftPanel currentMessage={message} saveScopeNote={!workflow.id?"首次保存会创建工作流，包含当前画布结构与本节点配置。":!JSON.parse(baseline||'{"nodes":[]}').nodes?.some((item:WorkflowNode)=>item.id===node.id)?"这是新增节点：保存会同时提交当前已添加的节点与连线，不包含其他面板未提交草稿。":"保存本节点与当前画布连线，其它节点的配置保持不变。"} key={`${session}:${node.id}`} node={node} workflow={workflow} registry={catalog} resources={resources} variables={variables} onCommit={async(next,change)=>{await persistNodeConfiguration(next,change);}} onCancel={()=>{setSelected([]);setInspectorOpen(false);}} onDirtyChange={setNodeDraftDirty} knowledgeScope={agent?.knowledge_resources} dataScope={agent?.data_resources} canManageModels={canManageModels} onManageModels={onNavigate?()=>navigatePage('models'):undefined}/></fieldset><details className={styles.details}><summary>可引用变量 · {variables.length}</summary>{variableError && <p role="status" className={styles.hint}>{variableError}</p>}<p className={styles.hint}>业务输入、全局变量、已连接的上游输出及只读系统/当前用户变量。账号与角色不能修改，授权以后台为准。</p>{variables.map(v => <button className={styles.variable} key={v.ref} title="复制显式引用" onClick={() => { navigator.clipboard?.writeText(JSON.stringify({ $ref: v.ref })).then(() => setNotice('变量引用已复制')).catch(e => setError(messageOf(e))); }}><span>{v.label}</span><small>{typeLabel(v.type)} ⧉</small></button>)}</details><details className={styles.details}><summary>输出类型与端口</summary><pre>{JSON.stringify(definition?.output_schema || {}, null, 2)}</pre><small>输出端口：{outputPorts(node, definition).join(' / ') || '终端节点'}</small></details>{['iteration', 'loop'].includes(node.type) && <><button disabled={readOnly||!registry || nodeDraftDirty} onClick={() => setSubflowId(node.id)}>编辑可视子流程 ↗</button><p className={styles.hint}>子流程可拖节点、连线和填写表单。迭代可引用当前项/序号，循环可引用当前状态/序号；外层连线仍禁止环路。</p></>}</div> : <div className={styles.empty}>选择一个节点查看配置与变量。</div>)}
          {tab === 'workflow' && <><h2>流程设置</h2><p className={styles.hint}>输入、变量和执行限制随流程保存在服务器。</p><h3>业务输入字段</h3><SchemaDefinition onValidity={valid('inputDefinition')} value={workflow.input_schema || { type: 'object' }} onChange={input_schema => mutate(w => ({ ...w, input_schema }))} /><UserVariables onChange={setCustomUserVariables}/><h3>流程全局变量</h3><SchemaDefinition onValidity={valid('variableDefinition')} label="全局变量" value={{ type: 'object', properties: workflow.variables || {} }} onChange={schema => {try{const next=redefineVariables(workflow,schema.properties||{});mutate(()=>next);}catch(e){setError(messageOf(e));}}} /><label className={styles.field}><span>并行执行上限</span><input type="number" min={1} max={4} value={workflow.limits?.max_concurrency ?? 4} onChange={e => { if (e.target.value) mutate(w => ({ ...w, limits: { ...w.limits, max_concurrency: Math.max(1, Math.min(4, Number(e.target.value))) } })); }} /></label><details className={styles.details}><summary>高级输入与变量结构</summary><JsonEditor label="流程输入结构" value={workflow.input_schema || { type: 'object' }} onChange={input_schema => mutate(w => ({ ...w, input_schema }))} onValidity={valid('schema')} /><JsonEditor label="流程变量声明" value={workflow.variables || {}} onChange={variables => mutate(w => ({ ...w, variables }))} onValidity={valid('variables')} /><JsonEditor label="执行限制" value={workflow.limits || {}} onChange={limits => mutate(w => ({ ...w, limits }))} onValidity={valid('limits')} /></details><details className={styles.details}><summary>完整流程 JSON（高级查看）</summary><pre>{JSON.stringify(workflow, null, 2)}</pre>{fileClient&&<button onClick={()=>setFileMode('export')}>选择格式并检查导出</button>}</details></>}
        </fieldset></aside>}>
<main className={styles.canvasColumn}><div ref={canvas} className={styles.canvas} onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; }} onDrop={e => { e.preventDefault(); const def = catalog.items.find(d => d.type === e.dataTransfer.getData('application/workflow-node')); if (def) add(def, screenToFlowPosition({ x: e.clientX, y: e.clientY })); }}>
          <div className={styles.canvasTools}><button disabled={!node || Boolean(busy)} onClick={duplicate}>复制节点</button><button disabled={(!selected.length && !selectedEdges.length) || Boolean(busy)} onClick={removeSelected}>删除选中</button><button onClick={() => fitView(canvasFitOptions(canvas.current?.clientWidth || 0, canvas.current?.clientHeight || 0))}>适应视图</button><button onClick={() => showInspector("workflow")}>流程设置</button><button onClick={() => { setResultOpen(true); setRunTab('inputs'); }}>试运行输入</button><span>滚轮缩放 · Shift框选 · Delete删除</span></div>
          <ReactFlow<FlowNode> key={session} nodes={flowNodes} edges={flowEdges} nodeTypes={nodeTypes} onNodesChange={nodesChange} onEdgesChange={edgesChange} onConnect={connect} nodesDraggable={!readOnly&&!busy} nodesConnectable={!readOnly&&!busy} deleteKeyCode={busy||readOnly ? null : ['Backspace', 'Delete']} onNodeClick={(_, n) => { setSelected([n.id]); showInspector('node'); if (run && !runScopePath.length && runWorkflow?.nodes.some(node => node.id === n.id)) { setResultNode(n.id); setResultOpen(true); } }} onPaneClick={() => { setSelected([]); if (tab === 'node') setInspectorOpen(false); }} minZoom={.15} maxZoom={2} defaultViewport={workflow.ui?.viewport || { x: 0, y: 0, zoom: 1 }} fitView fitViewOptions={{ padding: .2, maxZoom: 1 }} onMoveEnd={(event, viewport) => { if (event) mutate(w => ({ ...w, ui: { ...w.ui, viewport } })); }} attributionPosition="bottom-left"><CanvasViewport container={canvas} session={session} /><Background gap={24} color="color-mix(in srgb, var(--text-muted, #717d8e) 22%, transparent)" /><MiniMap pannable zoomable style={{ width: 130, height: 90 }} nodeColor="var(--node-control, #8895a8)" /><Controls showInteractive={false} /></ReactFlow>
        </div>
        <div className={styles.resultToggle}><button onClick={() => setResultOpen(value => !value)} aria-expanded={resultOpen}>{resultOpen ? '▾' : '▸'} 试运行 {run ? `· ${stateLabels[run.status] || '状态待同步'}` : '· 填写输入后开始'}</button>{run && progress && <small aria-label="节点执行进度">成功 {progress.succeeded}/{progress.total} · 跳过 {progress.skipped} · 失败 {progress.failed}</small>}{canRunAction(run, 'cancel') && <button disabled={Boolean(busy)} onClick={() => controlRun('cancel')}>取消运行</button>}{canRunAction(run, 'resume') && <button disabled={Boolean(busy)} onClick={() => controlRun('resume')}>继续运行</button>}</div>
        {resultOpen && <RunPanel><div className={styles.runTabs}><button aria-pressed={runTab === 'inputs'} onClick={() => setRunTab('inputs')}>试运行输入</button><button aria-pressed={runTab === 'results'} onClick={() => setRunTab('results')}>本次结果与节点进度</button>{run && <button disabled={Boolean(busy)} onClick={() => { runStateVersion.current++; setRunRefresh(value => value + 1); }}>刷新</button>}{onNavigate && <button onClick={() => navigatePage('runs')}>运行记录 ↗</button>}</div>
          {runTab === 'inputs' ? <div className={styles.runInputForm}><fieldset disabled={Boolean(busy)} className={styles.testFieldset}>
            {!legacy && workflowTestInputLayout(workflow,message,embedded).useMessage && <MessageComposer key={`${session}:${identityKey || 'session'}`} optional={!embedded&&workflow.nodes.some(node=>node.type==='start')} value={message} onChange={setMessage} disabled={Boolean(busy)} identityKey={identityKey} onUploadingChange={setUploading} />}
            <SchemaFields label="测试业务输入" schema={workflowTestInputLayout(workflow,message,embedded).schema} value={testInputs} onChange={setTestInputs} variables={[]} resources={resources} onValidity={valid('test-business')} />
            {!embedded && <label className={styles.field}><span>任务描述</span><textarea rows={2} value={prompt} onChange={e => setPrompt(e.target.value)} /></label>}
            <details className={styles.details}><summary>高级运行参数</summary><JsonEditor label="流程输入值" value={testInputs} onChange={setTestInputs} onValidity={valid('test-inputs')} /><JsonEditor label="财务查询条件" value={query} onChange={setQuery} onValidity={valid('test-query')} /></details>
            <button className={styles.primary} disabled={readOnly||!executeAllowed||Boolean(busy) || hasInvalid || uploading || isPendingRun(run) || (!legacy && !registry)} onClick={() => action('test')}>▷ 开始试运行</button><p className={styles.hint}>运行点击时的当前草稿。每次结果对应独立运行，不会自动保存工作流。</p>
          </fieldset></div> : <>
            {run && <div className={styles.executionIdentity}><span>{run.execution_identity?.kind === 'node_test' ? '本次节点测试' : run.execution_identity?.kind === 'draft' ? '本次草稿' : '本次运行'}</span><code>{run.id}</code>{runScopePath.length > 0 && <span>子流程 {runScopePath.join(' / ')}</span>}{run.execution_identity?.graph_sha256 && <small>快照 {run.execution_identity.graph_sha256.slice(0, 12)}</small>}{run.execution_identity?.revision && <small>版本标记 {run.execution_identity.revision}</small>}</div>}
            {run && <RunStatus run={run} />}{approvals.filter(item => item.status === 'pending').length > 1 && <p className={styles.hint}>本次有多个并行审批事项。<button onClick={() => setApprovalOpen(true)}>打开我的审批逐项处理</button></p>}{run && <RunApprovals items={approvals} decidableIds={run.capabilities?.can_approve && !run.capabilities.is_terminal && approvals.filter(item => item.status === 'pending').length === 1 ? approvals.filter(item => item.status === 'pending').map(item => item.id) : []} disabled={Boolean(busy)} onDecision={decideApproval} />}
            {runError && <div role="alert" className={styles.alert}>{runError}</div>}{run?.error && <div role="alert" className={styles.alert}>{messageOf(run.error)}{run.error.code && <details><summary>错误编号</summary><code>{run.error.code}</code></details>}</div>}
            <RunResults run={runViewWorkflow ? run : null} workflow={runViewWorkflow || { ...workflow, nodes: [], edges: [] }} registry={catalog} traces={runViewWorkflow ? traces : []} events={runViewWorkflow ? events : []} selected={resultNode} onSelect={setResultNode} traceError={runViewWorkflow ? traceError : '运行作用域与草稿不一致，无法显示节点结果'} />
          </>}
        </RunPanel>}
      </main>
      </CanvasWorkspace>{subflowId && registry && workflow.nodes.find(node => node.id === subflowId) && <SubflowEditor currentMessage={message} persistedWorkflow={JSON.parse(baseline)} graphSha256={workflow.graph_sha256} onPersistSettings={async change=>await persistNodeConfiguration(workflow,change)} onPersistNode={async (next,expected)=>await persistNodeConfiguration(workflow,{node:next,node_id:next.id,scope_path:[],expected_graph_sha256:expected})} open node={workflow.nodes.find(node => node.id === subflowId)!} parent={workflow} registry={registry} resources={resources} testBusy={Boolean(busy) || isPendingRun(run)} onTestNode={(node, path) => { setTestScopePath([...path]); setTestNode(clone(node)); }} onRepeatChange={patch => mutate(w => ({ ...w, nodes: w.nodes.map(node => node.id === subflowId ? { ...node, ...patch } : node) }))} onChange={body => mutate(w => ({ ...w, nodes: w.nodes.map(node => node.id === subflowId ? { ...node, config: { ...node.config, body } } : node) }))} onValidity={valid(`body-${subflowId}`)} onClose={() => {setSubflowId('');setSelected([]);setInspectorOpen(false);}} container={() => root.current || document.body} readonlyVariables={readonlyVariables} knowledgeScope={agent?.knowledge_resources} dataScope={agent?.data_resources} canManageModels={canManageModels} onManageModels={onNavigate ? () => navigatePage('models') : undefined} />}{testNode && <NodeTestDialog key={JSON.stringify([testScopePath, testNode.id])} node={testNode} workflow={workflow} scopePath={testScopePath} registry={registry} variables={[]} inputs={testInputs} resources={resources} busy={Boolean(busy)} container={() => root.current || document.body} onClose={() => setTestNode(null)} onTest={runSingleNode} />}</>}
  </div>;
}
export function WorkflowStudio(props: WorkflowStudioProps) { if (props.agentType === "simple") return <p>简单智能体使用配置表单，无需编排画布。</p>; return <ReactFlowProvider key={`${props.identityKey || 'legacy'}:${props.agentId || 'standalone'}`}><Studio {...props} /></ReactFlowProvider>; }
export { MessageComposer } from './message-composer';
export type { WorkflowStudioProps } from '@/lib/workflow/types';
export default WorkflowStudio;
