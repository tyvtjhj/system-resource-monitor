"""平台层测试：能力表、平台选择、统一接口、Linux 分区->父设备映射。

这些用例在 Windows / Linux / macOS 上都必须能通过（跨平台断言按 sys.platform 分支）。
"""

from __future__ import annotations

import sys

import pytest

import hardware_monitor.config as config
import hardware_monitor.platform as platform_info
from hardware_monitor.platform import linux as linux_platform
from hardware_monitor.platform import windows as windows_platform
from hardware_monitor.platform.linux import parent_device

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
    "partition_parent",
    "network_details",
    "temperature_sources",
    "integrated_gpu",
    "capabilities",
)


def test_capabilities_fields_and_os_consistency():
    caps = platform_info.capabilities()
    assert CAPABILITY_KEYS <= set(caps)
    assert isinstance(caps["cpu_temp_sources"], list)
    # 回退实现（macOS 等）也必须与配置一致，不能出现 other/linux 自相矛盾
    assert caps["os"] == config.PLATFORM


def test_capabilities_os_follows_config(monkeypatch):
    """macOS 等回退场景：config.PLATFORM=other 时 capabilities.os 也必须是 other。"""
    monkeypatch.setattr(config, "PLATFORM", "other")
    assert platform_info.capabilities()["os"] == "other"


def test_platform_selection():
    if sys.platform == "win32":
        assert config.PLATFORM == "windows"
    elif sys.platform.startswith("linux"):
        assert config.PLATFORM == "linux"
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


def test_linux_physical_disks_returns_list():
    """非 Linux 平台应为空列表；真实 Linux 上允许非空（不能无条件断言 == []）。"""
    disks = linux_platform.physical_disks()
    assert isinstance(disks, list)
    if not sys.platform.startswith("linux"):
        assert disks == []
    else:
        for disk in disks:
            assert disk["type"] == "physical"
            assert disk["device"].startswith("/dev/")


def test_parent_device_mapping():
    assert parent_device("/dev/nvme0n1p1") == "/dev/nvme0n1"
    assert parent_device("/dev/nvme0n1p12") == "/dev/nvme0n1"
    assert parent_device("nvme0n1p2") == "/dev/nvme0n1"
    assert parent_device("/dev/mmcblk0p1") == "/dev/mmcblk0"
    assert parent_device("/dev/sda1") == "/dev/sda"
    assert parent_device("/dev/sda2") == "/dev/sda"
    # 整盘或无输入没有父设备
    assert parent_device("/dev/sda") is None
    assert parent_device("") is None


def test_linux_partition_parent_mapping():
    assert linux_platform.partition_parent("/dev/sda1") == "/dev/sda"
    assert linux_platform.partition_parent("/dev/nvme0n1p2") == "/dev/nvme0n1"
    assert linux_platform.partition_parent("/dev/sda") is None


def test_windows_partition_parent_returns_none():
    assert windows_platform.partition_parent("C:\\") is None


def test_is_elevated_returns_bool():
    assert isinstance(platform_info.is_elevated(), bool)
