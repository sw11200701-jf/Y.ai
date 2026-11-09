import { Json } from './api';
export type PluginDraft = { name: string; description: string; default_connection_id?: string; endpoints: Json[]; tools: Json[] };
export function pluginPayload(draft: Json): PluginDraft {
  return { name: draft.name, description: draft.description || '', ...(draft.default_connection_id ? {default_connection_id:draft.default_connection_id}:{}), endpoints: (draft.endpoints || []).map((endpoint:Json)=>{const result={...endpoint};if(result.input_fields)delete result.input_schema;if(result.output_fields)delete result.output_schema;return result;}), tools: draft.tools || [] };
}
export function upsertEndpoint(draft: PluginDraft, previousId: string | undefined, endpoint: Json, tools: Json[]): PluginDraft {
  if (draft.endpoints.some(e => e.id === endpoint.id && e.id !== previousId)) throw Error('接口 ID 已存在，请使用唯一 ID');
  const otherTools = draft.tools.filter(t => t.endpoint_id !== previousId);
  const ids = tools.map(t => t.id);
  if (new Set(ids).size !== ids.length || otherTools.some(t => ids.includes(t.id))) throw Error('工具 ID 必须在插件内唯一');
  const index = draft.endpoints.findIndex(e => e.id === previousId);
  const endpoints = draft.endpoints.slice();
  if (index < 0) endpoints.push(endpoint); else endpoints[index] = endpoint;
  return { ...draft, endpoints, tools: [...otherTools, ...tools.map(t => ({ ...t, endpoint_id: endpoint.id }))] };
}
export function removeEndpoint(draft: PluginDraft, id: string): PluginDraft {
  return { ...draft, endpoints: draft.endpoints.filter(e => e.id !== id), tools: draft.tools.filter(t => t.endpoint_id !== id) };
}
export function pluginPurpose(plugin: Json) { return /合成|验收|联调|smoke|synthetic|integration/i.test(`${plugin.name} ${plugin.description}`) ? '验证样例' : '业务配置'; }
