/* VS Code / Trae / Cursor 扩展骨架（T5.3）。
 *
 * 设计：
 *   - 扩展宿主用 Node 18+ 全局 fetch 轮询 /api/all（含 token），避免 Webview 的 CORS/混合内容问题；
 *   - 状态栏显示 CPU / MEM；
 *   - 侧边栏 WebviewView 加载服务端 widget-core.js + widget.css 渲染缩略组件；
 *   - 命令打开完整面板（外部浏览器）。
 *
 * 明确不做：向 VS Code/Trae/Cursor 内置聊天区注入 DOM —— 平台没有公开注入 API。
 */
import * as vscode from 'vscode';

type Snapshot = Record<string, any>;

interface AdapterConfig {
  baseUrl: string;
  token: string;
  refreshInterval: number;
}

let statusBar: vscode.StatusBarItem | undefined;
let timer: ReturnType<typeof setInterval> | undefined;
let latest: Snapshot | null = null;
let widgetProvider: WidgetViewProvider | undefined;

function readConfig(): AdapterConfig {
  const cfg = vscode.workspace.getConfiguration('hardwareMonitor');
  const baseUrl = String(cfg.get<string>('baseUrl', 'http://127.0.0.1:5678')).replace(/\/+$/, '');
  const token = String(cfg.get<string>('token', '') || '');
  const refreshInterval = Math.max(1000, Number(cfg.get<number>('refreshInterval', 2000)));
  return { baseUrl, token, refreshInterval };
}

async function fetchSnapshot(): Promise<Snapshot | null> {
  const { baseUrl, token } = readConfig();
  try {
    const headers: Record<string, string> = {};
    if (token) headers['X-Monitor-Token'] = token;
    const response = await fetch(`${baseUrl}/api/all`, { headers });
    if (!response.ok) return null;
    return (await response.json()) as Snapshot;
  } catch {
    return null;
  }
}

function numberOf(value: unknown): number | null {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function updateStatusBar(data: Snapshot | null): void {
  if (!statusBar) return;
  if (!data) {
    statusBar.text = '$(pulse) HM --';
    statusBar.tooltip = 'Hardware Monitor: 服务不可用（检查 hardwareMonitor.baseUrl）';
    return;
  }
  const cpu = numberOf((data.cpu || {}).usage);
  const mem = numberOf((data.memory || {}).usage_percent);
  statusBar.text = `$(pulse) CPU ${cpu === null ? '--' : cpu.toFixed(0)}% · MEM ${mem === null ? '--' : mem.toFixed(0)}%`;
  statusBar.tooltip = 'Hardware Monitor：点击打开完整面板';
}

async function refresh(): Promise<void> {
  const data = await fetchSnapshot();
  latest = data;
  updateStatusBar(data);
  widgetProvider?.post({ type: data ? 'data' : 'offline', data: data ?? undefined });
}

function restart(): void {
  if (timer) clearInterval(timer);
  void refresh();
  timer = setInterval(() => { void refresh(); }, readConfig().refreshInterval);
}

class WidgetViewProvider implements vscode.WebviewViewProvider {
  private view: vscode.WebviewView | undefined;

  public resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true };
    view.webview.onDidReceiveMessage((message: { type?: string }) => {
      if (message && message.type === 'ready' && latest) {
        this.post({ type: 'data', data: latest });
      }
    });
    void this.render(view.webview);
  }

  public post(message: unknown): void {
    if (this.view) void this.view.webview.postMessage(message);
  }

  private async render(webview: vscode.Webview): Promise<void> {
    webview.html = await buildWebviewHtml(webview);
  }
}

async function buildWebviewHtml(webview: vscode.Webview): Promise<string> {
  const { baseUrl } = readConfig();
  let core = '';
  let css = '';
  try {
    const response = await fetch(`${baseUrl}/widget-core.js`);
    if (response.ok) core = await response.text();
  } catch { /* core 加载失败时 Webview 只显示空容器，状态栏仍可用 */ }
  try {
    const response = await fetch(`${baseUrl}/widget.css`);
    if (response.ok) css = await response.text();
  } catch { /* 忽略 */ }

  const nonce = `${Date.now()}${Math.random().toString(36).slice(2)}`;
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}'; img-src ${webview.cspSource} data:;">
  <style>${css}</style>
</head>
<body>
  <div id="root"></div>
  <script nonce="${nonce}">${core}</script>
  <script nonce="${nonce}">
    (function () {
      var widget = null;
      function mount() {
        if (window.HardwareMonitorWidget && !widget) {
          widget = window.HardwareMonitorWidget.mount(document.getElementById('root'), { theme: 'auto' });
        }
      }
      window.addEventListener('message', function (event) {
        var message = event.data || {};
        if (message.type === 'data') {
          mount();
          if (widget) widget.render(message.data);
        } else if (message.type === 'offline') {
          mount();
        }
      });
      mount();
      var api = typeof acquireVsCodeApi === 'function' ? acquireVsCodeApi() : null;
      if (api) api.postMessage({ type: 'ready' });
    })();
  </script>
</body>
</html>`;
}

export function activate(context: vscode.ExtensionContext): void {
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = 'hardwareMonitor.openDashboard';
  statusBar.text = '$(pulse) HM';
  statusBar.tooltip = 'Hardware Monitor';
  statusBar.show();
  context.subscriptions.push(statusBar);

  widgetProvider = new WidgetViewProvider();
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider('hardwareMonitor.widget', widgetProvider, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('hardwareMonitor.openDashboard', () => {
      void vscode.env.openExternal(vscode.Uri.parse(`${readConfig().baseUrl}/`));
    }),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand('hardwareMonitor.refresh', () => { void refresh(); }),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand('hardwareMonitor.toggleWidget', () => {
      void vscode.commands.executeCommand('workbench.view.extension.hardwareMonitor');
    }),
  );
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration('hardwareMonitor')) restart();
    }),
  );

  restart();
}

export function deactivate(): void {
  if (timer) clearInterval(timer);
  timer = undefined;
}
