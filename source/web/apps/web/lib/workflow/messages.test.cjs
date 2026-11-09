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
  const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), { fileName: file, compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
  const module = { exports: {} }; vm.runInNewContext(output, { exports: module.exports, require: key => dependencies[key] || require(key) }, { filename: file }); return module.exports;
}
const messages = compile(path.join(__dirname, 'messages.ts'));
const attachment = (id, media_kind) => ({ id, media_kind, filename: `${id}.synthetic`, mime: 'application/test', size_bytes: 128 });
test('five message types emit controlled IDs and never raw files, paths or URLs', () => {
  const cases = [
    { type: 'text', text: 'hello', attachments: [] },
    { type: 'image', text: '', attachments: [attachment('image_id', 'image')] },
    { type: 'audio', text: '', attachments: [attachment('audio_id', 'audio')] },
    { type: 'file', text: '', attachments: [attachment('document_id', 'document')] },
    { type: 'mixed', text: 'inspect attached', attachments: [attachment('image_id', 'image'), attachment('document_id', 'document')] },
  ];
  for (const draft of cases) { const envelope = messages.toMessageEnvelope(draft); assert.equal(envelope.type, draft.type); assert.equal(envelope.attachments, undefined); assert.equal(envelope.filename, undefined); assert.equal(envelope.url, undefined); assert.equal(envelope.path, undefined); assert.equal(envelope.base64, undefined); assert.equal(envelope.attachment_ids?.length || 0, draft.attachments.length); }
});
test('message matching rejects wrong media, excess files, empty mixed input and duplicate IDs', () => {
  assert.throws(() => messages.toMessageEnvelope({ type: 'text', text: ' ', attachments: [] }), /文字/);
  assert.throws(() => messages.toMessageEnvelope({ type: 'text', text: 'hello', attachments: [attachment('id', 'document')] }), /不能带附件/);
  assert.throws(() => messages.toMessageEnvelope({ type: 'image', text: '', attachments: [attachment('id', 'document')] }), /图片附件/);
  assert.throws(() => messages.toMessageEnvelope({ type: 'audio', text: '', attachments: [attachment('a', 'audio'), attachment('b', 'audio')] }), /一个音频/);
  assert.throws(() => messages.toMessageEnvelope({ type: 'file', text: '', attachments: [attachment('id', 'image')] }), /文档附件/);
  assert.throws(() => messages.toMessageEnvelope({ type: 'mixed', text: '', attachments: [attachment('id', 'document')] }), /文字和至少/);
  assert.throws(() => messages.toMessageEnvelope({ type: 'mixed', text: 'a', attachments: [attachment('same', 'image'), attachment('same', 'image')] }), /重复/);
});
test('file input preflight uses declared message constraints; server still validates bytes', () => {
  assert.match(messages.localFileError({ name: 'x.exe', size: 2 }, 'mixed'), /文件类型/);
  assert.match(messages.localFileError({ name: 'x.png', size: 11 * 1024 * 1024 }, 'image'), /10MiB/);
  assert.equal(messages.localFileError({ name: 'x.m4a', size: 1024 }, 'audio'), null);
  const clean = messages.validateUploadedAttachment({ ...attachment('id', 'document'), path: '/private/secret', url: 'https://not-forwarded', raw: 'secret' });
  assert.equal(clean.path, undefined); assert.equal(clean.url, undefined); assert.equal(clean.raw, undefined);
});
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
test('upload response arriving after identity switch cannot repopulate attachment state', async () => {
  let resolveUpload; const changes = []; const pending = new Promise(resolve => { resolveUpload = resolve; });
  const Composer = compile(path.resolve(__dirname, '../../components/workflow-studio/message-composer.tsx'), {
    '@/lib/errors': compile(path.resolve(__dirname, '../errors.ts')), '@/lib/workflow/messages': messages, '@/lib/workflow/api': { workflowApi: { uploadAttachment: () => pending } }, './studio.module.css': { __esModule: true, default: {} },
  }).default;
  let tree; const props = { value: { type: 'image', text: '', attachments: [] }, onChange: v => changes.push(v), identityKey: 'actor-a' };
  try {
    await act(async () => { tree = create(React.createElement(Composer, props)); await flush(); });
    let uploadTask;
    await act(async () => { uploadTask = tree.root.findByType('input').props.onChange({ target: { files: [{ name: 'synthetic.png', size: 12 }], value: 'file-selected' } }); await flush(); });
    await act(async () => { tree.update(React.createElement(Composer, { ...props, identityKey: 'actor-b' })); await flush(); });
    await act(async () => { resolveUpload(attachment('late_attachment_a', 'image')); await uploadTask; await flush(); });
    assert(!changes.some(v => v.attachments.some(a => a.id === 'late_attachment_a')));
    assert(changes.some(v => v.type === 'text' && v.attachments.length === 0));
  } finally { if (tree) await act(async () => tree.unmount()); }
});
