"""平台层测试：能力表、平台选择、统一接口、Linux 分区->父设备映射。

这些用例在 Windows / Linux / macOS 上都必须能通过（跨平台断言按 sys.platform 分支）。
"""

from __future__ import annotations

import os
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
    "cpu_freq_per_core",
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
    "cpu_freq",
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


def test_windows_cim_physical_disk_fallback(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows only")
    from types import SimpleNamespace
    from hardware_monitor.platform import windows

    payload = '[{"Model":"Test Disk","InterfaceType":"SCSI","Size":1073741824,"SerialNumber":"SN123"}]'
    monkeypatch.setattr(windows.subprocess, "run",
                        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=payload, stderr=""))
    disks = windows._physical_disks_via_cim()
    assert disks == [{
        "model": "Test Disk",
        "interface_type": "SCSI",
        "size_gb": 1.0,
        "serial_number": "SN123",
        "type": "physical",
    }]


# ---------------- CPU 逐核频率 ----------------
def test_parse_cpu_list_ranges():
    assert linux_platform._parse_cpu_list("0-3,8,10-11") == [0, 1, 2, 3, 8, 10, 11]
    assert linux_platform._parse_cpu_list("") == []
    assert linux_platform._parse_cpu_list(" 5 ") == [5]


def test_linux_cpu_freq_without_sysfs_returns_none(monkeypatch):
    monkeypatch.setattr(linux_platform, "_cpu_indexes", lambda: [])
    assert linux_platform.cpu_freq() is None


def test_linux_cpu_freq_builds_payload(monkeypatch):
    monkeypatch.setattr(linux_platform, "_cpu_indexes", lambda: [0, 1, 2, 3])
    monkeypatch.setattr(linux_platform, "_cpu_freq_khz", {0: 3_900_000, 1: 4_100_000}.get)
    monkeypatch.setattr(linux_platform, "_cpu_base_mhz", lambda: 3600.0)

    def siblings(path):
        index = int(path.split("/cpu/cpu")[1].split("/")[0])
        return "0-1" if index in (0, 1) else "2-3"

    monkeypatch.setattr(linux_platform, "_read_text", siblings)

    data = linux_platform.cpu_freq()

    assert data["source"] == "sysfs"
    assert data["per_core_mhz"] == [3900.0, 4100.0, None, None]
    assert data["avg_mhz"] == 4000.0
    assert data["max_mhz"] == 4100.0
    assert [entry["logical"] for entry in data["physical"]] == [[0, 1], [2, 3]]


def test_windows_physical_core_entries_group_siblings(monkeypatch):
    topology = [
        {"logical": [0, 8], "efficiency": 1},
        {"logical": [1, 9], "efficiency": 1},
        {"logical": [2], "efficiency": 0},
    ]
    monkeypatch.setattr(windows_platform, "_logical_core_topology", lambda: topology)
    per_core = [4000.0, 4100.0, 4200.0, None, None, None, None, None, 3000.0, None]

    entries = windows_platform._physical_core_entries(per_core)

    # 有大小核差异时按 EfficiencyClass 标 P/E，同一物理核取两个线程中的较大值
    assert [entry["label"] for entry in entries] == ["P0", "P1", "E0"]
    assert entries[0]["logical"] == [0, 8]
    assert entries[0]["mhz"] == 4000.0
    assert entries[1]["mhz"] == 4100.0


def test_windows_physical_core_entries_without_topology(monkeypatch):
    monkeypatch.setattr(windows_platform, "_logical_core_topology", lambda: [])
    assert windows_platform._physical_core_entries([4000.0]) == []


def test_capabilities_marks_per_core_frequency():
    assert platform_info.capabilities()["cpu_freq_per_core"] is True
