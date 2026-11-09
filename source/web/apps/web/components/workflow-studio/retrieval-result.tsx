'use client';
import type { ObjectValue } from '@/lib/workflow/types';
import { TypedValue } from './typed-value';
import styles from './studio.module.css';

const kinds: Record<string, string> = { document: '文档片段', note: '知识笔记', qa_pair: '知识问答', record: '结构化记录' };
const methods: Record<string, string> = { keyword_overlap_v1: '关键词检索', semantic_rrf_zh_v1: '语义排序', hybrid_rrf_zh_v1: '混合排序', typed_legacy_rank_fusion_v1: '分区融合排序', partitioned_retrieval: '分区检索', fixed_empty_scope: '指定范围无结果' };
const scoreKinds: Record<string, string> = { keyword_overlap: '关键词得分', semantic_rrf: '语义排序分数', hybrid_rrf: '混合排序分数', partition_rank_fusion: '分区排序分数', service_score: '检索分数' };
function Hit({ item }: { item: ObjectValue }) {
  const content = item.content || {}; const source = item.source || {}; const locator = source.locator || {};
  return <article className={styles.retrievalCard}><div className={styles.fieldTitle}><strong>{kinds[item.kind] || '授权知识片段'}</strong>{item.score?.available === true && item.score.value !== undefined ? <small>{scoreKinds[item.score.kind] || '检索分数'}：{String(item.score.value)}</small> : <small>检索分数未提供</small>}</div>
    {item.truncated === true && <p className={styles.warning}>该片段已截断，当前为预览内容。</p>}
    {item.kind === 'qa_pair' ? <><h4>问题</h4><TypedValue value={content.question} /><h4>答案</h4><TypedValue value={content.answer} />{content.matched_purpose && <small>本次命中：{({ primary: '主问题', alias: '相似问', child: '子问' } as Record<string, string>)[content.matched_purpose] || '已授权问答'}</small>}</> : item.kind === 'record' && Array.isArray(content.fields) ? <dl className={styles.typedObject}>{content.fields.map((field: ObjectValue, i: number) => <div key={`${field.name}-${i}`}><dt>{field.label || field.name}<small>{field.type}</small></dt><dd><TypedValue value={field.value} />{field.unit !== undefined && <small>单位：{String(field.unit)}</small>}{field.currency !== undefined && <small>币种：{String(field.currency)}</small>}</dd></div>)}</dl> : <TypedValue value={item.kind === 'document' ? content.text : content.body} />}
    <div className={styles.retrievalSource}><strong>来源：{source.display_name || source.label || '已授权知识来源'}</strong>{source.display_name && source.label && <small>{source.label}</small>}{['page', 'paragraph', 'start_line', 'end_line', 'sheet', 'row', 'table'].some(key => locator[key] !== undefined) && <small>{Object.entries(locator).filter(([key]) => key !== 'kind').map(([key, value]) => `${({ page: '页', paragraph: '段落', start_line: '起始行', end_line: '结束行', sheet: '工作表', row: '行', table: '表格' } as Record<string, string>)[key] || key} ${String(value)}`).join(' · ')}</small>}
      <details><summary>固定版本与来源标识</summary><TypedValue value={Object.fromEntries(Object.entries(source).filter(([key]) => ['document_id', 'version_id', 'chunk_id', 'source_id', 'source_version_id', 'unit_id', 'revision_id', 'source_ref'].includes(key)))} /></details>
    </div>{item.parent_context && <details className={styles.details}><summary>本次授权的父级上下文</summary><Hit item={item.parent_context} /></details>}
  </article>;
}
/** Only the knowledge helper's explicit read-time whitelist is accepted. */
export function RetrievalResult({ value }: { value: unknown }) {
  const projection = value as ObjectValue | undefined;
  if (!projection || projection.schema_version !== 'knowledge-retrieval-trace-v1' || !Array.isArray(projection.items)) return <p className={styles.hint}>本次尚未提供可展示的授权命中片段与来源。</p>;
  return <section className={styles.retrievalResult}><h4>实际检索问题</h4><TypedValue value={projection.query} /><p className={styles.hint}>{methods[projection.method] || '检索方式由服务提供'} · 本次返回 {projection.returned_count ?? projection.items.length} 条 · 当前展示 {projection.displayed_count ?? projection.items.length} 条</p>{projection.truncated === true && <p role="status" className={styles.warning}>检索证据超过展示上限，当前为截断预览。</p>}{projection.items.length ? projection.items.map((item: ObjectValue, index: number) => <Hit key={`${item.citation_id}-${index}`} item={item} />) : <p className={styles.hint}>本次指定范围内未返回命中片段。</p>}</section>;
}
