'use client';
import { fieldLabel } from '@/lib/workflow/labels';
import styles from './studio.module.css';

/** Display JSON types without treating a falsy value as a missing result. */
export function TypedValue({ value, depth = 0, fieldLabels = {} }: { value: unknown; depth?: number; fieldLabels?:Record<string,string> }) {
  if (value === undefined) return <span className={styles.missingValue}>未提供</span>;
  if (value === null) return <span className={styles.missingValue}>空值</span>;
  if (typeof value === 'boolean') return <span>{value ? '是（true）' : '否（false）'}</span>;
  if (typeof value === 'number') return <span>{String(value)}</span>;
  if (typeof value === 'string') return <div className={styles.typedText}>{value === '' ? <span className={styles.missingValue}>空文本（已返回）</span> : value}</div>;
  if (!Array.isArray(value) && (value as Record<string, unknown>).truncated === true) return <div className={styles.warning}>内容超过展示上限，当前显示截断预览，不能视为完整结果。{typeof (value as any).preview === 'string' && <div className={styles.typedText}>{(value as any).preview}</div>}</div>;
  if (depth >= 6) return <details className={styles.details}><summary>展开深层结构</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
  if (Array.isArray(value)) return value.length ? <ol className={styles.typedList}>{value.slice(0, 100).map((item, i) => <li key={i}><TypedValue value={item} depth={depth + 1} fieldLabels={fieldLabels} /></li>)}{value.length > 100 && <li>共 {value.length} 项，当前显示前 100 项；完整结构见技术详情。</li>}</ol> : <span className={styles.missingValue}>空列表（0 项）</span>;
  const entries = Object.entries(value as Record<string, unknown>);
  return entries.length ? <dl className={styles.typedObject}>{entries.slice(0, 100).map(([key, item]) => <div key={key}><dt>{fieldLabels[key] || fieldLabel(key)}<small>{key}</small></dt><dd><TypedValue value={item} depth={depth + 1} fieldLabels={fieldLabels} /></dd></div>)}{entries.length > 100 && <div>当前显示前 100 个字段；完整结构见技术详情。</div>}</dl> : <span className={styles.missingValue}>空对象（0 个字段）</span>;
}
