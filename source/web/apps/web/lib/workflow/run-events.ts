import type { RunEventPage, RunExecutionState, StudioEvent, StudioRun, WorkflowDefinition } from './types';

const pending = new Set(['queued', 'running', 'paused', 'waiting_approval', 'resuming', 'outcome_unknown']);
/** Frozen product states; no SDK-native state or implicit resume decision. */
export const isPendingRun = (run: StudioRun | null) => Boolean(run && (typeof run.capabilities?.is_terminal === 'boolean' ? !run.capabilities.is_terminal : run.engine?.name === 'langgraph' ? true : pending.has(run.status)));
export const runPollDelay = (run: StudioRun) => run.capabilities ? run.capabilities.is_terminal ? 0 : run.capabilities.is_active ? 1100 : 8000 : run.engine?.name === 'langgraph' ? 8000 : ['queued', 'running', 'resuming'].includes(run.status) ? 1100 : pending.has(run.status) ? 8000 : 0;
export function canRunAction(run: StudioRun | null, action: 'cancel' | 'resume') {
  if (!run || run.view_unavailable) return false;
  if (run.capabilities) return !run.capabilities.is_terminal && run.capabilities[action === 'cancel' ? 'can_cancel' : 'can_resume'] === true;
  return !run.engine && action === 'cancel' && ['queued', 'running'].includes(run.status);
}

export function consistentProjection(run: StudioRun, state: RunExecutionState) {
  if (run.last_sequence !== state.last_sequence) return true;
  if (run.status !== state.status) return false;
  return !run.capabilities || (['is_terminal', 'is_active', 'can_cancel', 'can_resume', 'can_approve', 'can_reconcile'] as const).every(key => run.capabilities?.[key] === state.capabilities[key]);
}

/** Scope separators remain product protocol, never a change to graph connections. */
export function eventBelongsToNode(event: StudioEvent, id: string) {
  if (!event.node_id) return false;
  if (event.node_id === id || event.node_id.startsWith(`${id}.`)) return true;
  const suffix = event.node_id.slice(id.length);
  return event.node_id.startsWith(id) && /^\[\d+\]\./.test(suffix);
}

export function appendEventPage(previous: StudioEvent[], page: RunEventPage, runId: string, after: number) {
  const items = orderedRunEvents(page.items, runId).filter(event => event.sequence > after);
  let expected = after + 1;
  let gap = page.has_more === true;
  items.forEach(event => { if (event.sequence !== expected) gap = true; expected = event.sequence + 1; });
  const last = Number.isSafeInteger(page.last_sequence) ? page.last_sequence! : expected - 1;
  if (last !== expected - 1 || last < after) gap = true;
  return { items: orderedRunEvents([...previous, ...items], runId), after: last, gap };
}

/** One authoritative event snapshot. Never append output or artifacts from replayed events. */
export function orderedRunEvents(events: StudioEvent[], runId?: string): StudioEvent[] {
  const bySequence = new Map<number, StudioEvent>();
  events.forEach(event => {
    if (!Number.isSafeInteger(event.sequence) || event.sequence < 0) return;
    if (runId && event.run_id && event.run_id !== runId) return;
    // Replayed immutable sequences occupy one row, including repeats within a response.
    if (!bySequence.has(event.sequence)) bySequence.set(event.sequence, event);
  });
  return [...bySequence.values()].sort((left, right) => left.sequence - right.sequence);
}

/** IDs are opaque product IDs, including scoped legacy IDs. Never strip a loop prefix. */
export function eventNodeStates(events: StudioEvent[]): Record<string, string> {
  const states: Record<string, string> = {};
  orderedRunEvents(events).forEach(event => {
    if (event.node_id && event.type.startsWith('node.')) states[event.node_id] = event.status || event.type.slice(5) || 'idle';
    if (event.node_id && event.type === 'approval.requested') states[event.node_id] = 'waiting_approval';
    if (event.node_id && event.type === 'effect.unknown') states[event.node_id] = 'outcome_unknown';
  });
  return states;
}

export function topLevelProgress(workflow: WorkflowDefinition, states: Record<string, string>) {
  const counts = { succeeded: 0, skipped: 0, failed: 0, total: workflow.nodes.length };
  workflow.nodes.forEach(node => {
    const status = states[node.id];
    if (status === 'completed' || status === 'succeeded') counts.succeeded++;
    else if (status === 'skipped') counts.skipped++;
    else if (status === 'failed') counts.failed++;
  });
  return counts;
}
