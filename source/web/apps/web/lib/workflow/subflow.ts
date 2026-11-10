import { clone, newWorkflow } from './adapter';
import type { WorkflowDefinition, WorkflowNode } from './types';

/** Creates existing v2 DSL only; never changes an existing graph without an explicit choice. */
export function newSubflow(parent: WorkflowDefinition, repeat: WorkflowNode): WorkflowDefinition {
  const result = newWorkflow(); result.name = repeat.type === 'iteration' ? '列表处理子流程' : '循环处理子流程'; result.input_schema = clone(parent.input_schema || { type: 'object' });
  const output = result.nodes[1];
  if (repeat.type === 'iteration') output.inputs = { item: { $ref: 'iteration.item' } };
  else {
    const state = repeat.inputs?.state ?? repeat.config.state;
    output.inputs = state && typeof state === 'object' && !Array.isArray(state) && !('$ref' in state) && Object.keys(state).every(key => /^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) ? Object.fromEntries(Object.keys(state).map(key => [key, { $ref: `loop.state.${key}` }])) : { $ref: 'loop.state' };
  }
  const props=Object.fromEntries(Object.keys(output.inputs).filter(k=>k!=='$ref').map(k=>[k,{}]));
  output.input_schema={type:'object',properties:props,additionalProperties:true};output.output_schema={type:'object',properties:props,additionalProperties:true};
  return result;
}

export function basicRepeat(parent: WorkflowDefinition, repeat: WorkflowNode): Pick<WorkflowNode, 'config' | 'inputs' | 'input_schema'> {
  const config = {
    ...repeat.config,
    body: newSubflow(parent, repeat),
    max_iterations: repeat.config.max_iterations ?? 3,
    timeout_seconds: repeat.config.timeout_seconds ?? 30,
  };
  if (repeat.type === 'loop') {
    return {
      config: { ...config, until: repeat.config.until ?? { left: { $ref: 'loop.index' }, operator: 'gte', right: 0 } },
      inputs: repeat.inputs,
      input_schema: repeat.input_schema,
    };
  }
  const schema = repeat.input_schema;
  return {
    config,
    inputs: { ...repeat.inputs, items: repeat.inputs?.items ?? [] },
    input_schema: { ...schema, type: 'object', properties: { ...schema?.properties, items: schema?.properties?.items ?? { type: 'array' } } },
  };
}

/** An ordinary bounded counter loop is a useful starting point, not a runtime shortcut. */
export function counterLoop(parent: WorkflowDefinition, repeat: WorkflowNode): { config: WorkflowNode['config']; inputs: WorkflowNode['inputs'];input_schema:WorkflowNode['input_schema'] } {
  const body = newWorkflow(); body.name = '计数循环子流程'; body.input_schema = clone(parent.input_schema || { type: 'object' });
  body.variables = { counter: { type: 'number', default: 1 } };
  body.nodes = [
    { id: 'start', type: 'start', config: {}, inputs: {}, ui: { position: { x: 60, y: 150 } } },
    { id: 'increment', type: 'assign', config: { name: 'counter', operation: 'increment' }, inputs: { value: { $ref: 'loop.state.counter' } }, ui: { position: { x: 350, y: 150 } } },
    { id: 'output', type: 'output', config: { presentation: 'auto' }, inputs: { counter: { $ref: 'nodes.increment.value' } }, ui: { position: { x: 640, y: 150 } } },
  ];
  body.nodes[2].input_schema={type:'object',properties:{counter:{type:'number'}},required:['counter'],additionalProperties:false};body.nodes[2].output_schema={type:'object',properties:{counter:{type:'number'}},required:['counter'],additionalProperties:false};
  body.edges = [{ source: 'start', target: 'increment', source_port: 'default', target_port: 'default' }, { source: 'increment', target: 'output', source_port: 'default', target_port: 'default' }];
  return { input_schema:{type:'object',properties:{state:{type:'object',properties:{counter:{type:'number'}},required:['counter'],additionalProperties:false}},required:['state'],additionalProperties:false},config: { ...repeat.config, body, max_iterations: 3, timeout_seconds: 30, until: { left: { $ref: 'loop.state.counter' }, operator: 'gte', right: 3 } }, inputs: { ...repeat.inputs, state: { counter: 0 } } };
}
