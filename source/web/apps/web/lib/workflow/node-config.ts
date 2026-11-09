import type { NodeRegistry, ObjectValue, Schema, VariableOption, WorkflowDefinition, WorkflowNode } from './types';

export const financeMetrics = { revenue: '营业收入', net_profit: '净利润', operating_cash_flow: '经营现金流', total_assets: '资产总额', total_liabilities: '负债总额' };
export const objectValue = (value: unknown): value is ObjectValue => !!value && typeof value === 'object' && !Array.isArray(value);
export const reference = (value: unknown): value is ObjectValue & { $ref: string } => objectValue(value) && typeof value.$ref === 'string';
export function nextField(value: ObjectValue, prefix = 'field') { let i = 1; while (Object.hasOwn(value, `${prefix}_${i}`)) i++; return `${prefix}_${i}`; }
export function literalDefault(type: string): any { return ({ string: '', number: 0, integer: 0, boolean: false, object: {}, array: [], null: null } as ObjectValue)[type]; }
export function selectedSchema(workflow: WorkflowDefinition, registry: NodeRegistry, ref: string): Schema {
  const parts = ref.split('.'); let schema: Schema = {};
  if (parts[0] === 'inputs') schema = workflow.input_schema || {};
  else if (parts[0] === 'vars') return workflow.variables?.[parts[1]] || {};
  else if (parts[0] === 'nodes') { const node = workflow.nodes.find(node => node.id === parts[1]); schema = registry.items.find(item => item.type === node?.type)?.output_schema || {}; parts.shift(); }
  for (const key of parts.slice(1)) schema = schema.properties?.[key] || {};
  return schema;
}
export function listFieldOptions(workflow: WorkflowDefinition, registry: NodeRegistry, value: any) {
  const fields = reference(value) ? selectedSchema(workflow, registry, value.$ref).items?.properties || {} : Array.isArray(value) && objectValue(value[0]) ? Object.fromEntries(Object.keys(value[0]).map(key => [key, {}])) : {};
  return Object.entries(fields).map(([name, schema]) => ({ name, label: (schema as Schema).title || name }));
}
export function queryVariableOptions(variables: VariableOption[], type: string): VariableOption[] {
  return variables.filter(variable => typeof variable.type === 'string' && (variable.type === 'any' || variable.type.split('|').includes(type)));
}
export function queryReferenceIssues(value: any, type: string, label: string, variables?: VariableOption[]): string[] {
  if (!objectValue(value) || !Object.hasOwn(value, '$ref')) return [];
  if (!reference(value) || !value.$ref.trim()) return [`请选择“${label}”的变量来源`];
  if (variables && !queryVariableOptions(variables, type).some(variable => variable.ref === value.$ref)) return [`“${label}”的变量来源已断开或类型不兼容，请重新选择`];
  return [];
}
export function queryIssues(query: any, requireSource: boolean, variables?: VariableOption[]): string[] {
  if (objectValue(query) && Object.hasOwn(query, '$ref')) return queryReferenceIssues(query, 'object', '查询口径', variables);
  if (!objectValue(query)) return ['请配置主体、年度和指标'];
  const issues: string[] = [];
  const dynamic = (value: any, type: string, label: string) => { const has = objectValue(value) && Object.hasOwn(value, '$ref'); if (has) issues.push(...queryReferenceIssues(value, type, label, variables)); return has; };
  if (!dynamic(query.entity, 'string', '查询主体') && (typeof query.entity !== 'string' || !query.entity.trim())) issues.push('请填写查询主体');
  if (!dynamic(query.periods, 'array', '比较年度')) {
    if (!Array.isArray(query.periods) || !query.periods.length) issues.push('请添加2000至2099年的比较年度');
    else for (const year of query.periods) if (!dynamic(year, 'string', '年度项') && (typeof year !== 'string' || !/^20\d{2}$/.test(year))) issues.push('固定年度需为2000至2099年；原值保留，请明确修改');
  }
  if (!dynamic(query.metric_codes, 'array', '财务指标')) {
    if (!Array.isArray(query.metric_codes) || !query.metric_codes.length) issues.push('请至少选择一个财务指标');
    else for (const code of query.metric_codes) if (!dynamic(code, 'string', '指标项') && (typeof code !== 'string' || !code)) issues.push('财务指标项需为文字或已连接的文字变量');
  }
  if (!dynamic(query.batch_id, 'string', '导入批次') && requireSource && (typeof query.batch_id !== 'string' || !query.batch_id.trim())) issues.push('请选择明确的导入批次');
  for (const [key, label] of [['currency', '币种'], ['unit', '金额单位'], ['scope', '报表口径']]) if (query[key] !== undefined && !dynamic(query[key], 'string', label) && typeof query[key] !== 'string') issues.push(`${label}需为文字或已连接的文字变量，原值保留`);
  return [...new Set(issues)];
}
export function nodePurpose(type: string): string {
  return ({ if_else: '按已配置的条件选择分支；两个出口都需要连接，跳过分支的结果须明确允许为空。', transform: '按输入和输出映射转换已有字段，不调用模型或外部服务。', document_generate: '按所选文档工具的真实字段，使用获准资料生成受控Word、Excel或PPT文件。', code: '可保存代码配置；当前未启用隔离运行环境，不能执行此节点。', trigger: '当前未启用定时或外部事件调度；可以使用消息触发节点接收当前输入。', start: '定义运行前需要用户填写的信息。', input: '定义运行前需要用户填写的信息。', output: '把文本、变量或受控文件返回给用户。', answer: '将固定文本或上游答案返回给用户。', end: '旧结束节点只终止执行；返回结果请使用“结束并返回结果”。', query: '从明确授权的财务来源查询事实。', calculate: '按相同主体、期间与口径计算财务指标。', chart: '用本次数据集生成柱状图。', report: '用本次数据集生成Word和Excel文件。', plugin: '选择已发布服务工具并配置参数。', http: '通过已发布插件安全调用接口。', assign: '修改一个已声明的流程变量。', aggregate: '在分支汇合后组合已有结果。', list: '对列表执行选择、排序或筛选。', template: '将固定文字与选定变量组合成文本。', iteration: '为列表中每一项运行同一组内部步骤。', loop: '让状态随每轮处理更新，达到条件或上限后停止。', human_approval: '等待指定人员批准后再继续。', knowledge_retrieval: '在固定发布资料版本中查找问题相关内容。', document_extractor: '读取受控资料文件中的可提取正文。', database_query: '按已授权表的字段、筛选和统计查询数据。', parameter_extractor: '从文本中提取你定义的字段。', question_classifier: '按分类说明把问题送入对应分支。', agent: '调用已配置智能体处理一项任务。', llm: '选择模型、编写提示词并定义回答格式。' } as ObjectValue)[type] || (type.startsWith('trigger_') ? '接收当前消息并向后续节点提供内容与附件。' : '该能力需要服务端声明支持后才能执行。');
}
