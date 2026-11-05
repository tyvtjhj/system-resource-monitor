"""HTTP 层测试：字段、状态码、token、CORS、页面。"""

from __future__ import annotations

import hardware_monitor.config as config
from hardware_monitor import state


def test_api_all_contains_core_fields_and_platform(client):
    resp = client.get("/api/all")
    assert resp.status_code == 200

    data = resp.get_json()
    for key in ("cpu", "memory", "disk", "platform", "latency_ms"):
        assert key in data
    assert data["platform"]["os"] in ("windows", "linux", "other")
    assert "capabilities" in data["platform"]


def test_single_metric_returns_value(client):
    resp = client.get("/api/cpu")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["usage"] == 12.3
    # 逐核频率随 CPU 指标一起下发（前端据此画「物理核心 · 频率」样式）
    assert body["freq"]["avg_mhz"] == 3900.0
    assert body["freq"]["physical"][0]["logical"] == [0, 1]


def test_single_metric_missing_returns_503(client):
    resp = client.get("/api/gpu")
    assert resp.status_code == 503
    body = resp.get_json()
    assert "error" in body


def test_api_start_health_check(client):
    resp = client.get("/api/start")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["ready"] is True
    assert "platform" in body
    assert "capabilities" in body
    assert "elevated" in body


def test_token_enforced_for_api(monkeypatch, app):
    monkeypatch.setattr(config, "TOKEN", "s3cret")
    client = app.test_client()

    assert client.get("/api/all").status_code == 401

    ok = client.get("/api/all", headers={"X-Monitor-Token": "s3cret"})
    assert ok.status_code == 200

    ok_query = client.get("/api/all?token=s3cret")
    assert ok_query.status_code == 200


def test_cors_header_present(client):
    resp = client.get("/api/all")
    assert "Access-Control-Allow-Origin" in resp.headers
    assert resp.headers["Access-Control-Allow-Methods"] == "GET, OPTIONS"


def test_pages_are_served(client):
    for path in ("/", "/monitor-mobile.html", "/monitor.html"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["Cache-Control"] == "no-cache"
        assert "text/html" in resp.headers["Content-Type"]


def test_error_snapshot_does_not_break_api_all(client):
    state.put_error("gpu", "no nvidia")
    data = client.get("/api/all").get_json()
    assert data["gpu_error"] == "no nvidia"
    assert data["cpu"]["usage"] == 12.3
