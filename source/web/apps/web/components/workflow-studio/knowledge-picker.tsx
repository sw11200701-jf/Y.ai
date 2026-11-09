'use client';
import { useState } from 'react';
import type { ObjectValue, StudioResources } from '@/lib/workflow/types';
import styles from './studio.module.css';

export function KnowledgePicker({ value, resources, scope, onChange }: { value: ObjectValue; resources: StudioResources; scope?: ObjectValue[]; onChange: (value: ObjectValue) => void }) {
  const versions = (resources.knowledgeVersions || []).filter(version => scope === undefined || scope.some(resource => typeof resource.resource_id === 'string' && resource.enabled !== false && [version.knowledge_base_id, version.file_id, version.resource_id, version.source_id].includes(resource.resource_id)));
  const literal = Array.isArray(value.version_ids) && Array.isArray(value.knowledge_base_ids) && !value.file_ids;
  const [editing, setEditing] = useState(!value.file_ids && (!value.version_ids || literal));
  const selected: string[] = literal ? value.version_ids : [];
  const missing = selected.filter(id => !versions.some(version => version.id === id));
  const select = (id: string, checked: boolean) => {
    const ids = checked ? [...selected, id] : selected.filter(item => item !== id);
    const byId = new Map(versions.map(version => [version.id, version]));
    // Keep unavailable selections and their prior library scope until explicitly removed.
    const bases = [...new Set([...ids.map(item => byId.get(item)?.knowledge_base_id).filter(Boolean), ...(ids.some(item => !byId.has(item)) && Array.isArray(value.knowledge_base_ids) ? value.knowledge_base_ids : [])])];
    const next = { ...value }; delete next.file_ids;
    if (ids.length) { next.version_ids = ids; next.knowledge_base_ids = bases; } else { delete next.version_ids; delete next.knowledge_base_ids; }
    onChange(next);
  };
  return <section className={styles.knowledgePicker}><h3>固定知识版本</h3><p className={styles.hint}>选择已发布的资料版本。后续发布新版本不会改变本流程；检索结果提供切片正文和来源。</p>{!editing ? <><p className={styles.hint}>当前使用已有文件范围或变量引用，已保留原值。</p><button onClick={() => setEditing(true)}>改选固定发布版本</button></> : <>{!literal && (value.file_ids || value.version_ids || value.knowledge_base_ids) && <p className={styles.warning}>选择版本后会替换当前知识范围；尚未选择时保留原值。</p>}{versions.map(version => <label className={styles.knowledgeChoice} key={version.id}><input type="checkbox" checked={selected.includes(version.id)} disabled={!selected.includes(version.id) && selected.length >= 20} onChange={e => select(version.id, e.target.checked)} /><span><strong>{version.resource_name}</strong><small>{version.library_name} · {version.version ? `版本 ${version.version}` : version.published_at ? new Date(version.published_at).toLocaleString('zh-CN') : '已发布'}{version.unit_count !== undefined ? ` · ${version.unit_count} 个知识单元` : version.chunk_count !== undefined ? ` · ${version.chunk_count} 片` : ''}</small><details><summary>版本标识</summary><code>{version.id}</code></details></span></label>)}{!versions.length && <p className={styles.hint}>{resources.warnings.some(warning => warning.includes('知识')) ? '知识目录读取失败，请刷新资源重试。已保存引用会保留。' : '暂无可读的发布版本。请先在知识库发布资料，再刷新资源。'}</p>}{missing.map(id => <div className={styles.warning} key={id}>已保存版本暂不可读取，未自动替换。<details><summary>查看标识</summary><code>{id}</code></details><button onClick={() => select(id, false)}>移除此引用</button></div>)}</>}{resources.warnings.filter(warning => warning.includes('知识')).map((warning, i) => <p role="alert" className={styles.errorText} key={i}>{warning}</p>)}</section>;
}
