"""Flask app factory 与全部 HTTP 路由。

`create_app()` 只注册路由与钩子，**不启动采样线程**，便于测试注入固定快照；
采样线程由 `hardware_monitor.__main__` 负责启动。现有路由/字段/状态码保持不变，
仅新增：`/api/all` 的 `platform` 字段，`/api/start` 的 `platform` 与 `capabilities`。
"""

from __future__ import annotations

import hmac
import os
import threading
import time

from flask import Flask, abort, jsonify, request, send_file

from . import __version__, config
from . import platform as platform_info
from .collectors import common
from .state import _snapshot, _state, _state_lock

# 按需计算的接口带短缓存（进程内）
_process_cache = {"at": 0.0, "data": None}
_process_lock = threading.Lock()
_network_cache = {"at": 0.0, "data": None}
_network_lock = threading.Lock()


def _token_ok():
    token = config.TOKEN
    if not token:
        return True
    supplied = request.headers.get("X-Monitor-Token") or request.args.get("token", "")
    return hmac.compare_digest(supplied, token)


def _add_html_headers(resp, frame_ancestors="'self'"):
    """HTML 统一安全头：禁 referrer、禁嗅探、不缓存、限制 iframe 父页面。"""
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = f"frame-ancestors {frame_ancestors}"
    return resp


def _serve_page(filename, frame_ancestors="'self'"):
    path = os.path.join(config.BASE_DIR, filename)
    if not os.path.isfile(path):
        abort(404)
    return _add_html_headers(send_file(path, mimetype="text/html"), frame_ancestors)


def _serve_plugin_asset(filename, mimetype):
    """插件静态资源（js/css）；统一 no-cache 与正确的 Content-Type。"""
    path = os.path.join(config.PLUGIN_DIR, filename)
    if not os.path.isfile(path):
        abort(404)
    resp = send_file(path, mimetype=mimetype)
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if mimetype.startswith("text/") or "javascript" in mimetype:
        resp.headers["Content-Type"] = f"{mimetype}; charset=utf-8"
    return resp


def _metric(key):
    with _state_lock:
        value = _state.get(key)
        error = _state.get(key + "_error")
    if value is None:
        return jsonify({"error": error or "not ready yet, retry in a second"}), 503
    return jsonify(value)


def _platform_payload():
    return {"os": config.PLATFORM, "capabilities": platform_info.capabilities()}


def create_app():
    """创建 Flask 应用（不启动采样线程）。"""
    app = Flask(__name__)

    @app.before_request
    def _guard_api():
        if request.method == "OPTIONS":
            return None
        if request.path.startswith("/api/") and not _token_ok():
            return jsonify({"error": "invalid or missing token"}), 401
        return None

    @app.after_request
    def _cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = config.CORS_ORIGIN
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Monitor-Token"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        return resp

    # ---------------- 页面 ----------------
    @app.route("/")
    @app.route("/monitor-mobile.html")
    def mobile_page():
        return _serve_page("monitor-mobile.html")

    @app.route("/monitor.html")
    def desktop_page():
        """桌面版页面（此前无路由，直接 404）。"""
        return _serve_page("monitor.html")

    # ---------------- 插件：集成中心 / 缩略页 / 注入脚本 / 元数据 ----------------
    @app.route("/plugin")
    def plugin_page():
        """集成中心：服务状态、缩略预览、配置生成、适配器说明。"""
        return _serve_page(os.path.join("plugin", "plugin.html"))

    @app.route("/widget")
    def widget_page():
        """缩略监控页（iframe 嵌入用）；frame-ancestors 由 MONITOR_FRAME_ANCESTORS 控制。"""
        return _serve_page(
            os.path.join("plugin", "widget.html"),
            frame_ancestors=config.FRAME_ANCESTORS,
        )

    @app.route("/plugin.css")
    def plugin_css():
        return _serve_plugin_asset("plugin.css", "text/css")

    @app.route("/widget.css")
    def widget_css():
        return _serve_plugin_asset("widget.css", "text/css")

    @app.route("/widget-core.js")
    def widget_core_js():
        return _serve_plugin_asset("widget-core.js", "application/javascript")

    @app.route("/widget.js")
    def widget_js():
        return _serve_plugin_asset("widget.js", "application/javascript")

    @app.route("/embed.js")
    def embed_js():
        return _serve_plugin_asset("embed.js", "application/javascript")

    # ---------------- 全量 / 单指标 ----------------
    @app.route("/api/all")
    def api_all():
        """统一接口：一次性返回全部硬件数据（读快照，不阻塞）。"""
        data = _snapshot()
        data["platform"] = _platform_payload()
        return jsonify(data)

    @app.route("/api/cpu")
    def api_cpu():
        return _metric("cpu")

    @app.route("/api/memory")
    def api_memory():
        return _metric("memory")

    @app.route("/api/disk")
    def api_disk():
        return _metric("disk")

    @app.route("/api/network-speed")
    def api_network_speed():
        return _metric("network_speed")

    @app.route("/api/temperature")
    def api_temperature():
        return _metric("temperature")

    @app.route("/api/gpu")
    def api_gpu():
        return _metric("gpu")

    @app.route("/api/igpu")
    def api_igpu():
        """核显（集成显卡）：利用率 / 共享内存。"""
        return _metric("igpu")

    @app.route("/api/gpu-engines")
    def api_gpu_engines():
        return _metric("gpu_engines")

    @app.route("/api/system")
    def api_system():
        return _metric("system_info")

    # ---------------- 按需计算 + 短缓存 ----------------
    @app.route("/api/network")
    def api_network():
        """网卡详情：地址、掩码、网关、DNS、MAC、速率、MTU、DHCP（5 秒缓存）。"""
        now = time.time()
        with _network_lock:
            cached = _network_cache["data"]
            if cached is not None and now - _network_cache["at"] < 5:
                return jsonify(cached)
        try:
            data = platform_info.network_details()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 500
        with _network_lock:
            _network_cache["data"] = data
            _network_cache["at"] = time.time()
        return jsonify(data)

    @app.route("/api/processes")
    def api_processes():
        """进程内存排行；侧栏打开时才请求，带 1.5 秒缓存避免重复枚举。"""
        now = time.time()
        with _process_lock:
            cached = _process_cache["data"]
            if cached is not None and now - _process_cache["at"] < 1.5:
                return jsonify(cached)
        try:
            data = common.collect_processes()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 500
        with _process_lock:
            _process_cache["data"] = data
            _process_cache["at"] = time.time()
        return jsonify(data)

    @app.route("/api/plugin/manifest")
    def api_plugin_manifest():
        """插件元数据：名称/版本/入口/鉴权方式/平台能力。"""
        return jsonify({
            "name": "hardware-monitor",
            "version": __version__,
            "api_version": 1,
            "base_url": request.host_url.rstrip("/"),
            "widget_url": "/widget",
            "dashboard_url": "/",
            "auth": {"type": "token", "header": "X-Monitor-Token"},
            "platform": {
                "os": config.PLATFORM,
                "capabilities": platform_info.capabilities(),
            },
        })

    # ---------------- 健康检查 ----------------
    @app.route("/api/start")
    def api_start():
        """健康检查：页面只能调用到已运行的服务，因此这里只回报状态。

        原实现会用 subprocess 另起一个 server.py，且误用了不存在的
        subprocess.is_alive()，既必然 500，又让局域网内任何人都能在主机上拉起进程。
        """
        with _state_lock:
            ready = "cpu" in _state
        capabilities = platform_info.capabilities()
        return jsonify({
            "status": "ok",
            "ready": ready,
            "host": config.HOST,
            "port": config.PORT,
            "elevated": platform_info.is_elevated(),
            "platform": config.PLATFORM,
            "capabilities": capabilities,
        })

    return app
