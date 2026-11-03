# VS Code / Trae / Cursor 扩展骨架

一个兼容 VS Code 扩展 API 的薄适配器：

- **状态栏**：显示 `CPU xx% · MEM xx%`（每 `refreshInterval` 刷新，点击打开完整面板）；
- **侧边栏 WebviewView**：加载服务端 `widget-core.js` + `widget.css` 渲染缩略组件；
- **命令**：`hardwareMonitor.openDashboard`（外部浏览器打开 `/`）、`hardwareMonitor.toggleWidget`、`hardwareMonitor.refresh`；
- **配置**：`hardwareMonitor.baseUrl`、`hardwareMonitor.token`、`hardwareMonitor.refreshInterval`。

## 明确的能力边界

VS Code / Trae / Cursor 的**内置聊天区没有公开的 DOM 注入 API**，因此本扩展：

- ✅ 提供活动栏图标 + 侧边栏缩略视图、状态栏、命令面板；
- ✅ 可通过 `baseUrl` 连接本机或远程转发地址；
- ❌ **不承诺**把组件注入聊天框左上角。若后续平台开放聊天扩展 API，再增加对应适配器。

## 构建与调试

```bash
cd plugin/adapters/vscode
npm install          # 安装 typescript 与 @types/vscode
npm run compile      # 产物输出到 out/（已被 .gitignore 忽略）
```

在 VS Code 中打开该目录按 `F5`（Run Extension）调试；打包可选：

```bash
npx @vscode/vsce package   # 生成 .vsix（*.vsix 已被 .gitignore 忽略，不提交）
```

## 远程 / Codespaces

扩展宿主里用 Node `fetch` 轮询 `/api/all`，Webview 不直接访问网络，因此不存在 CORS/混合内容问题。
远程场景把 `hardwareMonitor.baseUrl` 设为转发后的地址（例如 `http://127.0.0.1:5679` 的端口转发地址）即可。

## 安全

- token 只存在扩展配置里，由扩展宿主附加 `X-Monitor-Token`；不会写入 Webview URL。
- Webview 使用 CSP nonce + `default-src 'none'`，只允许内联样式与带 nonce 的脚本；`widget-core.js`/`widget.css` 由扩展宿主拉取后内联。
- 建议服务端 `MONITOR_HOST=127.0.0.1` 并启用 `MONITOR_TOKEN`。
