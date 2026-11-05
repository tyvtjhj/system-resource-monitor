# 硬件资源监控

Windows 本机的轻量硬件看板：Flask 采集 → JSON → 单页 HTML 仪表盘，无前端框架、无构建步骤、无数据库。

## 目录结构

```
server.py               兼容入口（转发到 hardware_monitor.__main__）
hardware_monitor/       后端包：config/state/sampler/api + collectors/ + platform/
tests/                  pytest 测试（状态层 / API / 采集工具 / 平台能力）
monitor-mobile.html     主页面（服务根路径 / 返回它），带 GPU 引擎侧栏、手机自适应
monitor.html            桌面版页面（/monitor.html），无 GPU 侧栏
pyproject.toml          项目元数据与依赖声明
requirements.in         直接依赖（锁文件输入）
requirements.txt        跨平台锁文件（uv pip compile --universal 生成）
requirements-dev.in/.txt 开发依赖（pytest / pytest-cov / ruff / pip-tools）
scripts/                bootstrap、lock-deps、doctor 等维护脚本
.env.example            环境变量示例（不会自动加载）
run.bat                Windows 启动脚本（优先 .venv）
run-hidden.vbs          Windows 无控制台启动（pythonw，后台静默）
run.sh                  Linux / macOS 启动脚本
docs/                   Code Wiki 与开发文档
plugin/                 插件与缩略组件（embed.js / widget / 集成中心 / 适配器）
plugins/dsh-hardware-monitor/  DSH 原生插件包（可用 install-dsh.ps1 安装；awesome-dsh-plugin 投稿入口）
```

 > T2 起后端拆分为 `hardware_monitor` 包：`platform/windows.py` 与 `platform/linux.py`
> 提供统一接口（物理盘、网卡详情、核显、能力表），
> `python server.py` / `run.bat` 等旧启动方式保持不变。

## 运行

```powershell
py -3.10 -m pip install -r requirements.txt   # 首次
py -3.10 server.py
```

浏览器打开 `http://127.0.0.1:5678/`；手机/局域网访问 `http://<本机IP>:5678/`。

> 同一端口只会跑一个实例：若 5678 已有监控在运行，新进程会提示占用它的 PID 并退出。

> 注意：PATH 上的 `python` 可能不是装了依赖的那个解释器，用 `py -3.10` 或 `run.bat` 最稳。

### 隐藏 Python 控制台（Windows）

后台常驻时不想看到 Python 控制台窗口：

- **双击 `run-hidden.vbs`**：用 `.venv\Scripts\pythonw.exe` 启动，完全无窗口；
- **DSH 插件自动拉起**：插件已优先使用 `pythonw.exe`，不再弹出 Python 窗口；
- 已经有一个可见的 Python 窗口时，结束该 `python.exe` 进程即可；若插件在运行，它会自动用隐藏方式重新拉起。

## 可复制环境（T1）

推荐用脚本一键创建项目内虚拟环境并安装锁定依赖：

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
.\.venv\Scripts\python.exe scripts\doctor.py      # 环境自检
```

```bash
# Linux / macOS
bash scripts/bootstrap.sh
.venv/bin/python scripts/doctor.py
```

- `run.bat` / `run.sh` 会**优先使用 `.venv`**，找不到时回退 `py -3.10`、`py -3` 或 `python3`；原有的 `py -3.10 server.py` 用法仍然可用。
- `bootstrap` 默认升级 pip（部分代理环境会卡住），可加 `-SkipPipUpgrade`（Windows）或 `SKIP_PIP_UPGRADE=1`（Linux/macOS）跳过；`-Recreate` / `RECREATE=1` 可重建 `.venv`。
- `scripts/doctor.py` 要求解释器属于项目 `.venv`（用系统解释器会以退出码 1 提示）；只诊断系统解释器时加 `--allow-system`。

### 依赖锁文件维护

- 直接依赖写在 `requirements.in` / `requirements-dev.in`（保持 ASCII，避免中文 Windows 的 GBK 解码问题）。
- 锁文件由 `scripts/lock-deps.ps1`（Windows）或 `scripts/lock-deps.sh`（Linux/macOS）生成：`uv pip compile --universal --python-version 3.10 ...`，保留 `sys_platform` 平台 marker，Windows/Linux 共用同一份锁。
- 说明：pip-tools 7.6.x 的 `pip-compile` **没有** `--universal` 选项（任务书假设与实际不符），因此锁脚本**要求安装 uv**（`pip install uv`）；缺少 uv 时脚本会拒绝执行，避免用平台锁覆盖跨平台锁、丢失 `sys_platform` marker。生成时先写临时文件并校验 uv 头与 `sys_platform` marker，通过后才替换已提交的锁文件。
- 升级依赖的流程：改 `.in` → 运行锁脚本 → 检查 `git diff` → 提交 `requirements*.txt`。
- 环境变量示例见 `.env.example`（程序不会自动加载）。
- 沙箱/代理受限环境若 pip 卡住，可先设 `PIP_NO_CACHE_DIR=1`、`PIP_DISABLE_PIP_VERSION_CHECK=1` 再执行。
- 锁脚本的临时文件（`requirements*.txt.tmp*`）已加入 `.gitignore`；脚本正常结束会清理，中断残留会在下次运行或下次手工执行时自动移除。

## 插件接入

插件与缩略组件已实现（T4/T5），入口如下：

- `GET /plugin`：集成中心（服务状态、缩略预览、配置片段生成、宿主能力矩阵）。
- `GET /widget`：可 iframe 嵌入的缩略监控页；`GET /embed.js`：宿主注入脚本（Shadow DOM + iframe + `postMessage` 握手，token 不进 URL）。
- `GET /api/plugin/manifest`：插件元数据（版本、能力表、鉴权方式）。
- 宿主适配器在 `plugin/adapters/`：Web/DSH 注入、用户脚本、VS Code 系扩展、Codex 状态行，见 [plugin/README.md](plugin/README.md)。

**DSH 原生插件（推荐）**：`plugins/dsh-hardware-monitor/` 提供输入框下方（composer dock）挂件、侧边栏完整面板与 `hardware_monitor_status` 工具。

### 一键安装（Windows）

在仓库目录直接运行：

```powershell
powershell -ExecutionPolicy Bypass -File install-dsh.ps1
```

或双击 `install-dsh.cmd`。不在仓库目录时，脚本会自动克隆/下载到 `%LOCALAPPDATA%\HardwareMonitor`：

```powershell
powershell -ExecutionPolicy Bypass -Command "$u='https://raw.githubusercontent.com/tyvtjhj/system-resource-monitor/main/install-dsh.ps1'; $p=Join-Path $env:TEMP 'install-dsh.ps1'; irm $u -OutFile $p; & $p"
```

GitHub 不可用时把 raw 地址换成 Gitee：
`https://gitee.com/tyvtjhj/system-resource-monitor/raw/main/install-dsh.ps1`。

前提：已安装 DSH 并至少启动过一次；Windows PowerShell 5.1+。脚本会复用 `scripts/bootstrap.ps1` 创建 `.venv` 并安装依赖，然后安装 DSH 插件、启动 5678 服务。

手动安装：`powershell -ExecutionPolicy Bypass -File .\plugins\dsh-hardware-monitor\install.ps1`；
刷新 DSH 页面（Ctrl+R）后输入框下方出现性能挂件，侧边栏出现「硬件监控」图标，点击打开原生完整面板。服务地址/Token 通过启动 DSH 前的
`HARDWARE_MONITOR_URL` / `HARDWARE_MONITOR_TOKEN` 配置。

页面脚本支持 `?api=<base-url>` 指定后端，便于反向代理/远程开发。

如果不装插件，最稳妥的集成方式仍是直接打开本机 `http://127.0.0.1:5678/`。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MONITOR_HOST` | `0.0.0.0` | 监听地址；改为 `127.0.0.1` 则只允许本机访问 |
| `MONITOR_PORT` | `5678` | 端口 |
| `MONITOR_TOKEN` | 空 | 设置后所有 `/api/*` 需带 `X-Monitor-Token` 头；页面用 `?token=xxx` 打开即可 |
| `MONITOR_CORS_ORIGIN` | `*` | 允许的跨域来源 |

## 接口

| 路由 | 说明 |
|---|---|
| `GET /` `/monitor-mobile.html` | 主页面 |
| `GET /monitor.html` | 桌面版页面 |
| `GET /api/all` | 一次返回全部指标（CPU/内存/磁盘/网络/GPU/引擎/系统信息） |
| `GET /api/cpu` `/api/memory` `/api/disk` | 单指标 |
| `GET /api/network-speed` `/api/gpu` `/api/gpu-engines` `/api/system` | 单指标 |
| `GET /api/igpu` | 核显：利用率、共享内存、各引擎占比 |
| `GET /api/processes` | 进程内存排行（RSS，供内存侧栏使用，1.5 秒缓存） |
| `GET /api/network` | 网卡详情（ipconfig）：地址/掩码/网关/DNS/MAC/速率/MTU/DHCP（5 秒缓存） |
| `GET /api/start` | 健康检查（只回报状态，不再拉起进程） |

单项采集失败时，`/api/all` 里会出现 `<指标>_error` 字段，其余指标照常返回。

## 数据来源

- `psutil`：CPU 占用/频率/核心数、内存、分区用量、网卡计数、开机时间、进程与线程数
- **CPU 逐核实时频率（Windows）**：性能计数器 `Processor Information(*,i)\Actual Frequency` 逐逻辑核采样（含睿频，与任务管理器一致），
  缺失时用 `% Processor Performance` × 基频换算；物理核归组取自 `GetLogicalProcessorInformationEx`，Intel 大小核标 `P*`/`E*`；
  Linux 走 `/sys/.../cpufreq/scaling_cur_freq` 与 `thread_siblings_list`。卡片显示全核平均频率，核心侧栏可在
  「物理核心 · 频率」与「线程 · 占用」两种样式间切换（选择会记住）
- `pynvml`（NVIDIA）：独立显卡型号、利用率、显存
- **核显（所有厂商通用）**：Windows 性能计数器 `GPU Engine(*)\Utilization Percentage` 按 LUID 汇总 3D/Compute/视频引擎，`GPU Adapter Memory` 提供共享/专用内存；核显通过「**共享内存最多的那块适配器**」识别（核显没有独立显存、用的是系统内存，独显的共享用量通常接近 0），型号名优先取 WMI `Win32_VideoController`，取不到时只在候选唯一的情况下用注册表 `DriverDesc`，否则显示「集成显卡」
- `nvidia-smi dmon -s u`：CUDA/3D/编码/解码引擎利用率（`-q` 为兜底解析）
- `WMI`：物理硬盘型号/接口/序列号
## 设计要点

采样在**单个后台线程**里按固定节奏进行，结果写入内存快照；HTTP 请求只读快照，
所以接口响应恒定在毫秒级，不会因为 `sleep` 采样或 `nvidia-smi` 子进程而变慢：

| 频率 | 项目 |
|---|---|
| 1s | CPU、内存、网络速率、GPU |
| 3s | 磁盘、系统信息（WMI/进程枚举）、GPU 引擎（子进程较重） |
| 2s | 前端轮询（上一轮结束后再计时，避免请求叠加；页面隐藏时暂停） |

网络速率由相邻两次采样求差分得出，不再在请求里 `sleep(0.5)`。

## 已知限制

- WMI 在受限账号/沙箱下会返回「拒绝访问」，此时物理硬盘列表为空；其余指标不受影响。
- 只有 NVIDIA 显卡有利用率与引擎数据；AMD/Intel 只能显示型号。
- Windows 上 `psutil` 把存活进程都报为 RUNNING，因此「运行中进程数」与「进程总数」通常相等。

## 历史修复记录

历史修复与设计演进见 [docs/](docs/) 下的 T1–T6 报告。

## 许可证

本项目原创代码采用 MIT 许可证，见 [LICENSE](LICENSE)。

