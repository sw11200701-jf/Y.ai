'use client';
import { useEffect, useRef, useState } from 'react';
import { Modal } from 'antd';
import { currentIdentityEpoch } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { workflowApi } from '@/lib/workflow/api';
import type { CheckedTestTarget, NodeRegistry, ObjectValue, StudioResources, TestVariables, VariableOption, WorkflowDefinition, WorkflowNode } from '@/lib/workflow/types';
import { inputDefaults } from '@/lib/workflow/debug-inputs';
import { checkDescription, scopeWorkflow, scopedTestVariables } from '@/lib/workflow/scoped-debug';
import { SchemaFields, ValueField } from './schema-fields';
import styles from './studio.module.css';

const epoch = () => typeof currentIdentityEpoch === 'function' ? currentIdentityEpoch() : 0;
const objectDefaults = (schema: import('@/lib/workflow/types').Schema) => inputDefaults(schema, {});
export function NodeTestDialog({ node, workflow, scopePath = [], inputs, resources, busy, container, onClose, onTest }: { node: WorkflowNode; workflow: WorkflowDefinition; scopePath?: string[]; registry: NodeRegistry | null; variables: VariableOption[]; inputs: ObjectValue; resources: StudioResources; busy: boolean; container: () => HTMLElement; onClose: () => void; onTest: (inputs: ObjectValue, variables: TestVariables, target: CheckedTestTarget) => Promise<void> }) {
  const [checked, setChecked] = useState<CheckedTestTarget | null>(null); const [loading, setLoading] = useState(true); const [attempt, setAttempt] = useState(0);
  const [business, setBusiness] = useState<ObjectValue>({}); const [nodes, setNodes] = useState<ObjectValue>({}); const [vars, setVars] = useState<ObjectValue>({}); const [scope, setScope] = useState<ObjectValue>({});
  const [invalid, setInvalid] = useState<Record<string, boolean>>({}); const [error, setError] = useState('');
  const ticket = useRef(0); const pending = useRef(false); const latest = useRef(workflow); latest.current = workflow;
  const serialized = JSON.stringify(workflow); const pathKey = JSON.stringify(scopePath);
  useEffect(() => {
    let live = true; const id = ++ticket.current; const actor = epoch(); const snapshot = JSON.parse(serialized) as WorkflowDefinition; const path = JSON.parse(pathKey) as string[];
    setLoading(true); setChecked(null); setError(''); setInvalid({}); setNodes({}); setVars({}); setScope({});
    void workflowApi.debugTarget(snapshot, node.id, path).then(description => {
      if (!live || id !== ticket.current || actor !== epoch()) return;
      checkDescription(description, snapshot, node.id, path);
      setChecked({ description, workflow: snapshot });
      setBusiness(inputDefaults(description.workflow_input_schema, path.length ? {} : inputs)); setVars(objectDefaults(description.variables_schema));
      setScope(description.scope_type === 'loop' ? { state: {}, index: 0 } : description.scope_type === 'iteration' ? { index: 0 } : {});
    }).catch(error => { if (live && id === ticket.current && actor === epoch()) setError(errorMessage(error)); }).finally(() => { if (live && id === ticket.current && actor === epoch()) setLoading(false); });
    return () => { live = false; ticket.current++; };
  }, [serialized, pathKey, node.id, attempt]);
  const validity = (key: string) => (valid: boolean) => setInvalid(previous => previous[key] === !valid ? previous : { ...previous, [key]: !valid });
  const description = checked?.description;
  const local = checked ? scopeWorkflow(checked.workflow, checked.description.scope_path) : null;
  const submit = async () => {
    if (!checked || loading || busy || pending.current || Object.values(invalid).some(Boolean)) return;
    pending.current = true; const id = ticket.current; const actor = epoch();
    try {
      setError('');
      if (JSON.stringify(checked.workflow) !== JSON.stringify(latest.current)) throw new Error('草稿已变化，请重新检查测试目标');
      const variables = scopedTestVariables(checked.description, nodes, vars, scope);
      await onTest(business, variables, checked);
    } catch (error) { if (id === ticket.current && actor === epoch()) setError(errorMessage(error)); }
    finally { if (id === ticket.current) pending.current = false; }
  };
  return <Modal open title={`测试此节点 · ${node.ui?.label || node.id}`} okText="运行此节点" cancelText="取消" confirmLoading={busy} maskClosable={!busy} keyboard={!busy} closable={!busy} cancelButtonProps={{ disabled: busy }} okButtonProps={{ disabled: busy || loading || !checked || Object.values(invalid).some(Boolean) }} getContainer={container} onCancel={() => { if (!busy) onClose(); }} onOk={submit}>
    <div className={styles.nodeTestForm}><p>仅执行当前目标节点，不会运行外围循环、同层上游、兄弟节点或其他迭代。账号与系统信息由后台提供。</p>
      {loading && <p role="status">正在检查当前草稿中的测试目标…</p>}
      {!loading && !checked && <button onClick={() => setAttempt(value => value + 1)}>重新检查测试目标</button>}
      {description && <><p className={styles.hint}>{description.scope_path.length ? `子流程：${description.scope_path.map(id => id).join(' / ')}` : '根画布节点'} · {node.ui?.label || node.id}</p><details className={styles.details}><summary>本次测试范围与限制</summary><code>{description.target_key}</code><p className={styles.hint}>步骤上限 {description.limits.max_steps} · 超时 {description.limits.timeout_seconds} 秒 · 并行上限 {description.limits.max_concurrency}</p></details>
        <fieldset disabled={busy || loading} className={styles.testFieldset}>
          <h3>本次业务输入</h3><SchemaFields label="测试业务输入" schema={description.workflow_input_schema} value={business} resources={resources} variables={[]} onChange={setBusiness} onValidity={validity('business')} />
          {description.scope_type && description.scope_schema && <><h3>{description.scope_type === 'iteration' ? '本次迭代的当前项与序号' : '本次循环的状态与序号'}</h3><SchemaFields label="本次业务上下文" schema={description.scope_schema} value={scope} resources={resources} variables={[]} onChange={setScope} onValidity={validity('scope')} /><p className={styles.hint}>只填写最近外围{description.scope_type === 'iteration' ? '迭代' : '循环'}在本次测试中的业务值；序号范围由服务端描述限定，不会执行外围步骤获取值。</p></>}
          {Object.keys(description.upstream_schemas).length > 0 && <h3>目标引用的同层上游输出</h3>}
          {Object.entries(description.upstream_schemas).map(([id, schema]) => <ValueField key={id} name={id} schema={{ ...schema, title: `${local?.nodes.find(node => node.id === id)?.ui?.label || id} · 同层上游输出` }} value={nodes[id]} required={false} variables={[]} allowReferences={false} onValidity={validity(`nodes.${id}`)} onChange={next => setNodes(previous => { const value = { ...previous }; if (next === undefined) delete value[id]; else value[id] = next; return value; })} />)}
          {Object.keys(description.variables_schema.properties || {}).length > 0 && <><h3>本层声明的全局变量</h3><SchemaFields label="本层测试变量" schema={description.variables_schema} value={vars} resources={resources} variables={[]} onChange={setVars} onValidity={validity('vars')} /></>}
          <p className={styles.hint}>账号、角色、权限、租户与工作空间不可编辑或模拟。资源访问仍由当前后台权限决定。</p>
        </fieldset></>}
      {error && <p role="alert" className={styles.errorText}>{error}</p>}
    </div>
  </Modal>;
}
