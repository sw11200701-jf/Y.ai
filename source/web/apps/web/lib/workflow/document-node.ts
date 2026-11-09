import { documentDefinitions, emptyDocuments, type DocumentDefinition, type DocumentDrafts, type DocumentToolId } from '@/lib/document-content';
import type { NodeRegistry, ObjectValue, Schema, StudioResources, VariableOption } from './types';
export type DocumentNodeTool = DocumentDefinition & { inputSchema: Schema };
export const documentNodeType = 'document_generate';
const object = (v: unknown): v is ObjectValue => !!v && typeof v === 'object' && !Array.isArray(v);
export function documentNodeTools(body: unknown): DocumentNodeTool[] {
  const definitions = documentDefinitions(body), items = (body as { items: ObjectValue[] }).items;
  return definitions.map(definition => {
    const raw = items.find(item => item.id === definition.id)!;
    if (!definition.rendererAvailable || !definition.executionAvailable || raw.artifact_contract_version !== 'generated-document-artifact-v1' || raw.workflow_node_type !== documentNodeType || !Array.isArray(raw.execution_paths) || !raw.execution_paths.includes('workflow.document_generate') || raw.deterministic_run_url !== `/api/v1/tools/document-generators/${definition.id}/runs`) throw Error('当前目录尚未声明匹配的工作流文档节点合同，请更新匹配服务后重试。');
    return { ...definition, inputSchema: raw.input_schema };
  });
}
export function documentNodeContract(registry: NodeRegistry) {
  const node = registry.items.find(item => item.type === documentNodeType), c = node?.config_schema, i = node?.input_schema, o = node?.output_schema;
  const ids = c?.properties?.tool_id?.enum;
  return node?.status === 'supported' && node.category === 'tools' && c?.additionalProperties === false && Array.isArray(ids) && ids.slice().sort().join(',') === 'document.excel.v1,document.powerpoint.v1,document.word.v1' && i?.properties?.arguments?.type === 'object' && o?.properties?.artifact_ids?.type === 'array' && o.properties.artifact_ids.items?.type === 'string';
}
export function containsDocumentReferences(value: unknown, depth = 0): boolean {
  if (depth > 16) return true;
  if (Array.isArray(value)) return value.some(item => containsDocumentReferences(item, depth + 1));
  return object(value) && (Object.hasOwn(value, '$ref') || Object.values(value).some(item => containsDocumentReferences(item, depth + 1)));
}
/** Validate the actual advertised content schema, not the registry's generic OBJECT slot. */
export function documentValueProblems(value: unknown, schema: Schema, variables: VariableOption[], shapeOnly = false): string[] {
  if (new TextEncoder().encode(JSON.stringify(value) ?? '').length > 262144) return ['内容大小超过256KiB，请拆分文件。'];
  const issues: string[] = []; let count = 0;
  const visit = (v: any, s: Schema, at: string, depth: number): boolean => {
    if (++count > 30000 || depth > 16) { issues.push('文档内容结构超过可视检查上限。'); return false; }
    if (object(v) && Object.hasOwn(v, '$ref')) {
      const variable = variables.find(item => item.ref === v.$ref);
      const ok = typeof v.$ref === 'string' && !!v.$ref && !!variable && Object.keys(v).every(key => ['$ref', 'optional'].includes(key)) && (v.optional === undefined || typeof v.optional === 'boolean');
      if (!ok) issues.push(`${at}：变量来源不可用或格式不完整，请重新选择。`);
      return ok;
    }
    if (s.$ref) { const key = String(s.$ref).split('/').at(-1)!; const target = schema.$defs?.[key]; if (!target) { issues.push('工具内容Schema引用不受支持。'); return false; } return visit(v, target, at, depth + 1); }
    if (Array.isArray(s.anyOf)) {
      const branch = s.anyOf.find((candidate: Schema) => candidate.type === 'null' ? v === null : candidate.$ref ? object(v) : candidate.type === 'number' ? typeof v === 'number' : candidate.type === 'integer' ? Number.isInteger(v) : candidate.type === 'string' ? typeof v === 'string' : candidate.type === 'boolean' ? typeof v === 'boolean' : candidate.type === 'array' ? Array.isArray(v) : candidate.type === 'object' ? object(v) : false);
      if (!branch) { issues.push(`${at}：内容类型不匹配。`); return false; } return visit(v, branch, at, depth + 1);
    }
    let ok = true; const fail = (message: string) => { issues.push(`${at}：${message}`); ok = false; };
    if (s.type === 'object') {
      if (!object(v)) { fail('需为内容对象。'); return false; }
      if (s.additionalProperties === false && Object.keys(v).some(key => !Object.hasOwn(s.properties || {}, key))) fail('含工具未声明的字段，原值保留，请明确处理。');
      for (const key of s.required || []) if (!Object.hasOwn(v, key)) fail(`缺少${s.properties?.[key]?.title || key}。`);
      for (const [key, child] of Object.entries(s.properties || {})) if (Object.hasOwn(v, key)) ok = visit(v[key], child, `${at} · ${child.title || key}`, depth + 1) && ok;
    } else if (s.type === 'array') {
      if (!Array.isArray(v)) { fail('需为内容列表。'); return false; }
      if (!shapeOnly && (v.length < (s.minItems ?? 0) || v.length > (s.maxItems ?? 30000))) fail('数量不符合该工具限制。');
      for (let n = 0; n < v.length && count <= 30000; n++) ok = visit(v[n], s.items || {}, `${at}第${n + 1}项`, depth + 1) && ok;
    } else if (s.type === 'string') {
      if (typeof v !== 'string') fail('需为文字或文字变量。');
      else if (!shapeOnly && (Array.from(v).length < (s.minLength ?? 0) || Array.from(v).length > (s.maxLength ?? Infinity))) fail('文字长度不符合该工具限制。');
    } else if (s.type === 'integer' ? !Number.isSafeInteger(v) : s.type === 'number' ? typeof v !== 'number' || !Number.isFinite(v) : s.type === 'boolean' ? typeof v !== 'boolean' : s.type === 'null' ? v !== null : false) fail('值类型不符合该工具内容格式。');
    if (s.enum && !s.enum.includes(v)) fail('值不在工具支持的范围内。');
    return ok;
  };
  visit(value, schema, '内容', 0); return [...new Set(issues)].slice(0, 30);
}
export function documentFormDraft(id: DocumentToolId, value: unknown, schema: Schema): DocumentDrafts | null {
  if (containsDocumentReferences(value) || documentValueProblems(value, schema, [], true).length) return null;
  const drafts = emptyDocuments(), raw = structuredClone(value) as ObjectValue;
  if (id === 'document.word.v1') drafts[id] = { title: raw.title, subtitle: raw.subtitle ?? '', sections: raw.sections.map((section: ObjectValue) => ({ heading: section.heading ?? '', paragraphs: section.paragraphs ?? [], bullets: section.bullets ?? [], table: section.table ?? null })) };
  if (id === 'document.excel.v1') drafts[id] = { title: raw.title, description: raw.description ?? '', sheets: raw.sheets };
  if (id === 'document.powerpoint.v1') drafts[id] = { title: raw.title, slides: raw.slides.map((slide: ObjectValue) => ({ title: slide.title, subtitle: slide.subtitle ?? '', body: slide.table ? 'table' : 'bullets', bullets: slide.bullets ?? [], table: slide.table ?? { headers: ['列1'], rows: [['']] } })) };
  return drafts;
}
export function documentSourceOptions(resources: StudioResources) {
  return [
    ...resources.documents.map(item => ({ kind: 'document', resource_id: item.id, label: item.name || item.filename || '资料文件' })),
    ...(resources.dataTables || []).map(item => ({ kind: 'data_table', resource_id: item.id, label: item.name || '数据表' })),
    ...(resources.knowledgeVersions || []).map(item => ({ kind: 'knowledge_version', resource_id: item.id, label: `${item.library_name || '知识'} · ${item.resource_name || '发布版本'}` })),
  ].filter((item, index, all) => typeof item.resource_id === 'string' && all.findIndex(other => other.kind === item.kind && other.resource_id === item.resource_id) === index);
}
