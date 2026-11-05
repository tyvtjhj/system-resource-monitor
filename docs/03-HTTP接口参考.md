> 注：该硬件指标功能已于 2026-11-05 移除；下文保留为历史归档。

# 03 HTTP 接口参考

服务由 Flask 提供，默认地址 `http://127.0.0.1:5678`。所有接口均为 `GET`，返回 `application/json`（页面路由返回 HTML）。

## 1. 通用约定

### 1.1 鉴权

只有设置了环境变量 `MONITOR_TOKEN` 才会启用鉴权，作用于所有 `/api/*`（[server.py#L1227-L1233](../server.py#L1227-L1233)）：

| 方式 | 示例 |
|---|---|
| 请求头 | `X-Monitor-Token: your-token` |
| 查询参数 | `?token=your-token` |

- 比较使用 `hmac.compare_digest`，避免时序侧信道。
- 未通过时返回 `401 {"error": "invalid or missing token"}`。
- `OPTIONS` 预检请求不校验。
- 页面文件本身不受 token 保护，但页面脚本会从 URL 的 `?token=` 读取并附加到 API 请求；不带 token 打开页面时数据区会显示连接失败。

### 1.2 CORS

`after_request` 钩子为**所有响应**附加（[server.py#L1236-L1241](../server.py#L1236-L1241)）：

```http
Access-Control-Allow-Origin: *
Access-Control-Allow-Headers: Content-Type, X-Monitor-Token
Access-Control-Allow-Methods: GET, OPTIONS
```

来源可通过 `MONITOR_CORS_ORIGIN` 覆盖。

### 1.3 缓存

- 页面响应：`Cache-Control: no-cache`。
- 前端请求：`cache: 'no-store'`。
- `/api/network` 服务端缓存 5 秒；`/api/processes` 缓存 1.5 秒。
- 其余 API 直接读内存快照，无额外缓存。

### 1.4 错误语义

| 状态码 | 场景 | 响应 |
|---|---|---|
| `200` | 正常 | 指标 JSON |
| `401` | token 缺失或错误 | `{"error": "invalid or missing token"}` |
| `404` | 页面文件不存在 | Flask 默认 404 |
| `500` | `/api/network`、`/api/processes` 现场计算异常 | `{"error": "异常信息"}` |
| `503` | 指标尚未就绪或值为 `None` | `{"error": "<key>_error 或 'not ready yet, retry in a second'"}` |

`/api/all` 永远不会因单个指标失败而报错；失败项以 `<key>_error` 字符串出现，其余字段照常返回。

## 2. 路由总表

| 方法与路径 | 处理函数 | 数据来源 | 说明 |
|---|---|---|---|
| `GET /` | `mobile_page` | `monitor-mobile.html` | 主页面 |
| `GET /monitor-mobile.html` | `mobile_page` | 同上 | 显式路径 |
| `GET /monitor.html` | `desktop_page` | `monitor.html` | 桌面版页面 |
| `GET /api/all` | `api_all` | 快照 | 一次返回全部指标 |
| `GET /api/cpu` | `api_cpu` | `cpu` | CPU |
| `GET /api/memory` | `api_memory` | `memory` | 内存 |
| `GET /api/disk` | `api_disk` | `disk` | 磁盘 |
| `GET /api/network-speed` | `api_network_speed` | `network_speed` | 实时速率 |
| `GET /api/gpu` | `api_gpu` | `gpu` | NVIDIA 独显 |
| `GET /api/gpu-engines` | `api_gpu_engines` | `gpu_engines` | 引擎利用率 |
| `GET /api/igpu` | `api_igpu` | `igpu` | 核显 |
| `GET /api/system` | `api_system` | `system_info` | 系统信息 |
| `GET /api/network` | `api_network` | 现场计算 + 5s 缓存 | 网卡详情 |
| `GET /api/processes` | `api_processes` | 现场计算 + 1.5s 缓存 | 进程内存排行 |
| `GET /api/start` | `api_start` | 快照状态 | 健康检查 |

## 3. 接口详情

### 3.1 `GET /api/all`

读取完整快照，并附加本轮采集耗时 `latency_ms`（[server.py#L1284-L1287](../server.py#L1284-L1287)）。

**顶层字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `cpu` | object | CPU 指标 |
| `memory` | object | 内存指标 |
| `network_speed` | object | 网络速率 |
| `gpu` | object \| null | NVIDIA 独显 |
| `disk` | array | 物理盘与分区混合 |
| `system_info` | object | 系统信息 |
| `igpu` | object \| null | 核显 |
| `gpu_engines` | object | GPU 引擎利用率 |
| `latency_ms` | number | 最近一轮采样耗时（毫秒） |
| `<key>_error` | string | 对应指标最近一次失败原因 |

**示例**（字段有删减）：

```json
{
  "cpu": {
    "usage": 12.4,
    "per_core": [8.0, 15.2, 3.1, 22.8],
    "freq_current_mhz": 3902.5,
    "count_logical": 16,
    "count_physical": 8,
    "load_avg": null,
    "freq": {
      "source": "pdh",
      "mode": "actual",
      "base_mhz": 2304.0,
      "avg_mhz": 3902.5,
      "min_mhz": 3525.9,
      "max_mhz": 4173.5,
      "per_core_mhz": [3790.5, 3967.2, 4084.5, 3924.3],
      "physical": [
        {"index": 0, "label": "C0", "efficiency": 0, "logical": [0, 1], "mhz": 3967.2}
      ]
    }
  },
  "memory": {
    "total_gb": 31.9,
    "used_gb": 18.2,
    "available_gb": 13.7,
    "usage_percent": 57.1
  },
  "network_speed": {
    "download_mbps": 1.23,
    "upload_mbps": 0.15,
    "bytes_sent_mb": 10240.5,
    "bytes_recv_mb": 88213.2,
    "packets_sent": 1200000,
    "packets_recv": 3400000
  },
  "gpu": {
    "name": "NVIDIA GeForce RTX 4070",
    "usage": 7,
    "used_gb": 1.8,
    "total_gb": 12.0
  },
    "sensors": {
      "GPU (NVIDIA GeForce RTX 4070)": 51.0,
      "CPU (CPU Package)": 58.0
    },
    "gpu": {"name": "GPU (NVIDIA GeForce RTX 4070)", "value": 51.0}
  },
  "disk": [
    {
      "model": "Samsung SSD 980 PRO 1TB",
      "interface_type": "SCSI",
      "size_gb": 931.5,
      "serial_number": "S6B0NL0T...",
      "type": "physical"
    },
    {
      "device": "C:\\",
      "mountpoint": "C:\\",
      "fstype": "NTFS",
      "total_gb": 930.5,
      "used_gb": 512.3,
      "free_gb": 418.2,
      "usage_percent": 55.1
    }
  ],
  "system_info": {
    "boot_time": "2026-11-01 09:12:33",
    "uptime": "2d 05:31",
    "total_processes": 312,
    "running_processes": 312,
    "thread_count": 4211
  },
  "igpu": {
    "name": "Intel(R) UHD Graphics 770",
    "usage": 4.2,
    "shared_gb": 0.35,
    "dedicated_gb": 0.0,
    "engines": {"3D": 2.1, "VideoDecode": 2.1},
    "luid": "0x00000000_0x00010868",
    "type": "integrated"
  },
  "gpu_engines": {
    "name": "NVIDIA GeForce RTX 4070",
    "engines": {"cuda": 5, "3d": 5, "encode": 0, "decode": 0},
    "mem_used_gb": 1.8,
    "mem_total_gb": 12.0,
    "gpu": {"name": "NVIDIA GeForce RTX 4070", "usage": 7, "used_gb": 1.8, "total_gb": 12.0}
  },
  "latency_ms": 14.2
}
```

### 3.2 `GET /api/cpu`

| 字段 | 类型 | 说明 |
|---|---|---|
| `usage` | number | 总占用百分比 |
| `per_core` | number[] | 每逻辑核心占用 |
| `freq_current_mhz` | number | 全核频率（逐核平均值，见 `freq.avg_mhz`）；读不到逐核频率时退回 psutil 单一读数，取不到为 0 |
| `count_logical` | number | 逻辑核心数 |
| `count_physical` | number | 物理核心数 |
| `load_avg` | number[] \| null | 仅类 Unix 系统有值 |
| `freq` | object \| 缺失 | 逐核实时频率；平台读不到时整个字段不出现 |

`freq` 子字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `source` | string | `pdh`（Windows 性能计数器）/ `sysfs`（Linux cpufreq） |
| `mode` | string | `actual`（直接读实际频率）/ `performance`（`% Processor Performance` × 基频） |
| `base_mhz` | number \| null | 标称基频 |
| `avg_mhz` / `min_mhz` / `max_mhz` | number | 全核平均 / 最低 / 最高 |
| `per_core_mhz` | (number \| null)[] | 按逻辑处理器序号索引的单核频率 |
| `physical` | object[] | 物理核归组：`index`、`label`（`C0` / `P0` / `E0`）、`efficiency`、`logical`、`mhz` |

`physical` 里的 `label` 在 Intel 大小核机器上是 `P*`（性能核）/ `E*`（能效核），其余机器是 `C*`；
`logical` 是该物理核包含的逻辑处理器序号（超线程的两个线程同属一条）。

### 3.3 `GET /api/memory`

`total_gb`、`used_gb`、`available_gb`、`usage_percent`。

### 3.4 `GET /api/disk`

数组，元素有两种形态：

- 物理硬盘：`model`、`interface_type`、`size_gb`、`serial_number`、`type="physical"`。
- 分区：`device`、`mountpoint`、`fstype`、`total_gb`、`used_gb`、`free_gb`、`usage_percent`（无 `type` 字段）。

WMI 不可用时只返回分区；无介质光驱等分区会被跳过。

### 3.5 `GET /api/network-speed`

| 字段 | 说明 |
|---|---|
| `download_mbps` | 下行速度，按 `(bytes_recv 差值) / 1024² / 秒` 计算 |
| `upload_mbps` | 上行速度，同样算法 |
| `bytes_sent_mb` / `bytes_recv_mb` | 开机以来累计发送/接收量（MiB） |
| `packets_sent` / `packets_recv` | 累计包数 |


```json
{
  "sensors": {"GPU (型号)": 51.0, "CPU (CPU Package)": 58.0},
  "gpu": {"name": "GPU (型号)", "value": 51.0}
}
```

- `cpu` / `gpu` 可能为 `null`。
- `sensors` 保留通用键值形式，包含 GPU 与 CPU 条目。

### 3.7 `GET /api/gpu`

```json
{"name": "NVIDIA GeForce RTX 4070", "usage": 7, "used_gb": 1.8, "total_gb": 12.0}
```

无 NVIDIA 设备或 NVML 不可用时快照值为 `null`，接口返回 503。

### 3.8 `GET /api/gpu-engines`

| 字段 | 说明 |
|---|---|
| `name` | GPU 型号 |
| `engines` | `{cuda, 3d, encode, decode}`，取自 `nvidia-smi dmon -s u`，`-q` 兜底 |
| `mem_used_gb` / `mem_total_gb` | 当轮显存 |
| `gpu` | 当轮 `gpu` 快照副本 |

无 `nvidia-smi` 时 `engines` 为空对象；仅在首轮尚未写入或采集异常时返回 503。

### 3.9 `GET /api/igpu`

| 字段 | 说明 |
|---|---|
| `name` | 核显型号；WMI/注册表都取不到时显示「集成显卡」 |
| `usage` | 各引擎利用率之和，封顶 100 |
| `shared_gb` / `dedicated_gb` | 共享/专用内存（GiB） |
| `engines` | 引擎类型 → 利用率，按类型名排序 |
| `luid` | 适配器 LUID |
| `type` | 固定 `"integrated"` |

未识别到核显、PDH 不可用或读取失败时快照值为 `null`，接口返回 503。

### 3.10 `GET /api/system`

```json
{
  "boot_time": "2026-11-01 09:12:33",
  "uptime": "2d 05:31",
  "total_processes": 312,
  "running_processes": 312,
  "thread_count": 4211
}
```

### 3.11 `GET /api/network`

按需计算网卡详情，5 秒缓存（[server.py#L1326-L1341](../server.py#L1326-L1341)）。

```json
{
  "adapters": [
    {
      "name": "以太网",
      "description": "Realtek PCIe GbE Family Controller",
      "mac": "00:E2:69:11:22:33",
      "ipv4": ["192.168.1.10"],
      "netmask": ["255.255.255.0"],
      "ipv6": ["fe80::1"],
      "gateway": ["192.168.1.1"],
      "dns": ["223.5.5.5", "8.8.8.8"],
      "dhcp": true,
      "speed_mbps": 1000,
      "mtu": 1500,
      "up": true
    }
  ],
  "count": 1
}
```

- WMI 不可用时 `gateway`、`dns` 为空数组，`dhcp` 为 `null`。
- 已启用（`up=true`）的网卡排在前面。

### 3.12 `GET /api/processes`

按需计算，1.5 秒缓存；同名进程已合并。

```json
{
  "total_mb": 32700.0,
  "sum_mb": 18200.0,
  "sum_private_mb": 18200.0,
  "sum_working_mb": 24500.0,
  "count": 120,
  "process_count": 312,
  "processes": [
    {
      "name": "chrome.exe",
      "pids": 18,
      "private_mb": 3200.5,
      "working_set_mb": 4100.2,
      "private_percent": 9.79,
      "working_set_percent": 12.54
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `total_mb` | 物理内存总量 |
| `sum_private_mb` / `sum_working_mb` | 所有分组两种口径的合计 |
| `count` | 合并后的进程组数 |
| `process_count` | 实际进程数 |
| `processes` | 按 `private_mb` 降序的前 40 组 |

`private_mb` 使用 USS，近似任务管理器「内存」列；`working_set_mb` 含共享页，数值偏大。

### 3.13 `GET /api/start`

健康检查，不会再拉起新进程（历史实现的安全修复）。

```json
{
  "status": "ok",
  "ready": true,
  "host": "0.0.0.0",
  "port": 5678,
  "elevated": false
}
```

| 字段 | 说明 |
|---|---|
| `ready` | 快照中是否已有 `cpu` |
| `elevated` | 当前是否管理员权限 |
| `host` / `port` | 实际监听配置 |

该接口也被 `monitor_already_running()` 用来做单实例探测。

## 4. 调用示例

```powershell
# 全量
curl http://127.0.0.1:5678/api/all

# 单指标
curl http://127.0.0.1:5678/api/cpu

# 带 token（二选一）
curl -H "X-Monitor-Token: your-token" http://127.0.0.1:5678/api/all
curl "http://127.0.0.1:5678/api/all?token=your-token"
```

```javascript
const res = await fetch('http://127.0.0.1:5678/api/all', {
  headers: { 'X-Monitor-Token': 'your-token' },
  cache: 'no-store',
});
const data = await res.json();
```
