"""硬件资源采集服务 - Flask REST API

设计：后台单线程按固定节奏采样，结果写入内存快照；HTTP 请求只读快照，
因此接口响应时间恒定在毫秒级，不再把 sleep/子进程开销压在请求线程上。
"""
import hmac
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime

import psutil
from flask import Flask, abort, jsonify, request, send_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------- 配置（均可用环境变量覆盖） ----------------
HOST = os.environ.get("MONITOR_HOST", "0.0.0.0")
PORT = int(os.environ.get("MONITOR_PORT", "5678"))
# 设置后才校验：请求需带 X-Monitor-Token 头或 ?token= 参数
TOKEN = os.environ.get("MONITOR_TOKEN", "").strip()
# 跨域来源；页面由本服务提供时同源即可，默认放宽以兼容直接打开的副本
CORS_ORIGIN = os.environ.get("MONITOR_CORS_ORIGIN", "*")

FAST_INTERVAL = 1.0    # CPU / 内存 / 网络速率 / GPU
SLOW_INTERVAL = 3.0    # 磁盘 / 系统信息（WMI、进程枚举较重）
ENGINE_INTERVAL = 3.0  # nvidia-smi 子进程较重，单独低频

app = Flask(__name__)

def is_elevated():
    """当前进程是否以管理员权限运行（Windows）。"""
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def request_elevation():
    """默认以管理员身份启动：非管理员时用 UAC 重新拉起自己并退出当前进程。

    返回 True 表示已经把工作交给提权后的新进程；False 表示已经在管理员权限下，
    或提权被拒绝/不可用，此时继续以普通权限运行（CPU 温度会降级为 ACPI）。

    防止递归：提权后的子进程会带 --elevated 参数，见到它就不再尝试提权。
    需要关掉自动提权时：命令行加 --no-elevate，或设 MONITOR_NO_ELEVATE=1。
    """
    if os.name != "nt" or is_elevated():
        return False
    if "--elevated" in sys.argv:
        return False
    if "--no-elevate" in sys.argv:
        return False
    if os.environ.get("MONITOR_NO_ELEVATE", "").strip().lower() in ("1", "true", "yes", "on"):
        return False

    try:
        import ctypes

        script = os.path.abspath(__file__)
        extra = [arg for arg in sys.argv[1:] if arg not in ("--no-elevate", "--elevated")]
        params = subprocess.list2cmdline([script, *extra, "--elevated"])
        hwnd = None
        rc = ctypes.windll.shell32.ShellExecuteW(
            hwnd, "runas", sys.executable, params, os.path.dirname(script), 1
        )
        if rc > 32:
            return True
        # 5 = 用户在 UAC 上点了"否"
        print(f"[WARN] 未获得管理员权限（ShellExecuteW={rc}），将以普通权限运行，CPU 温度为 ACPI 平台温度")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 提权失败: {exc}，将以普通权限运行")
    return False


# ---------------- 采样快照 ----------------
_state = {}
_state_lock = threading.Lock()
_stop = threading.Event()


def put(key, value):
    with _state_lock:
        _state.pop(key + "_error", None)
        _state[key] = value


def put_error(key, err):
    with _state_lock:
        _state[key + "_error"] = str(err)


def collect(key, fn, *args):
    """执行一个采集函数：失败只记录 <key>_error，不影响其他指标。"""
    try:
        value = fn(*args)
    except Exception as exc:  # noqa: BLE001 - 单个采集失败不应拖垮采样线程
        put_error(key, exc)
        print(f"[WARN] collect {key} failed: {exc}")
        return None
    put(key, value)
    return value


# ---------------- 采集：CPU / 内存 ----------------
def collect_cpu():
    freq = psutil.cpu_freq()
    return {
        "usage": round(psutil.cpu_percent(interval=None), 1),
        "per_core": [round(v, 1) for v in psutil.cpu_percent(interval=None, percpu=True)],
        "freq_current_mhz": round(freq.current, 1) if freq and freq.current else 0,
        "count_logical": psutil.cpu_count(logical=True),
        "count_physical": psutil.cpu_count(logical=False),
        "load_avg": os.getloadavg() if hasattr(os, "getloadavg") else None,
    }


def collect_memory():
    vm = psutil.virtual_memory()
    return {
        "total_gb": round(vm.total / (1024 ** 3), 1),
        "used_gb": round(vm.used / (1024 ** 3), 1),
        "available_gb": round(vm.available / (1024 ** 3), 1),
        "usage_percent": vm.percent,
    }


# ---------------- 采集：网络速率（由相邻两次采样求差分，不 sleep） ----------------
def net_speed(prev_counters, prev_ts, cur_counters, cur_ts):
    dt = max(cur_ts - prev_ts, 1e-6)
    return {
        "download_mbps": round((cur_counters.bytes_recv - prev_counters.bytes_recv) / 1024 ** 2 / dt, 2),
        "upload_mbps": round((cur_counters.bytes_sent - prev_counters.bytes_sent) / 1024 ** 2 / dt, 2),
        "bytes_sent_mb": round(cur_counters.bytes_sent / 1024 ** 2, 1),
        "bytes_recv_mb": round(cur_counters.bytes_recv / 1024 ** 2, 1),
        "packets_sent": cur_counters.packets_sent,
        "packets_recv": cur_counters.packets_recv,
    }


# ---------------- 采集：GPU / 温度（NVML，进程内只初始化一次） ----------------
class NvmlSession:
    """封装 pynvml，初始化失败时静默降级，不影响其他指标。"""

    def __init__(self):
        self.nvml = None
        self.handles = []
        try:
            import warnings

            with warnings.catch_warnings():
                # pynvml 包已改名 nvidia-ml-py，导入时的 FutureWarning 无信息量
                warnings.simplefilter("ignore", FutureWarning)
                import pynvml as nvml

            nvml.nvmlInit()
            self.nvml = nvml
            self.handles = [
                nvml.nvmlDeviceGetHandleByIndex(i)
                for i in range(nvml.nvmlDeviceGetCount())
            ]
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] NVML unavailable: {exc}")

    def _name(self, handle):
        name = self.nvml.nvmlDeviceGetName(handle)
        return name.decode() if isinstance(name, bytes) else str(name)

    def gpu(self):
        """返回 {name, usage, used_gb, total_gb}；无 NVIDIA 设备时返回 None。"""
        if not self.handles:
            return None
        names, usages = [], []
        used_mb = total_mb = 0
        for handle in self.handles:
            names.append(self._name(handle))
            try:
                usages.append(self.nvml.nvmlDeviceGetUtilizationRates(handle).gpu)
            except Exception:  # noqa: BLE001
                usages.append(0)
            try:
                mem = self.nvml.nvmlDeviceGetMemoryInfo(handle)
                used_mb += mem.used
                total_mb += mem.total
            except Exception:  # noqa: BLE001
                pass
        return {
            "name": ", ".join(names),
            "usage": round(sum(usages) / len(usages)) if usages else 0,
            "used_gb": round(used_mb / 1024 ** 3, 1),
            "total_gb": round(total_mb / 1024 ** 3, 1),
        }

    def temperatures(self):
        sensors = {}
        for handle in self.handles:
            try:
                sensors[f"GPU ({self._name(handle)})"] = round(
                    float(self.nvml.nvmlDeviceGetTemperature(handle, 0)), 1
                )
            except Exception:  # noqa: BLE001
                continue
        return {"sensors": sensors}


# ---------------- 采集：WMI（线程内单例，避免每次请求重连 DCOM） ----------------
_local = threading.local()


def wmi_client():
    """返回当前线程复用的 WMI 连接；不可用时返回 None（只尝试一次）。"""
    if hasattr(_local, "wmi"):
        return _local.wmi
    _local.wmi = None
    try:
        import pythoncom

        pythoncom.CoInitialize()
        import wmi as _wmi

        _local.wmi = _wmi.WMI()
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] WMI unavailable (physical disk / temp fallback disabled): {exc}")
        _local.wmi = None
    return _local.wmi


# ---------------- 采集：磁盘 ----------------
_disk_wmi_warned = False


def collect_disk():
    """物理硬盘（WMI，含未分配盘符的盘）+ 分区用量（psutil），返回混合列表。"""
    global _disk_wmi_warned
    disks = []

    client = wmi_client()
    if client is not None:
        try:
            for index, disk in enumerate(client.Win32_DiskDrive()):
                model = str(getattr(disk, "Model", "") or "").strip()
                size_gb = round(float(getattr(disk, "Size", 0) or 0) / (1024 ** 3), 1)
                disks.append({
                    "model": model or f"Disk {index}",
                    "interface_type": str(getattr(disk, "InterfaceType", "")),
                    "size_gb": size_gb if size_gb > 0 else None,
                    "serial_number": str(getattr(disk, "SerialNumber", "")).strip(),
                    "type": "physical",
                })
        except Exception as exc:  # noqa: BLE001
            if not _disk_wmi_warned:
                _disk_wmi_warned = True
                print(f"[WARN] WMI physical disk query failed (只会提示一次): {exc}")

    for part in psutil.disk_partitions(all=True):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            # 空光驱 / 无介质 / 无权限的分区直接跳过（原来只捕获 PermissionError）
            continue
        disks.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "used_gb": round(usage.used / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "usage_percent": usage.percent,
        })

    return disks


# ---------------- 采集：系统信息 ----------------
def count_processes():
    """返回 (进程总数, 运行中进程数, 线程总数)。

    注意：psutil.process_iter() 是生成器，不能直接 len()（原实现的崩溃点）。
    """
    pids = psutil.pids()
    running = threads = 0
    for proc in psutil.process_iter(["status", "num_threads"]):
        try:
            if proc.info.get("status") == psutil.STATUS_RUNNING:
                running += 1
            threads += proc.info.get("num_threads") or 0
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return len(pids), running, threads


def collect_system_info():
    boot = psutil.boot_time()
    uptime_seconds = max(time.time() - boot, 0)
    days, remainder = divmod(int(uptime_seconds), 86400)
    hours, minutes = divmod(remainder, 3600)[0], (remainder % 3600) // 60
    total, running, threads = count_processes()
    return {
        "boot_time": datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M:%S"),
        "uptime": f"{days}d {hours:02d}:{minutes:02d}" if days else f"{hours:02d}:{minutes:02d}",
        "total_processes": total,
        "running_processes": running,
        "thread_count": threads,
    }


# ---------------- 采集：核显（Windows GPU 引擎性能计数器） ----------------
class GpuPerfReader:
    """用 Windows 的 GPU 性能计数器读取核显数据（所有厂商通用，不依赖 NVML）。

    - 适配器识别：`GPU Adapter Memory` 里 **共享内存最多的** 那块 = 集成显卡
      （核显没有独立显存，用的是系统内存；独显的共享用量通常接近 0）。
    - 利用率：`GPU Engine(*)\\Utilization Percentage` 按 LUID 汇总 3D/Compute/视频引擎。
    - 名字：优先 WMI `Win32_VideoController`（只取正常工作的非 NVIDIA 适配器），
      读不到时退回注册表里的驱动描述。
    """

    ENGINE_TYPES = ("3D", "Compute", "VideoDecode", "VideoEncode")

    def __init__(self, refresh=20.0):
        self._lock = threading.Lock()
        self._refresh = refresh
        self._built_at = 0.0
        self._luid = None
        self._query = None
        self._handles = []
        self._name = None
        self._name_checked = False
        self._unavailable = False

    # ---- 适配器识别 ----
    @staticmethod
    def _adapter_memory():
        """返回 {luid: {"shared": bytes, "dedicated": bytes}}。"""
        import win32pdh

        try:
            items, instances = win32pdh.EnumObjectItems(
                None, None, "GPU Adapter Memory", win32pdh.PERF_DETAIL_WIZARD)
        except Exception:  # noqa: BLE001
            return {}
        if "Shared Usage" not in items:
            return {}

        result = {}
        query = win32pdh.OpenQuery()
        handles = []
        try:
            for instance in instances:
                luid = _luid_key(instance)
                if not luid:
                    continue
                for field in ("Shared Usage", "Dedicated Usage"):
                    path = win32pdh.MakeCounterPath(
                        (None, "GPU Adapter Memory", instance, None, 0, field))
                    try:
                        handles.append((luid, field, win32pdh.AddCounter(query, path)))
                    except Exception:  # noqa: BLE001
                        continue
            win32pdh.CollectQueryData(query)
            for luid, field, handle in handles:
                try:
                    _, value = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_LARGE)
                except Exception:  # noqa: BLE001
                    continue
                key = "shared" if field == "Shared Usage" else "dedicated"
                result.setdefault(luid, {"shared": 0, "dedicated": 0})[key] = value or 0
        except Exception:  # noqa: BLE001
            return {}
        finally:
            try:
                win32pdh.CloseQuery(query)
            except Exception:  # noqa: BLE001
                pass
        return result

    # ---- 名字（WMI 优先，注册表兜底） ----
    def _adapter_name(self):
        if self._name_checked:
            return self._name
        self._name_checked = True

        try:
            client = wmi_client()
            if client is not None:
                for controller in client.Win32_VideoController():
                    name = str(getattr(controller, "Name", "") or "")
                    vendor = str(getattr(controller, "AdapterCompatibility", "") or "")
                    error_code = int(getattr(controller, "ConfigManagerErrorCode", 0) or 0)
                    if error_code != 0 or not name:
                        continue
                    lowered = f"{name} {vendor}".lower()
                    if any(k in lowered for k in ("intel", "amd", "radeon", "vega", "apu")):
                        self._name = name
                        return name
        except Exception:  # noqa: BLE001
            pass

        try:
            import winreg

            key_path = (r"SYSTEM\CurrentControlSet\Control\Class"
                        r"\{4d36e968-e325-11ce-bfc1-08002be10318}")
            candidates = []
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as root:
                index = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    if not sub_name.isdigit():
                        continue
                    try:
                        with winreg.OpenKey(root, sub_name) as sub:
                            desc = str(winreg.QueryValueEx(sub, "DriverDesc")[0])
                    except OSError:
                        continue
                    lowered = desc.lower()
                    if any(k in lowered for k in ("intel", "amd", "radeon")) and "nvidia" not in lowered:
                        candidates.append(desc)
            # 注册表里可能残留已换掉的老设备（本例有 P630 与 UHD 两个），
            # 只有一个候选时才敢用，否则给通用名，避免张冠李戴。
            if len(candidates) == 1:
                self._name = candidates[0]
                return self._name
        except Exception:  # noqa: BLE001
            pass
        return None

    # ---- 建/重建查询 ----
    def _build(self):
        import win32pdh

        memory = self._adapter_memory()
        if not memory:
            return False
        # 共享内存最多的适配器 = 核显
        luid = max(memory, key=lambda key: memory[key]["shared"])
        if memory[luid]["shared"] <= 0:
            return False

        try:
            items, instances = win32pdh.EnumObjectItems(
                None, None, "GPU Engine", win32pdh.PERF_DETAIL_WIZARD)
        except Exception:  # noqa: BLE001
            return False
        if "Utilization Percentage" not in items:
            return False

        selected = []
        for instance in instances:
            match = INSTANCE_RE.match(instance)
            if not match:
                continue
            if _luid_key(instance) != luid:
                continue
            if match.group(6) not in self.ENGINE_TYPES:
                continue
            selected.append(instance)
        if not selected:
            return False

        query = win32pdh.OpenQuery()
        handles = []
        for instance in selected:
            path = win32pdh.MakeCounterPath(
                (None, "GPU Engine", instance, None, 0, "Utilization Percentage"))
            try:
                handles.append((match_type_of(instance), win32pdh.AddCounter(query, path)))
            except Exception:  # noqa: BLE001
                continue
        if not handles:
            win32pdh.CloseQuery(query)
            return False

        # 利用率是速率型计数器，第一次采集没有有效值，先预热一次
        try:
            win32pdh.CollectQueryData(query)
        except Exception:  # noqa: BLE001
            pass

        with self._lock:
            if self._query is not None:
                try:
                    win32pdh.CloseQuery(self._query)
                except Exception:  # noqa: BLE001
                    pass
            self._query = query
            self._handles = handles
            self._luid = luid
            self._built_at = time.time()
            self._shared_gb = round(memory[luid]["shared"] / 1024 ** 3, 2)
            self._dedicated_gb = round(memory[luid]["dedicated"] / 1024 ** 3, 2)
        return True

    # ---- 读取 ----
    def sample(self):
        if self._unavailable:
            return None
        try:
            import win32pdh
        except Exception:  # noqa: BLE001
            self._unavailable = True
            return None

        with self._lock:
            needs_build = self._query is None or time.time() - self._built_at > self._refresh
        if needs_build and not self._build():
            self._unavailable = True
            return None

        with self._lock:
            query, handles, luid = self._query, list(self._handles), self._luid
            shared_gb, dedicated_gb = self._shared_gb, self._dedicated_gb
        if query is None:
            return None

        try:
            win32pdh.CollectQueryData(query)
        except Exception:  # noqa: BLE001
            self._built_at = 0.0   # 下一轮重建
            return None

        per_engine = self._read_engines(win32pdh, handles)
        if per_engine is None:
            # 首次采集可能无效，稍等再取一次
            time.sleep(0.05)
            try:
                win32pdh.CollectQueryData(query)
            except Exception:  # noqa: BLE001
                self._built_at = 0.0
                return None
            per_engine = self._read_engines(win32pdh, handles)
        if per_engine is None:
            self._built_at = 0.0
            return None

        usage = min(round(sum(per_engine.values()), 1), 100.0)
        return {
            "name": self._adapter_name() or "集成显卡",
            "usage": usage,
            "shared_gb": shared_gb,
            "dedicated_gb": dedicated_gb,
            "engines": {k: round(min(v, 100.0), 1) for k, v in sorted(per_engine.items())},
            "luid": luid,
            "type": "integrated",
        }

    @staticmethod
    def _read_engines(win32pdh, handles):
        """读取全部引擎计数器；一个都读不到时返回 None。"""
        per_engine = {}
        dead = 0
        for engine_type, handle in handles:
            try:
                _, value = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_DOUBLE)
            except Exception:  # noqa: BLE001
                dead += 1
                continue
            if value:
                per_engine[engine_type] = per_engine.get(engine_type, 0.0) + value
        if dead and dead == len(handles):
            return None
        return per_engine


INSTANCE_RE = re.compile(
    r"pid_(\d+)_luid_(0x[0-9A-Fa-f]+)_(0x[0-9A-Fa-f]+)_phys_(\d+)_eng_(\d+)_engtype_(\S*)")
LUID_RE = re.compile(r"luid_(0x[0-9A-Fa-f]+)_(0x[0-9A-Fa-f]+)")


def _luid_key(instance):
    """把 `luid_0x00000000_0x00010868_phys_0` 归一成 `0x00000000_0x00010868`。"""
    match = LUID_RE.search(instance)
    return f"{match.group(1)}_{match.group(2)}" if match else None


def match_type_of(instance):
    """从实例名取出引擎类型（3D / Compute / VideoDecode …）。"""
    match = INSTANCE_RE.match(instance)
    return match.group(6) if match else "?"


# ---------------- 采集：进程内存排行（按需计算，短缓存） ----------------
_process_cache = {"at": 0.0, "data": None}
_process_lock = threading.Lock()

_network_cache = {"at": 0.0, "data": None}
_network_lock = threading.Lock()


def collect_processes(limit=40):
    """按名称合并的进程内存排行，供内存侧栏使用。

    - 合并同名进程：Edge/Chrome 的渲染进程各占一行会让总量看起来很小，
      任务管理器是把整组加在一起显示的。
    - 同时给两个口径：
      `private_mb` 私有（USS，约等于任务管理器的「内存」列 = 专用工作集），
      `working_set_mb` 工作集（含被多个进程共享的 DLL/共享内存，数值偏大）。
    """
    total = psutil.virtual_memory().total
    groups = {}

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            info = proc.info
            memory = proc.memory_info()
            try:
                private = proc.memory_full_info().uss
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                private = memory.private
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

        name = info.get("name") or f"PID {info['pid']}"
        key = name.lower()
        group = groups.setdefault(key, {
            "name": name,
            "pids": 0,
            "private_mb": 0.0,
            "working_set_mb": 0.0,
        })
        group["pids"] += 1
        group["private_mb"] += (private or 0) / 1024 ** 2
        group["working_set_mb"] += (memory.wset or 0) / 1024 ** 2

    rows = []
    for group in groups.values():
        rows.append({
            "name": group["name"],
            "pids": group["pids"],
            "private_mb": round(group["private_mb"], 1),
            "working_set_mb": round(group["working_set_mb"], 1),
            "private_percent": round(group["private_mb"] / (total / 1024 ** 2) * 100, 2),
            "working_set_percent": round(group["working_set_mb"] / (total / 1024 ** 2) * 100, 2),
        })

    rows.sort(key=lambda row: row["private_mb"], reverse=True)
    return {
        "total_mb": round(total / 1024 ** 2, 1),
        "sum_mb": round(sum(row["private_mb"] for row in rows), 1),
        "sum_private_mb": round(sum(row["private_mb"] for row in rows), 1),
        "sum_working_mb": round(sum(row["working_set_mb"] for row in rows), 1),
        "count": len(rows),
        "process_count": sum(row["pids"] for row in rows),
        "processes": rows[:limit],
    }


# ---------------- 采集：网卡详情（ipconfig） ----------------
def _normalize_mac(mac):
    """统一 MAC 写法：WMI 用 `00:E2:69`，psutil 用 `00-E2-69`。"""
    return re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()


def collect_network_detail():
    """网卡列表：地址/掩码/网关/DNS/MAC/速率/MTU/DHCP。

    WMI `Win32_NetworkAdapterConfiguration` 提供网关与 DNS（psutil 没有），
    psutil 提供友好的连接名、链路速率和 MTU；按 MAC 关联两者，WMI 不可用时退回纯 psutil。
    """
    addrs_by_name = psutil.net_if_addrs()
    stats_by_name = psutil.net_if_stats()

    # psutil 侧索引：MAC（去分隔符）与 IPv4 都作为关联键（WMI 用 `:`、psutil 用 `-`）
    by_mac, by_ip = {}, {}
    for name, addrs in addrs_by_name.items():
        entry = (name, stats_by_name.get(name))
        mac = next((a.address for a in addrs if a.family == psutil.AF_LINK), "")
        if mac:
            by_mac[_normalize_mac(mac)] = entry
        for addr in addrs:
            if addr.family == socket.AF_INET:
                by_ip[addr.address] = entry

    def lookup(mac, ips):
        found = by_mac.get(_normalize_mac(mac)) if mac else None
        if found:
            return found
        for ip in ips:
            if ip in by_ip:
                return by_ip[ip]
        return ("", None)

    def link_fields(stats):
        return {
            "speed_mbps": int(stats.speed) if stats else 0,
            "mtu": int(stats.mtu) if stats else 0,
            "up": bool(stats.isup) if stats else False,
        }

    adapters = []
    client = wmi_client()
    if client is not None:
        try:
            for cfg in client.Win32_NetworkAdapterConfiguration(IPEnabled=True):
                mac = str(getattr(cfg, "MACAddress", "") or "").upper()
                ips = [str(ip) for ip in (getattr(cfg, "IPAddress", None) or [])]
                masks = [str(mask) for mask in (getattr(cfg, "IPSubnet", None) or [])]
                gateway = [str(gw) for gw in (getattr(cfg, "DefaultIPGateway", None) or [])]
                dns = [str(server) for server in (getattr(cfg, "DNSServerSearchOrder", None) or [])]
                friendly, stats = lookup(mac, ips)
                adapters.append({
                    "name": friendly or str(getattr(cfg, "Description", "")),
                    "description": str(getattr(cfg, "Description", "")),
                    "mac": mac,
                    "ipv4": [ip for ip in ips if ":" not in ip],
                    "netmask": [mask for mask in masks if "." in mask],
                    "ipv6": [ip for ip in ips if ":" in ip],
                    "gateway": gateway,
                    "dns": dns,
                    "dhcp": bool(getattr(cfg, "DHCPEnabled", False)),
                    **link_fields(stats),
                })
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] WMI network config query failed: {exc}")

    if not adapters:
        for name, addrs in addrs_by_name.items():
            mac = next((a.address for a in addrs if a.family == psutil.AF_LINK), "")
            ipv4 = [a.address for a in addrs if a.family == socket.AF_INET and a.address != "127.0.0.1"]
            masks = [a.netmask for a in addrs if a.family == socket.AF_INET and a.netmask]
            ipv6 = [a.address for a in addrs
                    if a.family == socket.AF_INET6 and not a.address.startswith("::1")]
            if not ipv4 and not ipv6:
                continue
            adapters.append({
                "name": name,
                "description": name,
                "mac": mac.upper(),
                "ipv4": ipv4,
                "netmask": masks,
                "ipv6": ipv6,
                "gateway": [],
                "dns": [],
                "dhcp": None,
                **link_fields(stats_by_name.get(name)),
            })

    adapters.sort(key=lambda item: (not item["up"], item["name"].lower()))
    return {"adapters": adapters, "count": len(adapters)}


# ---------------- 采集：温度（GPU 用 NVML，CPU 多来源回退） ----------------
class LhmReader:
    """以子进程方式跑 LibreHardwareMonitorLib，读取 CPU Package 等内核级传感器。

    该库靠内核驱动读 MSR，**必须管理员权限**；普通权限下传感器能枚举但读数恒空，
    子进程会把结论以 {"ok": false, "error": ...} 报回来，上层据此回退到 ACPI。
    """

    def __init__(self, interval_ms=2000, max_age=10.0):
        self.interval_ms = interval_ms
        self.max_age = max_age
        self._lock = threading.Lock()
        self._state = None
        self._state_at = 0.0
        self._proc = None
        self._start_error = None
        self._script = os.path.join(BASE_DIR, "lhm_temp.ps1")

    @staticmethod
    def find_dll():
        """按常见安装位置找工作副产物；也可用 MONITOR_LHM_DLL 指定。"""
        candidates = [
            os.environ.get("MONITOR_LHM_DLL"),
            os.path.join(BASE_DIR, "vendor", "lhm", "LibreHardwareMonitorLib.dll"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "FanControl",
                         "LibreHardwareMonitorLib.dll"),
            os.path.join(os.environ.get("ProgramFiles", ""), "FanControl",
                         "LibreHardwareMonitorLib.dll"),
            os.path.join(os.environ.get("ProgramFiles", ""), "LibreHardwareMonitor",
                         "LibreHardwareMonitorLib.dll"),
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                return path
        return None

    def start(self):
        """启动子进程；返回错误信息字符串，成功返回 None。"""
        if self._proc is not None:
            return None
        if self._start_error:
            return self._start_error
        if os.name != "nt" or not os.path.isfile(self._script):
            self._start_error = "lhm_temp.ps1 不存在，跳过内核级温度"
            return self._start_error

        dll = self.find_dll()
        if not dll:
            self._start_error = ("未找到 LibreHardwareMonitorLib.dll"
                                 "（装 LibreHardwareMonitor，或用 MONITOR_LHM_DLL 指定路径）")
            return self._start_error

        powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        command = [
            powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self._script,
            "-DllPath", dll, "-IntervalMs", str(self.interval_ms),
        ]
        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = subprocess.Popen(command, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._start_error = f"启动温度子进程失败: {exc}"
            return self._start_error

        threading.Thread(target=self._pump, args=(self._proc,),
                         name="lhm-reader", daemon=True).start()
        return None

    def _pump(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            with self._lock:
                self._state = payload
                self._state_at = time.time()

    def read(self):
        """返回子进程最近一条结果；超过 max_age 视为失效。"""
        with self._lock:
            state, at = self._state, self._state_at
        if not state or time.time() - at > self.max_age:
            return None
        return state

    def exited(self):
        """子进程是否已经退出（例如权限不足、读不到传感器后自行结束）。"""
        return self._proc is not None and self._proc.poll() is not None

    def stop(self):
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


class CpuTemperature:
    """CPU 温度：内核级（LHM）→ OHM WMI → ACPI 热区计数器 → WMI 传感器。

    只有前两条能拿到接近真实的核心温度，且都需要驱动支持；
    ACPI 热区是「平台温度」，通常比核心温度低不少（本机 28 ℃ vs 内核 40 ℃）。
    """

    def __init__(self, interval=3.0):
        self._interval = interval
        self._cached = None
        self._cached_at = 0.0
        self._pdh_paths = None    # None=尚未枚举，[]=枚举过但没有可用计数器
        self._pdh_retry_at = 0.0  # 枚举失败后的下次重试时间，避免每轮白花 200ms
        self._lhm = LhmReader()
        self._lhm_disabled = False
        self._monitor_wmi_checked = False
        self._wmi_checked = False

    # --- 1) LibreHardwareMonitorLib：真正的 CPU Package，需要管理员 ---
    def _from_lhm(self):
        if self._lhm_disabled:
            return None

        error = self._lhm.start()
        if error:
            self._lhm_disabled = True
            print(f"[WARN] CPU 精确温度不可用: {error} —— 回退到 ACPI 平台温度")
            return None

        state = self._lhm.read()
        if state is None:
            if self._lhm.exited():
                self._lhm_disabled = True
                print("[WARN] CPU 精确温度不可用: 采集子进程已退出 —— 回退到 ACPI 平台温度")
            return None      # 子进程还没产出第一条结果
        if not state.get("ok"):
            self._lhm_disabled = True
            self._lhm.stop()
            print(f"[WARN] CPU 精确温度不可用: {state.get('error')} —— 回退到 ACPI 平台温度")
            return None

        return {
            "name": f"CPU ({state.get('name', 'Package')})",
            "value": round(float(state["value"]), 1),
            "source": "lhm",
        }

    # --- 2) OpenHardwareMonitor / LibreHardwareMonitor 的 WMI 命名空间（若在运行） ---
    def _from_monitor_wmi(self):
        if self._monitor_wmi_checked:
            return None
        self._monitor_wmi_checked = True
        try:
            import wmi as _wmi
        except Exception:  # noqa: BLE001
            return None

        for namespace in (r"root\LibreHardwareMonitor", r"root\OpenHardwareMonitor"):
            try:
                monitor = _wmi.WMI(namespace=namespace)
                candidates = []
                for sensor in monitor.Sensor():
                    if str(getattr(sensor, "SensorType", "")) != "Temperature":
                        continue
                    name = str(getattr(sensor, "Name", ""))
                    value = float(getattr(sensor, "Value", 0) or 0)
                    if not 5 < value < 120:
                        continue
                    lowered = name.lower()
                    if "package" in lowered or "tctl" in lowered or "cpu" in lowered:
                        candidates.append((name, value))
                if candidates:
                    name, value = max(candidates, key=lambda item: item[1])
                    return {"name": f"CPU ({name})", "value": round(value, 1), "source": "ohm"}
            except Exception:  # noqa: BLE001 - 未安装时命名空间不存在
                continue
        return None

    # --- 2) ACPI 热区：走性能计数器，不需要管理员权限 ---
    def _from_acpi_counter(self):
        try:
            import win32pdh
        except Exception:  # noqa: BLE001
            return None

        if self._pdh_paths is None:
            now = time.time()
            if now < self._pdh_retry_at:
                return None
            try:
                items, instances = win32pdh.EnumObjectItems(
                    None, None, "Thermal Zone Information", win32pdh.PERF_DETAIL_WIZARD
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] ACPI thermal zone counter unavailable: {exc}")
                self._pdh_retry_at = now + 60
                return None

            if "Temperature" in items and instances:
                self._pdh_paths = [
                    win32pdh.MakeCounterPath(
                        (None, "Thermal Zone Information", instance, None, 0, "Temperature")
                    )
                    for instance in instances
                ]
            else:
                self._pdh_paths = []
                self._pdh_retry_at = now + 60
                return None

        if not self._pdh_paths:
            return None

        query = None
        try:
            query = win32pdh.OpenQuery()
            handles = [win32pdh.AddCounter(query, path) for path in self._pdh_paths]
            win32pdh.CollectQueryData(query)
            hottest = None
            for attempt in range(2):
                hottest = self._hottest(win32pdh, handles)
                if hottest is not None:
                    break
                # 个别驱动首批采样返回 0，稍等再取一次
                time.sleep(0.02)
                win32pdh.CollectQueryData(query)
            if hottest is not None:
                return {"name": "CPU (ACPI)", "value": hottest, "source": "acpi"}
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] ACPI thermal zone read failed: {exc}")
            self._pdh_paths = None      # 换个查询重新枚举，但不要每轮都来
            self._pdh_retry_at = time.time() + 60
        finally:
            if query is not None:
                try:
                    win32pdh.CloseQuery(query)
                except Exception:  # noqa: BLE001
                    pass
        return None

    @staticmethod
    def _hottest(win32pdh, handles):
        hottest = None
        for handle in handles:
            try:
                _, kelvin = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_DOUBLE)
            except Exception:  # noqa: BLE001
                continue
            if not kelvin:
                continue
            celsius = round(kelvin - 273.15, 1)
            if 5 < celsius < 120 and (hottest is None or celsius > hottest):
                hottest = celsius
        return hottest

    # --- 3) 通用 WMI 温度传感器（多数机器上该类为空） ---
    def _from_wmi_sensor(self):
        if self._wmi_checked:
            return None
        self._wmi_checked = True
        client = wmi_client()
        if client is None:
            return None
        try:
            best = None
            for sensor in client.Win32_TemperatureSensor():
                name = str(getattr(sensor, "Name", "") or getattr(sensor, "DeviceID", ""))
                value = float(getattr(sensor, "CurrentReading", 0) or 0)
                if 5 < value < 120 and (best is None or value > best[1]):
                    best = (name, value)
            if best:
                return {"name": f"CPU ({best[0]})", "value": round(best[1], 1), "source": "wmi"}
        except Exception:  # noqa: BLE001
            pass
        return None

    def read(self):
        """带节流的读取：有值时 3 秒更新一次；没读到时不缓存，下一轮继续重试。"""
        now = time.time()
        if self._cached is not None and now - self._cached_at < self._interval:
            return self._cached
        self._cached_at = now
        self._cached = (
            self._from_lhm()
            or self._from_monitor_wmi()
            or self._from_acpi_counter()
            or self._from_wmi_sensor()
        )
        return self._cached


def collect_temperature(nvml, cpu_reader):
    """返回统一结构：sensors 供页面通用展示，cpu/gpu 为已分类的单项。"""
    sensors = {}
    gpu_entry = None
    for name, value in nvml.temperatures()["sensors"].items():
        sensors[name] = value
        if gpu_entry is None:
            gpu_entry = {"name": name, "value": value}

    cpu_entry = cpu_reader.read()
    if cpu_entry:
        sensors[cpu_entry["name"]] = cpu_entry["value"]

    return {"sensors": sensors, "cpu": cpu_entry, "gpu": gpu_entry}


# ---------------- 采集：GPU 引擎利用率（nvidia-smi） ----------------
def _nvidia_smi(args, timeout=4):
    kwargs = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(["nvidia-smi", *args], **kwargs)


def _parse_dmon(stdout):
    """dmon -s u 的列依次为：gpu sm mem enc dec jpg ofa（曾错位取成 mem/enc）。"""
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            sm, enc, dec = int(float(parts[1])), int(float(parts[3])), int(float(parts[4]))
        except ValueError:
            continue
        return {"cuda": sm, "3d": sm, "encode": enc, "decode": dec}
    return {}


def _parse_query(stdout):
    """nvidia-smi -q 的兜底解析：Utilization 段里的 Gpu / Encoder / Decoder。"""
    engines = {}
    patterns = {"gpu": r"Utilization\s*\n\s*Gpu\s*:\s*(\d+)\s*%",
                "encode": r"Encoder\s*:\s*(\d+)\s*%",
                "decode": r"Decoder\s*:\s*(\d+)\s*%"}
    if re.search(r"Utilization", stdout) is None:
        return engines
    for key, pattern in patterns.items():
        match = re.search(pattern, stdout)
        if not match:
            continue
        value = int(match.group(1))
        engines["cuda" if key == "gpu" else key] = value
        if key == "gpu":
            engines["3d"] = value
    return engines


_smi_state = {"checked": False, "ok": True}


def collect_engines(gpu_name=""):
    """返回 {engines, name, mem_used_gb, mem_total_gb}；无 nvidia-smi 时返回空引擎。"""
    result = {"engines": {}, "name": gpu_name, "mem_used_gb": 0, "mem_total_gb": 0}
    if not _smi_state["ok"]:
        return result

    try:
        engines = _parse_dmon(_nvidia_smi(["dmon", "-s", "u", "-c", "1"]).stdout)
    except FileNotFoundError:
        _smi_state["ok"] = False
        print("[WARN] nvidia-smi not found, GPU engine metrics disabled")
        return result
    except Exception:  # noqa: BLE001
        engines = {}

    if not engines:
        try:
            engines = _parse_query(_nvidia_smi(["-q"]).stdout)
        except Exception:  # noqa: BLE001
            engines = {}

    result["engines"] = engines
    return result


# ---------------- 采样线程 ----------------
def sampler_loop():
    nvml = NvmlSession()
    cpu_temp = CpuTemperature()
    igpu = GpuPerfReader()
    prev_counters = psutil.net_io_counters()
    prev_ts = time.time()

    # 预热：第一次 cpu_percent(interval=None) 恒为 0，先吃掉这一次
    psutil.cpu_percent(interval=None)
    psutil.cpu_percent(interval=None, percpu=True)

    next_slow = 0.0
    next_engine = 0.0

    while not _stop.is_set():
        cycle_started = time.time()
        try:
            collect("cpu", collect_cpu)
            collect("memory", collect_memory)

            cur_counters = psutil.net_io_counters()
            cur_ts = time.time()
            put("network_speed", net_speed(prev_counters, prev_ts, cur_counters, cur_ts))
            prev_counters, prev_ts = cur_counters, cur_ts

            gpu = collect("gpu", nvml.gpu)
            gpu_name = (gpu or {}).get("name", "")

            collect("temperature", collect_temperature, nvml, cpu_temp)

            if cycle_started >= next_slow:
                next_slow = cycle_started + SLOW_INTERVAL
                collect("disk", collect_disk)
                collect("system_info", collect_system_info)
                collect("igpu", igpu.sample)

            if cycle_started >= next_engine:
                next_engine = cycle_started + ENGINE_INTERVAL
                try:
                    engines = collect_engines(gpu_name)
                except Exception as exc:  # noqa: BLE001
                    put_error("gpu_engines", exc)
                    engines = None
                if engines is not None:
                    # 响应同时带顶层 name 和嵌套 gpu，前端两种取法都能用
                    merged = {
                        "name": gpu_name or engines.get("name", ""),
                        "engines": engines["engines"],
                        "mem_used_gb": (gpu or {}).get("used_gb", 0),
                        "mem_total_gb": (gpu or {}).get("total_gb", 0),
                        "gpu": gpu or {"name": gpu_name, "usage": 0},
                    }
                    put("gpu_engines", merged)

            # 仅记录本轮采集耗时，供 /api/all 的 latency_ms 使用
            with _state_lock:
                _state["_cycle_ms"] = round((time.time() - cycle_started) * 1000, 1)

            elapsed = time.time() - cycle_started
            _stop.wait(max(0.05, FAST_INTERVAL - elapsed))
        except Exception as exc:  # noqa: BLE001 - 采样线程不能死
            print(f"[WARN] sampler cycle failed: {exc}")
            _stop.wait(FAST_INTERVAL)


# ---------------- HTTP ----------------
def _token_ok():
    if not TOKEN:
        return True
    supplied = request.headers.get("X-Monitor-Token") or request.args.get("token", "")
    return hmac.compare_digest(supplied, TOKEN)


@app.before_request
def _guard_api():
    if request.method == "OPTIONS":
        return None
    if request.path.startswith("/api/") and not _token_ok():
        return jsonify({"error": "invalid or missing token"}), 401
    return None


@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = CORS_ORIGIN
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Monitor-Token"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return resp


def _serve_page(filename):
    path = os.path.join(BASE_DIR, filename)
    if not os.path.isfile(path):
        abort(404)
    resp = send_file(path, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/")
@app.route("/monitor-mobile.html")
def mobile_page():
    return _serve_page("monitor-mobile.html")


@app.route("/monitor.html")
def desktop_page():
    """桌面版页面（此前无路由，直接 404）。"""
    return _serve_page("monitor.html")


def _snapshot():
    with _state_lock:
        data = {
            k: v for k, v in _state.items() if not k.startswith("_")
        }
        cycle_ms = _state.get("_cycle_ms", 0)
    data["latency_ms"] = cycle_ms
    return data


def _metric(key):
    with _state_lock:
        value = _state.get(key)
        error = _state.get(key + "_error")
    if value is None:
        return jsonify({"error": error or "not ready yet, retry in a second"}), 503
    return jsonify(value)


@app.route("/api/all")
def api_all():
    """统一接口 - 一次性返回全部硬件数据（读快照，不阻塞）。"""
    return jsonify(_snapshot())


@app.route("/api/cpu")
def api_cpu():
    return _metric("cpu")


@app.route("/api/memory")
def api_memory():
    return _metric("memory")


@app.route("/api/disk")
def api_disk():
    return _metric("disk")


@app.route("/api/network-speed")
def api_network_speed():
    return _metric("network_speed")


@app.route("/api/temperature")
def api_temperature():
    return _metric("temperature")


@app.route("/api/gpu")
def api_gpu():
    return _metric("gpu")


@app.route("/api/igpu")
def api_igpu():
    """核显（集成显卡）：利用率 / 共享内存。"""
    return _metric("igpu")


@app.route("/api/network")
def api_network():
    """网卡详情（ipconfig）：地址、掩码、网关、DNS、MAC、速率、MTU、DHCP。"""
    now = time.time()
    with _network_lock:
        cached = _network_cache["data"]
        if cached is not None and now - _network_cache["at"] < 5:
            return jsonify(cached)
    try:
        data = collect_network_detail()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    with _network_lock:
        _network_cache["data"] = data
        _network_cache["at"] = time.time()
    return jsonify(data)


@app.route("/api/processes")
def api_processes():
    """进程内存排行；侧栏打开时才请求，带 1.5 秒缓存避免重复枚举。"""
    now = time.time()
    with _process_lock:
        cached = _process_cache["data"]
        if cached is not None and now - _process_cache["at"] < 1.5:
            return jsonify(cached)
    try:
        data = collect_processes()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    with _process_lock:
        _process_cache["data"] = data
        _process_cache["at"] = time.time()
    return jsonify(data)


@app.route("/api/gpu-engines")
def api_gpu_engines():
    return _metric("gpu_engines")


@app.route("/api/system")
def api_system():
    return _metric("system_info")


@app.route("/api/start")
def api_start():
    """健康检查：页面只能调用到已运行的服务，因此这里只回报状态。

    原实现会用 subprocess 另起一个 server.py，且误用了不存在的
    subprocess.is_alive()，既必然 500，又让局域网内任何人都能在主机上拉起进程。
    """
    with _state_lock:
        ready = "cpu" in _state
    return jsonify({
        "status": "ok",
        "ready": ready,
        "host": HOST,
        "port": PORT,
        "elevated": is_elevated(),
    })


def monitor_already_running():
    """探测本端口上是否已有本服务的实例（Windows 允许两个进程绑同一端口）。"""
    import http.client

    try:
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=1.5)
        headers = {"X-Monitor-Token": TOKEN} if TOKEN else {}
        conn.request("GET", "/api/start", headers=headers)
        resp = conn.getresponse()
        body = resp.read(400)
        conn.close()
        return resp.status == 200 and b'"status"' in body
    except Exception:  # noqa: BLE001
        return False


def listener_pids():
    """找出占用本端口的进程 PID（提权进程可能拿不到 PID，返回空列表）。"""
    try:
        pids = set()
        for conn in psutil.net_connections(kind="inet"):
            if (conn.laddr and conn.laddr.port == PORT
                    and conn.status == psutil.CONN_LISTEN and conn.pid):
                pids.add(conn.pid)
        return sorted(pids)
    except Exception:  # noqa: BLE001
        return []


def main():
    # 默认以管理员身份运行：CPU 内核级温度（PawnIO）需要提权
    if request_elevation():
        print("[OK] 已请求管理员权限，服务将在新的管理员窗口中启动；本窗口关闭")
        return

    if monitor_already_running():
        pids = listener_pids()
        who = f"（PID {', '.join(map(str, pids))}）" if pids else ""
        print(f"[ERROR] 端口 {PORT} 上已经有一个监控实例在运行{who}。\n"
              f"        请到它的控制台窗口按 Ctrl+C，或在任务管理器里结束该 python 进程；"
              f"也可以用 MONITOR_PORT 换一个端口。")
        sys.exit(0)

    thread = threading.Thread(target=sampler_loop, name="sampler", daemon=True)
    thread.start()

    # 等首个快照就绪，避免页面首次加载拿到空数据
    for _ in range(30):
        if _state.get("cpu"):
            break
        time.sleep(0.1)

    print(f"[OK] Hardware Monitor service started at http://127.0.0.1:{PORT}")
    if is_elevated():
        print("[OK] 运行权限: 管理员（CPU 温度可读内核级 CPU Package）")
    else:
        print("[INFO] 运行权限: 普通用户 —— CPU 温度只能读 ACPI 平台温度；"
              "需要精确值请用 run-admin.bat 以管理员身份启动")
    if HOST in ("0.0.0.0", ""):
        print(f"[OK] LAN access enabled on port {PORT} (set MONITOR_HOST=127.0.0.1 to restrict)")
    if TOKEN:
        print("[OK] Token auth enabled (append ?token=... to the page URL)")
    try:
        app.run(host=HOST, port=PORT, debug=False, threaded=True)
    except OSError as exc:
        print(f"[ERROR] cannot bind {HOST}:{PORT} - {exc} (already running?)")
        sys.exit(1)


if __name__ == "__main__":
    main()
