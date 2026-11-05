"""Linux 平台适配：提权判断、物理磁盘、网卡详情、CPU 温度来源与 AMD 核显。

设计约束：
- 只依赖标准库与 psutil；**在 Windows 上也能安全 import**（不使用 pwd / grp 等
  POSIX-only 模块，提权判断用 hasattr(os, "geteuid") 探测）。
- 所有 sysfs / procfs 读取都 try/except 吞掉异常，读不到时降级为 None / 空列表，
  绝不把异常抛给采样线程。

对外接口（与 platform/windows.py 同名同结构，由 platform/__init__.py 统一导出）：
    is_elevated, request_elevation, subprocess_flags, physical_disks,
    network_details, temperature_sources, integrated_gpu, capabilities
"""

from __future__ import annotations

import glob
import os
import re
import socket
from typing import Any, Callable

import psutil

__all__ = [
    "is_elevated",
    "request_elevation",
    "subprocess_flags",
    "physical_disks",
    "parent_device",
    "partition_parent",
    "network_details",
    "temperature_sources",
    "integrated_gpu",
    "cpu_freq",
    "capabilities",
]

# sysfs / procfs 路径常量（便于按发行版调整，也便于测试替换）
SYS_BLOCK = "/sys/block"
SYS_THERMAL = "/sys/class/thermal"
SYS_DRM = "/sys/class/drm"
PROC_ROUTE = "/proc/net/route"
RESOLV_CONF = "/etc/resolv.conf"

# /sys/block 下这些前缀是虚拟 / 可移除设备，不算物理硬盘
_VIRTUAL_DISK_PREFIXES = ("loop", "ram", "dm-", "sr", "zram")

# 分区设备名 -> 父设备 的匹配规则（任务书 T2.4）：
#   nvme0n1p1 -> nvme0n1 / mmcblk0p1 -> mmcblk0 / sda1 -> sda
_PARTITION_PATTERNS = (
    re.compile(r"^(nvme\d+n\d+)p\d+$"),
    re.compile(r"^(mmcblk\d+)p\d+$"),
    re.compile(r"^([a-z]+)\d+$"),
)

# CPU 温度合理区间（摄氏度）：过滤 0、负值与明显越界的脏读数
_MIN_TEMP = 5.0
_MAX_TEMP = 120.0

# hwmon 中常见的 CPU 芯片名（标签不规范时按芯片名兜底）
_CPU_HWMON_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "soc_thermal")
# thermal zone 中 CPU 相关 type 关键字（优先命中）
_CPU_THERMAL_KEYWORDS = ("x86_pkg_temp", "cpu", "soc")


# ---------------- 提权 ----------------
def is_elevated() -> bool:
    """当前进程是否 root（euid == 0）；无 os.geteuid（如 Windows）时返回 False。"""
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return False
    try:
        return geteuid() == 0
    except OSError:
        return False


def request_elevation(argv: list[str]) -> bool:
    """Linux 不自动提权：不调用 sudo、不弹窗，始终返回 False。

    argv 保留仅为与 Windows 版签名兼容；调用方应提示用户用 sudo 启动，
    或把用户加入所需用户组（video / adm 等）后重启服务。
    """
    del argv
    return False


def subprocess_flags() -> dict:
    """Linux 没有 CREATE_NO_WINDOW 之类的创建标志，返回空字典。"""
    return {}


# ---------------- 文件读取辅助（全部静默降级） ----------------
def _read_text(path: str) -> str:
    """读文本文件并去掉首尾空白；任何失败都返回空字符串。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except (OSError, ValueError):
        return ""


def _read_int(path: str) -> int | None:
    """读整数文件（取首个空白分隔项）；失败返回 None。"""
    text = _read_text(path)
    if not text:
        return None
    try:
        return int(text.split()[0])
    except (IndexError, ValueError):
        return None


def _plausible_temp(value: Any) -> bool:
    """温度是否为合理读数；NaN / inf / 非数字都返回 False。"""
    try:
        temp = float(value)
    except (TypeError, ValueError):
        return False
    return _MIN_TEMP < temp < _MAX_TEMP


def _temp_payload(name: str, value: Any, source: str) -> dict:
    """统一温度结构：{name, value(1 位小数), source}。"""
    return {"name": name, "value": round(float(value), 1), "source": source}


# ---------------- 物理硬盘 ----------------
def _disk_interface(name: str) -> str:
    """尽力从 sysfs 设备路径推断接口类型，推断不出返回 "unknown"。"""
    try:
        real = os.path.realpath(os.path.join(SYS_BLOCK, name, "device")).lower()
    except OSError:
        real = ""
    if "nvme" in real:
        return "NVMe"
    if "mmc" in real:
        return "MMC"
    if "/ata" in real or "sata" in real:
        return "SATA"
    if "usb" in real:
        return "USB"
    return "unknown"


def physical_disks() -> list[dict]:
    """物理硬盘列表：遍历 /sys/block，排除虚拟 / 可移除设备。

    sysfs 不存在（非 Linux）时返回 []；单个磁盘读取失败只跳过该盘。
    额外附带 `rotational` 字段（机械盘 True / 固态 False，读不到为 None），
    其余字段与 Windows 版 physical_disks() 保持一致。
    """
    try:
        if not os.path.isdir(SYS_BLOCK):
            return []
        names = sorted(os.listdir(SYS_BLOCK))
    except OSError:
        return []

    disks: list[dict] = []
    for name in names:
        if name.startswith(_VIRTUAL_DISK_PREFIXES):
            continue
        base = os.path.join(SYS_BLOCK, name)
        try:
            model = _read_text(os.path.join(base, "device", "model")) or name
            serial = _read_text(os.path.join(base, "device", "serial"))
            sectors = _read_int(os.path.join(base, "size"))
            rotational = _read_int(os.path.join(base, "queue", "rotational"))
            size_gb = round(sectors * 512 / 1024 ** 3, 1) if sectors and sectors > 0 else None
            disks.append({
                "device": f"/dev/{name}",
                "model": model,
                "interface_type": _disk_interface(name),
                "size_gb": size_gb,
                "serial_number": serial,
                "type": "physical",
                "rotational": bool(rotational) if rotational is not None else None,
            })
        except OSError:
            continue
    return disks


def parent_device(device: str) -> str | None:
    """把分区设备名映射到父设备名（任务书 T2.4）。

    支持 "/dev/nvme0n1p1"、"nvme0n1p1"、"sda1" 等写法；传入整盘设备
    （如 "/dev/sda"）或无法识别时返回 None，便于调用方判断。
    """
    if not device:
        return None
    name = os.path.basename(str(device).rstrip("/"))
    if not name:
        return None
    for pattern in _PARTITION_PATTERNS:
        match = pattern.match(name)
        if match:
            return f"/dev/{match.group(1)}"
    return None


def partition_parent(device: str) -> str | None:
    """平台统一接口：分区设备名 -> 父设备名（供 collect_disk 关联分区与整盘）。"""
    return parent_device(device)


# ---------------- 网卡详情 ----------------
def _read_default_gateways() -> dict[str, list[str]]:
    """解析 /proc/net/route 的默认路由，返回 {网卡名: [网关, ...]}。

    Destination 为 00000000 的行即默认路由；Gateway 是 4 字节地址的十六进制
    小端表示，如 0102A8C0 -> 192.168.2.1。
    """
    gateways: dict[str, list[str]] = {}
    try:
        with open(PROC_ROUTE, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except (OSError, ValueError):
        return gateways

    for line in lines[1:]:  # 第一行是 "Iface Destination Gateway ..." 表头
        fields = line.split()
        if len(fields) < 3 or fields[1] != "00000000":
            continue
        iface, gateway_hex = fields[0], fields[2]
        try:
            raw = int(gateway_hex, 16)
        except ValueError:
            continue
        gateway = ".".join(str((raw >> shift) & 0xFF) for shift in (0, 8, 16, 24))
        bucket = gateways.setdefault(iface, [])
        if gateway not in bucket:
            bucket.append(gateway)
    return gateways


def _read_dns_servers() -> list[str]:
    """解析 /etc/resolv.conf 的 nameserver 行，去重并保持顺序。"""
    servers: list[str] = []
    try:
        with open(RESOLV_CONF, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except (OSError, ValueError):
        return servers

    for line in lines:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0].lower() == "nameserver":
            server = parts[1]
            if server not in servers:
                servers.append(server)
    return servers


def network_details() -> dict:
    """网卡详情：psutil 提供地址 / 速率 / MTU，procfs 与 resolv.conf 补齐网关 / DNS。

    返回 {"adapters": [...], "count": n}，adapter 字段与 Windows 版一致；
    只保留至少有一个非回环 IPv4 / IPv6 地址的网卡，up 的在前、其余按名称排序。
    """
    try:
        addrs_by_name = psutil.net_if_addrs()
        stats_by_name = psutil.net_if_stats()
    except Exception:  # noqa: BLE001 - psutil 故障时整体降级为空
        addrs_by_name, stats_by_name = {}, {}

    gateways = _read_default_gateways()
    dns_servers = _read_dns_servers()
    adapters: list[dict] = []

    for name, addrs in (addrs_by_name or {}).items():
        try:
            mac = ""
            ipv4: list[str] = []
            netmask: list[str] = []
            ipv6: list[str] = []
            for addr in addrs or []:
                family = getattr(addr, "family", None)
                address = str(getattr(addr, "address", "") or "")
                if family == psutil.AF_LINK:
                    mac = address
                elif family == socket.AF_INET:
                    if address and address != "127.0.0.1":
                        ipv4.append(address)
                        mask = str(getattr(addr, "netmask", "") or "")
                        if mask:
                            netmask.append(mask)
                elif family == socket.AF_INET6:
                    if address and address != "::1":
                        ipv6.append(address)
            if not ipv4 and not ipv6:
                continue

            stats = (stats_by_name or {}).get(name)
            speed_mbps = int(getattr(stats, "speed", 0) or 0) if stats is not None else 0
            mtu = int(getattr(stats, "mtu", 0) or 0) if stats is not None else 0
            up = bool(getattr(stats, "isup", False)) if stats is not None else False
            adapters.append({
                "name": name,
                "description": name,
                "mac": mac.upper(),
                "ipv4": ipv4,
                "netmask": netmask,
                "ipv6": ipv6,
                "gateway": list(gateways.get(name, [])),
                "dns": list(dns_servers),
                "dhcp": None,  # Linux 无法可靠判断是否 DHCP，固定 None
                "speed_mbps": speed_mbps,
                "mtu": mtu,
                "up": up,
            })
        except Exception:  # noqa: BLE001 - 单块网卡异常不影响其他网卡
            continue

    adapters.sort(key=lambda item: (not item["up"], item["name"].lower()))
    return {"adapters": adapters, "count": len(adapters)}


# ---------------- CPU 温度 ----------------
def _hwmon_cpu_temp() -> dict | None:
    """来源 1：psutil.sensors_temperatures()（hwmon），标签优先。"""
    try:
        if not hasattr(psutil, "sensors_temperatures"):
            return None
        sensors = psutil.sensors_temperatures()
    except Exception:  # noqa: BLE001 - 部分环境不支持 / 无 hwmon
        return None
    if not sensors:
        return None

    readings: list[tuple[str, str, Any]] = []
    try:
        for chip, entries in sensors.items():
            for entry in entries or []:
                label = str(getattr(entry, "label", "") or "").strip()
                readings.append((str(chip), label, getattr(entry, "current", None)))
    except Exception:  # noqa: BLE001 - psutil 返回结构异常时降级
        return None

    # 优先标签：Intel coretemp 的 Package / Core 0，AMD k10temp 的 Tctl / Tdie
    for wanted in ("package id 0", "tctl", "tdie", "core 0"):
        for _chip, label, value in readings:
            if label.lower() == wanted and _plausible_temp(value):
                return _temp_payload(f"CPU ({label})", value, "hwmon")

    # 标签不规范时按 CPU 相关芯片名兜底（如 zenpower、cpu_thermal）
    for chip, label, value in readings:
        if any(key in chip.lower() for key in _CPU_HWMON_CHIPS) and _plausible_temp(value):
            return _temp_payload(f"CPU ({label or chip})", value, "hwmon")
    return None


def _thermal_cpu_temp() -> dict | None:
    """来源 2：/sys/class/thermal/thermal_zone*/temp（毫摄氏度）。"""
    try:
        temp_paths = sorted(glob.glob(os.path.join(SYS_THERMAL, "thermal_zone*", "temp")))
    except Exception:  # noqa: BLE001
        return None

    for temp_path in temp_paths:
        milli = _read_int(temp_path)
        if milli is None:
            continue
        value = milli / 1000.0
        if not (_MIN_TEMP < value < _MAX_TEMP):
            continue
        zone_dir = os.path.dirname(temp_path)
        zone_type = _read_text(os.path.join(zone_dir, "type")) or os.path.basename(zone_dir)
        # 只接受 CPU 相关的热区；acpitz/nvme 等平台热区不能冒充 CPU 温度，
        # 没有匹配时返回 None（任务书 T2.4：都没有返回 None）。
        if any(key in zone_type.lower() for key in _CPU_THERMAL_KEYWORDS):
            return _temp_payload(f"CPU ({zone_type})", value, "thermal")
    return None


def temperature_sources() -> list[Callable[[], dict | None]]:
    """按优先级返回 CPU 温度来源：hwmon -> thermal，每个都是无参可调用对象。"""
    return [_hwmon_cpu_temp, _thermal_cpu_temp]


# ---------------- AMD 核显 ----------------
def _amd_gpu_name(device_dir: str) -> str:
    """尽力从 device/uevent 推断名称，取不到时用「集成显卡」。"""
    uevent = _read_text(os.path.join(device_dir, "uevent"))
    driver = ""
    pci_id = ""
    for line in uevent.splitlines():
        key, _, value = line.partition("=")
        if key == "DRIVER":
            driver = value.strip()
        elif key == "PCI_ID":
            pci_id = value.strip()
    if "amdgpu" not in driver.lower() and "amdgpu" not in uevent.lower():
        return "集成显卡"
    return f"AMD Radeon ({pci_id})" if pci_id else "AMD Radeon"


def _gpu_temp(device_dir: str) -> float | None:
    """读 amdgpu 的 hwmon temp1_input（毫摄氏度），读不到返回 None。"""
    try:
        paths = sorted(glob.glob(os.path.join(device_dir, "hwmon", "hwmon*", "temp1_input")))
    except Exception:  # noqa: BLE001
        return None
    for path in paths:
        milli = _read_int(path)
        if milli is None:
            continue
        value = milli / 1000.0
        if _MIN_TEMP < value < _MAX_TEMP:
            return round(value, 1)
    return None


def integrated_gpu() -> dict | None:
    """AMD 核显尽力而为：amdgpu 的利用率 / VRAM 使用量 / 温度。

    读 /sys/class/drm/card*/device/gpu_busy_percent 等文件；Intel（i915 不暴露该
    文件）或无数据时返回 None。返回结构与 Windows iGPU 兼容，另外附带可选的
    `temperature_c` 与 `vram_total_gb`（读不到就不给）。
    """
    try:
        busy_paths = sorted(
            glob.glob(os.path.join(SYS_DRM, "card*", "device", "gpu_busy_percent"))
        )
    except Exception:  # noqa: BLE001
        return None

    for busy_path in busy_paths:
        # 只认 card0 / card1 这类真实显卡节点，跳过 card0-DP-1 之类的连接器别名
        card = os.path.basename(os.path.dirname(os.path.dirname(busy_path)))
        if not card.startswith("card") or not card[4:].isdigit():
            continue
        busy = _read_int(busy_path)
        if busy is None:
            continue
        device_dir = os.path.dirname(busy_path)
        usage = round(min(max(float(busy), 0.0), 100.0), 1)

        used_bytes = _read_int(os.path.join(device_dir, "mem_info_vram_used"))
        total_bytes = _read_int(os.path.join(device_dir, "mem_info_vram_total"))
        info = {
            "name": _amd_gpu_name(device_dir),
            "usage": usage,
            # 与 Windows GpuPerfReader 语义一致：两个 *_gb 都是「已用」内存
            "shared_gb": 0.0,
            "dedicated_gb": round(used_bytes / 1024 ** 3, 2) if used_bytes else 0.0,
            "engines": {"3D": usage},
            "luid": None,
            "type": "integrated",
        }
        if total_bytes:
            info["vram_total_gb"] = round(total_bytes / 1024 ** 3, 2)
        temp_c = _gpu_temp(device_dir)
        if temp_c is not None:
            info["temperature_c"] = temp_c
        return info
    return None


# ---------------- CPU 实时频率 ----------------
SYS_CPU = "/sys/devices/system/cpu"


def _parse_cpu_list(text: str) -> list[int]:
    """解析内核的 CPU 列表写法：`0-3,8,10-11`。"""
    result: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        start_text, _, end_text = part.partition("-")
        if start_text.isdigit() and end_text.isdigit():
            result.extend(range(int(start_text), int(end_text) + 1))
        elif part.isdigit():
            result.append(int(part))
    return result


def _cpu_indexes() -> list[int]:
    """sysfs 里存在的逻辑 CPU 序号。"""
    try:
        names = os.listdir(SYS_CPU)
    except OSError:
        return []
    return sorted(
        int(name[3:]) for name in names
        if name.startswith("cpu") and name[3:].isdigit()
    )


def _cpu_freq_khz(index: int) -> int | None:
    """当前频率（kHz）：scaling_cur_freq 优先，cpuinfo_cur_freq 兜底。"""
    for field in ("scaling_cur_freq", "cpuinfo_cur_freq"):
        value = _read_int(f"{SYS_CPU}/cpu{index}/cpufreq/{field}")
        if value and value > 0:
            return value
    return None


def _cpu_base_mhz() -> float | None:
    """标称基频（MHz）：cpufreq 的 base_frequency / cpuinfo_max_freq。"""
    for field in ("base_frequency", "cpuinfo_max_freq"):
        value = _read_int(f"{SYS_CPU}/cpu0/cpufreq/{field}")
        if value and value > 0:
            return round(value / 1000.0, 1)
    return None


def _physical_cores(per_core: list) -> list[dict]:
    """按 thread_siblings_list 把逻辑核归并成物理核（同一核的超线程同频）。"""
    groups: dict[tuple, list[int]] = {}
    for index in range(len(per_core)):
        siblings = _read_text(f"{SYS_CPU}/cpu{index}/topology/thread_siblings_list")
        parsed = _parse_cpu_list(siblings) if siblings else [index]
        groups.setdefault(tuple(sorted(parsed or [index])), []).append(index)

    entries = []
    for logical in groups.values():
        values = [per_core[index] for index in logical if index < len(per_core) and per_core[index]]
        entries.append({
            "index": len(entries),
            "label": f"C{len(entries)}",
            "efficiency": None,
            "logical": sorted(logical),
            "mhz": round(max(values), 1) if values else None,
        })
    return entries


def cpu_freq() -> dict | None:
    """逐核实时频率快照；没有 cpufreq（虚拟机 / 老内核）时返回 None。"""
    indexes = _cpu_indexes()
    if not indexes:
        return None

    per_core: list = [None] * (indexes[-1] + 1)
    for index in indexes:
        khz = _cpu_freq_khz(index)
        if khz:
            per_core[index] = round(khz / 1000.0, 1)

    present = [value for value in per_core if value]
    if not present:
        return None
    return {
        "source": "sysfs",
        "mode": "scaling",
        "base_mhz": _cpu_base_mhz(),
        "avg_mhz": round(sum(present) / len(present), 1),
        "min_mhz": round(min(present), 1),
        "max_mhz": round(max(present), 1),
        "per_core_mhz": per_core,
        "physical": _physical_cores(per_core),
    }


# ---------------- 能力表 ----------------
def capabilities() -> dict:
    """Linux 能力表：表示平台支持什么，不代表设备一定存在。"""
    return {
        "os": "linux",
        "elevated": is_elevated(),
        "cpu_temp_sources": ["hwmon", "thermal"],
        "gpu_nvidia": True,
        "gpu_integrated": True,
        "physical_disk": True,
        "network_detail": True,
        "cpu_freq_per_core": True,
        "lhm": False,
        "powershell": False,
    }
