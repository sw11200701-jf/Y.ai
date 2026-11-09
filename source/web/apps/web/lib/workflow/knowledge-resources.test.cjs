const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { create, act } = require('react-test-renderer');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
function compile(file, dependencies = {}) {
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const module = { exports: {} }; vm.runInNewContext(code, { module, exports: module.exports, require: id => dependencies[id] || require(id) }); return module.exports;
}
const errors = compile(path.resolve(__dirname, '../errors.ts'));
test('knowledge picker preserves a fixed unavailable version across refresh until explicit removal', async () => {
  const Picker = compile(path.resolve(__dirname, '../../components/workflow-studio/knowledge-picker.tsx'), { './studio.module.css': { __esModule: true, default: {} } }).KnowledgePicker;
  const changes = []; const base = { query: { $ref: 'nodes.start.message.text' }, knowledge_base_ids: ['kb-one'], version_ids: ['version-old'] };
  const version = { id: 'version-new', status: 'published', library_name: '知识库', resource_name: '新资料', knowledge_base_id: 'kb-two', version: 2 };
  let tree;
  function Parent({ versions }) { const [value, setValue] = React.useState(base); return React.createElement(Picker, { value, resources: { knowledgeVersions: versions, warnings: [] }, onChange: next => { changes.push(next); setValue(next); } }); }
  try {
    await act(async () => { tree = create(React.createElement(Parent, { versions: [] })); });
    await act(async () => { tree.update(React.createElement(Parent, { versions: [version] })); });
    assert.equal(changes.length, 0, 'refresh never rewrites saved version');
    const check = tree.root.findByType('input'); assert.equal(check.props.checked, false);
    await act(async () => check.props.onChange({ target: { checked: true } }));
    assert.deepEqual(JSON.parse(JSON.stringify(changes.at(-1).version_ids)), ['version-old', 'version-new']);
    assert.deepEqual(JSON.parse(JSON.stringify(changes.at(-1).knowledge_base_ids)).sort(), ['kb-one', 'kb-two']);
    assert.equal(changes.at(-1).query.$ref, 'nodes.start.message.text');
    const remove = tree.root.findAllByType('button').find(button => button.children.join('') === '移除此引用');
    await act(async () => remove.props.onClick());
    assert.deepEqual(JSON.parse(JSON.stringify(changes.at(-1).version_ids)), ['version-new']);
    assert.deepEqual(JSON.parse(JSON.stringify(changes.at(-1).knowledge_base_ids)), ['kb-two']);
  } finally { if (tree) await act(async () => tree.unmount()); }
});
test('knowledge scope narrows choices without rewriting a previously saved version', async () => {
  const Picker = compile(path.resolve(__dirname, '../../components/workflow-studio/knowledge-picker.tsx'), { './studio.module.css': { __esModule: true, default: {} } }).KnowledgePicker;
  let changes = 0; let tree;
  try {
    await act(async () => { tree = create(React.createElement(Picker, { value: { knowledge_base_ids: ['kb'], version_ids: ['v'] }, scope: [], resources: { knowledgeVersions: [{ id: 'v', knowledge_base_id: 'kb' }], warnings: [] }, onChange: () => changes++ })); });
    assert.equal(tree.root.findAllByType('input').length, 0); assert.equal(changes, 0); assert(JSON.stringify(tree.toJSON()).includes('暂不可读取'));
  } finally { if (tree) await act(async () => tree.unmount()); }
});

// Superseded loader cases now follow the frozen Schema6 capability contract.
require('./directory-r2.test.cjs');
