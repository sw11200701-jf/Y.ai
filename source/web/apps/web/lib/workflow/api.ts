import { currentIdentityEpoch, request, send } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import type { ApprovalDecision, ApprovalInboxItem, AttachmentMetadata, DebugRunRequest, DebugTarget, MessageEnvelope, NodeRegistry, ReadonlyVariableCatalog, RunApproval, RunEventPage, RunExecutionState, RunTracePage, StudioResources, StudioRun, WorkflowDefinition } from './types';
// GET reads time out explicitly; writes are never retried automatically.
const read=async<T=Record<string,any>>(path:string):Promise<T>=>{try{return await request<T>(path,typeof AbortSignal!=='undefined'&&typeof AbortSignal.timeout==='function'?{signal:AbortSignal.timeout(15000)}:undefined);}catch(e){if((e as Error).name==='TimeoutError')throw Error('读取超过15秒，请重试读取；不会自动重发保存或运行请求。');throw e;}};
export const workflowApi = {
  createNodeDraft:(workflow:WorkflowDefinition,node_id:string,scope_path:string[]=[])=>send<WorkflowDefinition>('/workflows/node-drafts',{workflow,node_id,scope_path}),
  saveNodeSettings:(id:string,change:import('./node-draft').NodeSettingsChange)=>send<WorkflowDefinition>(`/workflows/${encodeURIComponent(id)}/node-settings`,change,'PUT'),
  approvers: () => read<{items:{id:string;display_name:string;is_self:boolean;role_name?:string}[]}>('/run-approvers'),
  list: () => read<{ items: WorkflowDefinition[] }>('/workflows'),
  get: (id: string) => read<WorkflowDefinition>(`/workflows/${encodeURIComponent(id)}`),
  registry: () => read<NodeRegistry>('/workflows/node-registry'),
  variableCatalog: () => read<ReadonlyVariableCatalog>('/workflows/variable-catalog'),
  tableDictionary: (tableId: string, versionId?: string) => read<Record<string, any>>(`/database/tables/${encodeURIComponent(tableId)}/dictionary${versionId ? `?version_id=${encodeURIComponent(versionId)}` : ''}`),
  validate: (workflow: WorkflowDefinition) => send('/workflows/validate', workflow),
  save: (workflow: WorkflowDefinition) => send<WorkflowDefinition>(workflow.id ? `/workflows/${encodeURIComponent(workflow.id)}` : '/workflows', workflow, workflow.id ? 'PUT' : 'POST'),
  publish: (id: string, audience: import('../../components/asset-publication').Audience, expected_graph_sha256: string) =>
    send<import('../../components/asset-publication').Publication>(`/workflows/${encodeURIComponent(id)}/publish`, {...audience, expected_graph_sha256}),
  run: (workflow: WorkflowDefinition, inputs: Record<string, any>, query: Record<string, any>, prompt: string) => send<StudioRun>('/runs', { workflow, inputs, query, prompt }),
  debugRun: (draft: DebugRunRequest) => send<StudioRun>('/workflows/debug-runs', draft),
  debugTarget: (workflow: WorkflowDefinition, node_id: string, scope_path: string[] = []) => send<DebugTarget>('/workflows/debug-target', { workflow, node_id, scope_path }),
  trace: (id: string, after = 0) => read<RunTracePage>(`/runs/${encodeURIComponent(id)}/trace?after=${after}&limit=50`),
  runDetail: (id: string) => read<StudioRun>(`/runs/${encodeURIComponent(id)}`),
  events: (id: string, after?: number) => read<RunEventPage>(`/runs/${encodeURIComponent(id)}/events${after === undefined ? '' : `?after=${after}`}`),
  executionState: (id: string) => read<RunExecutionState>(`/runs/${encodeURIComponent(id)}/state`),
  resume: (id: string) => send<StudioRun>(`/runs/${encodeURIComponent(id)}/resume`),
  approvals: (id: string) => read<{ items: RunApproval[] }>(`/runs/${encodeURIComponent(id)}/approvals`),
  approvalInbox: () => read<{ items: ApprovalInboxItem[]; limit: number }>('/approvals?status=pending'),
  approvalDecision: (id: string, approvalId: string, decision: ApprovalDecision, note?: string) => send<RunApproval>(`/runs/${encodeURIComponent(id)}/approvals/${encodeURIComponent(approvalId)}/decision`, { decision, ...(note ? { note } : {}) }),
  cancel: (id: string) => send<StudioRun>(`/runs/${encodeURIComponent(id)}/cancel`),
  uploadAttachment: (file: File) => { const body = new FormData(); body.append('file', file); return request<AttachmentMetadata>('/attachments', { method: 'POST', body }); },
  agent: (id: string) => read<Record<string, any>>(`/agents/${encodeURIComponent(id)}`),
  bind: (agentId: string, workflowId: string) => send(`/agents/${encodeURIComponent(agentId)}/workflow`, { workflow_id: workflowId }, 'PUT'),
  publishAgent: (id: string) => send(`/agents/${encodeURIComponent(id)}/publish`),
  runAgent: (agentId: string, message: MessageEnvelope, inputs: Record<string, any>, query: Record<string, any>) => send<StudioRun>('/runs', { agent_id: agentId, message, inputs, query }),
};
type FinancialImportOption = { id: string; name: string; filename: string; status: 'committed' };
/** Read only caller-visible metadata, never the legacy /imports preview response. */
export async function loadFinancialImportBatches(q = ''): Promise<{ items: FinancialImportOption[] }> {
  if (typeof q !== 'string' || Array.from(q).length > 100) throw Error('批次目录查询最多100字。');
  const identity = identityGuard(), limit = 200, maxOffset = 10000;
  const items: FinancialImportOption[] = [], ids = new Set<string>(); let total: number | undefined;
  for (let offset = 0; offset <= maxOffset; offset += limit) {
    const page = await read<{ items: FinancialImportOption[]; total: number; offset: number; limit: number }>(`/workflow-resource-catalog/import-batches?offset=${offset}&limit=${limit}${q ? `&q=${encodeURIComponent(q)}` : ''}`);
    identity();
    if (!page || !Array.isArray(page.items) || !Number.isSafeInteger(page.total) || page.total < 0 || page.offset !== offset || page.limit !== limit || page.items.length > limit) throw Error('财务批次目录格式或分页位置异常，请重新读取。');
    if (total !== undefined && total !== page.total) throw Error('批次目录在读取期间已变化，未使用不完整结果，请刷新目录。');
    total = page.total;
    if (total > maxOffset + limit) throw Error('批次目录超过10200项可完整读取范围，未加载部分结果；请缩小目录查询范围后重试。');
    if (page.items.length !== Math.min(limit, Math.max(0, total - offset))) throw Error('批次目录提前结束或页数不一致，未使用不完整结果，请刷新目录。');
    for (const item of page.items) {
      if (!item || typeof item !== 'object' || Object.keys(item).some(key => !['id', 'name', 'filename', 'status'].includes(key)) || typeof item.id !== 'string' || !item.id || typeof item.name !== 'string' || typeof item.filename !== 'string' || item.status !== 'committed' || ids.has(item.id)) throw Error('财务批次目录含无效、重复或非已提交选项，已停止读取。');
      ids.add(item.id); items.push({ id: item.id, name: item.name, filename: item.filename, status: item.status });
    }
    if (items.length === total) return { items };
  }
  throw Error('批次目录超过分页上限，未使用不完整结果，请缩小目录查询范围后重试。');
}
export async function loadResources(): Promise<StudioResources> {
  const identity = identityGuard();
  const names = ['agents', 'plugins', 'connections', 'imports', 'documents'] as const;
  const values = await Promise.allSettled(names.map(path => path === 'imports' ? loadFinancialImportBatches() : read<{ items: Record<string, any>[] }>(`/${path}${path === 'agents' ? '?page_size=100' : ''}`)));
  const resources: StudioResources = { agents: [], agentVersions: [], models: [], documents: [], plugins: [], versions: [], connections: [], imports: [], warnings: [] };
  const labels = ['智能体', '插件', '连接', '导入批次', '资料'];
  values.forEach((result, i) => { if (result.status === 'fulfilled') resources[names[i]] = result.value.items; else resources.warnings.push(`${labels[i]}读取失败：${errorMessage(result.reason)}`); });
  identity();
  const imports = values[names.indexOf('imports')];
  if (imports.status === 'rejected') throw imports.reason; // Surface paging/denial failures; never present them as an empty selector.
  await Promise.all([
    (async()=>{const versions=await Promise.allSettled(resources.plugins.map(p=>read<{items:Record<string,any>[]}>(`/plugins/${encodeURIComponent(p.id)}/versions`)));identity();versions.forEach(result=>{if(result.status==='fulfilled')resources.versions.push(...result.value.items);else resources.warnings.push(`工具版本读取失败：${errorMessage(result.reason)}`);});})(),
    (async()=>{const versions=await Promise.allSettled(resources.agents.map(a=>read<{items:Record<string,any>[]}>(`/agents/${encodeURIComponent(a.id)}/versions`)));identity();versions.forEach(result=>{if(result.status==='fulfilled')resources.agentVersions.push(...result.value.items);});})(),
    (async()=>{try{const catalog=await read<{items:Record<string,any>[]}>('/model-catalog');identity();if(!Array.isArray(catalog.items))throw Error('模型目录格式异常');resources.models=catalog.items.map(model=>({...model,name:`${model.provider_name} · ${model.name}`}));}catch(error){resources.warnings.push(`模型目录读取失败：${errorMessage(error)}`);}})(),
    (async()=>{try{const knowledge=await loadPublishedKnowledge();identity();resources.knowledgeBases=knowledge.knowledgeBases;resources.knowledgeVersions=knowledge.knowledgeVersions;resources.warnings.push(...knowledge.warnings);}catch(error){resources.warnings.push(`知识版本读取失败：${errorMessage(error)}`);}})(),
    (async()=>{try{const data=await loadDatabaseResources();identity();resources.databases=data.databases;resources.dataTables=data.dataTables;resources.tableVersions=data.tableVersions;resources.warnings.push(...data.warnings);}catch(error){resources.warnings.push(`数据库读取失败：${errorMessage(error)}`);}})(),
  ]);
  identity();
  return resources;
}

/** Use only explicitly advertised Schema6 directories. A real denial never becomes an empty catalog. */
export async function loadPublishedKnowledge() {
  const identity = identityGuard();
  const capabilities = await read<{ schema_version: string; catalog: string; resource_collections: string[]; typed_sources: boolean; published_status: string }>('/workflows/knowledge-directory-capabilities');
  identity();
  if (capabilities.schema_version !== 'workflow-knowledge-directory-v1' || capabilities.catalog !== 'published_knowledge_versions' || capabilities.typed_sources !== false || capabilities.published_status !== 'published' || !Array.isArray(capabilities.resource_collections) || !capabilities.resource_collections.length || capabilities.resource_collections.length > 2 || new Set(capabilities.resource_collections).size !== capabilities.resource_collections.length || capabilities.resource_collections.some(collection => !['documents', 'datasets'].includes(collection))) throw new Error('知识目录能力契约不受支持，请更新匹配的服务后重新读取');
  const bases = await read<{ items: Record<string, any>[] }>('/knowledge-bases'); identity();
  if (!Array.isArray(bases.items)) throw new Error('知识库目录格式异常，请重新读取');
  const knowledgeBases = bases.items.filter(base => !['disabled', 'deleted'].includes(base.status));
  const lists = await Promise.all(knowledgeBases.flatMap(base => capabilities.resource_collections.map(async collection => {
    const entries = await read<{ items: Record<string, any>[] }>(`/knowledge-bases/${encodeURIComponent(base.id)}/${collection}`); identity();
    if (!Array.isArray(entries.items)) throw new Error('知识资源目录格式异常，请重新读取');
    const children = await Promise.all(entries.items.filter(root => root.status === 'published').map(async root => {
      if (typeof root.id !== 'string' || root.knowledge_base_id !== base.id) throw new Error('知识资源与所属知识库不一致，已停止读取');
      const response = await read<{ items: Record<string, any>[] }>(`/knowledge/${collection}/${encodeURIComponent(root.id)}/versions`); identity();
      if (!Array.isArray(response.items)) throw new Error('知识版本目录格式异常，请重新读取');
      const kind = collection === 'documents' ? 'knowledge_document' : 'knowledge_dataset';
      return response.items.filter(version => version.status === 'published' && (version.errors == null || Array.isArray(version.errors) && !version.errors.length)).map(version => {
        if (typeof version.id !== 'string' || typeof version.file_id !== 'string' || version.knowledge_base_id !== base.id || version.resource_id !== root.id || version.resource_kind !== kind) throw new Error('知识版本与当前资源或知识库不一致，已停止读取');
        return { ...version, library_name: base.name, resource_name: root.name || root.filename || '知识资料' };
      });
    }));
    identity(); return children.flat();
  })));
  identity();
  return { knowledgeBases, knowledgeVersions: lists.flat(), warnings: [] as string[] };
}

/** Metadata only: never fetch table rows, private schema or global sample data. */
export async function loadDatabaseResources() {
  const identity = identityGuard();
  const { items: databases } = await read<{ items: Record<string, any>[] }>('/databases');
  identity();
  const dataTables: Record<string, any>[] = []; const tableVersions: Record<string, any>[] = []; const warnings: string[] = [];
  const reads = await Promise.allSettled(databases.map(async database => {
    const { items } = await read<{ items: Record<string, any>[] }>(`/databases/${encodeURIComponent(database.id)}/tables`);
    identity();
    const tables = items.filter(table => table.status === 'published' && table.published_version_id);
    dataTables.push(...tables.map(table => ({ ...table, database_name: database.name })));
    const versions = await Promise.allSettled(tables.map(async table => {
      const result = await read<{ items: Record<string, any>[] }>(`/database/tables/${encodeURIComponent(table.id)}/versions`);
      return result.items.filter(version => version.status === 'published').map(version => ({ ...version, table_id: table.id }));
    }));
    versions.forEach(result => { if (result.status === 'fulfilled') tableVersions.push(...result.value); else warnings.push(`数据表版本读取失败：${errorMessage(result.reason)}`); });
  }));
  reads.forEach(result => { if (result.status === 'rejected') warnings.push(`数据表读取失败：${errorMessage(result.reason)}`); });
  return { databases, dataTables, tableVersions, warnings };
}

function identityGuard() {
  const epoch = typeof currentIdentityEpoch === 'function' ? currentIdentityEpoch() : 0;
  return () => { if (typeof currentIdentityEpoch === 'function' && epoch !== currentIdentityEpoch()) throw new Error('登录身份已变化，请重新打开资源目录'); };
}
