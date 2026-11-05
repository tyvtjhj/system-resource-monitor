"""DSH 原生插件单测（T6/D1）：清单、客户端 bundle、宿主工具与路由。

宿主半用「假 ctx + 本地假 Flask 服务」通过 Node 子进程验证，不依赖真实 DSH；
客户端 bundle 只做结构与语法检查（真实渲染需要在 DSH 页面刷新后观察）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DSH_DIR = REPO_ROOT / "plugins" / "dsh-hardware-monitor"
PROBE = REPO_ROOT / "tests" / "helpers" / "dsh_host_probe.mjs"
NODE = shutil.which("node")

SNAPSHOT = {
    "cpu": {"usage": 21.0, "per_core": [10.0], "freq_current_mhz": 3000.0,
            "count_logical": 8, "count_physical": 4, "load_avg": None},
    "memory": {"total_gb": 16.0, "used_gb": 8.0, "available_gb": 8.0, "usage_percent": 50.0},
    "gpu": {"name": "Fake GPU", "usage": 7, "used_gb": 1.0, "total_gb": 8.0},
    "network_speed": {"download_mbps": 1.5, "upload_mbps": 0.5, "bytes_sent_mb": 1.0,
                      "bytes_recv_mb": 2.0, "packets_sent": 1, "packets_recv": 2},
    "system_info": {"uptime": "01:00", "boot_time": "2026-11-03 10:00:00"},
    "platform": {"os": "windows", "capabilities": {"os": "windows"}},
    "latency_ms": 1.0,
}


class _FakeMonitorHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 - 静默
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/all":
            self._send(200, SNAPSHOT)
        elif path == "/api/processes":
            self._send(200, {
                "count": 1,
                "process_count": 42,
                "processes": [{
                    "name": "fake.exe", "pids": 2, "private_mb": 10.0,
                    "private_percent": 1.0, "working_set_mb": 20.0, "working_set_percent": 2.0,
                }],
            })
        elif path == "/api/start":
            self._send(200, {"status": "ok", "ready": True, "elevated": False,
                             "host": "127.0.0.1", "port": 0, "platform": "windows"})
        else:
            self._send(404, {"error": "not found"})


@pytest.fixture()
def fake_monitor():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeMonitorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _run_probe(base_url: str) -> dict:
    assert NODE, "node 不可用，无法验证 DSH 宿主插件"
    result = subprocess.run(
        [NODE, str(PROBE), base_url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert result.returncode == 0, f"probe failed: {result.stderr[-800:]}"
    return json.loads(result.stdout)


def test_dsh_package_manifest():
    manifest = json.loads((DSH_DIR / "package.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "dsh-hardware-monitor"
    assert manifest["type"] == "module"
    assert manifest["main"] == "lib/index.js"
    assert manifest["exports"]["./client"] == "./client.js"
    assert manifest["dsh"]["client"]["platform"] == "web"
    assert manifest["dsh"]["client"]["immediately"] is True
    assert manifest["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert "@deepseek-ai/dsh-client-ui-conversation" in manifest["dsh"]["client"]["inject"]


def test_dsh_cordis_patch_declares_plugin():
    patch = (DSH_DIR / "cordis.patch.yml").read_text(encoding="utf-8")
    assert "- insert:" in patch
    assert "id: dsh-hardware-monitor" in patch


def test_dsh_client_bundle_contract():
    client = (DSH_DIR / "client.js").read_text(encoding="utf-8")
    assert "window.__ModuleLoader__.load" in client
    assert "id: 'dsh-hardware-monitor'" in client
    assert "conversation.composer.dock" in client
    assert "conversation.session.header.corner" not in client
    assert "/hardware-monitor-api/summary" in client
    if NODE:
        result = subprocess.run([NODE, "--check", str(DSH_DIR / "client.js")],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=30)
        assert result.returncode == 0, result.stderr[-500:]


def test_dsh_host_plugin_routes_and_tool(fake_monitor):
    probe = _run_probe(fake_monitor)

    tool = probe["tool"]
    assert tool["ok"] is True, tool
    assert tool["cpu"] == 21.0
    assert tool["mem"] == 50.0
    assert tool["gpu"] == 7
    assert "CPU 21% · MEM 50%" in tool["text"]

    summary = probe["summary"]
    assert summary["status"] == 200
    assert summary["payload"]["ok"] is True
    assert summary["payload"]["base_url"] == fake_monitor
    assert summary["payload"]["data"]["cpu"]["usage"] == 21.0

    health = probe["health"]
    assert health["status"] == 200
    assert health["payload"]["ok"] is True

    processes = probe["processes"]
    assert processes["status"] == 200
    assert processes["payload"]["process_count"] == 42
    assert processes["payload"]["processes"][0]["name"] == "fake.exe"



def test_dsh_host_plugin_reports_service_down():
    probe = _run_probe("http://127.0.0.1:1")  # 必然拒连
    assert probe["tool"]["ok"] is False
    assert probe["tool"]["code"] == "SERVICE_DOWN"
    assert probe["summary"]["status"] == 503
    assert probe["summary"]["payload"]["ok"] is False


def test_dsh_skill_frontmatter():
    skill = (DSH_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---")
    assert 'name: "hardware-monitor"' in skill
    assert "hardware_monitor_status" in skill


def test_dsh_plugin_assets_served_by_flask(client):
    for path in (
        "/plugins/dsh-hardware-monitor/package.json",
        "/plugins/dsh-hardware-monitor/client.js",
        "/plugins/dsh-hardware-monitor/lib/index.js",
        "/plugins/dsh-hardware-monitor/adapter.config.json",
        "/plugins/dsh-hardware-monitor/SKILL.md",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path
    pkg = client.get("/plugins/dsh-hardware-monitor/package.json").get_json()
    assert pkg["name"] == "dsh-hardware-monitor"
