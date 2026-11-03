"""采集工具函数测试：网络差分、nvidia-smi 解析、MAC 归一化、磁盘拼装。"""

from __future__ import annotations

import socket
from types import SimpleNamespace

import psutil

from hardware_monitor.collectors import common, nvidia


def _counters(recv=0, sent=0, psent=0, precv=0):
    return SimpleNamespace(
        bytes_recv=recv, bytes_sent=sent, packets_sent=psent, packets_recv=precv
    )


def test_net_speed_diff():
    prev = _counters()
    cur = _counters(recv=1024 ** 2, sent=2 * 1024 ** 2, psent=10, precv=20)

    out = common.net_speed(prev, 0.0, cur, 1.0)

    assert out["download_mbps"] == 1.0
    assert out["upload_mbps"] == 2.0
    assert out["bytes_recv_mb"] == 1.0
    assert out["packets_recv"] == 20


def test_net_speed_zero_interval_is_safe():
    out = common.net_speed(_counters(), 1.0, _counters(), 1.0)
    assert out["download_mbps"] == 0.0


def test_parse_dmon_columns():
    stdout = (
        "# gpu sm mem enc dec jpg ofa\n"
        "0 7 0 3 1 0 0\n"
    )
    assert nvidia._parse_dmon(stdout) == {"cuda": 7, "3d": 7, "encode": 3, "decode": 1}


def test_parse_dmon_skips_garbage():
    assert nvidia._parse_dmon("nonsense\n") == {}


def test_parse_query_fallback():
    stdout = (
        "Utilization\n"
        "    Gpu                               : 42 %\n"
        "    Encoder                           : 3 %\n"
        "    Decoder                           : 1 %\n"
    )
    out = nvidia._parse_query(stdout)
    assert out == {"cuda": 42, "3d": 42, "encode": 3, "decode": 1}


def test_parse_query_without_utilization():
    assert nvidia._parse_query("Driver Version: 555.99\n") == {}


def test_normalize_mac_variants():
    assert common._normalize_mac("00:E2:69:11:22:33") == "00E269112233"
    assert common._normalize_mac("00-e2-69-11-22-33") == "00E269112233"
    assert common._normalize_mac("") == ""


def test_collect_disk_merges_physical_and_partitions(monkeypatch):
    part = SimpleNamespace(device="C:", mountpoint="C:\\", fstype="NTFS")
    usage = SimpleNamespace(
        total=100 * 1024 ** 3, used=50 * 1024 ** 3, free=50 * 1024 ** 3, percent=50.0
    )
    monkeypatch.setattr(common.psutil, "disk_partitions", lambda all=True: [part])
    monkeypatch.setattr(common.psutil, "disk_usage", lambda mountpoint: usage)

    physical = [{"model": "Test SSD", "type": "physical"}]
    rows = common.collect_disk(physical)

    assert rows[0] == {"model": "Test SSD", "type": "physical"}
    assert rows[1]["usage_percent"] == 50.0
    assert rows[1]["total_gb"] == 100.0


def _fake_disk_env(monkeypatch, device="/dev/sda1"):
    part = SimpleNamespace(device=device, mountpoint="/", fstype="ext4")
    usage = SimpleNamespace(
        total=100 * 1024 ** 3, used=50 * 1024 ** 3, free=50 * 1024 ** 3, percent=50.0
    )
    monkeypatch.setattr(common.psutil, "disk_partitions", lambda all=True: [part])
    monkeypatch.setattr(common.psutil, "disk_usage", lambda mountpoint: usage)


def test_collect_disk_attaches_parent_device(monkeypatch):
    _fake_disk_env(monkeypatch, device="/dev/sda1")

    rows = common.collect_disk([], partition_parent=common_parent_mapper)

    assert rows[0]["parent_device"] == "/dev/sda"


def common_parent_mapper(device):
    return "/dev/sda" if device == "/dev/sda1" else None


def test_collect_disk_without_mapper_keeps_schema(monkeypatch):
    _fake_disk_env(monkeypatch)

    rows = common.collect_disk([])

    assert "parent_device" not in rows[0]


def test_collect_disk_mapper_exception_is_ignored(monkeypatch):
    _fake_disk_env(monkeypatch)

    def broken_mapper(device):
        raise RuntimeError("mapper failed")

    rows = common.collect_disk([], partition_parent=broken_mapper)

    assert "parent_device" not in rows[0]
    assert rows[0]["usage_percent"] == 50.0


def test_collect_disk_skips_inaccessible_partitions(monkeypatch):
    part = SimpleNamespace(device="D:", mountpoint="D:\\", fstype="CDFS")

    def broken_usage(mountpoint):
        raise PermissionError("no media")

    monkeypatch.setattr(common.psutil, "disk_partitions", lambda all=True: [part])
    monkeypatch.setattr(common.psutil, "disk_usage", broken_usage)

    assert common.collect_disk([]) == []


def test_psutil_network_adapters(monkeypatch):
    def fake_addrs():
        return {
            "Ethernet": [
                SimpleNamespace(family=psutil.AF_LINK, address="00-11-22-33-44-55"),
                SimpleNamespace(family=socket.AF_INET, address="192.168.1.10",
                                netmask="255.255.255.0"),
                SimpleNamespace(family=socket.AF_INET6, address="fe80::1", netmask=None),
            ],
            "Loopback": [
                SimpleNamespace(family=socket.AF_INET, address="127.0.0.1",
                                netmask="255.0.0.0"),
            ],
        }

    monkeypatch.setattr(common.psutil, "net_if_addrs", fake_addrs)
    monkeypatch.setattr(
        common.psutil, "net_if_stats",
        lambda: {"Ethernet": SimpleNamespace(speed=1000, mtu=1500, isup=True)},
    )

    adapters = common.psutil_network_adapters()

    assert len(adapters) == 1  # 回环被过滤
    eth = adapters[0]
    assert eth["name"] == "Ethernet"
    assert eth["ipv4"] == ["192.168.1.10"]
    assert eth["gateway"] == []
    assert eth["dhcp"] is None
    assert eth["up"] is True
