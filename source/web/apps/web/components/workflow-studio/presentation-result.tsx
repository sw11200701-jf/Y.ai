'use client';
import { useState } from 'react';
import { download } from '@/lib/api';
import type { ObjectValue } from '@/lib/workflow/types';
import { errorMessage } from '@/lib/errors';
import styles from './studio.module.css';
const shown = (value: unknown) => value === null || value === undefined ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
function PresentedTable({ table }: { table: ObjectValue }) {
  const [page, setPage] = useState(0); const rows = Array.isArray(table.rows) ? table.rows : []; const columns = Array.isArray(table.columns) ? table.columns : [];
  return <div><div className={styles.presentedTable}><table><thead><tr>{columns.map((column: any, i: number) => <th key={i}>{typeof column === 'string' ? column : column.label || column.title || column.key || column.name || String(i + 1)}</th>)}</tr></thead><tbody>{rows.slice(page * 100, (page + 1) * 100).map((row: any, i: number) => <tr key={i}>{columns.map((column: any, j: number) => { const key = typeof column === 'string' ? column : column.key || column.dataIndex || column.name || String(j); return <td key={j}>{shown(Array.isArray(row) ? row[j] : row?.[key])}</td>; })}</tr>)}</tbody></table></div><div className={styles.tablePager}><span>{rows.length} 行 · 第 {page + 1} 页</span><button disabled={page === 0} onClick={() => setPage(p => p - 1)}>上一页</button><button disabled={(page + 1) * 100 >= rows.length} onClick={() => setPage(p => p + 1)}>下一页</button></div></div>;
}
export function PresentationResult({ items }: { items: ObjectValue[] }) {
  const [error, setError] = useState('');
  return <div className={styles.presentations}>{items.map((item, index) => <section key={`${item.node_id}-${index}`}><small>输出 · {item.node_id}</small>{item.kind === 'json' && Object.hasOwn(item, 'json') ? <pre aria-label="JSON 格式输出">{JSON.stringify(item.json, null, 2)}</pre> : <>{typeof item.text === 'string' && <div className={styles.presentedText}>{item.text}</div>}{item.table && <PresentedTable table={item.table} />}{Array.isArray(item.files) && item.files.map((file: ObjectValue) => <button key={file.id} onClick={async () => { try { setError(''); if (!file.id) throw new Error('缺少受控文件标识'); await download(`/artifacts/${encodeURIComponent(file.id)}/download`, file.filename || 'output'); } catch (e) { setError(errorMessage(e)); } }}>↓ {file.filename || '下载文件'}</button>)}</>}</section>)}{error && <p role="alert" className={styles.errorText}>{error}</p>}</div>;
}
