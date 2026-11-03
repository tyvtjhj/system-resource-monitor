/**
 * DSH 宿主插件测试探针（由 tests/test_dsh_plugin.py 以子进程方式调用）。
 *
 * 用法：node tests/helpers/dsh_host_probe.mjs <base_url>
 *
 * 用假的 ctx（webServer/tools）加载 plugin/adapters/dsh/lib/index.js，
 * 执行工具与两个路由，把结果以 JSON 打到 stdout；不依赖真实 DSH。
 */
const baseUrl = process.argv[2];
if (!baseUrl) {
  console.error('usage: node dsh_host_probe.mjs <base_url>');
  process.exit(2);
}

process.env.HARDWARE_MONITOR_URL = baseUrl;
process.env.HARDWARE_MONITOR_TOKEN = process.env.HARDWARE_MONITOR_TOKEN || '';

const moduleUrl = new URL('../../plugin/adapters/dsh/lib/index.js', import.meta.url);
const { apply } = await import(moduleUrl.href);

let handler = null;
let tool = null;
const webServer = {
  register(options) { handler = options.handler; return () => {}; },
};
const tools = {
  register(item) { tool = item; return () => {}; },
};
const effect = (fn) => fn();
const ctx = {
  inject(services, callback) {
    if (services.includes('webServer')) callback({ webServer, effect });
    if (services.includes('tools')) callback({ tools, effect });
  },
};

apply(ctx);
if (!handler || !tool) {
  console.error('plugin did not register handler/tool');
  process.exit(3);
}

function callRoute(url) {
  return new Promise((resolve) => {
    const res = {
      status: 0,
      writeHead(status) { this.status = status; },
      end(body) { resolve({ status: this.status, body }); },
    };
    handler({ method: 'GET', url }, res);
  });
}

let toolResult;
try {
  toolResult = await tool.execute();
} catch (error) {
  toolResult = { ok: false, thrown: String(error?.message || error) };
}

const summary = await callRoute('/hardware-monitor-api/summary');
const health = await callRoute('/hardware-monitor-api/health');

function safeJson(text) {
  try { return JSON.parse(text); } catch { return null; }
}

console.log(JSON.stringify({
  tool: toolResult,
  summary: { status: summary.status, payload: safeJson(summary.body) },
  health: { status: health.status, payload: safeJson(health.body) },
}));
