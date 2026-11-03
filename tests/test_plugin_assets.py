"""插件静态资源、安全响应头与 manifest 测试（T4.5）。"""

from __future__ import annotations

import pytest

import hardware_monitor
import hardware_monitor.config as config
from hardware_monitor import state


def _assert_common_html_headers(resp):
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Cache-Control"] == "no-cache"
    assert "X-Frame-Options" not in resp.headers


def test_plugin_page_headers(client):
    resp = client.get("/plugin")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["Content-Type"]
    _assert_common_html_headers(resp)
    assert "frame-ancestors 'self'" in resp.headers["Content-Security-Policy"]


def test_widget_page_frame_ancestors(client):
    resp = client.get("/widget")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["Content-Type"]
    _assert_common_html_headers(resp)
    csp = resp.headers["Content-Security-Policy"]
    assert "frame-ancestors" in csp
    assert "http://127.0.0.1:*" in csp
    assert "http://localhost:*" in csp


def test_embed_js_content_type_and_body(client):
    resp = client.get("/embed.js")
    assert resp.status_code == 200
    assert "application/javascript" in resp.headers["Content-Type"]
    assert "charset=utf-8" in resp.headers["Content-Type"]
    assert resp.headers["Cache-Control"] == "no-cache"
    body = resp.get_data(as_text=True)
    assert "HardwareMonitor" in body
    assert "postMessage" in body


def test_widget_and_plugin_assets(client):
    cases = (
        ("/widget.css", "text/css"),
        ("/widget-core.js", "application/javascript"),
        ("/widget.js", "application/javascript"),
        ("/plugin.css", "text/css"),
    )
    for path, ctype in cases:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert ctype in resp.headers["Content-Type"], path


def test_manifest_fields(client):
    resp = client.get("/api/plugin/manifest")
    assert resp.status_code == 200
    manifest = resp.get_json()

    assert manifest["name"] == "hardware-monitor"
    assert manifest["version"] == hardware_monitor.__version__
    assert manifest["api_version"] == 1
    assert manifest["base_url"].startswith("http")
    assert manifest["widget_url"] == "/widget"
    assert manifest["dashboard_url"] == "/"
    assert manifest["auth"] == {"type": "token", "header": "X-Monitor-Token"}
    assert manifest["platform"]["os"] == config.PLATFORM
    assert isinstance(manifest["platform"]["capabilities"], dict)


def test_adapter_assets_are_served(client):
    config_resp = client.get("/plugins/dsh-hardware-monitor/adapter.config.json")
    assert config_resp.status_code == 200
    assert "application/json" in config_resp.headers["Content-Type"]
    assert "targetSelector" in config_resp.get_json()

    inject = client.get("/plugins/dsh-hardware-monitor/inject.js")
    assert inject.status_code == 200
    assert "application/javascript" in inject.headers["Content-Type"]
    assert "HardwareMonitor" in inject.get_data(as_text=True)

    userscript = client.get("/plugin/adapters/userscript/hardware-monitor.user.js")
    assert userscript.status_code == 200
    assert "application/javascript" in userscript.headers["Content-Type"]


def test_dsh_native_plugin_assets(client):
    pkg = client.get("/plugins/dsh-hardware-monitor/package.json")
    assert pkg.status_code == 200
    manifest = pkg.get_json()
    assert manifest["name"] == "dsh-hardware-monitor"
    assert manifest["dsh"]["client"]["platform"] == "web"
    assert manifest["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"

    client_js = client.get("/plugins/dsh-hardware-monitor/client.js")
    assert client_js.status_code == 200
    assert "application/javascript" in client_js.headers["Content-Type"]
    assert "__ModuleLoader__" in client_js.get_data(as_text=True)

    host_js = client.get("/plugins/dsh-hardware-monitor/lib/index.js")
    assert host_js.status_code == 200
    assert "hardware_monitor_status" in host_js.get_data(as_text=True)

    skill = client.get("/plugins/dsh-hardware-monitor/SKILL.md")
    assert skill.status_code == 200
    assert "hardware_monitor_status" in skill.get_data(as_text=True)


def test_adapter_route_blocks_traversal(client):
    resp = client.get("/plugin/adapters/%2e%2e%2f%2e%2e%2fserver.py")
    assert resp.status_code in (400, 404)
    resp = client.get("/plugins/%2e%2e%2f%2e%2e%2fserver.py")
    assert resp.status_code in (400, 404)


def test_manifest_requires_token_when_configured(monkeypatch, client):
    monkeypatch.setattr(config, "TOKEN", "s3cret")

    assert client.get("/api/plugin/manifest").status_code == 401
    ok = client.get("/api/plugin/manifest", headers={"X-Monitor-Token": "s3cret"})
    assert ok.status_code == 200
    # 插件静态资源/适配器源文件不受 token 保护（iframe 与宿主加载需要）
    assert client.get("/embed.js").status_code == 200
    assert client.get("/plugins/dsh-hardware-monitor/adapter.config.json").status_code == 200


def test_widget_html_does_not_embed_token(client):
    """widget 页面本身不含 token；token 只走 postMessage。"""
    body = client.get("/widget").get_data(as_text=True)
    assert "X-Monitor-Token" not in body


@pytest.fixture(autouse=True)
def _clean_state():
    state.reset()
    yield
    state.reset()
