"""CLI 入口：参数解析、提权、单实例检测、启动采样线程与 Flask。

用法（兼容旧入口）：
    python server.py                 # 等价于 python -m hardware_monitor
    python -m hardware_monitor
    python server.py --no-elevate    # 不申请管理员权限
新增参数：
    --host / --port                  # 覆盖 MONITOR_HOST / MONITOR_PORT
    --version                        # 打印版本
优先级：命令行 > 环境变量 > 默认值。
"""

from __future__ import annotations

import argparse
import http.client
import sys
import threading
import time

import psutil

from . import __version__, config, sampler
from . import platform as platform_info
from .api import create_app
from .state import _state


def build_parser():
    parser = argparse.ArgumentParser(
        prog="hardware-monitor",
        description="本机硬件资源看板：Flask 后端 + 零构建单页前端",
    )
    parser.add_argument("--host", default=None, help="监听地址（覆盖 MONITOR_HOST）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（覆盖 MONITOR_PORT）")
    parser.add_argument(
        "--no-elevate",
        action="store_true",
        help="不申请管理员权限（Windows 下 CPU 温度降级为 ACPI 平台温度）",
    )
    parser.add_argument(
        "--elevated",
        action="store_true",
        help="内部使用：标记已提权，防止递归拉起",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"hardware-monitor {__version__}",
    )
    return parser


def _apply_cli_overrides(args):
    if args.host:
        config.HOST = args.host
    if args.port:
        config.PORT = args.port


def monitor_already_running():
    """探测本端口上是否已有本服务的实例（Windows 允许两个进程绑同一端口）。"""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=1.5)
        headers = {"X-Monitor-Token": config.TOKEN} if config.TOKEN else {}
        conn.request("GET", "/api/start", headers=headers)
        resp = conn.getresponse()
        body = resp.read(400)
        conn.close()
        return resp.status == 200 and b'"status"' in body
    except Exception:  # noqa: BLE001
        return False


def listener_pids():
    """找出占用本端口的进程 PID（提权进程可能拿不到 PID，返回空列表）。"""
    try:
        pids = set()
        for conn in psutil.net_connections(kind="inet"):
            if (conn.laddr and conn.laddr.port == config.PORT
                    and conn.status == psutil.CONN_LISTEN and conn.pid):
                pids.add(conn.pid)
        return sorted(pids)
    except Exception:  # noqa: BLE001
        return []


def _print_runtime_info():
    print(f"[OK] Hardware Monitor service started at http://127.0.0.1:{config.PORT}")
    if platform_info.is_elevated():
        print("[OK] 运行权限: 管理员（CPU 温度可读内核级 CPU Package）")
    elif config.PLATFORM == "windows":
        print("[INFO] 运行权限: 普通用户 —— CPU 温度只能读 ACPI 平台温度；"
              "需要精确值请用 run-admin.bat 以管理员身份启动")
    else:
        print("[INFO] 运行权限: 普通用户（不自动提权；读取部分传感器可能需要 sudo）")
    if config.HOST in ("0.0.0.0", ""):
        print(f"[OK] LAN access enabled on port {config.PORT} "
              f"(set MONITOR_HOST=127.0.0.1 to restrict)")
    if config.TOKEN:
        print("[OK] Token auth enabled (append ?token=... to the page URL)")


def main(argv=None):
    args = build_parser().parse_args(argv)
    _apply_cli_overrides(args)

    elevation_argv = list(argv) if argv is not None else sys.argv[1:]
    if platform_info.request_elevation(elevation_argv):
        print("[OK] 已请求管理员权限，服务将在新的管理员窗口中启动；本窗口关闭")
        return 0

    if monitor_already_running():
        pids = listener_pids()
        who = f"（PID {', '.join(map(str, pids))}）" if pids else ""
        print(f"[ERROR] 端口 {config.PORT} 上已经有一个监控实例在运行{who}。\n"
              "        请到它的控制台窗口按 Ctrl+C，或在任务管理器里结束该 python 进程；"
              "也可以用 MONITOR_PORT 换一个端口。")
        return 0

    thread = threading.Thread(target=sampler.sampler_loop, name="sampler", daemon=True)
    thread.start()

    # 等首个快照就绪，避免页面首次加载拿到空数据
    for _ in range(30):
        if _state.get("cpu"):
            break
        time.sleep(0.1)

    _print_runtime_info()

    app = create_app()
    try:
        app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
    except OSError as exc:
        print(f"[ERROR] cannot bind {config.HOST}:{config.PORT} - {exc} (already running?)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
