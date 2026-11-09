'use client';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { currentIdentityEpoch, request } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import DocumentContentFields from '../document-content-fields';
import { documentArguments, documentProblems, emptyDocuments, type DocumentDrafts, type DocumentToolId } from '@/lib/document-content';
import { containsDocumentReferences, documentFormDraft, documentNodeContract, documentNodeTools, documentSourceOptions, documentValueProblems, type DocumentNodeTool } from '@/lib/workflow/document-node';
import type { NodeRegistry, ObjectValue, StudioResources, VariableOption, WorkflowNode } from '@/lib/workflow/types';
import styles from './studio.module.css';
const epoch = () => typeof currentIdentityEpoch === 'function' ? currentIdentityEpoch() : 0;
const isReference = (v: unknown): v is { $ref: string } => !!v && typeof v === 'object' && !Array.isArray(v) && typeof (v as ObjectValue).$ref === 'string';
export function DocumentNodeEditor({ node, registry, resources, variables, onChange, onValidity, advanced }: { node: WorkflowNode; registry: NodeRegistry; resources: StudioResources; variables: VariableOption[]; onChange: (patch: Partial<WorkflowNode>) => void; onValidity: (valid: boolean) => void; advanced?: ReactNode }) {
  const [tools, setTools] = useState<DocumentNodeTool[]>([]), [loading, setLoading] = useState(true), [error, setError] = useState(''), [attempt, setAttempt] = useState(0), [locked, setLocked] = useState(false);
  const [drafts, setDrafts] = useState<DocumentDrafts>(emptyDocuments), cache = useRef<Partial<Record<DocumentToolId, DocumentDrafts>>>({}), applied = useRef('');
  const valid = useRef(onValidity); valid.current = onValidity;
  useEffect(() => {
    let live = true; const actor = epoch(), controller = new AbortController(); setTools([]); setLoading(true); setError('');
    const clear = () => { live = false; controller.abort(); cache.current = {}; setDrafts(emptyDocuments()); setTools([]); setLoading(false); setLocked(true); setError('身份或权限已变化，请重新打开文档节点。'); };
    const signals = ['zhangpu:session-expired', 'zhangpu:access-denied', 'zhangpu:permissions-changed', 'zhangpu:identity-changed']; signals.forEach(signal => window.addEventListener(signal, clear));
    void request('/tools/document-generators', { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) }).then(body => { if (!live || actor !== epoch()) return; const list = documentNodeTools(body); if (!list.length) throw Error('当前没有可用的固定文档工具。'); setTools(list); }).catch(failure => { if (live && actor === epoch()) setError(errorMessage(failure)); }).finally(() => { if (live && actor === epoch()) setLoading(false); });
    return () => { live = false; controller.abort(); signals.forEach(signal => window.removeEventListener(signal, clear)); };
  }, [attempt]);
  const config = node.config || {}, inputs = node.inputs || {}, tool = tools.find(item => item.id === config.tool_id), argument = inputs.arguments, serialized = JSON.stringify([config.tool_id, argument]);
  const sources = documentSourceOptions(resources), selected = Array.isArray(inputs.source_resources) ? inputs.source_resources : [], reference = isReference(argument), hasReferences = containsDocumentReferences(argument);
  const form = tool ? documentFormDraft(tool.id, argument, tool.inputSchema) : null;
  useEffect(() => { if (applied.current === serialized || !tool) return; applied.current = serialized; const value = documentFormDraft(tool.id, argument, tool.inputSchema); if (value) { cache.current[tool.id] = value; setDrafts(value); } }, [serialized, tool]);
  const issues: string[] = [];
  if (!documentNodeContract(registry)) issues.push('当前节点目录未声明匹配的固定文档生成合同。');
  if (!tool) issues.push('请选择当前目录中可用的文档格式。');
  if (Object.keys(config).some(key => key !== 'tool_id')) issues.push('配置含旧的额外字段，原值保留，请在高级兼容中明确处理。');
  if (Object.keys(inputs).some(key => !['arguments', 'source_resources'].includes(key))) issues.push('输入含工具未声明的字段，不能仅按通用对象认为已完成。');
  if (tool) {
    issues.push(...documentValueProblems(argument, tool.inputSchema, variables));
    if (form) { try { issues.push(...documentProblems(tool.id, form)); } catch { issues.push('已有内容暂不能由普通表单表示，原值保留，请明确处理。'); } }
  }
  if (Object.hasOwn(inputs, 'source_resources')) {
    if (isReference(inputs.source_resources)) { if (!variables.some(item => item.type === 'array' && item.ref === inputs.source_resources.$ref)) issues.push('来源列表变量不可用，请重新选择。'); }
    else if (!Array.isArray(inputs.source_resources) || selected.length > 32 || selected.some(item => !item || typeof item.kind !== 'string' || typeof item.resource_id !== 'string' || Object.keys(item).some(key => !['kind', 'resource_id'].includes(key)) || !sources.some(source => source.kind === item.kind && source.resource_id === item.resource_id)) || new Set(selected.map(item => JSON.stringify(item))).size !== selected.length) issues.push('部分显式来源当前不可选择或格式异常，请移除不可用来源或重新选择；后台仍检查export_detail。');
  }
  const issueKey = [...new Set(issues)].join('\n');
  useEffect(() => { valid.current(!locked && !loading && !error && !issueKey); }, [locked, loading, error, issueKey]); useEffect(() => () => valid.current(true), []);
  const updateContent = (id: DocumentToolId, value: DocumentDrafts) => { cache.current[id] = value; setDrafts(value); const argumentsValue = documentArguments(id, value); applied.current = JSON.stringify([id, argumentsValue]); onChange({ inputs: { ...inputs, arguments: argumentsValue } }); };
  if (locked) return <p role="alert">{error}</p>;
  return <section className={styles.nodeSettings}><h3>生成Word / Excel / PPT文件</h3><p className={styles.hint}>直接按所填内容生成一个真实文件，不调用模型；只有明确试运行才提交执行。保存配置只持久化节点草稿。</p>
    {loading && <p>正在读取实际工具版本与内容格式…</p>}{error && <p role="alert">{error}</p>}<button disabled={loading} onClick={() => setAttempt(value => value + 1)}>刷新文档工具目录</button>
    <label className={styles.field}><span>文档格式</span><select aria-label="生成文档格式" value={config.tool_id || ''} disabled={loading || !!error} onChange={event => { const next = tools.find(item => item.id === event.target.value); if (!next) return; const value = cache.current[next.id] || emptyDocuments(); cache.current[next.id] = value; setDrafts(value); const argumentsValue = documentArguments(next.id, value); applied.current = JSON.stringify([next.id, argumentsValue]); onChange({ config: { ...config, tool_id: next.id }, inputs: { ...inputs, arguments: argumentsValue } }); }}><option value="">选择实际可用格式</option>{config.tool_id && !tool && <option value={config.tool_id}>已保存格式（当前不可用）</option>}{tools.map(item => <option key={item.id} value={item.id}>{item.name} · 版本 {item.version}</option>)}</select></label>
    {issueKey && <div role="status" className={styles.configurationChecklist}><strong>还需要完成</strong><ul>{issueKey.split('\n').map(item => <li key={item}>{item}</li>)}</ul></div>}
    {tool && !loading && !error && <><h4>内容</h4><p className={styles.hint}>切换格式保留本次表单草稿；保存只提交当前所选格式。文本、0、否与空值分别保留，文件不执行公式或读取网址。</p>
      {form && !hasReferences ? <DocumentContentFields id={tool.id} value={applied.current===serialized?drafts:form} onChange={value => updateContent(tool.id, value)} /> : <p className={styles.hint}>{hasReferences ? '已保存完整内容或字段级变量引用，保持原值；实际内容在执行前按该工具完整Schema校验。' : '已有内容结构暂不能直接显示为普通表单，原值已完整保留。'}</p>}
      <label className={styles.field}><span>或使用上游完整内容对象</span><select aria-label="文档内容对象来源" value={reference ? argument.$ref : ''} onChange={event => { if (!event.target.value) return; onChange({ inputs: { ...inputs, arguments: { $ref: event.target.value } } }); }}><option value="">选择已连接的对象变量</option>{reference && !variables.some(item => item.ref === argument.$ref) && <option value={argument.$ref}>已保存来源（当前不可用）</option>}{variables.filter(item => ['object', 'any'].includes(item.type)).map(item => <option key={item.ref} value={item.ref}>{item.label}</option>)}</select></label>
      {(!form || hasReferences) && <button onClick={() => updateContent(tool.id, cache.current[tool.id] || emptyDocuments())}>明确改为普通内容表单</button>}
      <details className={styles.details}><summary>显式资料来源（可选）</summary><p>只在这里增加需要绑定的可见资料。省略不会清除本次run已经产生的来源；读取权限不等于导出权限，生成和下载均由服务器重验export_detail。</p>
        {isReference(inputs.source_resources) ? <><span>已保存来源列表变量，原值保留。</span><button onClick={() => { const next = { ...inputs }; delete next.source_resources; onChange({ inputs: next }); }}>明确移除显式来源变量</button></> : <>{sources.map(source => <label key={source.kind + source.resource_id} className={styles.check}><input type="checkbox" aria-label={`文档来源：${source.label}`} checked={selected.some(item => item.kind === source.kind && item.resource_id === source.resource_id)} disabled={selected.length >= 32 && !selected.some(item => item.kind === source.kind && item.resource_id === source.resource_id)} onChange={event => { const next = event.target.checked ? [...selected, { kind: source.kind, resource_id: source.resource_id }] : selected.filter(item => item.kind !== source.kind || item.resource_id !== source.resource_id); onChange({ inputs: { ...inputs, source_resources: next } }); }} />{source.label}</label>)}{selected.some(item => !sources.some(source => source.kind === item?.kind && source.resource_id === item?.resource_id)) && <button onClick={() => onChange({ inputs: { ...inputs, source_resources: selected.filter(item => sources.some(source => source.kind === item?.kind && source.resource_id === item?.resource_id)) } })}>明确移除当前不可用来源</button>}</>}
      </details><p className={styles.hint}>输出提供本步骤“产物文件引用”。把它连接到结束节点“返回文件”即可下载；不读取兄弟节点或扫描整个run的文件。</p></>}{advanced}
  </section>;
}
