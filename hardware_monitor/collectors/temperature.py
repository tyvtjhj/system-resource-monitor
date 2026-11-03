"""CPU 温度调度：按平台提供的来源顺序尝试，命中即返回。

平台模块（platform/windows.py、platform/linux.py）各自返回一组无参可调用对象，
本模块只负责统一的缓存/尝试顺序，保持原有语义：
  - 有值时 3 秒内直接返回缓存；
  - 没读到不缓存，下一轮继续尝试（各来源内部自行控制重试频率）。
"""

from __future__ import annotations

import time


class TemperatureReader:
    """把平台提供的 temperature_sources() 串成可节流的读取器。"""

    def __init__(self, interval=3.0):
        self._interval = interval
        self._cached = None
        self._cached_at = 0.0
        self._sources = None

    def _get_sources(self):
        if self._sources is None:
            from ..platform import temperature_sources

            try:
                self._sources = list(temperature_sources())
            except Exception as exc:  # noqa: BLE001 - 温度不可用不应影响采样线程
                print(f"[WARN] CPU temperature sources unavailable: {exc}")
                self._sources = []
        return self._sources

    def read(self):
        """带节流的读取：有值时 3 秒更新一次；没读到时不缓存，下一轮继续重试。"""
        now = time.time()
        if self._cached is not None and now - self._cached_at < self._interval:
            return self._cached
        self._cached_at = now
        self._cached = None
        for source in self._get_sources():
            try:
                value = source()
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] CPU temperature source failed: {exc}")
                continue
            if value:
                self._cached = value
                break
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
