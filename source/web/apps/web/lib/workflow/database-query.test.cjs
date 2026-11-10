const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const React = require('react');
const { create, act } = require('react-test-renderer');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const compile = require('./test-module.cjs');
const errors = compile(path.resolve(__dirname, '../errors.ts'));
const labels = compile(path.join(__dirname, 'labels.ts'));
const fields = compile(path.resolve(__dirname, '../../components/workflow-studio/schema-fields.tsx'), { '../model-picker': {__esModule:true,default:props=>React.createElement('model-picker',props)}, '@/lib/workflow/labels': labels, './studio.module.css': { __esModule: true, default: {} } });
const clone = value => JSON.parse(JSON.stringify(value));
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
test('database metadata loader excludes drafts and never queries row content or imports knowledge datasets', async () => {
  const calls = [];
  const routes = { '/databases': { items: [{ id: 'db', name: '数据集合' }] }, '/databases/db/tables': { items: [{ id: 'published', name: '已发布', status: 'published', published_version_id: 'v1', database_id: 'db' }, { id: 'draft', status: 'draft' }] }, '/database/tables/published/versions': { items: [{ id: 'v1', status: 'published' }, { id: 'v2', status: 'draft' }] } };
  const api = compile(path.join(__dirname, 'api.ts'), { '@/lib/errors': errors, '@/lib/api': { request: async url => { calls.push(url); assert(routes[url], url); return routes[url]; } } });
  const result = await api.loadDatabaseResources(); assert.deepEqual(clone(result.dataTables.map(table => table.id)), ['published']); assert.deepEqual(clone(result.tableVersions.map(version => version.id)), ['v1']);
  assert(!calls.some(url => /query|imports|dictionary|knowledge/.test(url)));
});
test('database catalog stops follow-up requests when login identity changes', async () => {
  let identity = 1; const calls = [];
  const api = compile(path.join(__dirname, 'api.ts'), { '@/lib/errors': errors, '@/lib/api': { currentIdentityEpoch: () => identity, request: async url => { calls.push(url); identity = 2; return { items: [{ id: 'previous-identity-db' }] }; } } });
  await assert.rejects(api.loadDatabaseResources(), /登录身份已变化/); assert.deepEqual(calls, ['/databases']);
});
async function harness(scope) {
  const pending = []; let node = { id: 'query', type: 'database_query', config: {}, inputs: {} }; let tree;
  const resources = { dataTables: [{ id: 'one', name: '表一', database_id: 'db-one' }, { id: 'two', name: '表二', database_id: 'db-two' }, { id: 'private', name: '范围外表', database_id: 'db-private' }], tableVersions: [{ id: 'version-one', table_id: 'one' }, { id: 'version-two', table_id: 'two' }], warnings: [] };
  const component = compile(path.resolve(__dirname, '../../components/workflow-studio/database-query.tsx'), { '@/lib/api': {}, '@/lib/errors': errors, '@/lib/workflow/labels': labels, './schema-fields': fields, './studio.module.css': { __esModule: true, default: {} }, '@/lib/workflow/api': { workflowApi: { tableDictionary: (id, version) => new Promise(resolve => pending.push({ id, version, resolve })) } } });
  function Parent() { const [state, setState] = React.useState(node); return React.createElement(component.DatabaseQuery, { node: state, resources, scope, variables: [], onValidity() {}, onChange: patch => { node = { ...node, ...patch }; setState(node); } }); }
  await act(async () => { tree = create(React.createElement(Parent)); await flush(); });
  return { tree, pending, node: () => node, change: async (element, value) => act(async () => { element.props.onChange({ target: { value } }); await flush(); }), choose: async id => act(async () => { tree.root.findByProps({ 'aria-label': '查询数据表' }).props.onChange({ target: { value: id } }); await flush(); }), resolve: async (id, value) => act(async () => { pending.find(item => item.id === id).resolve(value); await flush(); }), dispose: async () => act(async () => tree.unmount()) };
}
test('query form intersects Agent scope, pins selected version and ignores late dictionary from another table', async () => {
  const h = await harness([{ kind: 'data_table', resource_id: 'one', version_id: 'version-one' }, { kind: 'database', resource_id: 'db-two' }]);
  try {
    assert(!h.tree.root.findByProps({ 'aria-label': '查询数据表' }).findAllByType('option').some(option => option.props.value === 'private'));
    await h.choose('one'); assert.equal(h.node().config.table_version_id, 'version-one'); assert.equal(h.pending[0].version, 'version-one');
    await h.choose('two'); await h.resolve('one', { table_id: 'one', version_id: 'version-one', schema_fields: [{ name: 'old_private_field', type: 'string' }] });
    assert(!JSON.stringify(h.tree.toJSON()).includes('old_private_field'));
    await h.resolve('two', { table_id: 'two', version_id: 'version-two', policy_version: 7, schema_fields: [{ name: 'amount', meaning: '金额', type: 'decimal', unit: '元', caliber: '已发布口径' }] });
    assert(JSON.stringify(h.tree.toJSON()).includes('已发布口径')); assert(!JSON.stringify(h.tree.toJSON()).includes('old_private_field'));
    const add = h.tree.root.findAllByType('button').find(button => button.children.join('') === '添加筛选条件'); await act(async () => add.props.onClick());
    const select = h.tree.root.findAllByType('select').find(element => element.props['aria-label'] === '选择字段'); await h.change(select, 'amount');
    const value = h.tree.root.findAllByType('textarea').find(element => element.props.rows === 2 && element.props.value === ''); assert(value);
    await h.change(value, '9007199254740993.10'); assert.equal(h.node().inputs.query.filters[0].value, '9007199254740993.10');
    assert.equal(h.node().config.database_id, undefined); assert.deepEqual(clone(Object.keys(h.node().inputs)), ['table_id', 'query']);
  } finally { await h.dispose(); }
});
test('empty Agent data scope offers no tables and performs no dictionary read', async () => {
  const h = await harness([]);
  try { assert.equal(h.tree.root.findByProps({ 'aria-label': '查询数据表' }).findAllByType('option').length, 1); assert.equal(h.pending.length, 0); }
  finally { await h.dispose(); }
});
