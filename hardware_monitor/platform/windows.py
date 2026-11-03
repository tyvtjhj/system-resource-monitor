"""Windows 平台适配：UAC、WMI、PDH 性能计数器、LHM 子进程、网卡与物理盘。

本模块集中了所有 Windows 专有逻辑（相对 T1 的 server.py 只是搬移，行为保持不变）：
  - is_elevated / request_elevation：管理员检测与 UAC 重新拉起
  - wmi_client：线程内 WMI 单例（避免每轮重连 DCOM）
  - physical_disks：Win32_DiskDrive 物理盘信息（不含分区）
  - network_details：Win32_NetworkAdapterConfiguration + psutil 合并
  - GpuPerfReader / integrated_gpu：GPU 性能计数器读核显
  - LhmReader / temperature_sources：CPU 温度四级来源
  - subprocess_flags：CREATE_NO_WINDOW
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

from .. import config
from ..collectors.common import _normalize_mac, psutil_network_adapters


# ---------------- 权限与提权 ----------------
def is_elevated():
    """当前进程是否以管理员权限运行。"""
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def request_elevation(argv):
    """默认以管理员身份启动：非管理员时用 UAC 重新拉起自己。

    返回 True 表示已经把工作交给提权后的新进程；False 表示已在管理员权限下，
    或提权被拒绝/不可用，此时继续以普通权限运行（CPU 温度会降级为 ACPI）。

    防止递归：提权后的子进程会带 --elevated 参数，见到它就不再尝试提权。
    需要关掉自动提权时：命令行加 --no-elevate，或设 MONITOR_NO_ELEVATE=1。
    """
    if os.name != "nt" or is_elevated():
        return False
    if "--elevated" in argv:
        return False
    if "--no-elevate" in argv:
        return False
    if os.environ.get("MONITOR_NO_ELEVATE", "").strip().lower() in ("1", "true", "yes", "on"):
        return False

    try:
        import ctypes

        script = os.path.join(config.BASE_DIR, "server.py")
        extra = [arg for arg in argv if arg not in ("--no-elevate", "--elevated")]
        params = subprocess.list2cmdline([script, *extra, "--elevated"])
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, config.BASE_DIR, 1
        )
        if rc > 32:
            return True
        # 5 = 用户在 UAC 上点了"否"
        print(f"[WARN] 未获得管理员权限（ShellExecuteW={rc}），将以普通权限运行，"
              f"CPU 温度为 ACPI 平台温度")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 提权失败: {exc}，将以普通权限运行")
    return False


def subprocess_flags():
    """Windows 下隐藏子进程控制台窗口；其他平台返回空字典。"""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


# ---------------- WMI（线程内单例） ----------------
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


# ---------------- 物理硬盘 ----------------
_disk_wmi_warned = False


_disk_cache = []
_disk_cache_at = 0.0
_DISK_CACHE_TTL = 60.0


def _physical_disks_via_cim():
    """WMI COM 不可用时的兜底：用隐藏窗口的 PowerShell CIM 查询。

    某些环境下 python `wmi` 依赖的 gen_py 临时文件不可写，或 WMI 服务不可用，
    但 `Get-CimInstance Win32_DiskDrive` 仍可工作；这条路径不会弹出控制台窗口。
    """
    command = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "Get-CimInstance Win32_DiskDrive | "
        "Select-Object Model,InterfaceType,Size,SerialNumber | ConvertTo-Json -Compress",
    ]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            **subprocess_flags(),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        payload = json.loads(proc.stdout)
        if isinstance(payload, dict):
            payload = [payload]
        disks = []
        for index, row in enumerate(payload):
            if not isinstance(row, dict):
                continue
            model = str(row.get("Model") or "").strip()
            size_gb = round(float(row.get("Size") or 0) / (1024 ** 3), 1)
            disks.append({
                "model": model or f"Disk {index}",
                "interface_type": str(row.get("InterfaceType") or ""),
                "size_gb": size_gb if size_gb > 0 else None,
                "serial_number": str(row.get("SerialNumber") or "").strip(),
                "type": "physical",
            })
        return disks
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] CIM physical disk fallback failed: {exc}")
        return []


def physical_disks():
    """WMI Win32_DiskDrive：型号 / 接口 / 容量 / 序列号（不含分区）。

    结果缓存 60 秒；WMI 查询失败或返回空时改用 PowerShell CIM 兜底，
    避免「其他机器上物理盘识别不到」。
    """
    global _disk_wmi_warned, _disk_cache, _disk_cache_at
    now = time.time()
    if now - _disk_cache_at < _DISK_CACHE_TTL:
        return [dict(item) for item in _disk_cache]

    disks = []
    client = wmi_client()
    if client is not None:
        try:
            for index, disk in enumerate(client.Win32_DiskDrive()):
                model = str(getattr(disk, "Model", "") or "").strip()
                size_gb = round(float(getattr(disk, "Size", 0) or 0) / (1024 ** 3), 1)
                disks.append({
                    "model": model or f"Disk {index}",
                    "interface_type": str(getattr(disk, "InterfaceType", "") or ""),
                    "size_gb": size_gb if size_gb > 0 else None,
                    "serial_number": str(getattr(disk, "SerialNumber", "") or "").strip(),
                    "type": "physical",
                })
        except Exception as exc:  # noqa: BLE001
            if not _disk_wmi_warned:
                _disk_wmi_warned = True
                print(f"[WARN] WMI physical disk query failed (只会提示一次): {exc}")

    if not disks:
        disks = _physical_disks_via_cim()

    _disk_cache = disks
    _disk_cache_at = now
    return [dict(item) for item in disks]


def partition_parent(device):
    """Windows 分区盘符无法可靠映射到物理盘，统一返回 None（保持接口一致）。"""
    del device
    return None


# ---------------- 核显（GPU 性能计数器） ----------------
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
        self._shared_gb = 0.0
        self._dedicated_gb = 0.0

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
                    if (any(k in lowered for k in ("intel", "amd", "radeon"))
                            and "nvidia" not in lowered):
                        candidates.append(desc)
            # 注册表里可能残留已换掉的老设备，只有一个候选时才敢用
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


_gpu_perf_reader = None
_gpu_perf_lock = threading.Lock()


def integrated_gpu():
    """核显数据（进程内单例）；不可用时返回 None。"""
    global _gpu_perf_reader
    with _gpu_perf_lock:
        if _gpu_perf_reader is None:
            _gpu_perf_reader = GpuPerfReader()
        reader = _gpu_perf_reader
    try:
        return reader.sample()
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] integrated GPU sample failed: {exc}")
        return None


# ---------------- 网卡详情（WMI + psutil） ----------------
def network_details():
    """网卡列表：地址/掩码/网关/DNS/MAC/速率/MTU/DHCP。

    WMI `Win32_NetworkAdapterConfiguration` 提供网关与 DNS（psutil 没有），
    psutil 提供友好的连接名、链路速率和 MTU；按 MAC 关联两者，
    WMI 不可用或没有结果时退回纯 psutil。
    """
    import psutil

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
        adapters = psutil_network_adapters()

    adapters.sort(key=lambda item: (not item["up"], item["name"].lower()))
    return {"adapters": adapters, "count": len(adapters)}


# ---------------- CPU 温度：四级来源 ----------------
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
        self._script = os.path.join(config.BASE_DIR, "lhm_temp.ps1")

    @staticmethod
    def find_dll():
        """按常见安装位置找依赖 DLL；也可用 MONITOR_LHM_DLL 指定。"""
        candidates = [
            os.environ.get("MONITOR_LHM_DLL"),
            os.path.join(config.BASE_DIR, "vendor", "lhm", "LibreHardwareMonitorLib.dll"),
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
            **subprocess_flags(),
        }
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


# --- 来源 1) LibreHardwareMonitorLib：真正的 CPU Package，需要管理员 ---
_lhm = None
_lhm_disabled = False
_lhm_lock = threading.Lock()


def _lhm_source():
    global _lhm, _lhm_disabled
    if _lhm_disabled:
        return None

    with _lhm_lock:
        if _lhm is None:
            _lhm = LhmReader()
        reader = _lhm

    error = reader.start()
    if error:
        _lhm_disabled = True
        print(f"[WARN] CPU 精确温度不可用: {error} —— 回退到 ACPI 平台温度")
        return None

    state = reader.read()
    if state is None:
        if reader.exited():
            _lhm_disabled = True
            print("[WARN] CPU 精确温度不可用: 采集子进程已退出 —— 回退到 ACPI 平台温度")
        return None      # 子进程还没产出第一条结果
    if not state.get("ok"):
        _lhm_disabled = True
        reader.stop()
        print(f"[WARN] CPU 精确温度不可用: {state.get('error')} —— 回退到 ACPI 平台温度")
        return None

    return {
        "name": f"CPU ({state.get('name', 'Package')})",
        "value": round(float(state["value"]), 1),
        "source": "lhm",
    }


# --- 来源 2) OpenHardwareMonitor / LibreHardwareMonitor 的 WMI 命名空间 ---
_monitor_wmi_checked = False


def _ohm_source():
    global _monitor_wmi_checked
    if _monitor_wmi_checked:
        return None
    _monitor_wmi_checked = True
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


# --- 来源 3) ACPI 热区：走性能计数器，不需要管理员权限 ---
_pdh_paths = None       # None=尚未枚举，[]=枚举过但没有可用计数器
_pdh_retry_at = 0.0     # 枚举失败后的下次重试时间，避免每轮白花 200ms


def _acpi_source():
    global _pdh_paths, _pdh_retry_at
    try:
        import win32pdh
    except Exception:  # noqa: BLE001
        return None

    if _pdh_paths is None:
        now = time.time()
        if now < _pdh_retry_at:
            return None
        try:
            items, instances = win32pdh.EnumObjectItems(
                None, None, "Thermal Zone Information", win32pdh.PERF_DETAIL_WIZARD
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] ACPI thermal zone counter unavailable: {exc}")
            _pdh_retry_at = now + 60
            return None

        if "Temperature" in items and instances:
            _pdh_paths = [
                win32pdh.MakeCounterPath(
                    (None, "Thermal Zone Information", instance, None, 0, "Temperature")
                )
                for instance in instances
            ]
        else:
            _pdh_paths = []
            _pdh_retry_at = now + 60
            return None

    if not _pdh_paths:
        return None

    query = None
    try:
        query = win32pdh.OpenQuery()
        handles = [win32pdh.AddCounter(query, path) for path in _pdh_paths]
        win32pdh.CollectQueryData(query)
        hottest = None
        for _attempt in range(2):
            hottest = _hottest(win32pdh, handles)
            if hottest is not None:
                break
            # 个别驱动首批采样返回 0，稍等再取一次
            time.sleep(0.02)
            win32pdh.CollectQueryData(query)
        if hottest is not None:
            return {"name": "CPU (ACPI)", "value": hottest, "source": "acpi"}
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] ACPI thermal zone read failed: {exc}")
        _pdh_paths = None      # 换个查询重新枚举，但不要每轮都来
        _pdh_retry_at = time.time() + 60
    finally:
        if query is not None:
            try:
                win32pdh.CloseQuery(query)
            except Exception:  # noqa: BLE001
                pass
    return None


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


# --- 来源 4) 通用 WMI 温度传感器（多数机器上该类为空） ---
_wmi_sensor_checked = False


def _wmi_sensor_source():
    global _wmi_sensor_checked
    if _wmi_sensor_checked:
        return None
    _wmi_sensor_checked = True
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


def temperature_sources():
    """Windows 的 CPU 温度来源顺序：LHM → OHM/LHM WMI → ACPI → Win32 传感器。"""
    return [_lhm_source, _ohm_source, _acpi_source, _wmi_sensor_source]


# ---------------- 能力表 ----------------
def capabilities():
    """平台能力表（表示平台支持什么，不代表设备一定存在）。"""
    return {
        "os": "windows",
        "elevated": is_elevated(),
        "cpu_temp_sources": ["lhm", "ohm", "acpi", "wmi"],
        "gpu_nvidia": True,
        "gpu_integrated": True,
        "physical_disk": True,
        "network_detail": True,
        "lhm": bool(LhmReader.find_dll()),
        "powershell": True,
    }
