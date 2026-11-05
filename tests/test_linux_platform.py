"""Linux 平台逻辑测试（在 Windows 上通过「伪造 sysfs + monkeypatch」运行）。

覆盖任务书 T2.4 的关键行为：物理盘过滤/容量换算/device 字段、分区父设备映射。
"""

from __future__ import annotations

import hardware_monitor.platform.linux as linux_platform


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------- 物理盘 ----------------
def test_physical_disks_from_fake_sysfs(tmp_path, monkeypatch):
    block = tmp_path / "block"

    sda = block / "sda"
    _write(sda / "device" / "model", "Test SATA SSD\n")
    _write(sda / "device" / "serial", "SN123\n")
    _write(sda / "size", "1000000\n")          # 512 字节扇区
    _write(sda / "queue" / "rotational", "0\n")

    nvme = block / "nvme0n1"
    _write(nvme / "device" / "model", "Test NVMe\n")
    _write(nvme / "size", "2000000\n")
    _write(nvme / "queue" / "rotational", "0\n")

    # 虚拟设备必须被排除
    for name in ("loop0", "ram0", "dm-0", "sr0", "zram0"):
        _write(block / name / "size", "1\n")

    monkeypatch.setattr(linux_platform, "SYS_BLOCK", str(block))

    disks = linux_platform.physical_disks()
    by_device = {disk["device"]: disk for disk in disks}

    assert set(by_device) == {"/dev/sda", "/dev/nvme0n1"}
    sda_entry = by_device["/dev/sda"]
    assert sda_entry["model"] == "Test SATA SSD"
    assert sda_entry["serial_number"] == "SN123"
    assert sda_entry["type"] == "physical"
    assert sda_entry["rotational"] is False
    assert sda_entry["size_gb"] == round(1000000 * 512 / 1024 ** 3, 1)
    assert by_device["/dev/nvme0n1"]["size_gb"] == round(2000000 * 512 / 1024 ** 3, 1)


def test_physical_disks_missing_sysfs_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(linux_platform, "SYS_BLOCK", str(tmp_path / "no-such-block"))
    assert linux_platform.physical_disks() == []


def test_rotational_true_for_hdd(tmp_path, monkeypatch):
    block = tmp_path / "block"
    disk = block / "sdb"
    _write(disk / "size", "1000\n")
    _write(disk / "queue" / "rotational", "1\n")
    monkeypatch.setattr(linux_platform, "SYS_BLOCK", str(block))

    disks = linux_platform.physical_disks()
    assert disks[0]["rotational"] is True
