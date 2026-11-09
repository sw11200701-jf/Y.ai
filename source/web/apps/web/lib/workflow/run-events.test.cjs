const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const source = ts.transpileModule(fs.readFileSync(`${__dirname}/run-events.ts`, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const sandbox = { exports: {} }; vm.runInNewContext(source, sandbox);
const { orderedRunEvents, eventNodeStates, topLevelProgress, isPendingRun, runPollDelay, canRunAction, eventBelongsToNode, appendEventPage } = sandbox.exports;
const plain = value => JSON.parse(JSON.stringify(value));
test('legacy event replay sorts immutable sequence and keeps one row per event without mutating outputs', () => {
  const completed = { run_id: 'run-one', sequence: 4, type: 'node.completed', node_id: 'output', status: 'succeeded', data: { artifact_ids: ['artifact-fixed'], text: 'one output' } };
  const original = [{ sequence: 2, type: 'node.started', node_id: 'output', status: 'running' }, completed, { ...completed }, { sequence: 1, type: 'run.started', status: 'running' }];
  const before = JSON.stringify(original);
  const first = orderedRunEvents(original, 'run-one'); const replayed = orderedRunEvents([...original, ...first], 'run-one');
  assert.deepEqual(plain(replayed.map(event => event.sequence)), [1, 2, 4]); assert.equal(JSON.stringify(original), before);
  assert.deepEqual(plain(replayed.at(-1).data.artifact_ids), ['artifact-fixed']); assert.equal(replayed.filter(event => event.type === 'node.completed').length, 1);
});
test('foreign-run events cannot populate state while legacy events without run_id remain readable', () => {
  const events = orderedRunEvents([{ run_id: 'other', sequence: 4, type: 'node.completed', node_id: 'private', status: 'succeeded' }, { sequence: 1, type: 'node.skipped', node_id: 'legacy', status: 'skipped' }, { sequence: -1, type: 'node.failed', node_id: 'invalid' }], 'current');
  assert.deepEqual(plain(eventNodeStates(events)), { legacy: 'skipped' });
});
test('scoped loop node IDs stay distinct and do not complete their top-level parent or rewrite skipped joins', () => {
  const events = [{ sequence: 8, type: 'node.completed', node_id: 'loop.1.step', status: 'succeeded' }, { sequence: 3, type: 'node.started', node_id: 'loop', status: 'running' }, { sequence: 5, type: 'node.skipped', node_id: 'other', status: 'skipped' }, { sequence: 6, type: 'node.started', node_id: 'loop.0.step', status: 'running' }, { sequence: 7, type: 'node.completed', node_id: 'loop.0.step', status: 'succeeded' }];
  const states = eventNodeStates(events); assert.equal(states.loop, 'running'); assert.equal(states['loop.0.step'], 'succeeded'); assert.equal(states['loop.1.step'], 'succeeded'); assert.equal(states.other, 'skipped');
  assert.deepEqual(plain(topLevelProgress({ nodes: [{ id: 'loop' }, { id: 'other' }, { id: 'join' }] }, states)), { succeeded: 0, skipped: 1, failed: 0, total: 3 });
});
test('confirmed pause and recovery states poll for updates but never imply automatic resubmission', () => {
  for (const status of ['paused', 'waiting_approval', 'outcome_unknown']) { assert(isPendingRun({ status })); assert.equal(runPollDelay({ status }), 8000); }
  assert.equal(runPollDelay({ status: 'resuming' }), 1100);
  for (const status of ['succeeded', 'failed', 'cancelled', 'interrupted']) { assert.equal(isPendingRun({ status }), false); assert.equal(runPollDelay({ status }), 0); }
});
test('server capabilities govern controls and terminal polling even when a status enum differs', () => {
  const run = { status: 'running', engine: { name: 'langgraph' }, capabilities: { is_terminal: true, is_active: false, can_cancel: false, can_resume: false } };
  assert.equal(isPendingRun(run), false); assert.equal(runPollDelay(run), 0); assert.equal(canRunAction(run, 'cancel'), false);
  assert.equal(canRunAction({ status: 'paused', engine: { name: 'langgraph' } }, 'resume'), false);
  assert.equal(canRunAction({ ...run, capabilities: { ...run.capabilities, is_terminal: false, can_resume: true } }, 'resume'), true);
  assert.equal(canRunAction({ status: 'interrupted' }, 'resume'), false);
});
test('projected legacy engine records stay readable and interrupted records never gain resume', () => {
  const run = { status: 'interrupted', engine: { name: 'legacy' } };
  assert.equal(isPendingRun(run), false); assert.equal(runPollDelay(run), 0); assert.equal(canRunAction(run, 'resume'), false);
  assert.equal(canRunAction({ ...run, status: 'running' }, 'cancel'), false);
  assert.equal(canRunAction({ status: 'running' }, 'cancel'), true);
});
test('scoped event filtering includes product loop indexes without matching similarly named nodes', () => {
  assert(eventBelongsToNode({ node_id: 'loop[0].step' }, 'loop')); assert(eventBelongsToNode({ node_id: 'loop[2].inner[3].step' }, 'loop'));
  assert(eventBelongsToNode({ node_id: 'loop.legacy' }, 'loop')); assert(!eventBelongsToNode({ node_id: 'loopExtra[0].step' }, 'loop')); assert(!eventBelongsToNode({ node_id: 'loop[invalid].step' }, 'loop'));
});
test('incremental event pages de-duplicate replay and flag sequence gaps for a complete reread', () => {
  const initial = [{ sequence: 1, type: 'run.queued', run_id: 'r' }];
  const page = appendEventPage(initial, { items: [{ sequence: 1, type: 'run.queued', run_id: 'r' }, { sequence: 2, type: 'run.started', run_id: 'r' }, { sequence: 2, type: 'run.started', run_id: 'r' }], last_sequence: 2, has_more: false }, 'r', 1);
  assert.equal(page.gap, false); assert.equal(page.after, 2); assert.equal(page.items.length, 2);
  assert.equal(appendEventPage(page.items, { items: [{ sequence: 4, type: 'node.completed' }], last_sequence: 4 }, 'r', 2).gap, true);
  assert.equal(appendEventPage(page.items, { items: [], last_sequence: 3 }, 'r', 2).gap, true);
});
test('approval and unknown effect mark only their product scoped node until a later node event settles it', () => {
  const events = [{ sequence: 1, type: 'node.started', node_id: 'loop.0.approve', status: 'running' }, { sequence: 2, type: 'approval.requested', node_id: 'loop.0.approve' }, { sequence: 3, type: 'effect.unknown', node_id: 'loop.1.tool' }];
  assert.deepEqual(plain(eventNodeStates(events)), { 'loop.0.approve': 'waiting_approval', 'loop.1.tool': 'outcome_unknown' });
  const settled = eventNodeStates([...events, { sequence: 4, type: 'node.completed', node_id: 'loop.0.approve', status: 'succeeded' }]);
  assert.equal(settled['loop.0.approve'], 'succeeded'); assert.equal(settled['loop.1.tool'], 'outcome_unknown');
});
