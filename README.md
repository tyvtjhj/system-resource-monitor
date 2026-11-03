# 硬件资源监控

Windows 本机的轻量硬件看板：Flask 采集 → JSON → 单页 HTML 仪表盘，无前端框架、无构建步骤、无数据库。

## 目录结构

```
server.py               Flask 后端 + 后台采样线程（兼容入口；T2 后改为薄包装）
monitor-mobile.html     主页面（服务根路径 / 返回它），带 GPU 引擎侧栏、手机自适应
monitor.html            桌面版页面（/monitor.html），无 GPU 侧栏
lhm_temp.ps1            子进程：加载 LibreHardwareMonitor 读 CPU 内核级温度
vendor/lhm/             自带的 LibreHardwareMonitorLib（官方 0.9.6）+ 依赖 + 许可证说明
pyproject.toml          项目元数据与依赖声明
requirements.in         直接依赖（锁文件输入）
requirements.txt        跨平台锁文件（uv pip compile --universal 生成）
requirements-dev.in/.txt 开发依赖（pytest / pytest-cov / ruff / pip-tools）
scripts/                bootstrap、lock-deps、doctor 等维护脚本
.env.example            环境变量示例（不会自动加载）
run.bat / run-admin.bat Windows 启动脚本（优先 .venv）
run.sh                  Linux / macOS 启动脚本
docs/                   Code Wiki 与开发文档
```

## 运行

```powershell
py -3.10 -m pip install -r requirements.txt   # 首次
py -3.10 server.py                            # 默认自动申请管理员权限（UAC）
```

`server.py` **默认以管理员身份启动**：检测到没有提权时，会用 UAC 重新拉起一个管理员实例并关闭当前窗口（CPU 内核级温度需要 PawnIO 驱动）。

不想每次弹 UAC（例如在 PyCharm 里调试）：

```powershell
py -3.10 server.py --no-elevate
# 或设环境变量
$env:MONITOR_NO_ELEVATE='1'; py -3.10 server.py
```

普通权限下功能照常，只是 CPU 温度降级为 ACPI 平台温度（标签显示 `CPU ACPI`）。`run-admin.bat` 保留，效果等价（先在批处理里提权，再启动，省一次 UAC 确认差异）。

> 同一端口只会跑一个实例：若 5678 已有监控在运行，新进程会提示占用它的 PID 并退出（Windows 允许重复绑端口，不检测的话会出现两个实例抢答）。

浏览器打开 `http://127.0.0.1:5678/`；手机/局域网访问 `http://<本机IP>:5678/`。

> 注意：PATH 上的 `python` 可能不是装了依赖的那个解释器，用 `py -3.10` 或 `run.bat` 最稳。

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

- `run.bat` / `run-admin.bat` / `run.sh` 会**优先使用 `.venv`**，找不到时回退 `py -3.10`、`py -3` 或 `python3`；原有的 `py -3.10 server.py` 用法仍然可用。
- `bootstrap` 默认升级 pip（部分代理环境会卡住），可加 `-SkipPipUpgrade`（Windows）或 `SKIP_PIP_UPGRADE=1`（Linux/macOS）跳过；`-Recreate` / `RECREATE=1` 可重建 `.venv`。
- `scripts/doctor.py` 要求解释器属于项目 `.venv`（用系统解释器会以退出码 1 提示）；只诊断系统解释器时加 `--allow-system`。

### 依赖锁文件维护

- 直接依赖写在 `requirements.in` / `requirements-dev.in`（保持 ASCII，避免中文 Windows 的 GBK 解码问题）。
- 锁文件由 `scripts/lock-deps.ps1`（Windows）或 `scripts/lock-deps.sh`（Linux/macOS）生成：`uv pip compile --universal --python-version 3.10 ...`，保留 `sys_platform` 平台 marker，Windows/Linux 共用同一份锁。
- 说明：pip-tools 7.6.x 的 `pip-compile` **没有** `--universal` 选项（任务书假设与实际不符），因此锁脚本**要求安装 uv**（`pip install uv`）；缺少 uv 时脚本会拒绝执行，避免用平台锁覆盖跨平台锁、丢失 `sys_platform` marker。生成时先写临时文件并校验 uv 头与 `sys_platform` marker，通过后才替换已提交的锁文件。
- 升级依赖的流程：改 `.in` → 运行锁脚本 → 检查 `git diff` → 提交 `requirements*.txt`。
- 环境变量示例见 `.env.example`（程序不会自动加载）。
- 沙箱/代理受限环境若 pip 卡住，可先设 `PIP_NO_CACHE_DIR=1`、`PIP_DISABLE_PIP_VERSION_CHECK=1` 再执行。

## 插件接入（预留）

插件与嵌入组件属于任务书 T3/T4/T5 的范围，**尚未实现**；规划中的入口如下（实现后会在本节补充用法）：

- `GET /plugin`：集成中心页面（服务状态、配置生成、适配器说明）。
- `GET /widget`：可 iframe 嵌入的缩略监控页。
- `GET /embed.js`：宿主页面注入脚本（Shadow DOM + iframe + `postMessage` 握手）。
- `GET /api/plugin/manifest`：插件元数据（版本、能力表、鉴权方式）。
- 页面脚本计划支持 `?api=<base-url>` 指定后端地址，便于反向代理/远程开发。

在此之前，最稳妥的集成方式仍是直接打开本机 `http://127.0.0.1:5678/`。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MONITOR_HOST` | `0.0.0.0` | 监听地址；改为 `127.0.0.1` 则只允许本机访问 |
| `MONITOR_PORT` | `5678` | 端口 |
| `MONITOR_TOKEN` | 空 | 设置后所有 `/api/*` 需带 `X-Monitor-Token` 头；页面用 `?token=xxx` 打开即可 |
| `MONITOR_CORS_ORIGIN` | `*` | 允许的跨域来源 |
| `MONITOR_LHM_DLL` | 自动查找 | `LibreHardwareMonitorLib.dll` 路径（默认找 FanControl / LibreHardwareMonitor 安装目录） |

## 接口

| 路由 | 说明 |
|---|---|
| `GET /` `/monitor-mobile.html` | 主页面 |
| `GET /monitor.html` | 桌面版页面 |
| `GET /api/all` | 一次返回全部指标（CPU/内存/磁盘/网络/温度/GPU/引擎/系统信息） |
| `GET /api/cpu` `/api/memory` `/api/disk` | 单指标 |
| `GET /api/network-speed` `/api/temperature` `/api/gpu` `/api/gpu-engines` `/api/system` | 单指标 |
| `GET /api/igpu` | 核显：利用率、共享内存、各引擎占比 |
| `GET /api/processes` | 进程内存排行（RSS，供内存侧栏使用，1.5 秒缓存） |
| `GET /api/network` | 网卡详情（ipconfig）：地址/掩码/网关/DNS/MAC/速率/MTU/DHCP（5 秒缓存） |
| `GET /api/start` | 健康检查（只回报状态，不再拉起进程） |

单项采集失败时，`/api/all` 里会出现 `<指标>_error` 字段，其余指标照常返回。

## 数据来源

- `psutil`：CPU 占用/频率/核心数、内存、分区用量、网卡计数、开机时间、进程与线程数
- `pynvml`（NVIDIA）：独立显卡型号、利用率、显存、温度
- **核显（所有厂商通用）**：Windows 性能计数器 `GPU Engine(*)\Utilization Percentage` 按 LUID 汇总 3D/Compute/视频引擎，`GPU Adapter Memory` 提供共享/专用内存；核显通过「**共享内存最多的那块适配器**」识别（核显没有独立显存、用的是系统内存，独显的共享用量通常接近 0），型号名优先取 WMI `Win32_VideoController`，取不到时只在候选唯一的情况下用注册表 `DriverDesc`，否则显示「集成显卡」
- `nvidia-smi dmon -s u`：CUDA/3D/编码/解码引擎利用率（`-q` 为兜底解析）
- `WMI`：物理硬盘型号/接口/序列号
- CPU 温度（四条来源依次尝试，命中哪个就把来源写进显示名与页面标签）：
  1. **`vendor/lhm/LibreHardwareMonitorLib.dll`（内核级，需管理员）** —— 项目自带官方 LHM 0.9.6 及其依赖，由 `lhm_temp.ps1` 子进程加载读 `CPU Package`/`Core Max`，与 FanControl、HWiNFO 显示的数值一致；**不再依赖 FanControl 安装目录**（找不到自带库时才回退到 FanControl / LibreHardwareMonitor 的安装位置，或用 `MONITOR_LHM_DLL` 指定）；
  2. `root\LibreHardwareMonitor` / `root\OpenHardwareMonitor` WMI 命名空间（若这些程序正在运行）；
  3. Windows 性能计数器 `\Thermal Zone Information(*)\Temperature`（ACPI 热区，**不需要管理员**，但它是「平台温度」，比核心温度低不少）；
  4. `Win32_TemperatureSensor` —— 多数机器上该类为空。

  页面上的 CPU 标签会显示来源：`CPU PKG`（内核级）、`CPU ACPI`（平台热区）、`CPU OHM`/`CPU WMI`，鼠标悬停可看完整来源名。

## 设计要点

采样在**单个后台线程**里按固定节奏进行，结果写入内存快照；HTTP 请求只读快照，
所以接口响应恒定在毫秒级，不会因为 `sleep` 采样或 `nvidia-smi` 子进程而变慢：

| 频率 | 项目 |
|---|---|
| 1s | CPU、内存、网络速率、GPU、温度 |
| 3s | 磁盘、系统信息（WMI/进程枚举）、GPU 引擎（子进程较重） |
| 2s | 前端轮询（上一轮结束后再计时，避免请求叠加；页面隐藏时暂停） |

网络速率由相邻两次采样求差分得出，不再在请求里 `sleep(0.5)`。

## 已知限制

- WMI 在受限账号/沙箱下会返回「拒绝访问」，此时物理硬盘列表为空；其余指标不受影响。
- **CPU 温度**：Windows 没有统一的用户态接口。真正的核心温度（`CPU Package`）只能由内核驱动读 MSR 得到，**必须管理员权限**；以普通权限运行时程序会自动降级到 ACPI 热区（`CPU ACPI`），那是「平台热区」温度，通常比核心温度低 10 ℃ 左右、粒度 1 ℃。要拿到与 FanControl/HWiNFO 一致的读数，请用 `run-admin.bat` 启动；启动日志会打印当前是「管理员」还是「普通用户」，`/api/start` 里也有 `elevated` 字段。
  - FanControl 那段 `LibreHardwareMonitorLib` 走的是 **PawnIO** 驱动（新版 LHM 已从 WinRing0 切换过来）。LHM 通过 P/Invoke 加载 `PawnIOLib.dll`，而该文件只在 `C:\Program Files\PawnIO`、不在 PATH 上，所以 `lhm_temp.ps1` 会先把 PawnIO 目录补进 PATH，否则即使提权也会读到空值。
  - 若提权后仍显示 `CPU ACPI`，说明该驱动被系统拦（HVCI/易受攻击驱动黑名单）或 PawnIO 版本不匹配，此时可改用 HWiNFO 共享内存方案。
- 只有 NVIDIA 显卡有利用率与引擎数据；AMD/Intel 只能显示型号。
- Windows 上 `psutil` 把存活进程都报为 RUNNING，因此「运行中进程数」与「进程总数」通常相等。

## 本轮修复

1. `len(psutil.process_iter())` 崩溃（生成器无 `len()`）→ 进程/线程统计恢复正常。
2. 前端引用了 HTML 中不存在的 `uptimeDisplay`/`bootTimeDisplay`/`procRunning`，会在刷新时抛异常并冻结仪表盘 → 补齐系统信息条，取值改为安全写入。
3. `/api/start` 调用不存在的 `subprocess.is_alive()` 必然 500，且允许局域网触发拉起进程 → 改为健康检查，按钮改为「重新连接」。
4. `/monitor.html` 无路由（404）且写死过期 IP → 加路由并改为自动推导地址。
5. 请求内 `sleep` 采样（`/api/all` 约 730ms、`/api/network-speed` 约 515ms）→ 后台采样 + 快照，实测 8ms。
6. 前端 800ms 轮询掩盖不了 >800ms 的响应 → 改 2s 且串行调度，标签页隐藏时暂停。
7. `nvidia-smi dmon` 列错位（把 mem% 当编码、enc% 当解码）→ 按 `gpu sm mem enc dec` 修正。
8. 每次请求新建 WMI 连接 → 线程内单例，只初始化一次。
9. 安全：新增可选 token、可绑定 127.0.0.1；移除可被局域网利用的进程拉起入口。
10. 补充 `requirements.txt`、`run.bat`、本说明；删除重复 CSS、无用隐藏 canvas、未被 `try` 覆盖的 `import wmi`。
11. `createGauge()` 收了 `color` 参数却没把它放进返回对象，`setGauge()` 里 `gauge.color` 恒为 `undefined`，占用率 ≤60% 时描边变成非法值被丢弃（CSS 也没有兜底 stroke），整圈弧线消失 —— 这就是「CPU 圆环有时不显示」的原因；已改为返回 `color`、创建时预置合法描边，并对 `NaN`/越界值做钳制。
12. 温度卡片新增 **CPU 温度**：`/api/temperature` 增加 `cpu`/`gpu` 两个已分类字段（`sensors` 保持兼容），CPU 温度按「LibreHardwareMonitor → ACPI 热区性能计数器 → WMI 传感器」依次尝试，读不到时显示 `--`；无值不缓存、每轮重试，枚举失败则 60 秒后再试，避免开机初期一直空着。
13. **温度趋势拆成两条曲线**：`Sparkline` 支持多序列（共用一条纵轴便于比较），温度卡片同时画 CPU（蓝）与 GPU（红），数值标签前加了同色图例点；绘制改为「先画全部渐变面积、再画全部折线」，避免两条曲线接近时后画的面积盖住先画的线。
14. **CPU 温度改用内核级来源**：新增 `lhm_temp.ps1` + `LhmReader`，以子进程加载 `LibreHardwareMonitorLib.dll` 读 `CPU Package`（与 FanControl 同源，需管理员）；普通权限下子进程会明确回报「读数全空、需要管理员」，上层只提示一次并回退 ACPI，不会每轮重试。页面标签显示来源（`CPU PKG` / `CPU ACPI`），悬停看完整名称；新增 `run-admin.bat` 一键提权启动。
15. **默认自动提权**：`server.py` 启动时若未提权，用 `ShellExecuteW("runas")` 重新拉起自己（带 `--elevated` 防递归），失败则在原进程提示并继续以普通权限运行；可用 `--no-elevate` 或 `MONITOR_NO_ELEVATE=1` 关闭。子进程还会把 LHM 依赖的 `PawnIOLib.dll` 所在目录补进 `PATH`（它不在 PATH 上，否则提权了也读不到）。
16. **单实例检测**：启动前探测端口上是否已有本服务（Windows 允许两个进程绑同一端口），有则打印占用者 PID 并退出；`/api/start` 增加 `elevated` 字段，启动日志打印「运行权限: 管理员/普通用户」。
17. **新增核显（iGPU）卡片**：`GpuPerfReader` 用 Windows GPU 性能计数器读出集成显卡的利用率和共享内存（不依赖 NVML，AMD/Intel 通用），`/api/all` 增加 `igpu` 字段与 `/api/igpu` 路由，两页都新增青色仪表卡片。核显靠「共享内存最多的适配器」识别；首次建查询约 250ms，之后每次约 1ms，每 20 秒重建一次以跟上进程变化。
18. **新增内存侧栏**：点击 MEMORY 卡片从右侧滑出，列出进程内存排行，支持「按占用 / 按名称」排序，以及「专用 / 工作集」两个口径切换；侧栏打开时每 2 秒刷新，关闭即停止轮询。后端 `/api/processes`（1.5 秒缓存）**按进程名合并同名进程**（Edge/Chrome 的多个渲染进程会合并成一行并标注 ×N），并同时返回两个口径：`private_mb`（USS，≈ 任务管理器「内存」列的专用工作集）与 `working_set_mb`（工作集，含共享页，数值偏大）。另外四/三个侧栏都支持 `#memory` / `#disk` / `#cores` / `#gpu` 锚点直接展开。
19. **新增网络侧栏（ipconfig）**：点击 NETWORK 卡片滑出，按网卡分卡片显示 IPv4/IPv6、子网掩码、默认网关、DNS、MAC、链路速率、MTU、DHCP 与设备名。数据由 `Win32_NetworkAdapterConfiguration`（网关/DNS/DHCP）与 psutil（友好名称/速率/MTU）按 MAC + IP 关联合并，WMI 不可用时退回纯 psutil（此时无网关/DNS）。`/api/network` 带 5 秒缓存，打开时拉一次；锚点 `#network`。
20. **CPU 温度不再依赖 FanControl**：把官方 LibreHardwareMonitor 0.9.6 的 `LibreHardwareMonitorLib.dll` 及依赖（HidSharp、System.Memory 等）放进 `vendor/lhm/`，`find_dll()` 优先用它，其次才找 FanControl / LibreHardwareMonitor 安装目录；附 `NOTICE.txt` 说明来源与 MPL-2.0 许可。同时修掉一个真实坑：把 DLL 复制到项目目录后，`Assembly.LoadFrom` 会报 `0x80131515（Operation is not supported）`，改为**按字节加载** `Assembly.Load(File.ReadAllBytes(...))` 并注册 `AssemblyResolve` 解析同目录依赖，两个位置都验证可用。
21. **暗色模式**：页头右上角新增主题开关（亮色显示「🌙 暗色」，暗色显示「☀️ 亮色」），选择存 `localStorage`，首次访问跟随系统 `prefers-color-scheme`；`<head>` 里有一段极小脚本在首次绘制前应用主题，避免闪白。实现上把散落的写死浅色收敛成语义变量（`--track` / `--track-strong` / `--chip-good-*` / `--error-bg`），暗色只需覆盖一组变量即可同时作用于卡片、仪表底环、进度条、徽标与侧栏；另支持 `?theme=dark|light` 直接固定主题。
