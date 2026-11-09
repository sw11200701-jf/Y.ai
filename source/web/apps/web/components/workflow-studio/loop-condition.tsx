'use client';
import type { ObjectValue, StudioResources, VariableOption } from '@/lib/workflow/types';
import { ValueField } from './schema-fields';
import {enumLabel} from '@/lib/workflow/labels';
import styles from './studio.module.css';

export function LoopCondition({ value, state, labels = {}, resources, onChange, onValidity }: { value?: ObjectValue; state?: ObjectValue; labels?:ObjectValue; resources: StudioResources; onChange: (until: ObjectValue) => void; onValidity: (valid: boolean) => void }) {
  const variables: VariableOption[] = [{ ref: 'loop.state', label: '循环本轮输出状态', type: 'object' }, { ref: 'loop.index', label: '循环序号', type: 'integer' }, ...Object.entries(state || {}).map(([key, value]) => ({ ref: `loop.state.${key}`, label: `循环状态 · ${labels[key] || key}`, type: Array.isArray(value) ? 'array' : value === null ? 'any' : typeof value }))];
  return <section><h3>循环停止条件</h3><p className={styles.hint}>每轮执行后，用本轮输出状态判断是否停止。</p><ValueField name="left" schema={{title:'检查哪个状态'}} value={value?.left} variables={variables} required allowReferences onValidity={onValidity} onChange={left=>onChange({...value,left})}/><label className={styles.field}><span>停止比较方式</span><select aria-label="停止比较方式" value={value?.operator||''} onChange={event=>onChange({...value,operator:event.target.value})}><option value="">选择比较方式</option>{['eq','ne','gt','gte','lt','lte','contains','empty','not_empty'].map(operator=><option key={operator} value={operator}>{enumLabel(operator)}</option>)}</select></label>{!['empty','not_empty'].includes(value?.operator||'')&&<ValueField name="right" schema={{title:'达到什么值时停止'}} value={value?.right} variables={variables} required={false} allowReferences onValidity={onValidity} onChange={right=>onChange({...value,right})}/>}</section>;
}
