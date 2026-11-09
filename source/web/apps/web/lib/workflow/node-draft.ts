import type { Schema, WorkflowDefinition, WorkflowNode, WorkflowEdge } from './types';
import {scopeWorkflow} from './scoped-debug';
export type NodeSettingsChange = {expected_graph_sha256?:string;node_id:string;scope_path:string[];node:WorkflowNode;input_schema?:Schema;outgoing_edges?:WorkflowEdge[];structure?:WorkflowDefinition;topology?:Pick<WorkflowDefinition,'nodes'|'edges'>;include_body_layout?:true};
const layoutKeys = ['position'] as const;
/** Only known canvas coordinates are nonsemantic. Labels, all other UI, schema, config and edges still conflict. */
function configuration(node:WorkflowNode):WorkflowNode {
  const value=copy(node);delete value.position;
  if(value.ui){delete value.ui.position;if(!Object.keys(value.ui).length)delete value.ui;}
  if(value.config)delete value.config.ui_position;
  if(['iteration','loop'].includes(value.type)&&Array.isArray(value.config?.body?.nodes))value.config.body.nodes=value.config.body.nodes.map(configuration);
  return value;
}
/** Merge configuration with the selected layout; never turn a local move into a config write or snap it back. */
export function withNodeLayout(node:WorkflowNode,source:WorkflowNode,includeBodyLayout=false):WorkflowNode {
  const next=copy(node);
  for(const key of layoutKeys){if(Object.hasOwn(source,key))next[key]=copy(source[key]);else delete next[key];}
  if(source.ui&&Object.hasOwn(source.ui,'position'))next.ui={...next.ui,position:copy(source.ui.position)};
  else if(next.ui){delete next.ui.position;if(!Object.keys(next.ui).length)delete next.ui;}
  if(source.config&&Object.hasOwn(source.config,'ui_position'))next.config={...next.config,ui_position:copy(source.config.ui_position)};
  else if(next.config)delete next.config.ui_position;
  if(!includeBodyLayout&&['iteration','loop'].includes(next.type)&&Array.isArray(next.config?.body?.nodes)&&Array.isArray(source.config?.body?.nodes))next.config.body.nodes=next.config.body.nodes.map((child:WorkflowNode)=>{const old=source.config.body.nodes.find((item:WorkflowNode)=>item.id===child.id);return old?withNodeLayout(child,old):child;});
  return next;
}
/** Project strict server DTO and restore stored positions. The body-layout flag stays local to the modal save. */
export function nodeSettingsRequest(stored:WorkflowDefinition,change:NodeSettingsChange):NodeSettingsChange {
  const old=scopeWorkflow(stored,change.scope_path).nodes.find(node=>node.id===change.node_id);
  const result:NodeSettingsChange={expected_graph_sha256:change.expected_graph_sha256,node_id:change.node_id,scope_path:[...change.scope_path],node:old?withNodeLayout(change.node,old,change.include_body_layout):copy(change.node),...(Object.hasOwn(change,'input_schema')?{input_schema:copy(change.input_schema)}:{}),...(Object.hasOwn(change,'outgoing_edges')?{outgoing_edges:copy(change.outgoing_edges)}:{})};
  if(change.topology){
    const original=scopeWorkflow(stored,change.scope_path),topology=change.topology;
    if(!same(original.edges,topology.edges)||!same(original.nodes.map(node=>node.id),topology.nodes.map(node=>node.id))){
      // Commit canvas connections and membership with this node under the same
      // revision check. Existing sibling configuration and layout stay stored.
      const structure=copy(stored);delete structure.access;delete structure.graph_sha256;
      const scope=scopeWorkflow(structure,change.scope_path);
      scope.nodes=topology.nodes.map(node=>copy(original.nodes.find(item=>item.id===node.id)||node));
      scope.edges=copy(topology.edges);result.structure=structure;
    }
  }
  return result;
}
/** New topology uses the original whole-save route; existing node coordinates remain an unsaved local change. */
export function nodeStructureRequest(next:WorkflowDefinition,stored:WorkflowDefinition,change?:NodeSettingsChange):WorkflowDefinition {
  const value=copy(next);
  if(change){const local=scopeWorkflow(value,change.scope_path);local.nodes=local.nodes.map(node=>node.id===change.node_id?withNodeLayout(change.node,node,change.include_body_layout):node);if(Object.hasOwn(change,'input_schema'))local.input_schema=copy(change.input_schema);}
  if(next.id)value.nodes=value.nodes.map(node=>{const old=stored.nodes.find(item=>item.id===node.id);return old?withNodeLayout(node,old):node;});
  return value;
}
export function withWorkflowLayout(saved:WorkflowDefinition,current:WorkflowDefinition):WorkflowDefinition {
  return {...saved,nodes:saved.nodes.map(node=>{const local=current.nodes.find(item=>item.id===node.id);return local?withNodeLayout(node,local):node;})};
}
export function nodeSettingsChange(baseline:WorkflowDefinition,draft:WorkflowDefinition,nodeId:string):NodeSettingsChange {
  const original=baseline.nodes.find(node=>node.id===nodeId),node=draft.nodes.find(node=>node.id===nodeId);
  if(!original||!node)throw Error('节点不存在，请重新打开');
  if(JSON.stringify(baseline.variables)!==JSON.stringify(draft.variables))throw Error('全局变量需在流程设置中保存，不能夹带到节点提交');
  const changed=JSON.stringify(baseline.input_schema)!==JSON.stringify(draft.input_schema);
  if(changed&&!['start','input','trigger_text','trigger_image','trigger_audio','trigger_file','trigger_mixed'].includes(original.type))throw Error('只有开始节点可以关联保存输入结构');
  const outgoing=changedClassifierEdges(baseline,draft,nodeId);
  return {expected_graph_sha256:baseline.graph_sha256,node_id:nodeId,scope_path:[],node,topology:{nodes:copy(draft.nodes),edges:copy(draft.edges)},...(changed?{input_schema:draft.input_schema}:{}),...(outgoing?{outgoing_edges:outgoing}:{})};
}
export function mergeSavedNode(current:WorkflowDefinition,saved:WorkflowDefinition,change:NodeSettingsChange):WorkflowDefinition {
  const next=copy(current),local=scopeWorkflow(next,change.scope_path),server=scopeWorkflow(saved,change.scope_path);
  const node=server.nodes.find(node=>node.id===change.node_id);
  if(!node)throw Error('保存响应缺少目标节点，请重新读取');
  local.nodes=local.nodes.map(item=>item.id===change.node_id?withNodeLayout(node,item,change.include_body_layout):item);
  if(Object.hasOwn(change,'input_schema'))local.input_schema=copy(server.input_schema);
  if(Object.hasOwn(change,'outgoing_edges'))local.edges=[...local.edges.filter(edge=>edge.source!==change.node_id),...copy(server.edges.filter(edge=>edge.source===change.node_id))];
  if(saved.graph_sha256!==undefined)next.graph_sha256=saved.graph_sha256;
  return next;
}
const copy = <T,>(value: T): T => value === undefined ? value : JSON.parse(JSON.stringify(value));
const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);
function changedClassifierEdges(baseline:WorkflowDefinition,draft:WorkflowDefinition,nodeId:string):WorkflowEdge[]|undefined {
  if(same(baseline.edges,draft.edges))return undefined;
  const original=baseline.nodes.find(node=>node.id===nodeId),node=draft.nodes.find(node=>node.id===nodeId);
  if(original?.type!=='question_classifier'||node?.type!==original.type||!same(baseline.edges.filter(edge=>edge.source!==nodeId),draft.edges.filter(edge=>edge.source!==nodeId)))throw Error('节点配置只能关联修改当前问题分类节点的出口');
  return copy(draft.edges.filter(edge=>edge.source===nodeId));
}
/** Commit only this panel's node and changed schemas; never replace a sibling graph. */
export function commitNodeDraft(current: WorkflowDefinition, baseline: WorkflowDefinition, draft: WorkflowDefinition, nodeId: string): WorkflowDefinition {
  const old = baseline.nodes.find(node => node.id === nodeId), next = draft.nodes.find(node => node.id === nodeId);
  const live=current.nodes.find(node=>node.id===nodeId);
  if (!old || !next || !live || !same(configuration(live), configuration(old))) throw Error('节点已在其他操作中变化，请取消后重新打开配置');
  const patch: Partial<WorkflowDefinition> = {};
  const outgoing=changedClassifierEdges(baseline,draft,nodeId);
  if(outgoing){if(!same(current.edges.filter(edge=>edge.source===nodeId),baseline.edges.filter(edge=>edge.source===nodeId)))throw Error('当前分类出口已在其他编辑中变化，请取消后重新打开');patch.edges=[...current.edges.filter(edge=>edge.source!==nodeId),...outgoing];}
  for (const key of ['input_schema', 'variables'] as const) if (!same(baseline[key], draft[key])) {
    if (!same(current[key], baseline[key])) throw Error('流程变量或输入结构已变化，请取消后重新打开配置');
    (patch as Record<string, unknown>)[key] = copy(draft[key]);
  }
  return { ...current, ...patch, nodes: current.nodes.map(node => node.id === nodeId ? withNodeLayout(next,node) : node) };
}
