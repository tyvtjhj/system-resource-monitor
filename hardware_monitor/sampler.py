"""后台采样调度线程。

保持原节奏：快指标 1s（CPU/内存/网络速率/GPU/温度），慢指标 3s（磁盘/系统信息/核显），
GPU 引擎 3s。所有指标都由 `state.collect()` 执行，单个失败只记录 `<key>_error`，
采样线程本身不会退出。
"""

from __future__ import annotations

import threading
import time

import psutil

from . import config
from . import platform as platform_info
from .collectors import common, nvidia, temperature
from .state import _state, _state_lock, collect, put, put_error

_stop = threading.Event()


def stop():
    """请求采样线程退出（测试/优雅关闭用）。"""
    _stop.set()


def sampler_loop():
    nvml = nvidia.NvmlSession()
    cpu_temp = temperature.TemperatureReader()
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
            collect("cpu", common.collect_cpu, platform_info.cpu_freq)
            collect("memory", common.collect_memory)

            cur_counters = psutil.net_io_counters()
            cur_ts = time.time()
            put("network_speed", common.net_speed(prev_counters, prev_ts, cur_counters, cur_ts))
            prev_counters, prev_ts = cur_counters, cur_ts

            gpu = collect("gpu", nvml.gpu)
            gpu_name = (gpu or {}).get("name", "")

            collect("temperature", temperature.collect_temperature, nvml, cpu_temp)

            if cycle_started >= next_slow:
                next_slow = cycle_started + config.SLOW_INTERVAL
                collect("disk", common.collect_disk, platform_info.physical_disks(),
                        platform_info.partition_parent)
                collect("system_info", common.collect_system_info)
                collect("igpu", platform_info.integrated_gpu)

            if cycle_started >= next_engine:
                next_engine = cycle_started + config.ENGINE_INTERVAL
                try:
                    engines = nvidia.collect_engines(gpu_name)
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
            _stop.wait(max(0.05, config.FAST_INTERVAL - elapsed))
        except Exception as exc:  # noqa: BLE001 - 采样线程不能死
            print(f"[WARN] sampler cycle failed: {exc}")
            _stop.wait(config.FAST_INTERVAL)
