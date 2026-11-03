/**
 * DSH 客户端插件探针（由 tests/test_dsh_plugin.py 以子进程方式调用）。
 *
 * 用假的 window.__ModuleLoader__ + 假 React + 假 slots，加载 client.js，
 * 检查 apply() 注册了哪些 slot（侧边栏入口、完整面板、对话角落挂件）。
 */
const registrations = [];

globalThis.window = {
  __ModuleLoader__: {
    load(definition) { globalThis.__dshPluginDefinition = definition; },
  },
};

await import(new URL('../../plugin/adapters/dsh/client.js', import.meta.url).href);

const definition = globalThis.__dshPluginDefinition;
if (!definition || typeof definition.factory !== 'function') {
  console.error('client.js did not register a module definition');
  process.exit(2);
}

const fakeReact = {
  createElement: (type, props, ...children) => ({ type, props, children }),
  useState: (value) => [typeof value === 'function' ? value() : value, () => {}],
  useRef: (value) => ({ current: value }),
  useEffect: () => {},
};

const module = definition.factory((name) => {
  if (name === 'react') return fakeReact;
  throw new Error(`unexpected require: ${name}`);
});

const ctx = {
  slots: {
    inject(name, callback) {
      callback();
      return () => {};
    },
    register(options, component) {
      registrations.push({ name: options.name, id: options.id, key: options.key, order: options.order });
      void component;
      return () => {};
    },
  },
};

module.apply(ctx);
console.log(JSON.stringify({ id: definition.id, registrations }));
