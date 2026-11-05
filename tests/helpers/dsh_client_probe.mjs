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

await import(new URL('../../plugins/dsh-hardware-monitor/client.js', import.meta.url).href);

const definition = globalThis.__dshPluginDefinition;
if (!definition || typeof definition.factory !== 'function') {
  console.error('client.js did not register a module definition');
  process.exit(2);
}

const hookQueue = [];
const fakeReact = {
  createElement: (type, props, ...children) => ({ type, props, children }),
  useState: (value) => {
    const next = hookQueue.length > 0 ? hookQueue.shift() : (typeof value === 'function' ? value() : value);
    return [next, () => {}];
  },
  useRef: (value) => ({ current: value }),
  useEffect: () => {},
};

const module = definition.factory((name) => {
  if (name === 'react') return fakeReact;
  throw new Error(`unexpected require: ${name}`);
});

let panelComponent = null;
const ctx = {
  slots: {
    inject(name, callback) {
      callback();
      return () => {};
    },
    register(options, component) {
      registrations.push({ name: options.name, id: options.id, key: options.key, order: options.order });
      if (options.name === 'main') panelComponent = component;
      return () => {};
    },
  },
};

module.apply(ctx);

const panelState = {
  data: {
    cpu: {
      usage: 18.5, per_core: [10, 20, 30], count_physical: 8, count_logical: 16,
      freq_current_mhz: 3200,
      freq: { avg_mhz: 3200, min_mhz: 2800, max_mhz: 4000, per_core_mhz: [3000, 3100, 3200] },
    },
    memory: { used_gb: 8.2, total_gb: 16, usage_percent: 51.2, available_gb: 7.8 },
    gpu: { name: 'Fake GPU', usage: 9, used_gb: 1.2, total_gb: 8 },
    temperature: { cpu: { value: 47.5, source: 'acpi' }, gpu: { value: 40 } },
    network_speed: { download_mbps: 1.5, upload_mbps: 0.2 },
    system_info: { uptime: '01:00' },
    disk: [
      { model: 'Fake SSD', interface_type: 'SCSI', size_gb: 500, serial_number: 'SN', type: 'physical' },
      { device: 'C:/', mountpoint: 'C:/', fstype: 'NTFS', free_gb: 10, total_gb: 100, usage_percent: 90 },
    ],
    latency_ms: 2.5,
    platform: { os: 'windows' },
  },
  processes: { process_count: 3, processes: [{ name: 'fake.exe', pids: 2, private_mb: 10, working_set_mb: 20 }] },
  offline: false,
  updatedAt: new Date(),
  baseUrl: 'http://127.0.0.1:5678',
};

let panelRender = 'skipped';
let panelHasPhysicalModel = false;
let panelHasVisualization = false;
let panelHasFrequency = false;
let panelTextHasUndefined = false;
try {
  hookQueue.push(panelState);
  hookQueue.push(0);
  if (typeof panelComponent === 'function') {
    const tree = panelComponent();
    panelRender = 'ok';
    const treeText = JSON.stringify(tree);
    panelHasPhysicalModel = treeText.includes('Fake SSD');
    panelHasVisualization = treeText.includes('实时可视化');
    panelHasFrequency = treeText.includes('GHz');
    panelTextHasUndefined = treeText.includes('undefined');
  }
} catch (error) {
  panelRender = 'error: ' + String(error && error.message ? error.message : error);
}

console.log(JSON.stringify({
  id: definition.id,
  registrations,
  panelRender,
  panelHasPhysicalModel,
  panelHasVisualization,
  panelHasFrequency,
  panelTextHasUndefined,
}));
