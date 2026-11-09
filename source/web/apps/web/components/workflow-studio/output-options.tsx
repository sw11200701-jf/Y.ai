'use client';
import { useRef } from 'react';
import type { NodeDefinition, ObjectValue } from '@/lib/workflow/types';
import { enumLabel } from '@/lib/workflow/labels';
import styles from './studio.module.css';

export function OutputOptions({ value, definition, onChange }: { value: ObjectValue; definition?: NodeDefinition; onChange: (config: ObjectValue) => void }) {
  const previous = useRef(typeof value.presentation === 'string' && value.presentation !== 'json' ? value.presentation : 'auto');
  const formats = definition?.config_schema.properties?.presentation?.enum || ['auto', 'text', 'table', 'files', 'mixed'];
  const json = value.presentation === 'json'; const supported = formats.includes('json');
  return <section><h3>输出格式</h3><label className={styles.check}><input type="checkbox" checked={json} disabled={!supported && !json} onChange={e => { if (!json) previous.current = value.presentation || 'auto'; onChange({ ...value, presentation: e.target.checked ? 'json' : previous.current }); }} />输出为 JSON</label>{!supported && <p className={styles.hint}>当前服务尚未声明 JSON 展示能力，已保存的配置会保留。</p>}<label className={styles.field}><span>展示方式</span><select aria-label="输出展示方式" disabled={json} value={value.presentation || 'auto'} onChange={e => onChange({ ...value, presentation: e.target.value })}>{json && <option value="json">JSON</option>}{!formats.includes(value.presentation || 'auto') && !json && <option value={value.presentation}>已保存的展示方式</option>}{formats.filter(item => item !== 'json').map(item => <option value={item} key={item}>{enumLabel(item)}</option>)}</select></label><p className={styles.hint}>输出保留字段的真实类型。JSON 开关调整展示格式，0、否、空文本都不会被替换。</p></section>;
}
