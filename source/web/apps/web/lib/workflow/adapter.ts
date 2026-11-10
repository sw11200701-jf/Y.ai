import {newMessageWorkflow} from './message-workflow';
import type { NodeDefinition, NodeRegistry, Schema, StudioResources, VariableOption, WorkflowDefinition, WorkflowEdge, WorkflowNode } from './types';
import { fieldLabel } from './labels';

export const groups = ['触发', '行为', '结果'];
export const stateLabels: Record<string, string> = { supported: '已支持', needs_configuration: '需配置', unavailable: '未开放', queued: '排队中', running: '进行中', started: '进行中', succeeded: '成功', completed: '成功', failed: '失败', skipped: '已跳过', cancelled: '已取消', interrupted: '已中断', paused: '已暂停', waiting_approval: '等待审批', resuming: '正在恢复', outcome_unknown: '执行结果待核对', idle: '待运行' };
export const isLegacy = (workflow: WorkflowDefinition) => workflow.schemaVersion !== '2.0';
/** Stable across node deletion/reordered edge arrays; array indexes can delete unrelated edges. */
export const edgeKey = (edge: WorkflowEdge) => JSON.stringify([edge.source, edge.source_port || 'default', edge.target, edge.target_port || 'default']);
export function readinessReason(def?: NodeDefinition): string {
  if (!def?.reason) return '';
  if (def.reason.includes('实时注册表未连接')) return '能力目录未连接，恢复后即可重新检查。';
  if (def.type === 'code') return '尚未提供隔离的安全执行环境。';
  if (def.type === 'human_approval') return def.status === 'supported' ? '等待指定审批人处理；处理结果会保存在运行记录中。' : '尚未提供可用的审批与恢复能力。';
  if (def.type === 'trigger') return '尚未提供定时或事件触发服务。';
  if (def.reason.includes('Module integration pending')) return '节点执行服务尚未完成接入。';
  if (def.type === 'database_query') return def.status === 'supported' ? '按实际使用者行列权限查询发布表，返回明细、统计与版本口径。' : '数据查询服务尚未就绪，请刷新能力目录。';
  if (['plugin', 'http'].includes(def.type) && def.status === 'needs_configuration') return '请选择已发布工具版本和受保护连接。';
  if (def.reason.includes('Requires configured module/model')) return def.type === 'agent' ? '请选择独立配置的智能体。' : ['question_classifier', 'parameter_extractor'].includes(def.type) ? '请选择处理模式并完成必要配置。' : '请选择已配置的本机模型；无模型时测试会明确失败。';
  if (def.reason.includes('Explicit document IDs')) return '请选择已导入资料。支持文本提取与关键词检索，暂不支持OCR。';
  return /[\u3400-\u9fff]/.test(def.reason) ? def.reason : '当前能力尚未就绪，请检查节点配置或稍后重试。';
}
export function nodeGroup(node: NodeDefinition): string {
  if (isEntryType(node.type, node)) return '触发';
  if (['end', 'answer', 'output'].includes(node.type)) return '结果';
  return '行为';
}
export const typedTriggers = ['trigger_text', 'trigger_image', 'trigger_audio', 'trigger_file', 'trigger_mixed'];
export function isEntryType(kind: string, definition?: NodeDefinition): boolean { return ['start', 'input', 'trigger', ...typedTriggers].includes(kind) || definition?.category === 'trigger' || definition?.entry === true; }
export function entryConflict(workflow: WorkflowDefinition, definition: NodeDefinition): string | null {
  if (typedTriggers.includes(definition.type)) {
    if (workflow.nodes.some(n => ['start', 'input'].includes(n.type))) return '消息触发入口不能与旧开始/输入节点混用；请显式新建或迁移画布';
    if (workflow.nodes.some(n => n.type === definition.type)) return '每种消息触发类型只能有一个入口';
  } else if (['start', 'input'].includes(definition.type) && workflow.nodes.some(n => isEntryType(n.type))) return '旧开始/输入入口不能与其他入口混用';
  return null;
}
export function nodeSubgroup(node: NodeDefinition): string {
  if (nodeGroup(node) !== '行为') return '';
  return ({ intelligence: '模型与智能体', knowledge: '知识与文档', data: '结构化数据', control: '逻辑控制', transform: '数据与变量', tools: '插件与服务', finance: '财务与文件' } as Record<string, string>)[node.category] || '代码与扩展';
}
export function nodeTone(def?: NodeDefinition): string {
  if (['chart', 'report', 'answer', 'output', 'end'].includes(def?.type || '')) return 'output';
  return ({ intelligence: 'model', knowledge: 'data', data: 'data', control: 'control', transform: 'data', tools: 'tool', finance: 'compute' } as Record<string, string>)[def?.category || ''] || 'control';
}
export function outputPorts(node: WorkflowNode, definition?: NodeDefinition): string[] {
  if (['end', 'output', 'answer'].includes(node.type)) return [];
  if (node.type === 'if_else') return ['true', 'false'];
  if (node.type === 'question_classifier') {
    const classes = node.config.classes;
    // Class ports follow config, without inventing a different classifier protocol.
    return [...(Array.isArray(classes) ? classes.map((c: any) => typeof c === 'string' ? c : c.id || c.name || c.label).filter(Boolean) : classes && typeof classes === 'object' ? Object.keys(classes) : []), 'else'];
  }
  return (definition?.ports || ['default']).filter(p => p !== 'dynamic');
}
export function positionOf(node: WorkflowNode, index: number) {
  const p = node.ui?.position || node.position || node.config?.ui_position;
  return p && Number.isFinite(p.x) && Number.isFinite(p.y) ? { x: p.x, y: p.y } : { x: (index % 4) * 280 + 80, y: Math.floor(index / 4) * 180 + 100 };
}
export function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)); }
export function newWorkflow(): WorkflowDefinition {
  return { name: '未命名工作流', version: '1.0.0', schemaVersion: '2.0', mode: 'demo', ui:{field_contract:'business_v1'}, input_schema: { type: 'object', properties: {},additionalProperties:false }, variables: {}, limits: { max_steps: 256, timeout_seconds: 120 }, nodes: [
    { id: 'start', type: 'start', config: {}, inputs: {}, ui: { position: { x: 80, y: 180 } } },
    { id: 'answer', type: 'answer', config: {}, inputs: {},output_schema:{type:'object',properties:{},additionalProperties:false}, ui: { position: { x: 420, y: 180 } } },
  ], edges: [{ source: 'start', target: 'answer', source_port: 'default', target_port: 'default' }] };
}
/** New Agent canvases start with a real registered text entry; old definitions remain unchanged. */
export function newAgentWorkflow(registry: NodeRegistry): WorkflowDefinition {
  return newMessageWorkflow(registry,'text');
}
export function newNode(def: NodeDefinition, position: { x: number; y: number }): WorkflowNode {
  const defaults = (schema: Schema) => Object.fromEntries(Object.entries(schema.properties || {}).filter(([, s]) => s.default !== undefined).map(([k, s]) => [k, clone(s.default)]));
  return { id: `${def.type}_${globalThis.crypto?.randomUUID?.().replace(/-/g, '').slice(0, 10) || Date.now()}`, type: def.type, config: defaults(def.config_schema), inputs: defaults(def.input_schema), input_schema:{type:'object',properties:{},required:[],additionalProperties:false},...(['transform','output','answer','end'].includes(def.type)?{output_schema:{type:'object',properties:{},additionalProperties:false}}:{}),ui: { field_contract:'business_v1',position, ...(def.type === 'output' ? {label:'结束并返回结果'} : {}) } };
}
/** A selected fixed plugin version supplies its published argument field types. */
export function configuredInputSchema(node: WorkflowNode, definition: NodeDefinition | undefined, resources: StudioResources): Schema {
  const schema = definition?.input_schema || {};
  if (!['plugin', 'http'].includes(node.type)) return schema;
  const binding = node.config.binding;
  const version = resources.versions.find(version => version.id === binding?.plugin_version_id);
  const tool = version?.tools?.find((tool: Record<string, any>) => tool.id === binding?.tool_id);
  const endpoint = version?.endpoints?.find((endpoint: Record<string, any>) => endpoint.id === tool?.endpoint_id);
  if (!endpoint?.input_schema || typeof endpoint.input_schema !== 'object') return schema;
  return { ...schema, properties: { ...schema.properties, arguments: { ...endpoint.input_schema, title: '插件输入映射' } } };
}
export function ancestors(workflow: WorkflowDefinition, nodeId: string): Set<string> {
  const found = new Set<string>();
  const visit = (id: string) => workflow.edges.filter(e => e.target === id).forEach(e => { if (!found.has(e.source) && e.source !== nodeId) { found.add(e.source); visit(e.source); } });
  visit(nodeId); return found;
}
function schemaVariables(schema: Schema, prefix: string, label: string): VariableOption[] {
  return Object.entries(schema.properties || {}).flatMap(([name, field]) => {
    if (!field || typeof field !== 'object' || Array.isArray(field)) return [];
    const ref = `${prefix}.${name}`; const type = Array.isArray(field.type) ? field.type.join('|') : field.type || 'any';
    return [{ ref, label: `${label} · ${fieldLabel(name, field.title)}`, type }, ...(field.properties ? schemaVariables(field, ref, `${label} · ${fieldLabel(name, field.title)}`) : [])];
  });
}
export function availableVariables(workflow: WorkflowDefinition, nodeId: string, registry: NodeRegistry | null, readonlyVariables: VariableOption[] = [],customUserVariables:Record<string,any>[]=[]): VariableOption[] {
  const allowed = ancestors(workflow, nodeId);
  const reachability = new Map<string, boolean>();
  const canReachWithout = (id: string, avoid: string, visited = new Set<string>()): boolean => {
    if (id === avoid || visited.has(id)) return false;
    const key = `${avoid}:${id}`; if (reachability.has(key)) return reachability.get(key)!;
    const node = workflow.nodes.find(n => n.id === id);
    if (node && isEntryType(node.type, registry?.items.find(d => d.type === node.type))) return true;
    const next = new Set(visited); next.add(id);
    const result = workflow.edges.filter(e => e.target === id).some(e => canReachWithout(e.source, avoid, next)); reachability.set(key, result); return result;
  };
  const conditional=(id:string)=>workflow.nodes.some(node => node.id === id && typedTriggers.includes(node.type)) || Array.from(ancestors(workflow,id)).some(parent=>{const n=workflow.nodes.find(n=>n.id===parent);return n&&(['if_else','question_classifier'].includes(n.type)||n.type.startsWith('trigger_'));});
  const outputSchema=(n:WorkflowNode):Schema=>{
    if(isEntryType(n.type))return workflow.ui?.field_contract==='business_v1'?workflow.input_schema||{type:'object'}:n.output_schema||{type:'object',properties:{inputs:workflow.input_schema||{type:'object'}}};
    if(n.type==='llm'||n.type==='agent')return n.config.response_format?.type&&n.config.response_format.type!=='text'?(n.output_schema||n.config.response_format.schema||{type:'object'}):{type:'object',properties:{text:{type:'string'}}};
    if(n.output_schema)return n.output_schema;
    if(['output','answer','end'].includes(n.type))return {type:'object',properties:{values:{type:'object',properties:Object.fromEntries(Object.entries(n.inputs||{}).map(([key,value])=>[key,{type:typeof value==='object'?'any':typeof value}]))}}};
    const schema=registry?.items.find(d=>d.type===n.type)?.output_schema||{};
    return {...schema,properties:Object.fromEntries(Object.entries(schema.properties||{}).filter(([key])=>!['mode','usage','audit','warnings','source_refs','data_sources','policy_version','snapshot_id','capabilities','message','query','prompt'].includes(key)))};
  };
  return [...schemaVariables(workflow.input_schema||{},'inputs','业务输入'),...customUserVariables.map(v=>({ref:`user_vars.${v.name}`,label:`用户变量 · ${v.name}`,type:String(v.type)})),
    ...Object.entries(workflow.variables||{}).map(([key,value])=>({ref:`vars.${key}`,label:`全局变量 · ${value?.title||key}`,type:String(value?.type||'any')})),
    ...readonlyVariables,
    ...workflow.nodes.filter(n=>allowed.has(n.id)).flatMap(n=>schemaVariables(outputSchema(n),`nodes.${n.id}`,n.ui?.label||n.id).map(v=>({...v,optional:conditional(n.id)&&canReachWithout(nodeId,n.id)})))];
}
export function connectionError(workflow: WorkflowDefinition, edge: WorkflowEdge, registry: NodeRegistry | null): string | null {
  if (!edge.source || !edge.target || edge.source === edge.target) return '不能连接到节点自身';
  const source = workflow.nodes.find(n => n.id === edge.source); const target = workflow.nodes.find(n => n.id === edge.target);
  if (!source || !target) return '节点不存在';
  if (isEntryType(target.type, registry?.items.find(d => d.type === target.type))) return '开始或消息触发节点不能有输入连线';
  if (!outputPorts(source, registry?.items.find(d => d.type === source.type)).includes(edge.source_port || 'default')) return '请选择节点的合法输出端口';
  if (ancestors(workflow, source.id).has(target.id)) return '外层画布不允许环路，请使用有界循环节点';
  if (workflow.edges.some(e => e.source === edge.source && e.target === edge.target && (e.source_port || 'default') === (edge.source_port || 'default') && (e.target_port || 'default') === (edge.target_port || 'default'))) return '相同节点与端口之间已有连线，请勿重复连接';
  return null;
}
/** Conversion always creates a copy and retains original metadata. Runtime validation is required. */
export function convertLegacy(workflow: WorkflowDefinition, registry: NodeRegistry): WorkflowDefinition {
  const result = clone(workflow); delete result.id;
  result.name += ' · v2副本'; result.schemaVersion = '2.0'; result.ui = { ...result.ui, converted_from: workflow.id || workflow.name };
  const start = result.nodes.find(n => ['start', 'input'].includes(n.type));
  result.nodes = result.nodes.map(node => {
    const n = clone(node); n.inputs ||= {};
    const prior = Array.from(ancestors(result, n.id)).map(id => result.nodes.find(x => x.id === id)!);
    const nearest = (types: string[]) => prior.find(p => types.includes(p.type));
    const schema = registry.items.find(d => d.type === n.type)?.config_schema;
    // Old canvas put presentation data inside config. Archive it on the copy and migrate it out of strict v2 execution config.
    n.ui = { ...n.ui, position: positionOf(n, workflow.nodes.findIndex(x => x.id === n.id)), legacy_config: clone(n.config) };
    if (schema?.additionalProperties === false) n.config = Object.fromEntries(Object.entries(n.config).filter(([key]) => key in (schema.properties || {})));
    if (start && n.type === 'plugin' && !('arguments' in n.inputs)) n.inputs.arguments = { entity: { $ref: `nodes.${start.id}.query.entity` }, periods: { $ref: `nodes.${start.id}.query.periods` }, ...clone(node.config.arguments || {}) };
    if (start && n.type === 'query' && !('query' in n.inputs)) n.inputs.query = { $ref: `nodes.${start.id}.query` };
    // Legacy runner consumed a shared facts/dataset slot. Turn that implicit dependency into an explicit reference.
    if (n.type === 'calculate' && !('facts' in n.inputs)) { const p = nearest(['query', 'plugin']); if (p) n.inputs.facts = { $ref: `nodes.${p.id}.facts` }; }
    if (['chart', 'report'].includes(n.type) && !('dataset' in n.inputs)) { const p = nearest(['calculate']); if (p) n.inputs.dataset = { $ref: `nodes.${p.id}.dataset` }; }
    return n;
  });
  result.edges = result.edges.map(e => ({ ...e, source_port: e.source_port || 'default', target_port: e.target_port || 'default' }));
  // Registry is deliberately required so conversion cannot imply v2 runtime support without discovery.
  if (!registry.version.startsWith('2.')) throw new Error('后端尚未提供v2注册表');
  return result;
}
