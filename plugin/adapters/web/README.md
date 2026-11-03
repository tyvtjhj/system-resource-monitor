# 通用 Web / DSH / DeepSeek Web 接入

适用于任何能执行 `<script>`（或用户脚本注入脚本）的 Web/Electron 宿主：DSH、DeepSeek Web、Codex Web、普通网页等。
缩略组件通过 `embed.js` 以 iframe + Shadow DOM 方式挂载，完整面板走 `/`。

## 1. 最基本：视口左上角

```html
<script src="http://127.0.0.1:5678/embed.js"
        data-position="top-left"
        data-theme="auto"></script>
```

不写 `data-target` 时组件 `fixed` 在视口左上角；`data-position` 支持 `top-left|top-right|bottom-left|bottom-right`。

## 2. 挂到指定容器（对话区左上角）

```html
<div id="chat-container" style="position: relative;">
  <!-- 宿主对话区内容 -->
</div>

<script src="http://127.0.0.1:5678/embed.js"
        data-target="#chat-container"
        data-position="top-left"
        data-theme="auto"
        data-base-url="http://127.0.0.1:5678"
        data-collapsed="0"></script>
```

- `embed.js` 会优先 `absolute` 挂到目标容器（自动补 `position: relative`），失败退回 `fixed`。
- 目标选择器因宿主版本而异，由使用方填写；DSH 可参考 `adapters/dsh/`。
- 组件可拖动标题栏移动，位置与折叠状态存 `localStorage`。

## 3. 启用 token

服务端设置 `MONITOR_TOKEN` 后：

```html
<script src="http://127.0.0.1:5678/embed.js"
        data-target="#chat-container"
        data-token="你的口令"></script>
```

注意：

- token 只通过 `postMessage` 传给 iframe，**不会**进入 iframe URL/Referer；
- 不要把带 token 的页面分享给不可信的人；
- 生产环境建议服务只监听 `127.0.0.1`。

## 4. HTTPS 页面（混合内容）

HTTPS 页面嵌入 `http://127.0.0.1` 时：

- Chrome/Edge 对 loopback 有豁免，通常可用；
- Safari/iOS 等会拦截混合内容，此时请改用**用户脚本**（`adapters/userscript/`），由脚本直接取数并在页面内渲染，不使用 iframe。

## 5. 反向代理 / 非默认端口

- 如果服务在反向代理后，`data-base-url` 写代理地址（含前缀时请自行保证 `/embed.js` 可访问）。
- 同一服务页面感知：`/widget` 与 `/embed.js` 不依赖 5678 端口，用代理地址即可。

## 6. 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| iframe 空白 | CSP `frame-ancestors` 未允许宿主来源 | 设置 `MONITOR_FRAME_ANCESTORS` 追加宿主 origin |
| 组件显示离线 | 服务未启动 / token 错误 / 端口不对 | 打开 `/api/start` 验证；检查 `data-base-url` 与 token |
| 注入未生效 | 宿主 CSP 禁止外部脚本 | 改用用户脚本或扩展；或在宿主允许列表中加入服务地址 |
| 位置不对 | 目标容器是 static/被 overflow 裁剪 | 给容器 `position: relative`，或改用 `data-position` 视口四角 |
