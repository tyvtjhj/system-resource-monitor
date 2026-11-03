"""DSH 客户端 bundle 的 slot 注册探针测试（侧边栏入口 + 完整面板）。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "tests" / "helpers" / "dsh_client_probe.mjs"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="需要 node 验证客户端 bundle")
def test_dsh_client_registers_sidebar_entry_and_panel():
    result = subprocess.run(
        [NODE, str(PROBE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert result.returncode == 0, f"client probe failed: {result.stderr[-800:]}"
    payload = json.loads(result.stdout)
    assert payload["id"] == "dsh-hardware-monitor"
    assert payload["panelRender"] == "ok", payload
    assert payload["panelHasPhysicalModel"] is True, payload
    assert payload["panelTextHasUndefined"] is False, payload

    registrations = payload["registrations"]
    names = [(item["name"], item.get("id") or item.get("key")) for item in registrations]
    assert ("sidebar.panellist", "hardware-monitor") in names, names
    assert ("main", "hardware-monitor") in names, names
    assert ("conversation.composer.dock", "hardware-monitor") in names, names
    assert all(item["name"] != "conversation.session.header.corner" for item in registrations), names
