import type { ObjectValue, Schema, VariableOption, WorkflowNode } from './types';

export function inputDefaults(schema: Schema, value: ObjectValue): ObjectValue {
  const result = { ...value };
  Object.entries(schema.properties || {}).forEach(([name, field]) => {
    if (!field || typeof field !== 'object' || ['__proto__', 'constructor', 'prototype'].includes(name)) return;
    if (!Object.hasOwn(result, name) && Object.hasOwn(field, 'default')) result[name] = field.default === undefined ? undefined : JSON.parse(JSON.stringify(field.default));
    if (result[name] && typeof result[name] === 'object' && !Array.isArray(result[name]) && field.properties) result[name] = inputDefaults(field, result[name]);
  });
  return result;
}

export type TestReference = VariableOption & { optional?: boolean };
/** Only external data references become form fields; identity is always server-owned. */
export function testReferences(node: WorkflowNode, variables: VariableOption[]): TestReference[] {
  const found = new Map<string, TestReference>();
  const visit = (value: unknown) => {
    if (!value || typeof value !== 'object') return;
    if (!Array.isArray(value) && typeof (value as ObjectValue).$ref === 'string') {
      const ref = (value as ObjectValue).$ref as string;
      if (!/^(nodes|vars)\./.test(ref)) return;
      const known = variables.find(variable => variable.ref === ref);
      const optional = (value as ObjectValue).optional === true;
      found.set(ref, { ref, label: known?.label || ref, type: known?.type || 'any', optional: found.has(ref) ? found.get(ref)!.optional && optional : optional });
      return;
    }
    Object.values(value).forEach(visit);
  };
  visit(node.inputs);
  // A child body's nodes/vars belong to that body, never the outer test pool.
  visit(Object.fromEntries(Object.entries(node.config || {}).filter(([key]) => key !== 'body')));
  return [...found.values()];
}
export function testVariablePools(references: TestReference[], values: ObjectValue) {
  const pools: { nodes: ObjectValue; vars: ObjectValue } = { nodes: {}, vars: {} };
  // Whole-output values go first so a more specific field never silently disappears.
  for (const reference of [...references].sort((a, b) => a.ref.split('.').length - b.ref.split('.').length)) {
    const value = values[reference.ref];
    if (value === undefined) { if (!reference.optional) throw new Error(`请填写测试引用：${reference.label}`); continue; }
    const [pool, ...path] = reference.ref.split('.');
    if (!['nodes', 'vars'].includes(pool) || path.some(part => !/^[A-Za-z_][A-Za-z0-9_]*$|^\d+$/.test(part) || ['__proto__', 'constructor', 'prototype'].includes(part))) throw new Error('测试引用路径不合法，请检查节点配置');
    let target: any = pools[pool as keyof typeof pools];
    path.forEach((part, i) => {
      if (i === path.length - 1) target[part] = typeof value === 'object' && value !== null ? JSON.parse(JSON.stringify(value)) : value;
      else {
        if (target[part] === undefined) target[part] = /^\d+$/.test(path[i + 1]) ? [] : {};
        if (target[part] === null || typeof target[part] !== 'object') throw new Error('完整输出与子字段测试值冲突，请明确输入一致的值');
        target = target[part];
      }
    });
  }
  return pools;
}
