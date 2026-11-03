"""平台抽象层：按 sys.platform 选择实现，统一对外导出。

统一接口（两个平台模块都必须提供）：
    is_elevated() -> bool
    request_elevation(argv: list[str]) -> bool
    subprocess_flags() -> dict
    physical_disks() -> list[dict]
    network_details() -> dict            # {"adapters": [...], "count": n}
    temperature_sources() -> list[Callable]
    integrated_gpu() -> dict | None
    capabilities() -> dict

Windows 之外的平台走 Linux 实现（sysfs/hwmon 方案尽力而为，能力表会如实降级）。
`capabilities()` 的 `os` 字段统一取 `config.PLATFORM`，避免 macOS 等回退实现
出现「config.PLATFORM=other 而 capabilities.os=linux」的自相矛盾。
"""

from __future__ import annotations

import sys

from .. import config

if sys.platform == "win32":
    from . import windows as _impl
else:
    from . import linux as _impl

is_elevated = _impl.is_elevated
request_elevation = _impl.request_elevation
subprocess_flags = _impl.subprocess_flags
physical_disks = _impl.physical_disks
partition_parent = _impl.partition_parent
network_details = _impl.network_details
temperature_sources = _impl.temperature_sources
integrated_gpu = _impl.integrated_gpu


def capabilities():
    """平台能力表（平台支持什么，不代表设备存在）；os 以 config.PLATFORM 为准。"""
    caps = dict(_impl.capabilities())
    caps["os"] = config.PLATFORM
    return caps
