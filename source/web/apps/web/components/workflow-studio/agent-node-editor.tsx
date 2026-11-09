'use client';
import { useEffect, useRef, useState } from 'react';
import type { ObjectValue, StudioResources, VariableOption } from '@/lib/workflow/types';
import { nestedAgentVersions } from '@/lib/workflow/agent-node';
import { ValueField } from './schema-fields';
import styles from './studio.module.css';

export function AgentNodeEditor({ config, inputs, resources, variables, onChange, onValidity }: {
  config: ObjectValue; inputs: ObjectValue; resources: StudioResources; variables: VariableOption[];
  onChange: (patch: { config?: ObjectValue; inputs?: ObjectValue }) => void; onValidity: (valid: boolean) => void;
}) {
  const versions = nestedAgentVersions(resources.agentVersions);
  const agents = resources.agents.filter(agent => versions.some(version => version.agent_id === agent.id));
  const choices = versions.filter(version => version.agent_id === config.agent_id);
  const selected = choices.find(version => version.id === config.agent_version_id);
  const schema = selected?.config?.input_schema || { type: 'object', properties: {}, additionalProperties: false };
  const values = inputs.inputs || {};
  const wholeReference = typeof values === 'object' && typeof values.$ref === 'string';
  const [invalid, setInvalid] = useState<Record<string, boolean>>({});
  const validity = useRef(onValidity); validity.current = onValidity;
  const extras = wholeReference ? [] : Object.keys(values).filter(key => !Object.hasOwn(schema.properties || {}, key));
  const activeKeys = new Set(['prompt', ...(inputs.query !== undefined ? ['query'] : []), ...(wholeReference ? ['inputs'] : Object.keys(schema.properties || {}).map(key => `inputs.${key}`))]);
  const missing = !wholeReference && (schema.required || []).some((key: string) => values[key] === undefined || typeof values[key]?.$ref === 'string' && !values[key].$ref);
  const valid = !!selected && inputs.prompt !== undefined && !(typeof inputs.prompt?.$ref === 'string' && !inputs.prompt.$ref) && !missing && !Object.entries(invalid).some(([key, bad]) => activeKeys.has(key) && bad) && !(schema.additionalProperties === false && extras.length);
  useEffect(() => { validity.current(valid); }, [valid, config.agent_id, config.agent_version_id]);
  const check = (key: string) => (ok: boolean) => setInvalid(old => old[key] === !ok ? old : { ...old, [key]: !ok });
  const put = (key: string, value: any) => { const next = { ...inputs }; if (value === undefined) delete next[key]; else next[key] = value; onChange({ inputs: next }); };
  const map = (key: string, value: any) => { const next = { ...values }; if (value === undefined) delete next[key]; else next[key] = value; put('inputs', next); };
  return <section>
    <p className={styles.hint}>选择可嵌套的简单智能体及固定发布版本。主流程智能体与旧Skill绑定不支持嵌套；后续发布不会替换当前选择。</p>
    <label className={styles.field}><span>调用哪个智能体 *</span><select aria-label="调用智能体" value={config.agent_id || ''} onChange={event => { const next: ObjectValue = { ...config, agent_id: event.target.value }; delete next.agent_version_id; onChange({ config: next }); }}><option value="">选择已发布简单智能体</option>{config.agent_id && !agents.some(agent => agent.id === config.agent_id) && <option value={config.agent_id}>已保存智能体（当前不可用）</option>}{agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
    <label className={styles.field}><span>固定发布版本 *</span><select aria-label="智能体发布版本" value={config.agent_version_id || ''} onChange={event => { const next = { ...config }; if (event.target.value) next.agent_version_id = event.target.value; else delete next.agent_version_id; onChange({ config: next }); }}><option value="">选择固定发布版本</option>{config.agent_version_id && !selected && <option value={config.agent_version_id}>已保存版本（当前不可用）</option>}{choices.map(version => <option key={version.id} value={version.id}>版本 {version.version} · {version.name}</option>)}</select></label>
    {!selected && <p role="alert" className={styles.warning}>请明确选择可用的固定发布版本。已保存的不可用引用会保留，不会自动改用草稿或其他版本。</p>}
    <ValueField name="prompt" schema={{type:'string',title:'交给智能体的任务'}} value={inputs.prompt} required variables={variables} allowReferences onChange={value => put('prompt', value)} onValidity={check('prompt')} />
    {selected && <><h3>子智能体业务输入</h3>{missing && <p role="alert">请完成发布版本声明的必填业务输入映射。</p>}{wholeReference ? <><ValueField name="inputs" schema={schema} value={values} variables={variables} allowReferences required onChange={value => put('inputs',value)} onValidity={check('inputs')} /><button type="button" onClick={() => put('inputs',{})}>改为逐字段映射</button></> : <>{Object.entries(schema.properties || {}).map(([key, field]: [string, any]) => <ValueField key={`${selected.id}:${key}`} name={key} schema={field} value={values[key]} required={schema.required?.includes(key)} variables={variables} allowReferences onChange={value => map(key,value)} onValidity={check(`inputs.${key}`)} />)}{!Object.keys(schema.properties || {}).length && <p>此发布版本未声明业务输入字段。</p>}{extras.map(key => <p key={key} className={styles.warning}>保留的映射“{key}”未在此版本声明。<button type="button" onClick={() => map(key,undefined)}>移除此映射</button></p>)}</>}</>}
    {inputs.query !== undefined && <ValueField name="query" required={false} schema={{type:'object',title:'已有业务查询口径'}} value={inputs.query} variables={variables} allowReferences onChange={value => put('query',value)} onValidity={check('query')} />}
  </section>;
}
