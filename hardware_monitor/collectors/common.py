"""跨平台通用采集器：全部基于 psutil。

平台专有部分（物理硬盘型号、网卡网关/DNS、核显）由 `hardware_monitor.platform`
提供，采样调度层负责拼装；本模块不 import platform，避免循环依赖。
"""

from __future__ import annotations

import os
import re
import socket
import time
from datetime import datetime

import psutil


# ---------------- CPU / 内存 / 网络速率 ----------------
def collect_cpu(cpu_freq_fn=None):
    """CPU 汇总指标。

    `cpu_freq_fn` 由采样层注入（`platform.cpu_freq`）：它按平台给出逐逻辑核
    实时频率和物理核归组，本模块不直接 import platform，避免循环依赖。
    频率读不到时只少一个 `freq` 字段，其余指标照常返回。
    """
    freq = psutil.cpu_freq()
    payload = {
        "usage": round(psutil.cpu_percent(interval=None), 1),
        "per_core": [round(v, 1) for v in psutil.cpu_percent(interval=None, percpu=True)],
        "freq_current_mhz": round(freq.current, 1) if freq and freq.current else 0,
        "count_logical": psutil.cpu_count(logical=True),
        "count_physical": psutil.cpu_count(logical=False),
        "load_avg": os.getloadavg() if hasattr(os, "getloadavg") else None,
    }

    detail = None
    if callable(cpu_freq_fn):
        try:
            detail = cpu_freq_fn()
        except Exception:  # noqa: BLE001 - 频率只是增强信息，失败不能拖垮 CPU 采集
            detail = None
    if isinstance(detail, dict) and detail.get("per_core_mhz"):
        payload["freq"] = detail
        # 卡片上的「全核频率」用逐核平均值，比 psutil 的单一读数（常是基频）更准
        if detail.get("avg_mhz"):
            payload["freq_current_mhz"] = detail["avg_mhz"]
    return payload


def collect_memory():
    vm = psutil.virtual_memory()
    return {
        "total_gb": round(vm.total / (1024 ** 3), 1),
        "used_gb": round(vm.used / (1024 ** 3), 1),
        "available_gb": round(vm.available / (1024 ** 3), 1),
        "usage_percent": vm.percent,
    }


def net_speed(prev_counters, prev_ts, cur_counters, cur_ts):
    """由相邻两次 net_io_counters 求差分，不在请求线程里 sleep。"""
    dt = max(cur_ts - prev_ts, 1e-6)
    return {
        "download_mbps": round(
            (cur_counters.bytes_recv - prev_counters.bytes_recv) / 1024 ** 2 / dt, 2
        ),
        "upload_mbps": round(
            (cur_counters.bytes_sent - prev_counters.bytes_sent) / 1024 ** 2 / dt, 2
        ),
        "bytes_sent_mb": round(cur_counters.bytes_sent / 1024 ** 2, 1),
        "bytes_recv_mb": round(cur_counters.bytes_recv / 1024 ** 2, 1),
        "packets_sent": cur_counters.packets_sent,
        "packets_recv": cur_counters.packets_recv,
    }


# ---------------- 系统信息 ----------------
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


# ---------------- 进程内存排行（按需计算） ----------------
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


# ---------------- 磁盘：平台物理盘 + psutil 分区 ----------------
def collect_disk(physical=None, partition_parent=None):
    """物理硬盘（平台提供）+ 分区用量（psutil），返回混合列表。

    `partition_parent` 是平台提供的映射函数（分区设备名 -> 父设备名）；
    返回非空时给分区条目附加 `parent_device`，前端可据此把分区与整盘关联。
    Windows 没有可靠的映射，传 None 时不附加该字段（保持原有响应结构）。
    """
    disks = []
    seen_partitions = set()

    # 物理盘：只接受 dict，避免平台层返回 None/异常值时前端渲染出 undefined 行。
    for item in physical or []:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        # 空对象/没有任何可识别字段的物理盘条目直接丢弃（避免前端出现空行）。
        if not any(entry.get(key) for key in ("model", "device", "mountpoint", "name", "serial_number")):
            continue
        entry["type"] = "physical"
        disks.append(entry)

    for part in psutil.disk_partitions(all=True):
        device = str(getattr(part, "device", "") or "").strip()
        mountpoint = str(getattr(part, "mountpoint", "") or "").strip()
        # 没有盘符/挂载点的分区（部分虚拟盘、未挂载卷）直接跳过，避免空条目。
        if not device and not mountpoint:
            continue
        key = (device, mountpoint)
        if key in seen_partitions:
            continue
        try:
            usage = psutil.disk_usage(mountpoint or device)
        except (PermissionError, OSError):
            # 空光驱 / 无介质 / 无权限的分区直接跳过
            continue
        seen_partitions.add(key)
        entry = {
            # device 与 mountpoint 始终是字符串；极端环境下两者取其一兜底。
            "device": device or mountpoint,
            "mountpoint": mountpoint or device,
            "fstype": str(getattr(part, "fstype", "") or ""),
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "used_gb": round(usage.used / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "usage_percent": usage.percent,
        }
        if partition_parent is not None:
            try:
                parent = partition_parent(device or mountpoint)
            except Exception:  # noqa: BLE001 - 映射失败不应影响磁盘数据
                parent = None
            if parent:
                entry["parent_device"] = parent
        disks.append(entry)

    return disks


# ---------------- 网卡详情：psutil 回退 / 关联辅助 ----------------
def _normalize_mac(mac):
    """统一 MAC 写法：WMI 用 `00:E2:69`，psutil 用 `00-E2-69`。"""
    return re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()


def psutil_network_adapters():
    """纯 psutil 的网卡列表（无网关/DNS，dhcp=None），作为 WMI 不可用时的回退。"""
    adapters = []
    for name, addrs in psutil.net_if_addrs().items():
        mac = next((a.address for a in addrs if a.family == psutil.AF_LINK), "")
        ipv4 = [
            a.address for a in addrs
            if a.family == socket.AF_INET and a.address != "127.0.0.1"
        ]
        masks = [a.netmask for a in addrs if a.family == socket.AF_INET and a.netmask]
        ipv6 = [
            a.address for a in addrs
            if a.family == socket.AF_INET6 and not a.address.startswith("::1")
        ]
        if not ipv4 and not ipv6:
            continue
        stats = psutil.net_if_stats().get(name)
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
            "speed_mbps": int(stats.speed) if stats else 0,
            "mtu": int(stats.mtu) if stats else 0,
            "up": bool(stats.isup) if stats else False,
        })
    adapters.sort(key=lambda item: (not item["up"], item["name"].lower()))
    return adapters
