"""pytest fixtures：构造注入固定快照的 Flask app，不启动真实采样线程。"""

from __future__ import annotations

import pytest

import hardware_monitor.config as config
from hardware_monitor import api as api_module
from hardware_monitor import state


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setattr(config, "TOKEN", "")
    state.reset()
    state.put("cpu", {
        "usage": 12.3,
        "per_core": [1.0, 2.0],
        "freq_current_mhz": 3000.0,
        "count_logical": 8,
        "count_physical": 4,
        "load_avg": None,
    })
    state.put("memory", {
        "total_gb": 16.0,
        "used_gb": 8.0,
        "available_gb": 8.0,
        "usage_percent": 50.0,
    })
    state.put("disk", [{
        "device": "C:",
        "mountpoint": "C:\\",
        "fstype": "NTFS",
        "total_gb": 100.0,
        "used_gb": 50.0,
        "free_gb": 50.0,
        "usage_percent": 50.0,
    }])
    yield api_module.create_app()
    state.reset()


@pytest.fixture()
def client(app):
    return app.test_client()
