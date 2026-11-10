// Runs real Studio/JsonEditor React effects with controlled API responses.
// Graph drawing is stubbed; actual drag/connect/visual acceptance remains browser QA.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const React = require('react');
const { create, act } = require('react-test-renderer');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const web = path.resolve(__dirname, '../..');
const compile = require('./test-module.cjs');
const labels = compile(path.join(__dirname, 'labels.ts'));
const uiLabels = compile(path.join(web, 'lib/ui-labels.ts'));
const errors = compile(path.join(web, 'lib/errors.ts'));
const adapter = compile(path.join(__dirname, 'adapter.ts'), { './labels': labels }, { crypto: require('node:crypto').webcrypto });
const runEvents = compile(path.join(__dirname, 'run-events.ts'));
const debugInputs = compile(path.join(__dirname, 'debug-inputs.ts'));
const scopedDebug = compile(path.join(__dirname, 'scoped-debug.ts'));
const messages = compile(path.join(__dirname, 'messages.ts'));
const css = new Proxy({}, { get: (_, key) => String(key) });
const schemaFields = compile(path.join(web, 'components/workflow-studio/schema-fields.tsx'), { '../model-picker': p => React.createElement('model-picker',p), '@/lib/workflow/labels': labels, './studio.module.css': { __esModule: true, default: css } });
const nodeLibrary = compile(path.join(web, 'components/workflow-studio/node-library.tsx'), { '@/lib/workflow/adapter': adapter, '@/lib/workflow/messages': messages,
    '@/lib/workflow/debug-inputs': debugInputs, './studio.module.css': { __esModule: true, default: css } });
const linkedWorkflow = compile(path.join(web, 'components/workflow-studio/linked-workflow.tsx'), { './studio.module.css': { __esModule: true, default: css } });
const presentationResult = compile(path.join(web, 'components/workflow-studio/presentation-result.tsx'), { '@/lib/errors': errors, '@/lib/api': { download: async () => {} }, './studio.module.css': { __esModule: true, default: css } });
const clone = v => JSON.parse(JSON.stringify(v));
const text = node => typeof node === 'string' ? node : Array.isArray(node) ? node.map(text).join(' ') : node ? text(node.children) : '';
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
const children = p => React.createElement(React.Fragment, null, p.children);
async function harness(options = {}) {
  const listeners = new Map(); const observers = []; let leaveGuard; let confirms = 0; let confirmResult = true; let nativeConfirms = 0; const modalRecords = []; const saveRequests = []; const viewport = { width: options.width || 1400, height: options.height || 700 }; const saved = new Map();
  const detailSnapshots = new Map(); const tracePending = []; const decisionPending = []; const resumePending = []; const cancelPending = []; const pending = []; const timers = new Map(); const calls = []; let seq = 0; let timerId = 0;
  let mainWorkflow = options.unbound ? null : 'linked-flow';
  const registry = clone(require(path.resolve(web, '../../contracts/node-registry.json')));
  if (!registry.items.some(d => d.type === 'trigger_text')) registry.items.push({ type: 'trigger_text', category: 'trigger', label: '文字消息', ports: ['default'], config_schema: {}, input_schema: {}, output_schema: { type: 'object' }, status: 'supported' });
  registry.items.forEach(d => { if (!['code', 'human_approval', 'trigger'].includes(d.type)) d.status = 'supported'; });
  registry.items.forEach(d => Object.assign(d, options.registryOverrides?.[d.type] || {}));
  const resources = { agents: [], agentVersions: [], models: [], documents: [], plugins: [], versions: [], connections: [], imports: [], warnings: [] };
  const api = {
    variableCatalog: async () => ({items:['system.run_id','system.tenant_id','system.workspace_id','user.id','user.role'].map(ref=>({ref,label:ref,schema:{type:'string'},readonly:true,source:'server'}))}), list: async () => ({ items: options.items || [] }), registry: async () => registry, validate: async w => { calls.push(['validate', w]); return { valid: true }; },
    save: async w => { calls.push(['save', w]); if (options.saveError) throw options.saveError; const complete = value => { const result = value || { ...clone(w), id: w.id || 'saved-workflow' }; saved.set(result.id, result); return result; }; if (options.deferSave) return new Promise((resolve, reject) => saveRequests.push({ resolve: value => resolve(complete(value)), reject })); return complete(); },
    saveNodeSettings: async (id, payload) => {
      const {publication,access,...graph}=clone(payload.structure);
      return {...graph,id,graph_sha256:'b'.repeat(64)};
    },
    publish: async (id,audience,graphHash) => {
      calls.push(['publish-workflow',id,clone(audience),graphHash]);
      return {status:'published',visibility:audience.visibility,revision:audience.expected_revision+1,version:2};
    },
    run: async (w, inputs, query, prompt) => { calls.push(['run', clone(w), inputs, query, prompt]); return { id: `run-${++seq}`, status: 'succeeded' }; },
    debugRun: async draft => { calls.push(['debugRun', clone(draft)]); const created=await api.run(draft.workflow, draft.inputs, draft.query, draft.prompt); return options.debugRunResult || (draft.node_test ? {...created,execution_identity:{kind:'node_test',node_id:draft.node_test.node_id,scope_path:draft.node_test.scope_path||[],target_key:'root/'+draft.node_test.node_id,source_graph_sha256:'a'.repeat(64),graph_sha256:'b'.repeat(64)}} : created); },
    trace: async (id, after) => { calls.push(['trace', id, after]); if(options.deferTrace)return new Promise((resolve,reject)=>tracePending.push({id,resolve,reject})); if(options.traceError)throw options.traceError; if(options.tracePages?.length)return options.tracePages.shift(); return {run_id:id,items:options.traces||[],last_sequence:0,has_more:false}; },
    cancel: id => new Promise(resolve => { calls.push(['cancel', id]); cancelPending.push({ id, resolve }); }),
    approvalDecision: (id, approvalId, decision, note) => new Promise((resolve, reject) => { calls.push(['decision', id, approvalId, decision, note]); decisionPending.push({ id, approvalId, resolve, reject }); }),
    resume: id => new Promise(resolve => { calls.push(['resume', id]); resumePending.push({ id, resolve }); }),
    executionState: async id => { calls.push(['state', id]); const value = detailSnapshots.get(id) || {}; const terminal = ['succeeded','failed','cancelled','interrupted'].includes(value.status); const state = { run_id: id, status: value.status, engine: value.engine, capabilities: value.capabilities || { is_terminal: terminal, is_active: ['queued','running','resuming'].includes(value.status), can_cancel: false, can_resume: false, can_approve: false, can_reconcile: false }, last_sequence: value.last_sequence ?? 1, approvals: value.approvals || [] }; return options.stateOverride ? options.stateOverride(state) : state; },
    runDetail: id => new Promise(resolve => pending.push({ id, resolve: value => { detailSnapshots.set(id, value); resolve(value); } })),
    events: async (id, after) => { calls.push(['events', id, after]); if (options.eventPages?.length) return options.eventPages.shift(); const items = options.events || [{ sequence: 1, type: 'node.completed', node_id: 'answer', status: 'succeeded', data: { outputs: { values: { text: id } } } }]; return { items: after === undefined ? items : items.filter(event => event.sequence > after), last_sequence: Math.max(0, ...items.map(event => event.sequence)), has_more: false }; },
    agent: async id => { calls.push(['owner',id]);if (options.agentError) throw options.agentError; return { id, name: 'synthetic-agent', main_workflow_id: mainWorkflow }; },
    get: async id => { calls.push(['get', id]); if (options.getError) throw options.getError; return saved.has(id) ? clone(saved.get(id)) : { ...clone(options.workflow || adapter.newWorkflow()), id }; },
    bind: async (agent, workflow) => { calls.push(['bind', agent, workflow]); if (options.bindError) throw options.bindError; mainWorkflow = workflow; return {}; },
    runAgent: async (agent, message, inputs) => { calls.push(['runAgent', agent, clone(message), inputs]); return { id: `run-${++seq}`, status: 'queued' }; },
  };
  const globals = { window: { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); }, confirm: () => { nativeConfirms++; return confirmResult; } }, document: { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); }, fullscreenElement: null }, crypto: require('node:crypto').webcrypto, ResizeObserver: class { constructor(callback) { this.callback = callback; observers.push(this); } observe() {} disconnect() {} }, setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id) };
  const flowApi = { screenToFlowPosition: p => p, fitView: options => calls.push(['fit', options]) };
  const flow = { ReactFlowProvider: children, ReactFlow: p => React.createElement('flow-surface', p, p.children), Background: () => null, Controls: () => null, MiniMap: () => null, useReactFlow: () => flowApi, useNodesInitialized: () => true };
  const canvasWorkspace = compile(path.join(web, 'components/workflow-studio/canvas-workspace.tsx'), { './studio.module.css': { __esModule: true, default: css } }, globals);
  const canvasViewport = compile(path.join(web, 'components/workflow-studio/canvas-viewport.tsx'), { '@xyflow/react': flow }, globals);
  const knowledgePicker = compile(path.join(web, 'components/workflow-studio/knowledge-picker.tsx'), { './studio.module.css': { __esModule: true, default: css } }, globals);
  const databaseQuery = compile(path.join(web, 'components/workflow-studio/database-query.tsx'), { '@/lib/api': {}, '@/lib/errors': errors, '@/lib/workflow/api': { workflowApi: api }, '@/lib/workflow/labels': labels, './schema-fields': schemaFields, './studio.module.css': { __esModule: true, default: css } }, globals);
  const runApprovals = compile(path.join(web, 'components/workflow-studio/run-approvals.tsx'), { './studio.module.css': { __esModule: true, default: css } }, globals);
  const runStatus = compile(path.join(web, 'components/workflow-studio/run-status.tsx'), { './studio.module.css': { __esModule: true, default: css } }, globals);
  const leaveConfirmation = compile(path.join(web, 'components/workflow-studio/use-leave-confirmation.ts'));
  const showModal = (kind, config) => {
    if (options.modalThrows) throw Error('modal unavailable');
    const record = { kind, config, destroyed: false }; modalRecords.push(record);
    const handle = { destroy() { if (record.destroyed) return; record.destroyed = true; config.afterClose?.(); config.content?.props?.onRelease?.(); } };
    if (kind === 'confirm') { confirms++; if (!options.manualDialogs) confirmResult ? config.onOk() : config.onCancel(); }
    return handle;
  };
  const modalApi = { confirm: config => showModal('confirm', config), success: config => showModal('success', config) };
  const Studio = compile(path.join(web, 'components/workflow-studio/index.tsx'), {
    '@xyflow/react': flow, '@/lib/ui-labels': uiLabels, '@/lib/load-timing':compile(path.join(web,'lib/load-timing.ts')),
    './use-leave-confirmation': leaveConfirmation,
    './run-panel': compile(path.join(web,'components/workflow-studio/run-panel.tsx'),{'./studio.module.css':{__esModule:true,default:css}},globals),
    './approval-inbox': {ApprovalInbox:p=>p.open?React.createElement('approval-inbox',p):null},
    './run-results': compile(path.join(web,'components/workflow-studio/run-results.tsx'),{'@/lib/workflow/run-events':runEvents,'@/lib/workflow/adapter':adapter,'./presentation-result':presentationResult,'./retrieval-result':compile(path.join(web,'components/workflow-studio/retrieval-result.tsx'),{'./typed-value':compile(path.join(web,'components/workflow-studio/typed-value.tsx'),{'@/lib/workflow/labels':labels,'./studio.module.css':{__esModule:true,default:css}}),'./studio.module.css':{__esModule:true,default:css}}),'./typed-value':compile(path.join(web,'components/workflow-studio/typed-value.tsx'),{'@/lib/workflow/labels':labels,'./studio.module.css':{__esModule:true,default:css}}),'./studio.module.css':{__esModule:true,default:css}}),
    './schema-definition': compile(path.join(web,'components/workflow-studio/schema-definition.tsx'),{'./schema-fields':schemaFields,'@/lib/workflow/labels':labels,'./studio.module.css':{__esModule:true,default:css}}),
    './node-test-dialog': {NodeTestDialog:p=>React.createElement('node-test-dialog',p)},
    ...(options.mockNodePanel?{'./node-draft-panel':{NodeDraftPanel:p=>React.createElement('node-settings-panel',p)}}:{}),
    '../asset-publication': {
      PublicationBadges: () => null,
      PublicationDialog: p => React.createElement('publication-dialog',p),
    },
    './subflow-editor': {SubflowEditor:p=>React.createElement('subflow-editor',p)},
    './loop-condition': {LoopCondition:p=>React.createElement('loop-condition',p)},
    './condition-editor': {ConditionEditor:p=>React.createElement('condition-editor',p)},
    './output-options': {OutputOptions:p=>React.createElement('output-options',p)},
    '../workflow-file-tools': p => React.createElement('workflow-file-tools',p), '@/lib/workflow-file-api':{workflowFileClient:{}},
    'antd': { Dropdown: children, Alert: children, Button: p => React.createElement('button',p,p.children), Select: p => React.createElement('model-select',p), Tag: children, App: { useApp: () => ({ modal: modalApi }) }, Input: p => React.createElement('input', p), Modal: p => p.open ? React.createElement('section', { role: 'dialog', 'aria-label': p.title }, p.children, React.createElement('button', { onClick: p.onOk, disabled: p.okButtonProps?.disabled }, p.okText), React.createElement('button', { onClick: p.onCancel, disabled: p.cancelButtonProps?.disabled }, p.cancelText)) : null },
    '@/lib/errors': errors, '@/lib/workflow/labels': labels, './canvas-workspace': canvasWorkspace, './canvas-viewport': canvasViewport, './knowledge-picker': knowledgePicker, './database-query': databaseQuery, './run-status': runStatus, './run-approvals': runApprovals,
    '../../../../contracts/node-registry.json': registry,
    '@/lib/api': { request: async () => ({ items: [] }), currentIdentityEpoch: () => 1, defaultQuery: { entity: 'SYNTHETIC-DEMO', periods: ['2024', '2025'] } },
    '@/lib/workflow/api': { workflowApi: api, loadResources: async () => options.loadResources?options.loadResources():options.deferResources?new Promise(()=>{}):resources },
    '@/lib/workflow/adapter': adapter,
    '@/lib/workflow/run-events': runEvents,
    '@/lib/workflow/debug-inputs': debugInputs,
    '@/lib/workflow/scoped-debug': scopedDebug,
    '@/lib/workflow/messages': messages,
    './node-library': nodeLibrary,
    './linked-workflow': linkedWorkflow,
    './presentation-result': presentationResult,
    './message-composer': { __esModule: true, default: p => React.createElement('message-composer', p) },
    './schema-fields': schemaFields,
    './llm-editor': p => React.createElement('llm-editor',p),
    './studio-node': { StudioNode: () => null, portLabel: (_node, port) => labels.enumLabel(port) },
    '../model-picker': p => React.createElement('model-picker',p),
    './studio.module.css': { __esModule: true, default: css },
  }, globals).default;
  let tree;
  const initialProps = options.embedded ? { embedded: true, agentId: 'agent-a', workflowId: options.unbound ? undefined : 'linked-flow', identityKey: 'actor-a', onPublish: async audience => { calls.push(['publish',clone(audience)]); return { id: 'published-version', version: 1 }; } } : {};
  Object.assign(initialProps, { fullPage: true, ...options.props, onBack: () => calls.push(['back']), onLeaveGuardChange: guard => { leaveGuard = guard; } });
  const createNodeMock = element => element.type === 'div' ? { get clientWidth() { return viewport.width; }, get clientHeight() { return viewport.height; }, getBoundingClientRect: () => ({ left: 0, top: 0 }), addEventListener(name,fn,capture) { if(name==='pointerdown')assert.equal(capture,true);listeners.set('canvas:'+name,fn); }, removeEventListener(name,fn) {if(listeners.get('canvas:'+name)===fn)listeners.delete('canvas:'+name);} } : null;
  function Parent() { const [id, setId] = React.useState(initialProps.workflowId); return React.createElement(Studio, { ...initialProps, workflowId: id, onWorkflowChange: setId }); }
  await act(async () => { tree = create(React.createElement(options.unbound ? Parent : Studio, initialProps), { createNodeMock }); await flush(); });
  const alias = label => ({'▷ 测试运行':'▷ 试运行','保存并测试':'▷ 试运行','测试输入':'试运行输入'})[label] || label;
  const buttons = original => {const label=alias(original); return tree.root.findAllByType('button').filter(n => text({ children: n.props.children }) === label || n.props['aria-label'] === label);};
  const click = async label => { await act(async () => { const button=buttons(label)[0];assert(button,label);assert(!button.props.disabled,label+' enabled');await button.props.onClick();await flush(); }); if(['▷ 测试运行','保存并测试'].includes(label)) await act(async()=>{const start=buttons('▷ 开始试运行')[0];assert(start&&!start.props.disabled);await start.props.onClick();await flush();}); };
  if (!options.embedded&&!options.props?.createOnOpen&&!options.props?.workflowId) await click('＋ 新建工作流');
  if (options.unbound && !options.startOnly) await click('新建工作流');
  return { get tree() { return tree; }, pending, tracePending, cancelPending, resumePending, decisionPending, calls, buttons, click, listeners, observers,
    modalRecords, saveRequests, nativeConfirms: () => nativeConfirms, resize: async (width, height = viewport.height) => act(async () => { viewport.width = width; viewport.height = height; observers.forEach(observer => observer.callback()); await flush(); }),
    leaveGuard: () => leaveGuard, confirms: () => confirms, confirm: value => { confirmResult = value; },
    runTimers: async () => act(async () => { const work = [...timers.values()]; timers.clear(); work.forEach(fn => fn()); await flush(); }),
    reload: async () => { await act(async () => tree.unmount()); await act(async () => { tree = create(React.createElement(Studio, initialProps), { createNodeMock }); await flush(); }); }, content: () => text(tree.toJSON()),
    updateIdentity: async key => act(async () => { tree.update(React.createElement(Studio, { ...initialProps, identityKey: key })); await flush(); }),
    complete: async (id, value) => act(async () => { const next = pending.find(p => p.id === id); assert(next, `pending ${id}`); pending.splice(pending.indexOf(next), 1); next.resolve(value); await flush(); }),
    dispose: async () => act(async () => tree.unmount()),
  };
}
test('terminal POST response still loads real final outputs and events', async () => {
  const h = await harness();
  try { await h.click('▷ 测试运行'); assert.equal(h.pending[0].id, 'run-1'); await h.complete('run-1', { id: 'run-1', status: 'succeeded', outputs: { text: 'fresh-output-one' }, variable_pool: { nodes: { answer: { values: { text: 'fresh-output-one' } } } } });
    assert(h.content().includes('fresh-output-one')); assert(h.content().includes('node.completed'));
  } finally { await h.dispose(); }
});
test('next failed test clears previous successful output before new response', async () => {
  const h = await harness();
  try { await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'succeeded', outputs: { text: 'old-output-must-clear' } }); assert(h.content().includes('old-output-must-clear'));
    await h.click('▷ 测试运行'); assert(!h.content().includes('old-output-must-clear'));
    await h.complete('run-2', { id: 'run-2', status: 'failed', error: { code: 'VARIABLE_NOT_FOUND', message: 'missing input' }, outputs: {} });
    assert(!h.content().includes('old-output-must-clear')); assert(h.content().includes('VARIABLE_NOT_FOUND'));
  } finally { await h.dispose(); }
});
test('late poll from another editor session cannot populate the new workflow', async () => {
  const h = await harness();
  try { await h.click('▷ 测试运行'); await h.click('返回流程列表'); await h.click('＋ 新建工作流');
    await h.complete('run-1', { id: 'run-1', status: 'succeeded', outputs: { text: 'late-previous-session' } });
    assert(!h.content().includes('late-previous-session')); assert(h.content().includes('填写输入后开始'));
  } finally { await h.dispose(); }
});
test('unfinished JSON disables save, validation and test rather than saving previous value', async () => {
  const h = await harness();
  try { await h.click('流程设置');
    await act(async () => { h.tree.root.findAllByType('textarea').find(n => n.props['aria-label'] === '流程输入结构').props.onChange({ target: { value: '{"type":' } }); await flush(); });
    for (const label of ['保存', '校验', '▷ 测试运行']) assert.equal(h.buttons(label)[0].props.disabled, true, `${label} disabled`);
    assert.equal(h.calls.filter(c => c[0] === 'save').length, 0); assert(h.listeners.has('beforeunload'), 'unfinished editor text is unsaved work');
  } finally { await h.dispose(); }
});
test('an incomplete loop opens its visual subflow before node settings are saved', async () => {
  const workflow = adapter.newWorkflow();
  workflow.nodes.push({id:'repeat', type:'loop', inputs:{}, config:{}});
  const h = await harness({props:{workflowId:'incomplete-loop'},workflow});
  try {
    const flow = h.tree.root.findByType('flow-surface');
    await act(() => flow.props.onNodeClick({},flow.props.nodes.find(node => node.id === 'repeat')));
    await h.click('编辑可视子流程 ↗');
    assert.equal(h.tree.root.findByType('subflow-editor').props.node.id,'repeat');
    assert.equal(h.calls.filter(call => call[0] === 'save').length,0);
  } finally { await h.dispose(); }
});
test('simultaneous node/edge deletion preserves unrelated graph connections', async () => {
  const h = await harness();
  try {
    const flow = () => h.tree.root.findByType('flow-surface');
    const addTemplate = async () => {
      await act(async()=>{h.tree.root.findAllByProps({role:'tab'}).find(n=>n.children[0]==='行为').props.onClick();await flush();});
      await act(async()=>{h.tree.root.findAllByType('button').find(n=>n.children[0]==='数据与变量').props.onClick();await flush();});
      await act(async()=>{const button=h.tree.root.findAllByType('button').find(n=>n.props.draggable&&n.findAllByType('strong').some(s=>s.children.join('')==='模板转换'));assert(button);button.props.onClick();await flush();});
    };
    await addTemplate(); await addTemplate();
    const templates = flow().props.nodes.filter(n => n.data.node.type === 'template'); assert.equal(templates.length, 2);
    const [first, second] = templates; const originalEdge = flow().props.edges[0].id;
    await act(async () => { await flow().props.onBeforeDelete({ nodes: [], edges: [{ id: originalEdge }] }); await flush(); });
    for (const [source, target] of [['start', first.id], [first.id, second.id], [second.id, 'answer']]) await act(async () => { flow().props.onConnect({ source, target, sourceHandle: 'default', targetHandle: 'default' }); await flush(); });
    assert.equal(flow().props.edges.length, 3);
    const deleted = flow().props.edges.filter(e => e.source === first.id || e.target === first.id).map(e => ({ type: 'remove', id: e.id }));
    await act(async () => { await flow().props.onBeforeDelete({ nodes: [first], edges: deleted }); await flush(); });
    assert.equal(flow().props.edges.length, 1); assert.equal(flow().props.edges[0].source, second.id); assert.equal(flow().props.edges[0].target, 'answer');
    assert(!flow().props.nodes.some(n => n.id === first.id));
    await h.click('撤销');
    assert.equal(flow().props.edges.length, 3);
    assert(flow().props.nodes.some(n => n.id === first.id));
    await h.click('重做');
    assert(!flow().props.nodes.some(n => n.id === first.id));
    assert.equal(flow().props.edges.length, 1);
    const start = flow().props.nodes.find(n => n.id === 'start');
    h.confirm(false);
    await act(() => flow().props.onBeforeDelete({nodes:[start],edges:[]}));
    assert(flow().props.nodes.some(n => n.id === 'start'));
    assert.equal(h.nativeConfirms(),1);
  } finally { await h.dispose(); }
});
test('measured node dimensions survive editor mutations for minimap and fit-view bounds', async () => {
  const h = await harness();
  try {
    const flow = () => h.tree.root.findByType('flow-surface');
    await act(async () => { flow().props.onNodesChange([{ type: 'dimensions', id: 'start', dimensions: { width: 215, height: 114 } }]); await flush(); });
    assert.deepEqual(clone(flow().props.nodes.find(n => n.id === 'start').measured), { width: 215, height: 114 });
    await act(async () => { h.tree.root.findAllByType('input').find(n => n.props['aria-label'] === '工作流名称').props.onChange({ target: { value: 'edited after measuring' } }); await flush(); });
    assert.deepEqual(clone(flow().props.nodes.find(n => n.id === 'start').measured), { width: 215, height: 114 });
  } finally { await h.dispose(); }
});
test('identity key change removes old editor and rejects a late run response', async () => {
  const h = await harness();
  try { await h.click('▷ 测试运行'); await h.updateIdentity('actor-b'); assert(!h.content().includes('运行事件与节点结果')); await h.click('＋ 新建工作流'); await h.complete('run-1', { id: 'run-1', status: 'succeeded', outputs: { text: 'private-actor-a-output' } }); assert(!h.content().includes('private-actor-a-output')); assert(h.content().includes('填写输入后开始')); }
  finally { await h.dispose(); }
});
test('embedded draft tests use current graph and Agent scope without saving or rebinding', async () => {
  const h = await harness({ embedded: true, workflow: { ...adapter.newWorkflow(), ui: {} } });
  try {
    assert.equal(h.buttons('返回流程列表').length, 0);
    await h.click('测试输入');
    await act(async () => { h.tree.root.findByType('message-composer').props.onChange({ type: 'text', text: 'typed message', attachments: [] }); await flush(); });
    await h.click('保存并测试');
    const mutations = h.calls.map(c => c[0]); assert(!mutations.includes('save')); assert(!mutations.includes('bind')); assert(!mutations.includes('runAgent'));
    const request = h.calls.find(c => c[0] === 'debugRun')[1]; assert.equal(request.agent_id, 'agent-a'); assert.deepEqual(request.message, { type: 'text', text: 'typed message' }); assert.equal(request.workflow.id,'linked-flow');
    assert(!h.content().includes('独立工作流发布'));
  } finally { await h.dispose(); }
});
test('embedded publication waits for audience confirmation, then saves a changed canvas before the Agent release', async () => {
  const h = await harness({ embedded: true });
  try {
    const input=h.tree.root.findByProps({'aria-label':'工作流名称'});
    await act(async()=>{input.props.onChange({target:{value:'Updated canvas'}});await flush();});
    await h.click('发布智能体及流程');
    assert(!h.calls.some(c=>['save','publish'].includes(c[0])));
    const audience={visibility:'partial',user_ids:['member-a'],role_ids:[],expected_revision:0};
    await act(async()=>{await h.tree.root.findByType('publication-dialog').props.onPublish(audience);});
    const mutations=h.calls.map(c=>c[0]);
    assert(mutations.includes('save')&&mutations.indexOf('save')<mutations.indexOf('publish'));
    assert.deepEqual(h.calls.find(c=>c[0]==='publish')[1],audience);
  }
  finally { await h.dispose(); }
});
test('adding a node to a published independent workflow keeps the audience revision', async () => {
  const publication={status:'published',visibility:'public',revision:4,version:1};
  const workflow={...adapter.newWorkflow(),id:'existing-flow',graph_sha256:'a'.repeat(64),publication};
  const h=await harness({workflow,props:{workflowId:workflow.id},mockNodePanel:true});
  try {
    const surface=h.tree.root.findByType('flow-surface');
    await act(async()=>{surface.props.onNodeClick({},surface.props.nodes[0]);await flush();});
    const extra={...clone(workflow.nodes[0]),id:'new-node'};
    await act(async()=>{await h.tree.root.findByType('node-settings-panel').props.onCommit(
      {...workflow,nodes:[...workflow.nodes,extra]},
      {expected_graph_sha256:workflow.graph_sha256,node_id:extra.id,scope_path:[],node:extra});});
    await h.click('发布工作流');
    const dialog=h.tree.root.findByType('publication-dialog');
    assert.equal(dialog.props.value.revision,4);
    await act(async()=>{await dialog.props.onPublish({visibility:'private',user_ids:[],role_ids:[],expected_revision:4});});
    const [,id,audience,graphHash]=h.calls.find(c=>c[0]==='publish-workflow');
    assert.equal(id,workflow.id);assert.equal(audience.expected_revision,4);assert.equal(graphHash,'b'.repeat(64));
  }finally{await h.dispose();}
});
test('output presentation renders text, exact decimal table cells and protected file actions', async () => {
  const h = await harness();
  try { await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'succeeded', presentations: [{ node_id: 'output', kind: 'mixed', text: 'public visible output', table: { columns: ['金额'], rows: [{ '金额': '9007199254740993.10' }] }, files: [{ id: 'artifact-one', filename: 'output.xlsx' }] }] });
    assert(h.content().includes('public visible output')); assert(h.content().includes('9007199254740993.10')); assert(h.content().includes('output.xlsx')); assert.equal(h.tree.root.findAllByType('table').length, 1);
  } finally { await h.dispose(); }
});
test('unbound Agent draft tests leave its binding and publish capability unchanged', async () => {
  const h = await harness({ embedded: true, unbound: true });
  try {
    assert.equal(h.buttons('发布智能体及流程')[0].props.disabled, true, 'not publishable before confirmed binding');
    await h.click('测试输入');
    await act(async () => { h.tree.root.findByType('message-composer').props.onChange({ type: 'text', text: 'new private graph', attachments: [] }); await flush(); });
    await h.click('保存并测试');
    assert.equal(h.calls.filter(c => c[0] === 'save').length, 0); assert.equal(h.calls.filter(c => c[0] === 'bind').length, 0); assert.equal(h.calls.filter(c => c[0] === 'debugRun').length, 1);
    assert.equal(h.pending[0]?.id, 'run-1', 'run polling survives the parent workflowId update');
    await h.complete('run-1', { id: 'run-1', status: 'succeeded', outputs: { text: 'first-linked-run' } });
    assert(h.content().includes('first-linked-run')); assert.equal(h.buttons('发布智能体及流程')[0].props.disabled, true);
  } finally { await h.dispose(); }
});
test('panel resize preserves viewport and leaves graph clean; explicit fit remains available', async () => {
  const h = await harness({ embedded: true });
  try {
    await h.runTimers(); assert(h.calls.some(call => call[0] === 'fit'));
    assert.equal(h.tree.root.findByType('flow-surface').props.onMoveEnd, undefined);
    assert(h.content().includes('已保存')); assert(!h.listeners.has('beforeunload'));
    const before = h.calls.filter(call => call[0] === 'fit').length;
    await act(async () => { h.observers.forEach(observer => observer.callback()); await flush(); });
    await h.runTimers(); assert.equal(h.calls.filter(call => call[0] === 'fit').length, before);
    await h.click('收起节点库'); assert.equal(h.buttons('展开节点库')[0].props['aria-expanded'], false);
    await h.click('展开节点库');
    const separator = h.tree.root.findAllByProps({ role: 'separator' }).find(node => node.props['aria-label'] === '调整节点库宽度');
    await act(async () => { separator.props.onKeyDown({ key: 'ArrowRight', preventDefault() {} }); await flush(); });
    assert.equal(h.tree.root.findAllByProps({ role: 'separator' }).find(node => node.props['aria-label'] === '调整节点库宽度').props['aria-valuenow'], 272);
    assert(h.content().includes('已保存')); assert(!h.listeners.has('beforeunload'));
  } finally { await h.dispose(); }
});

test('drag fit: selection resize cannot fit between pointer-down and final node position; release preserves viewport without changing DSL', async () => {
  const h=await harness({embedded:true});
  try {
    await h.runTimers();const fitCount=()=>h.calls.filter(call=>call[0]==='fit').length;
    const before=fitCount(),flow=()=>h.tree.root.findByType('flow-surface');
    const original=clone(flow().props.nodes.map(n=>n.data.node));const target=flow().props.nodes[0];
    h.listeners.get('canvas:pointerdown')({pointerId:1});
    await act(async()=>{flow().props.onNodesChange([{type:'select',id:target.id,selected:true},{type:'position',id:target.id,dragging:true,position:{x:target.position.x+20,y:target.position.y+20}}]);await flush();});
    assert.equal(h.buttons('收起属性区').length,0);await h.resize(900,720);await h.runTimers();assert.equal(fitCount(),before,'auto fit must not change transform during pointer gesture');
    await act(async()=>{flow().props.onNodesChange([{type:'position',id:target.id,dragging:false,position:{x:target.position.x+40,y:target.position.y+40}}]);h.listeners.get('pointerup')({pointerId:1});await flush();});
    const positioned=clone(flow().props.nodes.map(n=>n.data.node));await h.runTimers();assert.equal(fitCount(),before);
    assert.deepEqual(clone(flow().props.nodes.map(n=>n.data.node)),positioned,'deferred fit only changes the view');
    assert.deepEqual(positioned[0].ui.position,{x:target.position.x+40,y:target.position.y+40});assert.deepEqual(positioned.slice(1),original.slice(1));
    await h.click('适应视图');assert.deepEqual(clone(flow().props.nodes.map(n=>n.data.node)),positioned);
    await h.click('保存');await h.click('确认保存');assert.deepEqual(clone(h.calls.find(call=>call[0]==='save')[1].nodes),positioned);
  }finally{await h.dispose();}
  assert(!h.listeners.has('canvas:pointerdown'));assert(!h.listeners.has('pointerup'));assert(!h.listeners.has('pointercancel'));
});

test('drag fit: an initial pending fit and pointer cancel are fenced; unmount cancels the deferred fit',async()=>{
  const h=await harness({embedded:true});const before=h.calls.filter(call=>call[0]==='fit').length;
  h.listeners.get('canvas:pointerdown')({pointerId:7});await h.runTimers();assert.equal(h.calls.filter(call=>call[0]==='fit').length,before);
  await h.resize(1000,700);h.listeners.get('pointercancel')({pointerId:7});await h.dispose();await h.runTimers();assert.equal(h.calls.filter(call=>call[0]==='fit').length,before);
});
test('unsaved navigation is guarded and saving then reloading restores the same Agent graph', async () => {
  const h = await harness({ embedded: true });
  try {
    const edit = value => act(async () => { h.tree.root.findByProps({ 'aria-label': '工作流名称' }).props.onChange({ target: { value } }); await flush(); });
    await edit('saved-private-canvas'); h.confirm(false);
    assert(h.listeners.has('beforeunload')); assert.equal(await h.leaveGuard()(), false);
    await h.click('返回AI工作室'); assert.equal(h.calls.filter(call => call[0] === 'back').length, 0);
    await h.click('保存'); assert.equal(h.buttons('确认保存').length, 1); await h.click('确认保存'); assert.equal(await h.leaveGuard()(), true); assert(!h.listeners.has('beforeunload'));
    await h.reload(); assert.equal(h.tree.root.findByProps({ 'aria-label': '工作流名称' }).props.value, 'saved-private-canvas');
    assert(h.calls.filter(call => call[0] === 'get').every(call => call[1] === 'linked-flow'));
    const confirms = h.confirms(); await h.click('返回AI工作室'); assert.equal(h.confirms(), confirms); assert.equal(h.calls.filter(call => call[0] === 'back').length, 1);
  } finally { await h.dispose(); }
  assert.equal(h.leaveGuard(), null);
});
test('generic test sends empty query and prompt instead of a financial sample', async () => {
  const h = await harness();
  try { await h.click('▷ 测试运行'); const call = h.calls.find(call => call[0] === 'run'); assert.deepEqual(clone(call[3]), {}); assert.equal(call[4], ''); }
  finally { await h.dispose(); }
});
test('simple Agent guard never mounts the canvas or fetches workflow resources', async () => {
  const h = await harness({ embedded: true, props: { agentType: 'simple' } });
  try { assert(h.content().includes('简单智能体使用配置表单')); assert.equal(h.tree.root.findAllByType('flow-surface').length, 0); assert.equal(h.calls.length, 0); }
  finally { await h.dispose(); }
});
test('query node output renders actual rows without converting decimal strings and includes version metadata', async () => {
  const h = await harness();
  try {
    await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'succeeded', outputs: {}, variable_pool: { nodes: { answer: { rows: [{ amount: '9007199254740993.10' }], columns: [{ name: 'amount', type: 'decimal' }], total: 1, version_id: 'fixed-published-version', policy_version: 9, source_refs: ['database:synthetic'] } } } });
    await act(async () => { h.tree.root.findByProps({ 'aria-label': '本次节点执行进度' }).findAllByType('button').find(button=>button.props['data-node-id']==='answer').props.onClick(); await flush(); });
    assert.equal(h.tree.root.findAllByType('table').length, 1); assert(h.content().includes('9007199254740993.10')); assert(h.content().includes('fixed-published-version')); assert(h.content().includes('policy_version'));
  } finally { await h.dispose(); }
});
test('canvas save keeps product DSL node IDs ports references and UI metadata without an engine selector', async () => {
  const workflow = clone(adapter.newWorkflow()); workflow.ui = { marker: 'preserve-layout-extension' }; workflow.input_schema.properties.message = {type:'string'}; workflow.nodes[1].inputs = {text:{$ref:'inputs.message'}}; workflow.nodes[0].ui.label = '消息入口';
  const h = await harness({ embedded: true, workflow });
  try {
    await act(async () => { h.tree.root.findByType('flow-surface').props.onNodesChange([{ type: 'position', id: 'start', position: { x: 120, y: 210 } }]); await flush(); });
    await h.click('保存'); assert.equal(h.buttons('确认保存').length, 1); await h.click('确认保存'); const sent = h.calls.find(call => call[0] === 'save')[1];
    assert.equal(sent.schemaVersion, '2.0'); assert.deepEqual(clone(sent.nodes.map(node => node.id)), ['start', 'answer']); assert.deepEqual(clone(sent.edges), workflow.edges);
    assert.equal(sent.nodes[1].inputs.text.$ref, 'inputs.message'); assert.equal(sent.ui.marker, 'preserve-layout-extension'); assert.equal(sent.nodes[0].ui.label, '消息入口'); assert.deepEqual(clone(sent.nodes[0].ui.position), { x: 120, y: 210 });
    assert(!('engine' in sent)); assert(!h.tree.root.findAllByType('select').some(node => /langgraph|legacy/i.test(JSON.stringify(node.props.children))));
  } finally { await h.dispose(); }
});
test('replayed unordered events yield one final node state and do not duplicate protected file output', async () => {
  const completed = { sequence: 3, type: 'node.completed', node_id: 'answer', status: 'succeeded', data: { artifact_ids: ['fixed-artifact'] } };
  const h = await harness({ events: [completed, { sequence: 2, type: 'node.started', node_id: 'answer', status: 'running' }, { ...completed }] });
  try {
    await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'succeeded', presentations: [{ node_id: 'answer', kind: 'files', files: [{ id: 'fixed-artifact', filename: 'replayed.xlsx' }] }] });
    assert.equal(h.tree.root.findByType('flow-surface').props.nodes.find(node => node.id === 'answer').data.runStatus, 'succeeded');
    assert.equal(h.tree.root.findAllByType('li').filter(node => node.props['data-state'] === 'succeeded').length, 1); assert.equal(h.tree.root.findAllByType('button').filter(button => text({ children: button.props.children }).includes('replayed.xlsx')).length, 1);
  } finally { await h.dispose(); }
});
test('waiting and resuming run keep same run ID and output snapshot with server-denied controls hidden', async () => {
  const h = await harness(); const engine = { name: 'langgraph', version: '1', dsl_sha256: 'frozen-dsl-hash' };
  try {
    await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'waiting_approval', engine, outputs: {} });
    assert(h.content().includes('等待指定审批人')); assert(h.content().includes('frozen-dsl-hash')); assert.equal(h.buttons('▷ 测试运行')[0].props.disabled, true);
    assert.equal(h.buttons('取消运行').length, 0); assert.equal(h.buttons('恢复运行').length, 0); assert.equal(h.buttons('批准').length, 0);
    await h.runTimers(); await h.complete('run-1', { id: 'run-1', status: 'resuming', engine, outputs: {} }); assert(h.content().includes('正在恢复流程'));
    await h.runTimers(); await h.complete('run-1', { id: 'run-1', status: 'succeeded', engine, outputs: { text: 'one final restored output' } });
    assert(h.content().includes('one final restored output')); assert.equal(h.calls.filter(call => call[0] === 'run').length, 1); assert.equal(h.buttons('▷ 测试运行')[0].props.disabled, false);
  } finally { await h.dispose(); }
});
test('late cancel response after session expiration cannot refill a different editor', async () => {
  const h = await harness();
  try {
    await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'running' });
    await act(async () => { void h.buttons('取消运行')[0].props.onClick(); await flush(); });
    assert.equal(h.cancelPending.length, 1); assert.equal(h.buttons('取消运行')[0].props.disabled, true);
    await act(async () => { h.listeners.get('zhangpu:session-expired')(); await flush(); });
    await h.click('＋ 新建工作流');
    await act(async () => { h.cancelPending[0].resolve({ id: 'run-1', status: 'cancelled', outputs: { text: 'private-old-cancel-output' } }); await flush(); });
    assert(!h.content().includes('private-old-cancel-output')); assert(h.content().includes('填写输入后开始'));
  } finally { await h.dispose(); }
});
test('authorized resume sends the same run once even on repeated clicks and then reads authoritative state', async () => {
  const h = await harness(); const engine = { name: 'langgraph', version: '1', dsl_sha256: 'fixed' }; const capabilities = { is_terminal: false, is_active: false, can_cancel: true, can_resume: true, can_approve: false, can_reconcile: false };
  try {
    await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'paused', engine, capabilities });
    await act(async () => { const resume = h.buttons('继续运行')[0].props.onClick; void resume(); void resume(); await flush(); });
    assert.equal(h.resumePending.length, 1); assert.equal(h.buttons('继续运行')[0].props.disabled, true);
    await act(async () => { h.resumePending[0].resolve({ id: 'run-1', status: 'resuming', engine, capabilities: { ...capabilities, can_resume: false, is_active: true } }); await flush(); });
    await h.complete('run-1', { id: 'run-1', status: 'succeeded', engine, capabilities: { ...capabilities, is_terminal: true, can_cancel: false, can_resume: false }, outputs: { text: 'resumed-once' } });
    assert.equal(h.calls.filter(call => call[0] === 'resume').length, 1); assert.equal(h.calls.filter(call => call[0] === 'run').length, 1); assert(h.content().includes('resumed-once')); assert.equal(h.buttons('继续运行').length, 0);
  } finally { await h.dispose(); }
});
test('late resume response cannot restore a previous account output', async () => {
  const h = await harness(); const engine = { name: 'langgraph' }; const capabilities = { is_terminal: false, is_active: false, can_cancel: false, can_resume: true, can_approve: false, can_reconcile: false };
  try {
    await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'paused', engine, capabilities });
    await act(async () => { void h.buttons('继续运行')[0].props.onClick(); await flush(); }); await h.updateIdentity('another-account');
    await act(async () => { h.resumePending[0].resolve({ id: 'run-1', status: 'resuming', engine, outputs: { text: 'previous-account-output' } }); await flush(); });
    assert(!h.content().includes('previous-account-output')); assert.equal(h.buttons('继续运行').length, 0);
  } finally { await h.dispose(); }
});
test('a durable event sequence gap rereads the complete safe snapshot and history without applying effects', async () => {
  const events = [{ run_id: 'run-1', sequence: 1, type: 'run.started', status: 'running' }, { run_id: 'run-1', sequence: 2, type: 'node.started', node_id: 'answer', status: 'running' }, { run_id: 'run-1', sequence: 3, type: 'node.completed', node_id: 'answer', status: 'succeeded' }];
  const h = await harness({ eventPages: [{ items: events.slice(0, 1), last_sequence: 1, has_more: false }, { items: events.slice(2), last_sequence: 3, has_more: false }, { items: events, last_sequence: 3, has_more: false }] });
  const engine = { name: 'langgraph' }; const capabilities = { is_terminal: false, is_active: true, can_cancel: false, can_resume: false, can_approve: false, can_reconcile: false };
  try {
    await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'running', engine, capabilities, last_sequence: 1 });
    await h.runTimers(); await h.complete('run-1', { id: 'run-1', status: 'running', engine, capabilities, last_sequence: 3 });
    assert.deepEqual(h.calls.filter(call => call[0] === 'events').map(call => call[2]), [0, 1, 0]); assert.equal(h.calls.filter(call => call[0] === 'state').length, 3);
    assert.equal(h.tree.root.findByType('flow-surface').props.nodes.find(node => node.id === 'answer').data.runStatus, 'succeeded'); assert.equal(h.calls.filter(call => ['run', 'resume', 'cancel'].includes(call[0])).length, 1);
  } finally { await h.dispose(); }
});
test('approval form submits an ordinary decision once and never resumes as the approver', async () => {
  const h = await harness(); const engine = { name: 'langgraph' }; const capabilities = { is_terminal: false, is_active: false, can_cancel: false, can_resume: false, can_approve: true, can_reconcile: false };
  const approval = { id: 'approval-one', run_id: 'run-1', node_id: 'approval', title: '确认继续', status: 'pending', expires_at: '2026-10-21T12:00:00Z' };
  try {
    await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'waiting_approval', engine, capabilities, approvals: [approval] });
    await act(async () => { h.tree.root.findByProps({ 'aria-label': '审批说明：确认继续' }).props.onChange({ target: { value: '已核对' } }); await flush(); });
    await act(async () => { const approve = h.buttons('同意')[0].props.onClick; approve(); approve(); await flush(); });
    assert.equal(h.decisionPending.length, 1); assert.deepEqual(h.calls.find(call => call[0] === 'decision').slice(1), ['run-1', 'approval-one', 'approve', '已核对']);
    await act(async () => { h.decisionPending[0].resolve({ ...approval, status: 'approved', note: '已核对' }); await flush(); });
    assert.equal(h.calls.filter(call => call[0] === 'resume').length, 0); assert.equal(h.calls.filter(call => call[0] === 'run').length, 1);
    await h.complete('run-1', { id: 'run-1', status: 'waiting_approval', engine, capabilities: { ...capabilities, can_approve: false, can_resume: true }, approvals: [{ ...approval, status: 'approved' }] });
    assert.equal(h.buttons('同意').length, 0); assert.equal(h.buttons('继续运行').length, 1);
  } finally { await h.dispose(); }
});
test('expired approval error is Chinese and does not apply a decision or create a replacement run', async () => {
  const h = await harness(); const engine = { name: 'langgraph' }; const capabilities = { is_terminal: false, is_active: false, can_cancel: false, can_resume: false, can_approve: true, can_reconcile: false };
  const approval = { id: 'approval-one', run_id: 'run-1', node_id: 'approval', title: '审批事项', status: 'pending', expires_at: '2026-10-20T12:00:00Z' };
  try {
    await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'waiting_approval', engine, capabilities, approvals: [approval] });
    await act(async () => { h.buttons('拒绝')[0].props.onClick(); await flush(); });
    await act(async () => { h.decisionPending[0].reject(Object.assign(new Error('审批已过期，请刷新状态'), { code: 'APPROVAL_EXPIRED' })); await flush(); });
    assert(h.content().includes('审批已过期')); assert.equal(h.buttons('拒绝').length, 0); assert.equal(h.calls.filter(call => call[0] === 'resume').length, 0); assert.equal(h.calls.filter(call => call[0] === 'run').length, 1);
  } finally { await h.dispose(); }
});
test('same-sequence capability disagreement hides controls rather than picking a permissive projection', async () => {
  const h = await harness({ stateOverride: state => ({ ...state, capabilities: { ...state.capabilities, can_resume: true } }) });
  const engine = { name: 'langgraph' }; const capabilities = { is_terminal: false, is_active: false, can_cancel: false, can_resume: false, can_approve: false, can_reconcile: false };
  try {
    await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'waiting_approval', engine, capabilities, last_sequence: 1 });
    assert(h.content().includes('运行操作权限正在更新')); assert.equal(h.buttons('继续运行').length, 0); assert.equal(h.calls.filter(call => call[0] === 'resume').length, 0);
  } finally { await h.dispose(); }
});
test('late approval response after account switch cannot refill previous static requests or output', async () => {
  const h = await harness(); const engine = { name: 'langgraph' }; const capabilities = { is_terminal: false, is_active: false, can_cancel: false, can_resume: false, can_approve: true, can_reconcile: false };
  const approval = { id: 'approval-old', run_id: 'run-1', node_id: 'approval', title: 'previous-account-request', status: 'pending', expires_at: '2026-10-21T12:00:00Z' };
  try {
    await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'waiting_approval', engine, capabilities, approvals: [approval] });
    await act(async () => { h.buttons('同意')[0].props.onClick(); await flush(); }); await h.updateIdentity('next-account');
    await act(async () => { h.decisionPending[0].resolve({ ...approval, status: 'approved', note: 'previous-account-note' }); await flush(); });
    assert(!h.content().includes('previous-account-request')); assert(!h.content().includes('previous-account-note')); assert.equal(h.buttons('同意').length, 0);
  } finally { await h.dispose(); }
});
test('ambiguous multiple pending approvals remain read-only despite a run-level approval capability', async () => {
  const h = await harness(); const engine = { name: 'langgraph' }; const capabilities = { is_terminal: false, is_active: false, can_cancel: false, can_resume: false, can_approve: true, can_reconcile: false };
  const approval = { id: 'approval-one', run_id: 'run-1', node_id: 'approval', title: '审批事项', status: 'pending', expires_at: '2026-10-21T12:00:00Z' };
  try {
    await h.click('▷ 测试运行'); await h.complete('run-1', { id: 'run-1', status: 'waiting_approval', engine, capabilities, approvals: [approval, { ...approval, id: 'approval-two' }] });
    assert(h.content().includes('审批事项')); assert.equal(h.buttons('同意').length, 0); assert.equal(h.buttons('拒绝').length, 0); assert.equal(h.calls.filter(call => call[0] === 'decision').length, 0);
  } finally { await h.dispose(); }
});
test('UI recheck: empty start prioritizes creation without a disabled existing-workflow card', async () => {
  const h = await harness({ embedded: true, unbound: true, startOnly: true });
  try {
    assert.equal(h.buttons('新建工作流').length, 1); assert.equal(h.tree.root.findAllByType('article').length, 1);
    assert.equal(h.tree.root.findAllByType('select').length, 0); assert.equal(h.buttons('关联工作流').length, 0);
    assert(h.content().includes('暂无可用的已有工作流')); assert(!h.content().includes('高阶 AI 工作流'));
  } finally { await h.dispose(); }
});
test('UI recheck: failed linked-workflow read offers retry rather than replacing its existing binding', async () => {
  const options = { embedded: true, getError: new Error('暂时无法读取工作流') }; const h = await harness(options);
  try {
    assert(h.content().includes('暂时无法打开工作流')); assert.equal(h.buttons('新建工作流').length, 0); assert.equal(h.calls.filter(call => call[0] === 'bind').length, 0);
    options.getError = undefined; await h.click('重新读取工作流'); assert.equal(h.tree.root.findAllByType('flow-surface').length, 1);
  } finally { await h.dispose(); }
});
test('UI recheck: inspector starts collapsed and selection or explicit settings open it without changing DSL', async () => {
  const h = await harness({ embedded: true });
  try {
    assert.equal(h.buttons('展开属性区')[0].props['aria-expanded'], false); assert.equal(h.buttons('发布智能体及流程').length, 1);
    const flow = () => h.tree.root.findByType('flow-surface'); const before = clone(flow().props.nodes.map(node => node.data.node));
    await act(async () => { flow().props.onNodeClick({}, flow().props.nodes[1]); await flush(); }); assert.equal(h.buttons('收起属性区')[0].props['aria-expanded'], true);
    await act(async () => { flow().props.onPaneClick(); await flush(); }); assert.equal(h.buttons('展开属性区')[0].props['aria-expanded'], false);
    await h.click('流程设置'); assert.equal(h.buttons('收起属性区')[0].props['aria-expanded'], true);
    assert.deepEqual(clone(flow().props.nodes.map(node => node.data.node)), before); assert(!h.listeners.has('beforeunload'));
  } finally { await h.dispose(); }
});
test('UI recheck: narrow panels use one drawer and preserve canvas columns when editing a node', async () => {
  const h = await harness({ embedded: true, width: 390, height: 844 });
  try {
    assert.equal(h.buttons('展开节点库').length, 1); assert.equal(h.buttons('展开属性区').length, 1);
    await h.click('展开节点库'); assert(h.tree.root.findByProps({ 'data-side': 'library' }).props['data-drawer']);
    const flow = h.tree.root.findByType('flow-surface'); await act(async () => { flow.props.onNodeClick({}, flow.props.nodes[1]); await flush(); });
    assert.equal(h.buttons('展开节点库').length, 1); assert.equal(h.tree.root.findByProps({ 'data-side': 'inspector' }).props['data-drawer'], true);
    const grid = h.tree.root.findByProps({ 'data-compact': true }); assert.equal(grid.props.style['--library-width'], '36px'); assert.equal(grid.props.style['--inspector-width'], '36px');
    await h.click('收起属性区'); assert.equal(h.buttons('展开属性区').length, 1);
  } finally { await h.dispose(); }
});
test('UI recheck: pointer resizing is cleaned up on unmount and does not dirty the workflow', async () => {
  const h = await harness({ embedded: true });
  try {
    const splitter = h.tree.root.findByProps({ 'aria-label': '调整节点库宽度' });
    await act(async () => { splitter.props.onPointerDown({ clientX: 100, preventDefault() {} }); h.listeners.get('pointermove')({ clientX: 145 }); await flush(); });
    assert.equal(h.tree.root.findByProps({ 'aria-label': '调整节点库宽度' }).props['aria-valuenow'], 297); assert(!h.listeners.has('beforeunload'));
  } finally { await h.dispose(); }
  assert(!h.listeners.has('pointermove')); assert(!h.listeners.has('pointerup'));
});
test('UI recheck: delayed leave dialog is singular and cancellation preserves selected raw input', async () => {
  const h = await harness({ embedded: true, manualDialogs: true });
  try {
    const flow = h.tree.root.findByType('flow-surface'); await act(async () => { flow.props.onNodeClick({}, flow.props.nodes[1]); await flush(); });
    const name = h.tree.root.findByProps({ 'aria-label': '节点草稿名称' }); await act(async () => { name.props.onChange({ target: { value: '未保存的节点名称' } }); await flush(); });
    let first; let second; await act(async () => { first = h.leaveGuard()(); second = h.leaveGuard()(); await flush(); });
    assert.equal(second, false); assert.equal(h.modalRecords.filter(record => record.kind === 'confirm').length, 1);
    await act(async () => { h.modalRecords.at(-1).config.onCancel(); await flush(); }); assert.equal(await first, false);
    assert.equal(h.tree.root.findByProps({ 'aria-label': '节点草稿名称' }).props.value, '未保存的节点名称'); assert.equal(h.tree.root.findByType('flow-surface').props.nodes.find(node => node.id === 'answer').selected, true);
    assert(h.listeners.has('beforeunload')); assert.equal(h.nativeConfirms(), 0);
  } finally { await h.dispose(); }
});
async function renameDraft(h) {
  await act(() => h.tree.root.findByProps({'aria-label':'工作流名称'}).props.onChange({target:{value:'Synthetic unsaved draft'}}));
}
test('UI recheck: confirming a delayed dialog routes once even after repeated internal clicks', async () => {
  const h = await harness({ manualDialogs: true });
  try {
    await renameDraft(h);
    let navigation; await act(async () => { navigation = h.buttons('返回AI工作室')[0].props.onClick(); void h.buttons('返回AI工作室')[0].props.onClick(); await flush(); });
    assert.equal(h.modalRecords.filter(record => record.kind === 'confirm').length, 1);
    await act(async () => { h.modalRecords.at(-1).config.onOk(); await flush(); }); await navigation;
    assert.equal(h.calls.filter(call => call[0] === 'back').length, 1); assert.equal(h.nativeConfirms(), 0);
  } finally { await h.dispose(); }
});
test('UI recheck: unmount resolves pending guard false and late dialog confirmation cannot navigate', async () => {
  const h = await harness({ manualDialogs: true }); await renameDraft(h); const pending = h.leaveGuard()(); const dialog = h.modalRecords.at(-1);
  await h.dispose(); assert.equal(await pending, false); assert.equal(h.leaveGuard(), null); assert.equal(dialog.destroyed, true);
  dialog.config.onOk(); assert.equal(h.calls.filter(call => call[0] === 'back').length, 0);
});
test('UI recheck: rename draft is guarded and save dialog prevents stacked leave confirmations', async () => {
  const h = await harness({ embedded: true, manualDialogs: true });
  try {
    await h.click('保存'); await act(async () => { h.tree.root.findByProps({ 'aria-label': '保存工作流名称' }).props.onChange({ target: { value: '仅弹窗中的未存名称' } }); await flush(); });
    assert(h.listeners.has('beforeunload')); assert.equal(await h.leaveGuard()(), false); assert.equal(h.modalRecords.length, 0);
    await h.click('取消'); assert(!h.listeners.has('beforeunload')); assert.equal(h.calls.filter(call => call[0] === 'save').length, 0);
  } finally { await h.dispose(); }
});
test('UI recheck: save failure preserves modal name graph draft and node selection', async () => {
  const options = { embedded: true, saveError: new Error('服务暂不可用，请重试') }; const h = await harness(options);
  try {
    const flow = h.tree.root.findByType('flow-surface'); await act(async () => { flow.props.onNodeClick({}, flow.props.nodes[1]); await flush(); });
    await h.click('保存'); await act(async () => { h.tree.root.findByProps({ 'aria-label': '保存工作流名称' }).props.onChange({ target: { value: '失败后保留此名称' } }); await flush(); });
    await h.click('确认保存'); assert.equal(h.tree.root.findByProps({ 'aria-label': '保存工作流名称' }).props.value, '失败后保留此名称'); assert(h.content().includes('服务暂不可用'));
    assert.equal(h.tree.root.findByType('flow-surface').props.nodes.find(node => node.id === 'answer').selected, true); assert.equal(h.modalRecords.filter(record => record.kind === 'success').length, 0);
    options.saveError = undefined; await h.click('确认保存'); assert.equal(h.tree.root.findAllByProps({ 'aria-label': '保存工作流名称' }).length, 0); assert.equal(h.modalRecords.filter(record => record.kind === 'success').length, 0); assert(h.content().includes('画布已保存'));
  } finally { await h.dispose(); }
});
test('UI recheck: repeated save clicks issue one save and show inline confirmation', async () => {
  const h = await harness({ embedded: true, deferSave: true });
  try {
    await h.click('保存'); await act(async () => { const save = h.buttons('确认保存')[0].props.onClick; void save(); void save(); await flush(); });
    assert.equal(h.saveRequests.length, 1); assert.equal(h.calls.filter(call => call[0] === 'save').length, 1); assert.equal(h.buttons('取消')[0].props.disabled, true);
    await act(async () => { h.saveRequests[0].resolve(); await flush(); }); assert.equal(h.modalRecords.filter(record => record.kind === 'success').length, 0); assert(h.content().includes('画布已保存'));
  } finally { await h.dispose(); }
});
test('UI recheck: first bind failure keeps the saved graph and retries without posting a duplicate copy', async () => {
  const options = { embedded: true, unbound: true, bindError: new Error('关联失败，请重试') }; const h = await harness(options);
  try {
    await h.click('保存'); await h.click('确认保存'); assert.equal(h.buttons('确认保存').length, 1); assert.equal(h.modalRecords.filter(record => record.kind === 'success').length, 0);
    options.bindError = undefined; await h.click('确认保存'); const saves = h.calls.filter(call => call[0] === 'save'); assert.equal(saves.length, 2); assert.equal(saves[0][1].id, undefined); assert.equal(saves[1][1].id, 'saved-workflow');
  } finally { await h.dispose(); }
});
test('UI recheck: stale save completion cannot show success in a different identity', async () => {
  const h = await harness({ embedded: true, deferSave: true });
  try {
    await h.click('保存'); await act(async () => { void h.buttons('确认保存')[0].props.onClick(); await flush(); }); await h.updateIdentity('another-user');
    await act(async () => { h.saveRequests[0].resolve(); await flush(); }); assert.equal(h.modalRecords.filter(record => record.kind === 'success').length, 0); assert.equal(h.tree.root.findAllByProps({ 'aria-label': '保存工作流名称' }).length, 0);
  } finally { await h.dispose(); }
});
test('UI recheck: parent-owned navigation awaits the registered guard without opening a second dialog', async () => {
  let h; const navigations = [];
  h = await harness({ manualDialogs: true, props: { parentGuardsNavigation: true, onNavigate: async page => { if (await h.leaveGuard()()) navigations.push(page); } } });
  try {
    await renameDraft(h); await h.click('▷ 测试运行'); let navigation; await act(async () => { navigation = h.buttons('运行记录 ↗')[0].props.onClick(); await flush(); });
    assert.equal(h.modalRecords.filter(record => record.kind === 'confirm').length, 1);
    await act(async () => { h.modalRecords.at(-1).config.onOk(); await flush(); }); await navigation; assert.deepEqual(navigations, ['runs']); assert.equal(h.modalRecords.filter(record => record.kind === 'confirm').length, 1);
  } finally { await h.dispose(); }
});
test('UI recheck: unsupported fullscreen reports Chinese feedback instead of a client exception', async () => {
  const h = await harness();
  try { await h.click('全屏'); assert(h.content().includes('当前浏览器不支持全屏')); }
  finally { await h.dispose(); }
});
test('UI recheck: unavailable dialog service leaves the draft in place without a client exception', async () => {
  const h = await harness({ modalThrows: true });
  try { await renameDraft(h); await h.click('返回AI工作室'); assert(h.content().includes('暂时无法显示离开提示')); assert.equal(h.calls.filter(call => call[0] === 'back').length, 0); assert.equal(h.tree.root.findAllByType('flow-surface').length, 1); assert.equal(h.nativeConfirms(), 0); }
  finally { await h.dispose(); }
});

for(const status of ['needs_configuration','unavailable'])test(`LLM inspector registry note stays neutral without masking runtime unavailability: ${status}`,async()=>{
 const reason=status==='needs_configuration'?'请选择已配置的本机模型；无模型时测试会明确失败。':'模型执行模块暂不可用';
 const workflow=adapter.newWorkflow();workflow.nodes.push({id:'llm-inspector',type:'llm',config:{model_config_id:'saved-ref'},inputs:{prompt:'任务'},ui:{position:{x:300,y:100}}});
 const h=await harness({embedded:true,workflow,registryOverrides:{llm:{status,reason}},props:{canManageModels:true,onNavigate:()=>{}}});
 try{await act(async()=>{const flow=h.tree.root.findByType('flow-surface');flow.props.onNodeClick({},flow.props.nodes.find(n=>n.id==='llm-inspector'));await flush();});
 const note=h.tree.root.findByProps({className:'readiness'});const content=text(note);assert.equal(note.props['data-state'],status);
 if(status==='needs_configuration'){assert(content.includes('模型以可用目录为准'));assert(!content.includes('本机模型'));assert(!content.includes('需配置'));assert.equal(h.buttons('模型管理 ↗').length,1);}else assert(content.includes(reason));
 }finally{await h.dispose();}
});

test('opening a canvas does not wait for unrelated resource catalogs and reuses the current authorized owner read',async()=>{
 const h=await harness({embedded:true,deferResources:true,props:{initialAgent:{id:'agent-a',name:'synthetic-agent',agent_type:'advanced',main_workflow_id:'linked-flow'}}});
 try{assert(h.tree.root.findByType('flow-surface'));assert(h.content().includes('资源目录正在加载'));assert.equal(h.calls.filter(c=>c[0]==='owner').length,0);assert.equal(h.calls.filter(c=>c[0]==='get').length,1);}finally{await h.dispose();}
});

test('new standalone workflow opens directly and executes without creating or binding an Agent',async()=>{
 const h=await harness({props:{createOnOpen:true}});
 try{assert.equal(h.tree.root.findByType('flow-surface').props.nodes.length,2);assert.equal(h.buttons('返回流程列表').length,0);await h.click('▷ 测试运行');assert.equal(h.calls.filter(c=>c[0]==='run').length,1);assert.equal(h.calls.filter(c=>['bind','runAgent','publish'].includes(c[0])).length,0);}finally{await h.dispose();}
});


test('failed standalone read is explicitly retryable and does not submit a save or run',async()=>{
 const options={getError:Error('暂时读取失败'),props:{workflowId:'wf-independent'}};const h=await harness(options);
 try{assert(h.content().includes('暂时无法打开工作流'));assert.equal(h.tree.root.findAllByType('flow-surface').length,0);assert.equal(h.calls.filter(c=>['save','run','bind','runAgent'].includes(c[0])).length,0);options.getError=undefined;await h.click('重新读取工作流');assert.equal(h.tree.root.findAllByType('flow-surface').length,1);assert.equal(h.calls.filter(c=>c[0]==='get').length,2);}finally{await h.dispose();}
});

// Real Studio guards and props; dialog/API behavior is tested separately.
test('file import checks the real dirty guard before creation and opens a standalone draft without rebinding an Agent',async()=>{
 const imported=[];const h=await harness({embedded:true,props:{onWorkflowImported:value=>imported.push(value)}});
 try{await h.click('导入');let tools=h.tree.root.findByType('workflow-file-tools');assert.equal(tools.props.mode,'import');
 await act(()=>h.tree.root.findByProps({'aria-label':'工作流名称'}).props.onChange({target:{value:'脏稿保留'}}));h.confirm(false);let allowed;await act(async()=>{allowed=await tools.props.beforeCreate();await flush();});assert.equal(allowed,false);assert.equal(h.tree.root.findByProps({'aria-label':'工作流名称'}).props.value,'脏稿保留');
 h.confirm(true);await act(async()=>{allowed=await tools.props.beforeCreate();await flush();});assert.equal(allowed,true);tools=h.tree.root.findByType('workflow-file-tools');tools.props.onCreatingChange(true);await act(async()=>{assert.equal(await h.leaveGuard()(),false);});tools.props.onCreatingChange(false);
 await act(async()=>{await tools.props.onCreated({id:'new-import',name:'新独立草稿',nodes:[],edges:[]});await flush();});assert.equal(imported[0].id,'new-import');assert.equal(h.calls.filter(c=>['save','bind','runAgent','publish'].includes(c[0])).length,0);assert.equal(h.tree.root.findByProps({'aria-label':'工作流名称'}).props.value,'脏稿保留');
 }finally{await h.dispose();}
});


// 2026-10-26 draft/trace integration. Controlled API, never a real provider call.
const draftIdentity={kind:'draft',graph_sha256:'1234567890abcdef',revision:'synthetic-revision'};
test('draft debugging freezes current unsaved graph and preserves saved binding',async()=>{
 const h=await harness({embedded:true,workflow:{...adapter.newWorkflow(),ui:{}}});try{
 await act(()=>h.tree.root.findByProps({'aria-label':'工作流名称'}).props.onChange({target:{value:'unsaved synthetic graph'}}));await h.click('测试输入');await act(()=>h.tree.root.findByType('message-composer').props.onChange({type:'text',text:'synthetic draft',attachments:[]}));await h.click('保存并测试');
 const draft=h.calls.find(c=>c[0]==='debugRun')[1];assert.equal(draft.workflow.name,'unsaved synthetic graph');assert.equal(draft.agent_id,'agent-a');assert.equal(h.calls.filter(c=>['save','bind','runAgent'].includes(c[0])).length,0);
 await act(()=>h.tree.root.findByProps({'aria-label':'工作流名称'}).props.onChange({target:{value:'next edit'}}));assert.equal(draft.workflow.name,'unsaved synthetic graph');
 }finally{await h.dispose();}
});
test('blank presentation falls back to typed final values including zero false and empty text',async()=>{
 const h=await harness();try{await h.click('▷ 测试运行');await h.complete('run-1',{id:'run-1',status:'succeeded',execution_identity:draftIdentity,presentations:[{node_id:'answer',kind:'auto'}],outputs:{zero:0,no:false,empty:''}});
 assert(h.content().includes('否（false）'));assert(h.content().includes('空文本（已返回）'));assert(h.content().includes('zero'));assert(!h.content().includes('没有可交付结果'));assert(h.calls.some(c=>c[0]==='trace'));
 const viewer=h.tree.root.findByProps({className:'runOutput'});assert(viewer.findAllByType('pre').every(pre=>{let parent=pre.parent;while(parent&&parent!==viewer){if(parent.type==='details')return true;parent=parent.parent;}return false;}));
 }finally{await h.dispose();}
});
test('success without final output is visibly incomplete instead of placeholder success',async()=>{
 const h=await harness();try{await h.click('▷ 测试运行');await h.complete('run-1',{id:'run-1',status:'succeeded',outputs:{},presentations:[{kind:'auto'}]});assert(h.content().includes('没有可交付结果'));}finally{await h.dispose();}
});
test('trace shows actual inputs outputs Chinese error and explicitly provided reasoning separately',async()=>{
 const trace={node_id:'answer',node_type:'llm',status:'failed',inputs:{prompt:'synthetic actual input'},outputs:{text:'synthetic answer'},error:{code:'SYNTHETIC_ERROR',message:'合成节点执行失败',request_id:'safe-request'},reasoning:{available:true,text:'公开摘要示例',kind:'provider_summary',source:'provider.message.reasoning_summary'}};
 const h=await harness({traces:[trace]});try{await h.click('▷ 测试运行');await h.complete('run-1',{id:'run-1',status:'failed',execution_identity:draftIdentity,outputs:{}});await act(()=>h.tree.root.findByProps({'aria-label':'本次节点执行进度'}).findAllByType('button').find(button=>button.props['data-node-id']==='answer').props.onClick());assert(h.content().includes('synthetic actual input'));assert(h.content().includes('synthetic answer'));assert(h.content().includes('合成节点执行失败'));assert(h.content().includes('safe-request'));assert(h.content().includes('公开摘要示例'));assert(h.content().includes('provider.message.reasoning_summary'));
 const reasoning=h.tree.root.findAllByType('details').find(item=>text(item).includes('供应商摘要'));assert.notEqual(reasoning.props.open,true);
 }finally{await h.dispose();}
});
test('trace pagination follows cursor and rejects a page from another run',async()=>{
 const options={tracePages:[{run_id:'run-1',items:[],last_sequence:5,has_more:true},{run_id:'other-run',items:[{node_id:'answer',node_type:'llm',status:'succeeded',outputs:{text:'must not appear'}}],last_sequence:8,has_more:false}]};const h=await harness(options);try{
 await h.click('▷ 测试运行');await h.complete('run-1',{id:'run-1',status:'succeeded',execution_identity:draftIdentity,outputs:{text:'do not retain on invalid read'}});assert.deepEqual(h.calls.filter(c=>c[0]==='trace').map(c=>c[2]),[0,5]);assert(h.content().includes('节点详情分页不完整'));assert(!h.content().includes('must not appear'));assert(!h.content().includes('do not retain on invalid read'));
 }finally{await h.dispose();}
});
test('trace failure clears visible cached results and controls',async()=>{
 const options={traces:[]};const h=await harness(options);try{await h.click('▷ 测试运行');await h.complete('run-1',{id:'run-1',status:'running',execution_identity:draftIdentity,outputs:{text:'private cached value'}});assert(h.content().includes('private cached value'));options.traceError=Error('无权读取本次运行');await h.runTimers();await h.complete('run-1',{id:'run-1',status:'running',execution_identity:draftIdentity,outputs:{text:'private cached value'}});assert(!h.content().includes('private cached value'));assert(h.content().includes('无权读取本次运行'));assert.equal(h.buttons('取消运行').length,0);}finally{await h.dispose();}
});
test('late trace response after identity change cannot populate the new editor',async()=>{
 const h=await harness({deferTrace:true});try{await h.click('▷ 测试运行');await h.complete('run-1',{id:'run-1',status:'succeeded',execution_identity:draftIdentity,outputs:{text:'private final'}});assert.equal(h.tracePending.length,1);await h.updateIdentity('actor-new');await act(async()=>{h.tracePending[0].resolve({run_id:'run-1',items:[{node_id:'answer',node_type:'llm',status:'succeeded',outputs:{text:'private trace'}}],last_sequence:1,has_more:false});await flush();});assert(!h.content().includes('private final'));assert(!h.content().includes('private trace'));}finally{await h.dispose();}
});
test('node test submits only explicit data pools and current node ID without running upstream in UI',async()=>{
 const h=await harness();try{const flow=h.tree.root.findByType('flow-surface');await act(()=>flow.props.onNodeClick({},flow.props.nodes.find(n=>n.id==='answer')));await h.click('▷ 测试此节点');const dialog=h.tree.root.findByType('node-test-dialog');await act(async()=>{await dialog.props.onTest({message:'synthetic test'},{nodes:{},vars:{}},{workflow:clone(dialog.props.workflow),description:{source_graph_sha256:'a'.repeat(64),target_key:'root/answer',node_id:'answer',node_type:'answer',scope_path:[],scope_type:null,workflow_input_schema:{},input_schema:{},output_schema:{},upstream_schemas:{},variables_schema:{type:'object',properties:{}},scope_schema:null,limits:{max_steps:40,timeout_seconds:5,max_concurrency:1}}});await flush();});const draft=h.calls.find(c=>c[0]==='debugRun')[1];assert.equal(draft.node_test.node_id,'answer');assert.deepEqual(draft.node_test.variables,{nodes:{},vars:{}});assert.equal(h.calls.filter(c=>['save','bind','runAgent'].includes(c[0])).length,0);assert.equal(h.calls.filter(c=>c[0]==='debugRun').length,1);
 }finally{await h.dispose();}
});
test('read-only identity variables are offered without writable system/user form fields',()=>{
 const choices=adapter.availableVariables(adapter.newWorkflow(),'answer',null,['system.run_id','system.tenant_id','system.workspace_id','user.id','user.role'].map(ref=>({ref,label:ref,type:'string',readonly:true})));for(const ref of ['system.run_id','system.tenant_id','system.workspace_id','user.id','user.role'])assert(choices.some(v=>v.ref===ref&&v.readonly));
 const node={id:'n',type:'template',config:{},inputs:{a:{$ref:'user.role'},b:{$ref:'system.run_id'},c:{$ref:'nodes.up.text'},d:{$ref:'vars.count'}}};const refs=debugInputs.testReferences(node,choices);assert.deepEqual(clone(refs.map(v=>v.ref)),['nodes.up.text','vars.count']);
 const pools=debugInputs.testVariablePools(refs,{'nodes.up.text':'','vars.count':0});assert.deepEqual(clone(pools),{nodes:{up:{text:''}},vars:{count:0}});assert.throws(()=>debugInputs.testVariablePools([{ref:'user.role',label:'role',type:'string'}],{'user.role':'admin'}),/不合法/);
});

 test('a separate presentation never hides other terminal output fields',async()=>{const h=await harness();try{await h.click('▷ 测试运行');await h.complete('run-1',{id:'run-1',status:'succeeded',presentations:[{node_id:'table-out',kind:'table',table:{columns:['amount'],rows:[[0]]}}],outputs:{text:'terminal answer without a presentation',table:{columns:['amount'],rows:[[0]]},flag:false}});assert(h.content().includes('terminal answer without a presentation'));assert(h.content().includes('否（false）'));assert.equal(h.tree.root.findAllByType('table').length,1);}finally{await h.dispose();}});
