'use client';
import { useEffect, useRef, useState } from 'react';
import { currentIdentityEpoch } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { workflowApi } from '@/lib/workflow/api';
import { enumLabel } from '@/lib/workflow/labels';
import type { ObjectValue, StudioResources, VariableOption, WorkflowNode } from '@/lib/workflow/types';
import { ValueField } from './schema-fields';
import styles from './studio.module.css';

/** UI intersection only. Server authorization and Agent scope are rechecked at execution. */
export function scopedTables(tables: ObjectValue[], scope?: ObjectValue[]) {
  return scope === undefined ? tables : tables.filter(table => scope.some(resource => typeof resource.resource_id === 'string' && resource.enabled !== false && (resource.kind === 'data_table' && resource.resource_id === table.id || resource.kind === 'database' && resource.resource_id === table.database_id)));
}
const epoch = () => typeof currentIdentityEpoch === 'function' ? currentIdentityEpoch() : 0;
export function DatabaseQuery({ node, resources, scope, variables, onChange, onValidity }: { node: WorkflowNode; resources: StudioResources; scope?: ObjectValue[]; variables: VariableOption[]; onChange: (fields: ObjectValue) => void; onValidity: (valid: boolean) => void }) {
  const tableId = typeof node.inputs?.table_id === 'string' ? node.inputs.table_id : '';
  const versionId = node.config.table_version_id || '';
  const tables = scopedTables(resources.dataTables || [], scope);
  const selected = tables.find(table => table.id === tableId);
  const pinned = scope?.find(resource => resource.enabled !== false && resource.kind === 'data_table' && resource.resource_id === tableId)?.version_id;
  const versions = (resources.tableVersions || []).filter(version => version.table_id === tableId && (!pinned || version.id === pinned));
  const key = `${tableId}:${versionId}`;
  const [dictionary, setDictionary] = useState<{ key: string; catalog?: StudioResources; value?: ObjectValue; error?: string }>({ key: '' });
  const request = useRef(0); const invalid = useRef(new Set<string>());
  const validity = useRef(onValidity); validity.current = onValidity;
  const valid = (id: string) => (ok: boolean) => { ok ? invalid.current.delete(id) : invalid.current.add(id); validity.current(!invalid.current.size); };
  useEffect(() => () => validity.current(true), []);
  useEffect(() => {
    const token = ++request.current; const identity = epoch(); let live = true;
    if (!selected || !tableId || pinned && pinned !== versionId) return;
    setDictionary({ key, catalog: resources });
    workflowApi.tableDictionary(tableId, versionId || undefined).then(value => {
      if (!live || token !== request.current || identity !== epoch()) return;
      if (value.table_id !== tableId || versionId && value.version_id !== versionId) { setDictionary({ key, catalog: resources, error: '字段字典与所选表或版本不一致，请刷新资源。' }); return; }
      setDictionary({ key, catalog: resources, value });
    }).catch(error => { if (live && token === request.current && identity === epoch()) setDictionary({ key, catalog: resources, error: errorMessage(error) }); });
    return () => { live = false; };
  }, [key, selected, resources, pinned]);
  const visible = selected && (!pinned || pinned === versionId) && dictionary.key === key && dictionary.catalog === resources ? dictionary : undefined;
  const fields: ObjectValue[] = visible?.value?.schema_fields || [];
  const query = node.inputs?.query;
  const form = query === undefined || query && typeof query === 'object' && !Array.isArray(query) && !('$ref' in query);
  const update = (patch: ObjectValue) => onChange({ inputs: { ...node.inputs, query: { ...(query || {}), ...patch } } });
  const fieldSelect = (value: string, change: (value: string) => void, empty = '选择字段', extras: string[] = []) => <select aria-label={empty} value={value || ''} onChange={e => change(e.target.value)}><option value="">{empty}</option>{value && !fields.some(field => field.name === value) && !extras.includes(value) && <option value={value}>已保存字段（当前不可用）</option>}{fields.map(field => <option key={field.name} value={field.name}>{field.meaning || field.name}{field.unit ? ` · ${field.unit}` : ''}</option>)}{extras.filter(name => name && !fields.some(field => field.name === name)).map(name => <option key={name} value={name}>{name} · 统计结果</option>)}</select>;
  const list = (name: string): ObjectValue[] => Array.isArray(query?.[name]) ? query[name] : [];
  const patchList = (name: string, index: number, patch: ObjectValue) => update({ [name]: list(name).map((item, i) => i === index ? { ...item, ...patch } : item) });
  const remove = (name: string, index: number) => update({ [name]: list(name).filter((_, i) => i !== index) });
  return <section className={styles.databaseQuery}><h3>数据查询范围</h3><p className={styles.hint}>仅显示当前账号可用且在智能体配置范围内的发布数据表。运行时重新检查行列权限；更换表会清空查询条件。</p>
    <label className={styles.field}><span>数据库与数据表</span><select aria-label="查询数据表" value={tableId} onChange={e => {
      const config = { ...node.config }; delete config.database_id; delete config.table_version_id;
      const fixed = scope?.find(resource => resource.enabled !== false && resource.kind === 'data_table' && resource.resource_id === e.target.value)?.version_id;
      if (fixed) config.table_version_id = fixed;
      onChange({ config, inputs: { ...node.inputs, table_id: e.target.value, query: {} } });
    }}><option value="">选择发布数据表</option>{tableId && !selected && <option value={tableId}>已保存的数据表（当前不可用）</option>}{tables.map(table => <option key={table.id} value={table.id}>{table.database_name || '已授权数据'} · {table.name}</option>)}</select></label>
    {!tables.length && <p className={styles.hint}>暂无可选数据表。请检查资料授权、智能体数据范围和表的发布状态，然后刷新资源。</p>}
    {node.inputs?.table_id?.$ref && <p className={styles.warning}>动态表引用已保留。高阶智能体当前需要显式选择固定数据表。</p>}
    {tableId && !selected && <p role="alert" className={styles.errorText}>原有表引用已保留，当前不可配置或已撤权；请重新选择。</p>}
    <label className={styles.field}><span>数据版本</span><select aria-label="查询数据版本" disabled={!selected} value={versionId} onChange={e => { const config = { ...node.config }; if (e.target.value) config.table_version_id = e.target.value; else delete config.table_version_id; onChange({ config }); }}><option value="" disabled={Boolean(pinned)}>使用该表当前发布版本</option>{versionId && !versions.some(version => version.id === versionId) && <option value={versionId}>已保存版本（当前不可用）</option>}{versions.map(version => <option key={version.id} value={version.id}>{version.published_at ? new Date(version.published_at).toLocaleString('zh-CN') : '已发布'} · {version.id.slice(-8)}</option>)}</select></label>
    {visible?.error && <p role="alert" className={styles.errorText}>{visible.error}</p>}
    {pinned && versionId !== pinned ? <p role="alert" className={styles.errorText}>当前版本不在智能体固定版本范围内，请重新选择。</p> : selected && !visible?.value && !visible?.error && <p role="status" className={styles.hint}>正在读取已授权的字段字典…</p>}
    {visible?.value && <details className={styles.details}><summary>字段、单位与口径 · {fields.length} 项</summary>{fields.map(field => <p className={styles.hint} key={field.name}><strong>{field.meaning || field.name}</strong> · {field.type === 'decimal' ? '十进制数值' : enumLabel(field.type)}{field.unit ? ` · ${field.unit}` : ''}{field.caliber ? ` · ${field.caliber}` : ''}</p>)}<small>权限版本 {visible.value.policy_version} · 依据已发布表结构</small></details>}
    {!form ? <><p className={styles.hint}>当前使用查询变量或高级内容，原值已保留。高级输入中可继续设置变量。</p><button onClick={() => onChange({ inputs: { ...node.inputs, query: {} } })}>改用查询表单</button></> : <fieldset className={styles.queryForm} disabled={!visible?.value}><h3>返回字段</h3><p className={styles.hint}>未指定时由服务端按当前权限返回可用字段。</p>{fields.map(field => <label className={styles.check} key={field.name}><input type="checkbox" checked={Array.isArray(query?.select) && query.select.includes(field.name)} onChange={e => { const values = Array.isArray(query?.select) ? query.select : []; const next = e.target.checked ? [...values, field.name] : values.filter((name: string) => name !== field.name); update({ select: next.length ? next : null }); }} />{field.meaning || field.name}</label>)}
      <h3>筛选条件</h3>{list('filters').map((filter, index) => <div className={styles.fieldList} key={index}>{fieldSelect(filter.field, field => patchList('filters', index, { field, value: undefined }))}<select aria-label={`第${index + 1}条比较方式`} value={filter.op || 'eq'} onChange={e => patchList('filters', index, { op: e.target.value, value: e.target.value === 'in' ? [] : '' })}>{['eq', 'ne', 'in', 'gt', 'gte', 'lt', 'lte'].map(op => <option key={op} value={op}>{op === 'in' ? '属于列表' : enumLabel(op)}</option>)}</select><ValueField name="value" schema={{ type: filter.op === 'in' ? 'array' : fields.find(field => field.name === filter.field)?.type === 'boolean' ? 'boolean' : 'string', ...(filter.op === 'in' ? { items: { type: 'string' } } : {}) }} value={filter.value} onChange={value => patchList('filters', index, { value })} variables={variables} required allowReferences onValidity={valid(`filter-${index}`)} /><button onClick={() => remove('filters', index)}>移除条件</button></div>)}<button disabled={list('filters').length >= 20} onClick={() => update({ filters: [...list('filters'), { field: '', op: 'eq', value: '' }] })}>添加筛选条件</button>
      <h3>分组字段</h3>{fields.map(field => <label className={styles.check} key={field.name}><input type="checkbox" checked={Array.isArray(query?.group_by) && query.group_by.includes(field.name)} onChange={e => { const values = Array.isArray(query?.group_by) ? query.group_by : []; update({ group_by: e.target.checked ? [...values, field.name] : values.filter((name: string) => name !== field.name) }); }} />{field.meaning || field.name}</label>)}
      <h3>统计</h3>{list('aggregates').map((aggregate, index) => <div className={styles.fieldList} key={index}><select aria-label={`第${index + 1}项统计方式`} value={aggregate.op} onChange={e => patchList('aggregates', index, { op: e.target.value })}>{Object.entries({ count: '计数', sum: '合计', min: '最小值', max: '最大值', avg: '平均值' }).map(([op, label]) => <option key={op} value={op}>{label}</option>)}</select>{fieldSelect(aggregate.field, field => patchList('aggregates', index, { field: field || null }), aggregate.op === 'count' ? '统计行数（可不选字段）' : '选择统计字段')}<input aria-label={`第${index + 1}项统计名称`} placeholder="统计结果名称" value={aggregate.name || ''} onChange={e => patchList('aggregates', index, { name: e.target.value })} /><button onClick={() => remove('aggregates', index)}>移除统计</button></div>)}<button disabled={list('aggregates').length >= 16} onClick={() => update({ aggregates: [...list('aggregates'), { op: 'count', name: `数量${list('aggregates').length + 1}` }] })}>添加统计</button>
      <h3>排序</h3>{list('order_by').map((sort, index) => <div className={styles.fieldList} key={index}>{fieldSelect(sort.field, field => patchList('order_by', index, { field }), '排序字段', list('aggregates').map(item => item.name))}<label className={styles.check}><input type="checkbox" checked={Boolean(sort.descending)} onChange={e => patchList('order_by', index, { descending: e.target.checked })} />降序</label><button onClick={() => remove('order_by', index)}>移除排序</button></div>)}<button disabled={list('order_by').length >= 8} onClick={() => update({ order_by: [...list('order_by'), { field: '', descending: false }] })}>添加排序</button>
      <label className={styles.field}><span>最多返回行数</span><input aria-label="查询返回行数" type="number" min={1} max={1000} value={query?.limit ?? 100} onChange={e => update({ limit: Number(e.target.value) })} /></label><label className={styles.field}><span>跳过前几行</span><input aria-label="查询起始位置" type="number" min={0} max={100000} value={query?.offset ?? 0} onChange={e => update({ offset: Number(e.target.value) })} /></label>
    </fieldset>}
    {resources.warnings.filter(warning => /数据库|数据表/.test(warning)).map((warning, index) => <p className={styles.errorText} key={index}>{warning}</p>)}
    <details className={styles.details}><summary>已保存的范围标识</summary><code>{tableId || '尚未选择'}{versionId ? ` · ${versionId}` : ''}</code></details>
  </section>;
}
