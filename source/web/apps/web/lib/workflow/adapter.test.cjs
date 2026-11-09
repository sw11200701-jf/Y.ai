const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const compiled = ts.transpileModule(fs.readFileSync(`${__dirname}/adapter.ts`, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const labelSandbox = { exports: {} }; vm.runInNewContext(ts.transpileModule(fs.readFileSync(`${__dirname}/labels.ts`, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, labelSandbox);
const sandbox = { exports: {}, require: id => id === './labels' ? labelSandbox.exports : require(id), crypto: require('node:crypto').webcrypto };
vm.runInNewContext(compiled, sandbox);
const a = sandbox.exports;
const normalize = v => JSON.parse(JSON.stringify(v));
const registry = { version: '2.0.0', items: [{ type: 'template', output_schema: { properties: { text: { type: 'string' } } }, ports: ['default'] }, { type: 'plugin', config_schema: { properties: { binding: {}, arguments: {} }, additionalProperties: false } }], limits: {} };
test('legacy copy retains original and unknown fields and binds nearest prior fact producer', () => {
  const original = { id: 'existing', name: 'old', version: '1.0.0', mode: 'demo', custom: { keep: true }, nodes: [
    { id: 's', type: 'start', config: {} }, { id: 'p', type: 'plugin', config: { binding: { plugin_version_id: 'v1', tool_id: 't', connection_id: 'c' }, ui_position: { x: 30, y: 50 }, extra: 'preserve' } }, { id: 'q', type: 'query', config: {} }, { id: 'c', type: 'calculate', config: {} }, { id: 'r', type: 'report', config: {} }, { id: 'e', type: 'end', config: {} },
  ], edges: [{ source: 's', target: 'p', extra: 4 }, { source: 'p', target: 'q' }, { source: 'q', target: 'c' }, { source: 'c', target: 'r' }, { source: 'r', target: 'e' }] };
  const before = JSON.stringify(original); const next = a.convertLegacy(original, registry);
  assert.equal(JSON.stringify(original), before); assert.equal(next.id, undefined); assert.equal(next.schemaVersion, '2.0');
  assert.equal(next.nodes[3].inputs.facts.$ref, 'nodes.q.facts'); assert.equal(next.nodes[4].inputs.dataset.$ref, 'nodes.c.dataset');
  assert.deepEqual(normalize(next.nodes[1].config.binding), original.nodes[1].config.binding);
  assert.deepEqual(normalize(next.nodes[1].ui.legacy_config), original.nodes[1].config); assert.deepEqual(normalize(next.nodes[1].ui.position), { x: 30, y: 50 });
  assert.equal(next.nodes[1].config.ui_position, undefined); assert.equal(next.nodes[1].inputs.arguments.entity.$ref, 'nodes.s.query.entity');
  assert.equal(next.nodes[2].inputs.query.$ref, 'nodes.s.query'); assert.equal(next.edges[0].extra, 4); assert.equal(next.custom.keep, true);
});
test('connection guard rejects cycles, terminal outputs, duplicate ports and non-aggregate joins', () => {
  const w = a.newWorkflow(); w.nodes.splice(1, 0, { id: 't', type: 'template', config: {} }); w.edges = [{ source: 'start', target: 't' }, { source: 't', target: 'answer' }];
  assert.match(a.connectionError(w, { source: 'answer', target: 't' }, registry), /端口/);
  assert.match(a.connectionError(w, { source: 't', target: 'start' }, registry), /开始/);
  assert.match(a.connectionError(w, { source: 't', target: 'answer' }, registry), /已有连线/);
  const cyclic = { ...w, nodes: [...w.nodes, { id: 'later', type: 'template', config: {} }], edges: [...w.edges, { source: 'answer', target: 'later' }] };
  assert.match(a.connectionError(cyclic, { source: 'later', target: 't' }, registry), /环路/);
});
test('IF ports preserve true/false and classifier derives exact registered class IDs', () => {
  assert.deepEqual(normalize(a.outputPorts({ type: 'if_else', config: {} })), ['true', 'false']);
  assert.deepEqual(normalize(a.outputPorts({ type: 'question_classifier', config: { classes: [{ id: 'revenue', label: '收入', keywords: ['收入'] }] } })), ['revenue', 'else']);
  assert.deepEqual(normalize(a.outputPorts({ type: 'answer', config: {} })), []);
});
test('variable choices include ancestors only, declared types and nested output paths', () => {
  const w = a.newWorkflow(); w.nodes.splice(1, 0, { id: 't', type: 'template', config: {} }, { id: 'unconnected', type: 'template', config: {} });
  w.edges = [{ source: 'start', target: 't' }, { source: 't', target: 'answer' }]; w.variables = { amount: { type: 'string', default: '1234567890123456789.12' } };
  const variables = a.availableVariables(w, 'answer', registry);
  assert(variables.some(v => v.ref === 'nodes.t.text' && v.type === 'string'));
  assert(variables.some(v => v.ref === 'vars.amount' && v.type === 'string'));
  assert(!variables.some(v => v.ref.startsWith('nodes.unconnected')));
  assert(!variables.some(v => v.ref.startsWith('nodes.answer')));
});
test('position adapter reads old layout and new UI position without mutating config', () => {
  const old = { config: { ui_position: { x: 321, y: 456 } } }; const before = JSON.stringify(old);
  assert.deepEqual(normalize(a.positionOf(old, 0)), { x: 321, y: 456 }); assert.equal(JSON.stringify(old), before);
  assert.deepEqual(normalize(a.positionOf({ ...old, ui: { position: { x: 11, y: 22 } } }, 0)), { x: 11, y: 22 });
});
test('new graph has explicit DSL version and exact reference object', () => {
  const w = a.newWorkflow(); assert.equal(w.schemaVersion, '2.0'); assert.equal(w.version, '1.0.0');
  assert.deepEqual(normalize(w.nodes[1].inputs.text), { $ref: 'inputs.message' }); assert.equal(w.edges[0].source_port, 'default');
});
test('edge identity remains stable when node and incident edges are deleted together', () => {
  const edges = [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }, { source: 'c', target: 'd' }];
  const removedKeys = new Set(edges.filter(e => e.target === 'b' || e.source === 'b').map(a.edgeKey));
  const afterNodeDeletion = edges.filter(e => e.target !== 'b' && e.source !== 'b');
  assert.deepEqual(afterNodeDeletion.filter(e => !removedKeys.has(a.edgeKey(e))), [{ source: 'c', target: 'd' }]);
  assert.notEqual(a.edgeKey({ source: 'a', target: 'b', source_port: 'true' }), a.edgeKey({ source: 'a', target: 'b', source_port: 'false' }));
});
test('branch-dependent variable choices are optional while dominating upstream outputs are required', () => {
  const w = a.newWorkflow(); w.nodes = ['start', 'decision', 'yes', 'no', 'merge', 'answer'].map(id => ({ id, type: id === 'start' ? 'start' : id === 'answer' ? 'answer' : 'template', config: {} }));
  w.edges = [{ source: 'start', target: 'decision' }, { source: 'decision', target: 'yes' }, { source: 'decision', target: 'no' }, { source: 'yes', target: 'merge' }, { source: 'no', target: 'merge' }, { source: 'merge', target: 'answer' }];
  const choices = a.availableVariables(w, 'merge', registry);
  assert.equal(choices.find(v => v.ref === 'nodes.yes.text').optional, true);
  assert.equal(choices.find(v => v.ref === 'nodes.no.text').optional, true);
  assert.equal(choices.find(v => v.ref === 'nodes.decision.text').optional, false);
  assert(choices.some(v => v.ref === 'nodes.decision' && v.type === 'object'));
});
test('new Agent canvas uses exact message trigger and visible output while old defaults remain compatible', () => {
  const catalog = { ...registry, items: [...registry.items, { type: 'trigger_text', status: 'supported', category: 'trigger' }, { type: 'output', status: 'supported' }] };
  const workflow = a.newAgentWorkflow(catalog); assert.equal(workflow.nodes[0].type, 'trigger_text'); assert.equal(workflow.nodes[1].type, 'output'); assert.equal(workflow.nodes[1].inputs.text.$ref, 'nodes.message_text.message.text');
  assert.equal(a.newWorkflow().nodes[0].type, 'start'); assert.throws(() => a.newAgentWorkflow(registry), /尚未就绪/);
});
test('typed entries may coexist once per type, cannot mix legacy roots or accept input edges', () => {
  const workflow = { ...a.newWorkflow(), nodes: [{ id: 't', type: 'trigger_text', config: {} }, { id: 'o', type: 'output', config: {} }], edges: [] };
  assert.equal(a.entryConflict(workflow, { type: 'trigger_image' }), null);
  assert.match(a.entryConflict(workflow, { type: 'trigger_text' }), /只能有一个/);
  assert.match(a.entryConflict(workflow, { type: 'start' }), /不能.*混用/);
  assert.match(a.entryConflict(a.newWorkflow(), { type: 'trigger_audio' }), /不能.*混用/);
  assert.match(a.connectionError(workflow, { source: 'o', target: 't' }, registry), /不能有输入/);
});
test('multi-entry variable pool references are branch-optional and expose controlled message metadata', () => {
  const workflow = { ...a.newWorkflow(), nodes: [{ id: 't', type: 'trigger_text', config: {} }, { id: 'i', type: 'trigger_image', config: {} }, { id: 'm', type: 'aggregate', config: {} }], edges: [{ source: 't', target: 'm' }, { source: 'i', target: 'm' }] };
  const choices = a.availableVariables(workflow, 'm', registry);
  assert.equal(choices.find(v => v.ref === 'nodes.t.message.text').optional, true);
  assert.equal(choices.find(v => v.ref === 'nodes.i.message.attachment_ids').type, 'array');
  assert(!choices.some(v => /\.path|\.base64|\.token/.test(v.ref)));
});
test('knowledge variables expose real slice text and source with empty-hit optionality and no duplicate references', () => {
  const workflow = { ...a.newWorkflow(), nodes: [{ id: 'start', type: 'start', config: {} }, { id: 'knowledge', type: 'knowledge_retrieval', config: {} }, { id: 'reply', type: 'output', config: {} }], edges: [{ source: 'start', target: 'knowledge' }, { source: 'knowledge', target: 'reply' }] };
  const choices = a.availableVariables(workflow, 'reply', { ...registry, items: [{ type: 'knowledge_retrieval', output_schema: { properties: { hits: { type: 'array' }, used_versions: { type: 'array' } } } }] });
  assert.equal(choices.find(v => v.ref === 'nodes.knowledge.hits.0.text').optional, true);
  assert.equal(choices.find(v => v.ref === 'nodes.knowledge.hits.0.source_ref').type, 'string');
  assert.equal(choices.find(v => v.ref === 'nodes.knowledge.used_chunks').type, 'array');
  assert.equal(choices.filter(v => v.ref === 'nodes.knowledge.used_versions').length, 1);
});
test('database variables expose rows columns version and policy only from connected upstream query nodes', () => {
  const workflow = { ...a.newWorkflow(), nodes: [{ id: 'start', type: 'start', config: {} }, { id: 'db', type: 'database_query', config: {} }, { id: 'unused', type: 'database_query', config: {} }, { id: 'reply', type: 'output', config: {} }], edges: [{ source: 'start', target: 'db' }, { source: 'db', target: 'reply' }] };
  const choices = a.availableVariables(workflow, 'reply', registry);
  for (const key of ['rows', 'columns', 'version_id', 'policy_version', 'source_refs']) assert(choices.some(v => v.ref === `nodes.db.${key}`));
  assert(!choices.some(v => v.ref.startsWith('nodes.unused')));
});
