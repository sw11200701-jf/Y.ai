import { Binding, Json } from './api';
export const baseAgentFields = ['name', 'description', 'bindings', 'planner_mode', 'model_config_id'];
export function isWorkflowAsset(value:Json){return value.agent_type==='advanced'||!value.agent_type&&!!value.main_workflow_id;}
export async function agentContract() {
  const response = await fetch('/openapi.json', { cache: 'no-store', signal:AbortSignal.timeout(15000) });
  if (!response.ok) throw Error('无法读取实际后端配置契约');
  const api = await response.json(); const route = api.paths?.['/api/v1/agents']?.post;
  const ref = route?.requestBody?.content?.['application/json']?.schema?.$ref?.split('/').at(-1);
  const properties = api.components?.schemas?.[ref || 'AgentWrite']?.properties || {};
  return { fields: Object.keys(properties), paths: Object.keys(api.paths || {}), properties };
}
export async function bindingId(binding: Binding) {
  const bytes = new TextEncoder().encode([binding.plugin_version_id, binding.tool_id, binding.connection_id].join('\x00'));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return 'binding_' + Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('').slice(0, 24);
}
export function supportedAgentPayload(values: Json, fields: string[]) { return Object.fromEntries(Object.entries(values).filter(([key]) => fields.includes(key))); }
export function requireAgentModelContract(values:Json,fields:string[]){
  if(values.planner_mode!=='model')return;
  const keys=['model_config_id','model_config_version','model_parameters','context_budget'].filter(key=>values[key]!==undefined&&values[key]!==null);
  if(keys.some(key=>!fields.includes(key)))throw Error('当前后端尚未支持完整模型配置，请刷新或等待版本更新；本次修改尚未保存。');
}
