# Hardware Monitor 插件与宿主适配

本目录提供通用缩略组件（`/widget` + `widget-core.js`）与各宿主接入方式。
插件依赖已运行的监控服务（默认 `http://127.0.0.1:5678`），详见 [T4 完成报告](../docs/T4-通用插件与缩略组件-完成报告.md)。

## 能力矩阵

| 宿主 | 对话区左上角 | 完整页面 | 方案 |
|---|---|---|---|
| DSH / Web / Electron | 可配置（需宿主提供选择器） | 是 | `plugin/embed.js` + `data-target` |
| DeepSeek Web / Codex Web 等 HTTPS 页面 | 可配置 | 是 | 用户脚本 `userscript/hardware-monitor.user.js`（避开混合内容） |
| Trae / Cursor / VS Code | **否**（内置聊天没有公开 DOM 注入 API） | 是 | 扩展侧边栏 / 面板 / 状态栏（`vscode/`） |
| Codex CLI | 否 | 浏览器 | 状态行脚本（`codex/statusline.py`） |

> 「对话区左上角」依赖宿主 DOM 是否稳定。DSH 等宿主的选择器由使用方填写；
> 无法注入时请退回用户脚本或独立窗口，不要承诺 VS Code 系内置聊天注入。

## 目录

```text
plugin/
├── widget.html / widget.css / widget-core.js / widget.js   # 缩略组件（T4）
├── plugin.html / plugin.css                                # 集成中心（T4）
├── embed.js                                                # 宿主注入脚本（T4）
├── README.md                                               # 本文件
└── adapters/
    ├── web/README.md                 # DSH / DeepSeek / Codex Web 通用接入
    ├── dsh/adapter.config.json       # DSH 配置模板
    ├── dsh/inject.js                 # 读取配置并加载 embed.js
    ├── userscript/hardware-monitor.user.js
    ├── codex/statusline.py
    └── vscode/                       # VS Code / Trae / Cursor 扩展骨架
```

## 通用接入（embed.js）

```html
<script src="http://127.0.0.1:5678/embed.js"
        data-target="#chat-container"
        data-position="top-left"
        data-theme="auto"
        data-base-url="http://127.0.0.1:5678"
        data-token=""
        data-collapsed="0"></script>
```

- `data-token` 可选；token 只通过 `postMessage` 传给 iframe，不会出现在 iframe URL。
- 外部 https 宿主需配置服务端 `MONITOR_FRAME_ANCESTORS` 允许该来源，否则 `/widget` 会被 CSP 拒绝嵌入。
- 集成中心 `/plugin` 可直接生成上面的片段。

## 从服务地址加载适配器

适配器源文件通过服务直接提供，便于 DSH 等宿主按 URL 注入：

- `GET /plugin/adapters/dsh/inject.js`（同目录 `adapter.config.json` 可相对 fetch）
- `GET /plugin/adapters/dsh/adapter.config.json`
- `GET /plugin/adapters/userscript/hardware-monitor.user.js`
- `GET /plugin/adapters/vscode/...`（含 README、源码；`node_modules`/`out` 不提供）

示例：在 DSH 页面注入 `http://127.0.0.1:5678/plugin/adapters/dsh/inject.js` 即可；
配置默认取同目录 `adapter.config.json`，也可在注入前设置 `window.DSH_HARDWARE_MONITOR_CONFIG`
或 `window.DSH_HARDWARE_MONITOR_CONFIG_URL` 覆盖。

## 安全建议

1. 插件场景设置 `MONITOR_HOST=127.0.0.1`，并启用 `MONITOR_TOKEN`。
2. 不要把 token 写进 URL 或提交到公开仓库；用户脚本用 `GM_setValue` 保存。
3. 跨站嵌入时显式配置 `MONITOR_FRAME_ANCESTORS`，不要使用 `*`。

## API

- 缩略页：`GET /widget`（`?embed=1` 时等待宿主 `postMessage` 初始化）
- 注入脚本：`GET /embed.js`
- 元数据：`GET /api/plugin/manifest`
- 完整面板：`GET /`
