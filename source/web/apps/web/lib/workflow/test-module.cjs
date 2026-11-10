const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const ts = require('typescript');
const web = path.resolve(__dirname, '../..');

module.exports = function compile(file, overrides = {}, globals = {}) {
  const modules = new Map();
  function load(absolute) {
    if (modules.has(absolute)) return modules.get(absolute).exports;
    const module = { exports: {} };
    modules.set(absolute, module);
    const localRequire = createRequire(absolute);
    const code = ts.transpileModule(fs.readFileSync(absolute, 'utf8'), {
      fileName: absolute,
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
    }).outputText;
    vm.runInNewContext(code, {
      exports: module.exports, module, crypto: globalThis.crypto, URL, URLSearchParams, TextEncoder, TextDecoder, Headers, FormData,
      AbortController, AbortSignal, setTimeout, clearTimeout,
      require(id) {
        if (Object.hasOwn(overrides, id)) return overrides[id];
        if (id.endsWith('.module.css')) return { __esModule: true, default: {} };
        if (id.startsWith('.') || id.startsWith('@/')) {
          const base = id.startsWith('@/') ? path.resolve(web, id.slice(2)) : path.resolve(path.dirname(absolute), id);
          const file = [base, base + '.ts', base + '.tsx', path.join(base, 'index.tsx')].find(candidate => ['.ts', '.tsx'].includes(path.extname(candidate)) && fs.existsSync(candidate));
          if (file) return load(file);
        }
        return localRequire(id);
      },
      ...globals,
    }, { filename: absolute });
    return module.exports;
  }
  return load(path.resolve(web, file));
};
