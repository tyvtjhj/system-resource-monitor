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
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    from .windows import (  # noqa: F401
        capabilities,
        integrated_gpu,
        is_elevated,
        network_details,
        physical_disks,
        request_elevation,
        subprocess_flags,
        temperature_sources,
    )
else:
    from .linux import (  # noqa: F401
        capabilities,
        integrated_gpu,
        is_elevated,
        network_details,
        physical_disks,
        request_elevation,
        subprocess_flags,
        temperature_sources,
    )
