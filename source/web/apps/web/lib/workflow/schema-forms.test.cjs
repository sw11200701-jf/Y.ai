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
const labels = compile(path.join(__dirname, 'labels.ts'));
const { SchemaFields } = compile(path.resolve(__dirname, '../../components/workflow-studio/schema-fields.tsx'), { '../model-picker': {__esModule:true,default:props=>React.createElement('model-picker',props)}, '@/lib/workflow/labels': labels, './studio.module.css': { __esModule: true, default: {} } });
const resources = { agents: [], agentVersions: [], models: [], documents: [], plugins: [], versions: [], connections: [], imports: [], warnings: [] };
async function form(schema, value, label = '输入') {
  let current = value; let valid = true; let tree;
  function Parent() { const [data, setData] = React.useState(value); return React.createElement(SchemaFields, { label, schema, value: data, resources, variables: [], onChange: next => { current = next; setData(next); }, onValidity: next => { valid = next; } }); }
  await act(async () => { tree = create(React.createElement(Parent)); });
  return { tree, value: () => current, valid: () => valid, change: async (node, value) => act(async () => node.props.onChange({ target: { value } })), click: async title => act(async () => tree.root.findAllByType('button').find(node => node.children.join('') === title).props.onClick()), dispose: async () => act(async () => tree.unmount()) };
}

test('non-LLM model nodes use the shared picker, retain unknown settings and update the version together',async()=>{
 const h=await form({properties:{model_config_id:{type:'string'},model_config_version:{type:'string'}},additionalProperties:false},{model_config_id:'saved',model_config_version:'p1:m1',future:{keep:true}},'配置');
 try{assert.equal(h.tree.root.findAllByType('textarea').length,1,'only the collapsed advanced JSON remains');const picker=h.tree.root.findByType('model-picker');assert.equal(picker.props.value,'saved');assert.equal(picker.props.version,'p1:m1');await act(()=>picker.props.onChange({id:'selected',config_version:'p2:m2'}));assert.equal(h.value().model_config_id,'selected');assert.equal(h.value().model_config_version,'p2:m2');assert.equal(h.value().future.keep,true);}finally{await h.dispose();}
});
test('Chinese enum and scalar type controls retain canonical values and numeric condition operands', async () => {
  const h = await form({ type: 'object', properties: { presentation: { enum: ['auto', 'text', 'table', 'files'] }, left: {} } }, { presentation: 'text', left: 'amount' });
  try {
    const select = h.tree.root.findAllByType('select').find(node => node.findAllByType('option').some(option => option.props.value === 'files'));
    assert(select.findAllByType('option').some(option => option.children.join('') === '文件'));
    await h.change(select, 'files'); assert.equal(h.value().presentation, 'files');
    await h.change(h.tree.root.findByProps({ 'aria-label': '比较左侧固定值类型' }), 'number');
    await h.change(h.tree.root.findByProps({ type: 'number' }), '12.5'); assert.equal(h.value().left, 12.5);
  } finally { await h.dispose(); }
});
test('table form preserves decimal strings while editing cells and adding rows', async () => {
  const h = await form({ properties: { table: { type: 'object' } } }, { table: { columns: ['金额'], rows: [['9007199254740993.10']] } });
  try {
    assert.equal(h.tree.root.findByProps({ 'aria-label': '第1行金额' }).props.value, '9007199254740993.10');
    await h.click('添加一行'); await h.change(h.tree.root.findByProps({ 'aria-label': '第2行金额' }), '0.00');
    assert.equal(h.value().table.rows[0][0], '9007199254740993.10'); assert.equal(h.value().table.rows[1][0], '0.00');
  } finally { await h.dispose(); }
});
