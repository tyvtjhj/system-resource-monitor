# DSH 适配器（原生插件 + Web 注入回退）

DSH 是 Electron/Web 宿主，支持 **client plugin + host plugin**。本目录提供两种接入方式：

| 方式 | 适用 | 效果 |
|---|---|---|
| **原生 DSH 插件（推荐）** | 有 DSH 插件管理器的桌面/Web 版 | 对话右上角实时挂件 + `hardware_monitor_status` 工具 |
| Web 注入（`inject.js` + `embed.js`） | 无法安装插件、但可注入脚本的 Web/Electron 宿主 | 视口/容器角落的缩略组件（iframe） |

## A. 原生 DSH 插件

### 文件

```text
plugins/dsh-hardware-monitor/
├── package.json           # DSH 插件清单（dsh.client / dsh.bundle）
├── client.js              # 浏览器半：对话区右上角挂件（React，slot 注入）
├── lib/index.js           # 宿主半：/hardware-monitor-api 代理 + hardware_monitor_status 工具
├── cordis.patch.yml       # bundle patch（把插件行插入 profile）
├── install.ps1            # 安装到 DSH desktop profile
├── uninstall.ps1          # 卸载
├── SKILL.md               # 给模型的工具使用说明（安装时复制到 ~/.dsh/skills）
├── adapter.config.json    # 仅 Web 注入方式使用
└── inject.js              # 仅 Web 注入方式使用
```

### 安装

```powershell
powershell -ExecutionPolicy Bypass -File .\plugins\dsh-hardware-monitor\install.ps1
```

脚本会：在 `~/.dsh/plugins` 与 profile `node_modules` 建立 junction → 把插件加入 profile
`dependencies` 与 `dsh.profile.bundles` → 安装 skill → 触发 DSH 热加载。
安装完成后**刷新 DSH 页面（Ctrl+R）**，对话区右上角出现挂件。

### 配置

在启动 DSH 的终端里设置环境变量（默认值见括号）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `HARDWARE_MONITOR_URL` | `http://127.0.0.1:5678` | 监控服务地址 |
| `HARDWARE_MONITOR_TOKEN` | 空 | 服务端 `MONITOR_TOKEN`；启用后必须设置，否则工具/挂件显示离线 |
| `HARDWARE_MONITOR_AUTOSTART` | `1` | 设为 `0/false` 关闭「插件自动拉起监控服务」 |
| `HARDWARE_MONITOR_REPO` | 自动推导 | 监控仓库目录（插件不在仓库内时的兜底） |
| `HARDWARE_MONITOR_PYTHON` | `.venv` 优先 | 指定 Python 可执行文件 |

> **重启后自动恢复**：DSH 启动或插件重载时，宿主半会检测 `/api/start`；若 5678 未运行，
> 会用仓库 `.venv\Scripts\python.exe -m hardware_monitor --no-elevate` 后台拉起（避免 UAC），
> 并每 30 秒看门狗重试；日志见 `plugins/dsh-hardware-monitor/service.log`。

挂件点击展开明细；「打开完整面板」会打开 `http://127.0.0.1:5678/`。
若启用 token，可在 DSH 页面控制台执行下面命令，让面板链接自动带 token（仅存本机 localStorage）：

```js
localStorage.setItem('hardware-monitor-token', '你的口令')
```

### 界面入口

- **输入框下方（composer dock）**：实时小挂件（CPU/MEM/GPU/TEMP，点击向上展开明细），不再占用对话右上角；
- **侧边栏图标**：像插件市场一样在侧边栏注册入口（`sidebar.panellist`），点击后
  由宿主打开 `main(key=hardware-monitor)` 面板；面板是**原生 React 面板**（不再用 iframe），
  通过 DSH 同源代理 `/hardware-monitor-api/{summary,processes}` 渲染指标、每核占用、磁盘与进程 Top，
  并提供「在浏览器打开」链接（需要 token 时从 `localStorage.hardware-monitor-token` 读取并拼接）；
- 安装/重载后需刷新 DSH 页面（Ctrl+R）加载客户端半。

### 工具

`hardware_monitor_status`：返回 CPU/内存/GPU/温度/网络/开机时长，并附 `text` 供模型直接引用。
服务未启动或 token 错误时返回 `ok=false` 与原因，不会编造数据。

### 卸载

```powershell
powershell -ExecutionPolicy Bypass -File .\plugins\dsh-hardware-monitor\uninstall.ps1
```

## B. Web 注入回退（无插件管理器时）

适配器源文件由服务提供：

- `http://127.0.0.1:5678/plugins/dsh-hardware-monitor/inject.js`
- `http://127.0.0.1:5678/plugins/dsh-hardware-monitor/adapter.config.json`

在可注入脚本的宿主页面注入 `inject.js`，配置默认取同目录 `adapter.config.json`；
控制台/eval 注入时 `document.currentScript` 为空，请先设置：

```js
window.DSH_HARDWARE_MONITOR_CONFIG_URL = 'http://127.0.0.1:5678/plugins/dsh-hardware-monitor/adapter.config.json';
window.DSH_HARDWARE_MONITOR_CONFIG = { targetSelector: '#chat-container', baseUrl: 'http://127.0.0.1:5678' };
```

`targetSelector` 由使用方按宿主实际 DOM 填写；找不到时回退视口左上角。
外部 https 宿主需在服务端 `MONITOR_FRAME_ANCESTORS` 中显式允许其来源。

## 实时可视化

侧边栏面板在「物理硬盘」与「进程 Top」之间提供「实时可视化」区块：

- CPU、内存：百分比环 + 最近 60 秒趋势线；
- 网络：按活动网卡速率计算利用率百分比环，并显示 ↓/↑ MB/s 与链路速率；
- 数据每 2 秒刷新，趋势保留 30 个采样点；链路速率取 `/api/network` 中 up 网卡的最大 `speed_mbps`。

## 磁盘识别说明

- 物理盘优先用 WMI `Win32_DiskDrive`；WMI 不可用（受限 TEMP、WMI 服务异常等）时自动回退到隐藏窗口的
  PowerShell `Get-CimInstance Win32_DiskDrive`；
- 分区来自 psutil，空盘符/无介质/无权限/重复分区会被过滤，字段统一为字符串；
- DSH 面板把「物理硬盘」和「磁盘分区」分开渲染，不会再出现 `undefined` 空条目；
- 若仍显示「未识别到物理硬盘」，可用 `run.bat` 以管理员启动一次（部分机器的 WMI/CIM 查询需要提权）。

## 限制

- DSH 原生插件的 slot 依赖 DSH 版本（`conversation.session.header.corner`，回退 `conversation.composer.dock`）；
  若未来 slot 改名，需要同步更新 `client.js`。
- 客户端半需要刷新页面才加载；宿主半热加载后工具即可用。
- Web 注入方式依赖宿主允许外部脚本且能注入；VS Code 系不适用（见 `plugin/README.md`）。
