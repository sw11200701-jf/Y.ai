import type { DebugTarget, ExecutionIdentity, ObjectValue, TestVariables, WorkflowDefinition } from './types';

export const sameScope = (left: string[] = [], right: string[] = []) => Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((id, index) => id === right[index]);
/** Resolve editor-owned static body IDs only. Never parse a dynamic execution key. */
export function scopeWorkflow(root: WorkflowDefinition, path: string[]): WorkflowDefinition {
  if (!Array.isArray(path) || path.length > 3) throw new Error('子流程路径超过支持范围');
  let current = root;
  for (const id of path) {
    const owner = current.nodes.find(node => node.id === id);
    const body = owner?.config.body;
    if (!owner || !['iteration', 'loop'].includes(owner.type) || !body || body.schemaVersion !== '2.0' || !Array.isArray(body.nodes) || !Array.isArray(body.edges)) throw new Error('当前草稿中没有这条子流程路径，请重新选择节点');
    current = body;
  }
  return current;
}
export function checkDescription(description: DebugTarget, root: WorkflowDefinition, nodeId: string, path: string[]) {
  const current = scopeWorkflow(root, path); const node = current.nodes.find(node => node.id === nodeId);
  if (!node || !description || description.node_id !== nodeId || description.node_type !== node.type || !Array.isArray(description.scope_path) || !sameScope(description.scope_path, path) || !/^[a-f0-9]{64}$/.test(description.source_graph_sha256) || typeof description.target_key !== 'string' || !description.target_key || !description.upstream_schemas || !description.variables_schema || !description.workflow_input_schema || !description.limits || !['max_steps', 'timeout_seconds', 'max_concurrency'].every(key => Number.isFinite(description.limits[key as keyof typeof description.limits]) && description.limits[key as keyof typeof description.limits] > 0) || Object.keys(description.upstream_schemas).some(id => !current.nodes.some(node => node.id === id))) throw new Error('服务端测试目标与当前草稿不一致，请重新检查');
  const ownerType = path.length ? scopeWorkflow(root, path.slice(0, -1)).nodes.find(node => node.id === path[path.length - 1])?.type : null;
  if (description.scope_type !== ownerType || Boolean(description.scope_schema) !== Boolean(ownerType)) throw new Error('测试作用域描述不一致，请重新检查');
  return description;
}
export function scopedTestVariables(description: DebugTarget, nodes: ObjectValue, vars: ObjectValue, scope: ObjectValue): TestVariables {
  if (Object.keys(nodes).some(id => !Object.hasOwn(description.upstream_schemas, id)) || Object.keys(vars).some(name => !Object.hasOwn(description.variables_schema.properties || {}, name))) throw new Error('只能填写当前节点所需的同层上游输出和本层全局变量');
  const result: TestVariables = { nodes, vars };
  if (!description.scope_type) { if (Object.keys(scope).length) throw new Error('根节点测试不接受循环业务上下文'); return result; }
  const index = scope.index;
  const schema = description.scope_schema!;
  if (!Number.isSafeInteger(index) || index < (schema.properties?.index?.minimum ?? 0) || index > (schema.properties?.index?.maximum ?? -1)) throw new Error('请填写当前作用域允许范围内的整数序号');
  if (Object.keys(scope).some(key => !Object.hasOwn(schema.properties || {}, key))) throw new Error('测试上下文包含未允许的字段');
  if (description.scope_type === 'iteration') {
    if (!Object.hasOwn(scope, 'item') || scope.item === undefined) throw new Error('请填写本次迭代的当前项，可先选择文本、数值、是/否、对象或列表类型');
    result.iteration = { item: scope.item, index };
  } else {
    if (!scope.state || typeof scope.state !== 'object' || Array.isArray(scope.state)) throw new Error('请填写本次循环的状态对象');
    result.loop = { state: scope.state, index };
  }
  return result;
}
export function matchesTestIdentity(identity: ExecutionIdentity | undefined, target: DebugTarget) {
  return identity?.kind === 'node_test' && identity.node_id === target.node_id && Array.isArray(identity.scope_path) && sameScope(identity.scope_path, target.scope_path) && identity.target_key === target.target_key && identity.source_graph_sha256 === target.source_graph_sha256 && /^[a-f0-9]{64}$/.test(identity.graph_sha256 || '');
}
