"""Windows 平台适配：WMI、PDH 性能计数器、网卡与物理盘。

本模块集中了所有 Windows 专有逻辑（相对 T1 的 server.py 只是搬移，行为保持不变）：
  - is_elevated：当前进程权限检测（仅用于状态展示）
  - wmi_client：线程内 WMI 单例（避免每轮重连 DCOM）
  - physical_disks：Win32_DiskDrive 物理盘信息（不含分区）
  - network_details：Win32_NetworkAdapterConfiguration + psutil 合并
  - GpuPerfReader / integrated_gpu：GPU 性能计数器读核显
  - subprocess_flags：CREATE_NO_WINDOW
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time

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


# ---------------- CPU 实时频率（逐逻辑核 + 物理核归组） ----------------
# 物理核拓扑基本不变，成功读到后缓存 5 分钟，避免每秒都调一次 ctypes。
_TOPOLOGY_CACHE = {"at": 0.0, "data": None}


def _logical_core_topology():
    """物理核 → 逻辑处理器映射：`[{"logical": [0, 8], "efficiency": 1}, ...]`。

    `efficiency` 是 Windows 的 EfficiencyClass（越大越强），Intel 大小核机器上
    P 核与 E 核不同；AMD / 老 Intel 全核相同。取不到时返回 `[]`，前端退回按
    逻辑核展示，不影响其余功能。
    """
    now = time.time()
    cached = _TOPOLOGY_CACHE["data"]
    if cached is not None and now - _TOPOLOGY_CACHE["at"] < 300:
        return cached

    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001 - 非 Windows / ctypes 缺失
        _TOPOLOGY_CACHE.update(at=now, data=[])
        return []

    relation_processor_core = 0
    try:
        kernel32 = ctypes.windll.kernel32
        length = wintypes.DWORD(0)
        kernel32.GetLogicalProcessorInformationEx(
            relation_processor_core, None, ctypes.byref(length))
        if not length.value:
            _TOPOLOGY_CACHE.update(at=now, data=[])
            return []
        buffer = ctypes.create_string_buffer(length.value)
        if not kernel32.GetLogicalProcessorInformationEx(
                relation_processor_core, buffer, ctypes.byref(length)):
            _TOPOLOGY_CACHE.update(at=now, data=[])
            return []
        raw = buffer.raw[:length.value]
    except Exception:  # noqa: BLE001
        _TOPOLOGY_CACHE.update(at=now, data=[])
        return []

    ptr = ctypes.sizeof(ctypes.c_void_p)   # KAFFINITY 宽度：x64=8 / x86=4
    group_affinity = ptr + 8               # GROUP_AFFINITY: Mask + Group(2) + Reserved(6)
    # PROCESSOR_RELATIONSHIP: Flags(1) EfficiencyClass(1) Reserved(20) GroupCount(2) 后面才是掩码
    mask_offset = 32

    cores = []
    offset = 0
    while offset + 8 <= len(raw):
        relationship = int.from_bytes(raw[offset:offset + 4], "little")
        size = int.from_bytes(raw[offset + 4:offset + 8], "little")
        if size < 8 or offset + size > len(raw):
            break
        entry = raw[offset:offset + size]
        if relationship == relation_processor_core and size >= mask_offset + group_affinity:
            efficiency = entry[9]
            group_count = int.from_bytes(entry[30:32], "little")
            logical = []
            pos = mask_offset
            for _ in range(group_count):
                if pos + group_affinity > len(entry):
                    break
                mask = int.from_bytes(entry[pos:pos + ptr], "little")
                group = int.from_bytes(entry[pos + ptr:pos + ptr + 2], "little")
                pos += group_affinity
                for bit in range(ptr * 8):
                    if mask >> bit & 1:
                        logical.append(group * 64 + bit)
            if logical:
                cores.append({"logical": sorted(logical), "efficiency": efficiency})
        offset += size

    _TOPOLOGY_CACHE.update(at=now, data=cores)
    return cores


def _physical_core_entries(per_core_mhz):
    """把逐逻辑核频率归并成物理核条目；没有拓扑信息时返回 `[]`。

    同一个物理核上的超线程共享执行单元，频率取两个线程中的较大值更贴近
    任务管理器的读数（空闲线程可能报 0）。
    """
    topology = _logical_core_topology()
    if not topology:
        return []
    classes = sorted({core["efficiency"] for core in topology}, reverse=True)
    hybrid = len(classes) > 1
    counters = {}
    entries = []
    for core in topology:
        logical = [index for index in core["logical"] if 0 <= index < len(per_core_mhz)]
        values = [per_core_mhz[index] for index in logical if per_core_mhz[index]]
        if not logical:
            continue
        prefix = "C"
        if hybrid:
            prefix = "P" if core["efficiency"] == classes[0] else "E"
        counters[prefix] = counters.get(prefix, 0) + 1
        entries.append({
            "index": len(entries),
            "label": f"{prefix}{counters[prefix] - 1}",
            "efficiency": core["efficiency"],
            "logical": logical,
            "mhz": round(max(values), 1) if values else None,
        })
    return entries


class CpuFreqReader:
    """逐逻辑核实时频率（MHz），数据源是性能计数器 `Processor Information(*,i)`。

    - `Actual Frequency`：当前实际频率（含睿频），与任务管理器「速度」一致；
    - 该计数器缺失时退回 `% Processor Performance`（相对基频的百分比）× 基频；
    - 实例名形如 `0,3`：0 是处理器组、3 是组内逻辑处理器序号。
    """

    OBJECT = "Processor Information"
    COUNTER_ACTUAL = "Actual Frequency"
    COUNTER_PERFORMANCE = "% Processor Performance"
    COUNTER_BASE = "Processor Frequency"

    def __init__(self, refresh=30.0, retry_after=60.0):
        self._lock = threading.Lock()
        self._refresh = refresh
        self._retry_after = retry_after
        self._query = None
        self._handles = []
        self._mode = None
        self._base_mhz = 0.0
        self._built_at = 0.0
        self._failed_at = 0.0

    def _close(self):
        query, self._query = self._query, None
        self._handles = []
        self._mode = None
        if query is not None:
            try:
                import win32pdh
                win32pdh.CloseQuery(query)
            except Exception:  # noqa: BLE001
                pass

    @classmethod
    def _read_base_mhz(cls, win32pdh, instance):
        """基频：`Processor Frequency` 计数器；读不到时退回 psutil 的最大频率。"""
        try:
            query = win32pdh.OpenQuery()
            try:
                path = win32pdh.MakeCounterPath(
                    (None, cls.OBJECT, instance, None, 0, cls.COUNTER_BASE))
                handle = win32pdh.AddCounter(query, path)
                win32pdh.CollectQueryData(query)
                _, value = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_DOUBLE)
                if value and value > 0:
                    return round(float(value), 1)
            finally:
                try:
                    win32pdh.CloseQuery(query)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        try:
            import psutil
            freq = psutil.cpu_freq()
            if freq and freq.max:
                return round(float(freq.max), 1)
        except Exception:  # noqa: BLE001
            pass
        return 0.0

    def _build(self):
        try:
            import win32pdh
        except Exception:  # noqa: BLE001 - 没装 pywin32 时静默降级
            return False
        try:
            items, instances = win32pdh.EnumObjectItems(
                None, None, self.OBJECT, win32pdh.PERF_DETAIL_WIZARD)
        except Exception:  # noqa: BLE001
            return False

        if self.COUNTER_ACTUAL in items:
            mode = "actual"
        elif self.COUNTER_PERFORMANCE in items:
            mode = "performance"
        else:
            return False
        counter = self.COUNTER_ACTUAL if mode == "actual" else self.COUNTER_PERFORMANCE

        cores = []
        for name in instances:
            group_text, _, index_text = name.partition(",")
            if not group_text.isdigit() or not index_text.isdigit():
                continue
            cores.append((int(group_text), int(index_text), name))
        if not cores:
            return False
        cores.sort()

        query = win32pdh.OpenQuery()
        handles = []
        try:
            for group, index, name in cores:
                path = win32pdh.MakeCounterPath((None, self.OBJECT, name, None, 0, counter))
                handles.append((group * 64 + index, win32pdh.AddCounter(query, path)))
        except Exception:  # noqa: BLE001
            try:
                win32pdh.CloseQuery(query)
            except Exception:  # noqa: BLE001
                pass
            return False

        self._query = query
        self._handles = handles
        self._mode = mode
        # 基频只在建表时读一次：performance 模式要用它换算频率，actual 模式只用于展示
        self._base_mhz = CpuFreqReader._read_base_mhz(win32pdh, cores[0][2])
        self._built_at = time.time()
        return True

    def _read_values(self):
        import win32pdh
        win32pdh.CollectQueryData(self._query)

        def read_all():
            values = []
            for _, handle in self._handles:
                _, value = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_DOUBLE)
                values.append(value)
            return values

        try:
            values = read_all()
        except Exception:  # noqa: BLE001 - 部分计数器首轮无数据，补采一次
            time.sleep(0.05)
            win32pdh.CollectQueryData(self._query)
            values = read_all()

        if self._mode == "performance":
            if not self._base_mhz:
                return []
            return [value * self._base_mhz / 100.0 for value in values]
        return values

    def sample(self):
        """返回频率快照；本机读不到时返回 None。"""
        with self._lock:
            now = time.time()
            if self._query is None:
                if now - self._failed_at < self._retry_after:
                    return None
                if not self._build():
                    self._failed_at = now
                    return None
            elif now - self._built_at > self._refresh:
                self._close()
                if not self._build():
                    self._failed_at = now
                    return None
            try:
                values = self._read_values()
            except Exception:  # noqa: BLE001
                self._close()
                self._failed_at = now
                return None
            if len(values) != len(self._handles):
                self._close()
                return None
            ids = [logical for logical, _ in self._handles]

        per_core = [None] * (max(ids) + 1 if ids else 0)
        for logical, value in zip(ids, values):
            per_core[logical] = round(float(value), 1) if value and value > 0 else None

        present = [value for value in per_core if value]
        if not present:
            return None
        return {
            "source": "pdh",
            "mode": self._mode,
            "base_mhz": self._base_mhz or None,
            "avg_mhz": round(sum(present) / len(present), 1),
            "min_mhz": round(min(present), 1),
            "max_mhz": round(max(present), 1),
            "per_core_mhz": per_core,
            "physical": _physical_core_entries(per_core),
        }


_cpu_freq_reader = None
_cpu_freq_lock = threading.Lock()


def cpu_freq():
    """逐核实时频率快照；读不到（非 Windows / 未装 pywin32 / 计数器缺失）返回 None。"""
    global _cpu_freq_reader
    with _cpu_freq_lock:
        if _cpu_freq_reader is None:
            _cpu_freq_reader = CpuFreqReader()
        reader = _cpu_freq_reader
    return reader.sample()


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


# ---------------- 能力表 ----------------
def capabilities():
    """平台能力表（表示平台支持什么，不代表设备一定存在）。"""
    return {
        "os": "windows",
        "elevated": is_elevated(),
        "gpu_nvidia": True,
        "gpu_integrated": True,
        "physical_disk": True,
        "network_detail": True,
        "cpu_freq_per_core": True,
        "powershell": True,
    }
