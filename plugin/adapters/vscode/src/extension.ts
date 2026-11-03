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

const FETCH_TIMEOUT_MS = 4000;
const VIEW_ID = 'hardwareMonitor.widget';

let statusBar: vscode.StatusBarItem | undefined;
let refreshTimer: ReturnType<typeof setTimeout> | undefined;
let refreshing = false;
let disposed = false;
let latest: Snapshot | null = null;
let widgetProvider: WidgetViewProvider | undefined;

function readConfig(): AdapterConfig {
  const cfg = vscode.workspace.getConfiguration('hardwareMonitor');
  const baseUrl = String(cfg.get<string>('baseUrl', 'http://127.0.0.1:5678')).replace(/\/+$/, '');
  const token = String(cfg.get<string>('token', '') || '');
  const refreshInterval = Math.max(1000, Number(cfg.get<number>('refreshInterval', 2000)));
  return { baseUrl, token, refreshInterval };
}

function dashboardUri(): vscode.Uri {
  const { baseUrl, token } = readConfig();
  // 页面只从 URL 读 token；启用 MONITOR_TOKEN 时，不带 token 打开面板会一直 401/离线。
  // 这是与既有页面（?token=）兼容的最小改动；token 仅出现在外部浏览器地址栏。
  if (!token) return vscode.Uri.parse(`${baseUrl}/`);
  return vscode.Uri.parse(`${baseUrl}/?token=${encodeURIComponent(token)}`);
}

async function fetchSnapshot(): Promise<Snapshot | null> {
  const { baseUrl, token } = readConfig();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const headers: Record<string, string> = {};
    if (token) headers['X-Monitor-Token'] = token;
    const response = await fetch(`${baseUrl}/api/all`, {
      headers,
      signal: controller.signal,
    });
    if (!response.ok) return null;
    return (await response.json()) as Snapshot;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
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
    statusBar.tooltip = 'Hardware Monitor: 服务不可用（检查 hardwareMonitor.baseUrl / token）';
    return;
  }
  const cpu = numberOf((data.cpu || {}).usage);
  const mem = numberOf((data.memory || {}).usage_percent);
  statusBar.text = `$(pulse) CPU ${cpu === null ? '--' : cpu.toFixed(0)}% · MEM ${mem === null ? '--' : mem.toFixed(0)}%`;
  statusBar.tooltip = 'Hardware Monitor：点击打开完整面板';
}

async function refresh(): Promise<void> {
  if (refreshing || disposed) return;
  refreshing = true;
  try {
    const data = await fetchSnapshot();
    latest = data;
    updateStatusBar(data);
    widgetProvider?.post({ type: data ? 'data' : 'offline', data: data ?? undefined });
  } finally {
    refreshing = false;
  }
}

function scheduleNext(): void {
  if (disposed) return;
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    void refresh().then(scheduleNext, scheduleNext);
  }, readConfig().refreshInterval);
}

function restart(): void {
  if (refreshTimer) clearTimeout(refreshTimer);
  void refresh().then(scheduleNext, scheduleNext);
}

class WidgetViewProvider implements vscode.WebviewViewProvider {
  private view: vscode.WebviewView | undefined;
  private visible = false;

  public get isVisible(): boolean {
    return this.visible;
  }

  public resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    this.visible = view.visible;
    view.webview.options = { enableScripts: true };
    view.webview.onDidReceiveMessage((message: { type?: string }) => {
      if (message && message.type === 'ready' && latest) {
        this.post({ type: 'data', data: latest });
      }
    });
    view.onDidChangeVisibility(() => {
      this.visible = view.visible;
      if (view.visible && latest) this.post({ type: 'data', data: latest });
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
    vscode.window.registerWebviewViewProvider(VIEW_ID, widgetProvider, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('hardwareMonitor.openDashboard', () => {
      void vscode.env.openExternal(dashboardUri());
    }),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand('hardwareMonitor.refresh', () => { void refresh(); }),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand('hardwareMonitor.toggleWidget', async () => {
      // 真正的显示/隐藏：优先用视图级 toggle；旧版本不支持时回退到聚焦侧边栏
      try {
        if (widgetProvider?.isVisible) {
          await vscode.commands.executeCommand('workbench.action.toggleViewVisibility', VIEW_ID);
        } else {
          await vscode.commands.executeCommand('workbench.view.extension.hardwareMonitor');
        }
      } catch {
        try {
          await vscode.commands.executeCommand('workbench.action.toggleSidebarVisibility');
        } catch {
          await vscode.commands.executeCommand('workbench.view.extension.hardwareMonitor');
        }
      }
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
  disposed = true;
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = undefined;
}
