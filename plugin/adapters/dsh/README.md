# DSH 适配器

面向 DSH（DeepSeek Harness）等可注入脚本的 Web/Electron 宿主，把缩略组件挂到对话区左上角。

## 文件

- `adapter.config.json`：目标选择器、位置、主题、服务地址、token（模板）。
- `inject.js`：读取配置 → 加载 `/embed.js` → `window.HardwareMonitor.mount()`。

## 加载方式（任选）

1. **从服务地址直接注入**（推荐，文件随服务提供）：

   ```
   http://127.0.0.1:5678/plugin/adapters/dsh/inject.js
   ```

   此时配置会相对脚本地址取
   `http://127.0.0.1:5678/plugin/adapters/dsh/adapter.config.json`。

2. **手动复制** `inject.js` + `adapter.config.json` 到宿主可访问的位置，注入前者。

3. 在调试台/用户脚本里 `eval` 注入时，`document.currentScript` 为 `null`，
   无法相对定位配置。请显式设置：

   ```js
   window.DSH_HARDWARE_MONITOR_CONFIG_URL = 'http://127.0.0.1:5678/plugin/adapters/dsh/adapter.config.json';
   window.DSH_HARDWARE_MONITOR_CONFIG = { targetSelector: '你的选择器', baseUrl: 'http://127.0.0.1:5678' };
   // 然后注入 inject.js
   ```

## 配置字段

| 字段 | 说明 |
|---|---|
| `targetSelector` | 对话区容器 CSS 选择器；为空/找不到时回退视口左上角 |
| `position` | `top-left` / `top-right` / `bottom-left` / `bottom-right` |
| `theme` | `auto` / `light` / `dark` |
| `baseUrl` | 监控服务地址（默认 `http://127.0.0.1:5678`） |
| `token` | 可选；只经 `postMessage` 传给 iframe，不进 URL |
| `collapsed` | 初始是否折叠 |

## 限制

- DSH 的 DOM 结构可能随版本变化，`targetSelector` 需要按实际页面填写；
- 无法注入时请改用用户脚本或独立窗口打开 `/widget`、`/`；
- 外部 https 宿主需在服务端配置 `MONITOR_FRAME_ANCESTORS` 允许其来源。
