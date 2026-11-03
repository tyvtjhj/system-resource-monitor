"""平台层测试：能力表字段、平台选择、windows/linux 模块可导入。"""

from __future__ import annotations

import sys

import pytest

import hardware_monitor.config as config
import hardware_monitor.platform as platform_info
from hardware_monitor.platform import linux as linux_platform
from hardware_monitor.platform import windows as windows_platform

CAPABILITY_KEYS = {
    "os",
    "elevated",
    "cpu_temp_sources",
    "gpu_nvidia",
    "gpu_integrated",
    "physical_disk",
    "network_detail",
    "lhm",
    "powershell",
}

PLATFORM_FUNCTIONS = (
    "is_elevated",
    "request_elevation",
    "subprocess_flags",
    "physical_disks",
    "network_details",
    "temperature_sources",
    "integrated_gpu",
    "capabilities",
)


def test_capabilities_fields():
    caps = platform_info.capabilities()
    assert CAPABILITY_KEYS <= set(caps)
    assert isinstance(caps["cpu_temp_sources"], list)
    assert caps["os"] == config.PLATFORM


def test_platform_selection():
    if sys.platform == "win32":
        assert config.PLATFORM == "windows"
        assert platform_info.capabilities()["os"] == "windows"
    elif sys.platform.startswith("linux"):
        assert config.PLATFORM == "linux"
        assert platform_info.capabilities()["os"] == "linux"
    else:
        assert config.PLATFORM == "other"


@pytest.mark.parametrize("module", [windows_platform, linux_platform])
def test_platform_modules_expose_interface(module):
    for name in PLATFORM_FUNCTIONS:
        assert callable(getattr(module, name)), f"{module.__name__} 缺少 {name}"


def test_platform_package_reexports_current_platform():
    for name in PLATFORM_FUNCTIONS:
        assert callable(getattr(platform_info, name))


def test_windows_temperature_sources_are_callables():
    sources = windows_platform.temperature_sources()
    assert len(sources) == 4
    assert all(callable(source) for source in sources)


def test_linux_subprocess_flags_empty():
    assert linux_platform.subprocess_flags() == {}


def test_linux_physical_disks_degrades_on_windows():
    # 非 Linux 环境没有 /sys/block，应返回空列表而不是抛异常
    assert linux_platform.physical_disks() == []


def test_is_elevated_returns_bool():
    assert isinstance(platform_info.is_elevated(), bool)
