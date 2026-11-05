#!/usr/bin/env python3
"""硬件资源监控 · 环境自检（T1.3）

检查项：
  1. 解释器归属：默认必须运行在项目 `.venv` 内（`--allow-system` 可放宽）
  2. Python 版本（>= 3.10）与解释器路径
  3. 关键依赖的已安装版本
  4. 当前平台
  5. 服务端口占用情况（被占用但不是本服务时判失败）
  6. 若服务已在运行，请求 /api/start 并打印状态

用法：
  .venv/Scripts/python.exe scripts/doctor.py    # Windows
  .venv/bin/python scripts/doctor.py            # Linux / macOS
  python scripts/doctor.py --allow-system       # 用系统解释器诊断，只告警不判失败

只依赖标准库，可在未安装项目依赖的解释器上直接运行。
环境变量：MONITOR_HOST / MONITOR_PORT / MONITOR_TOKEN（与 server.py 一致）。
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
import platform
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

MIN_PYTHON = (3, 10)
DEFAULT_PORT = 5678
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_VENV = PROJECT_ROOT / ".venv"

# (importlib.metadata 包名, 是否必需, 用途说明)
PACKAGES = [
    ("flask", True, "HTTP 服务与路由"),
    ("psutil", True, "CPU / 内存 / 磁盘 / 网络 / 进程"),
    ("WMI", sys.platform == "win32", "物理硬盘 / 网卡详情（仅 Windows）"),
    ("pywin32", sys.platform == "win32", "性能计数器与 WMI 初始化（仅 Windows）"),
    ("pynvml", False, "NVIDIA 显卡利用率与显存"),
]


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _venv_state() -> tuple[bool, str]:
    """返回 (当前解释器是否属于项目 .venv, 当前 sys.prefix)。"""
    prefix = Path(sys.prefix)
    try:
        current = prefix.resolve()
    except OSError:
        current = prefix
    try:
        expected = EXPECTED_VENV.resolve()
    except OSError:
        expected = EXPECTED_VENV
    return current == expected and EXPECTED_VENV.is_dir(), str(current)


def _probe_host() -> str:
    host = os.environ.get("MONITOR_HOST", "0.0.0.0").strip()
    if host in ("", "0.0.0.0", "::", "[::]"):
        return "127.0.0.1"
    return host


def _probe_port() -> int:
    raw = os.environ.get("MONITOR_PORT", str(DEFAULT_PORT)).strip()
    try:
        port = int(raw)
    except ValueError:
        print(f"[WARN] MONITOR_PORT={raw!r} 不是整数，回退到 {DEFAULT_PORT}")
        return DEFAULT_PORT
    if not 1 <= port <= 65535:
        print(f"[WARN] MONITOR_PORT={port} 超出 1-65535，回退到 {DEFAULT_PORT}")
        return DEFAULT_PORT
    return port


def _port_in_use(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _fetch_start(host: str, port: int) -> tuple[dict | None, str]:
    """请求 /api/start；返回 (payload, reason)。reason != "ok" 表示无法确认本服务。"""
    url = f"http://{host}:{port}/api/start"
    request = urllib.request.Request(url)
    token = os.environ.get("MONITOR_TOKEN", "").strip()
    if token:
        request.add_header("X-Monitor-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, f"连接失败: {exc}"
    try:
        payload = json.loads(body)
    except ValueError:
        return None, "响应不是 JSON（可能不是本服务）"
    if not isinstance(payload, dict) or "status" not in payload:
        return None, "响应缺少 status 字段（可能不是本服务）"
    return payload, "ok"


def _print_header() -> None:
    print("=" * 60)
    print(" Hardware Monitor · 环境自检")
    print("=" * 60)


def _ensure_utf8_pipe() -> None:
    """输出被重定向到管道/文件时改用 UTF-8，避免中文在日志中变成乱码。

    交互式控制台保持默认编码（Windows 控制台直接写 Unicode，显示正常）。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or stream.isatty():
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _report_venv(ok: bool, allow_system: bool) -> bool:
    """检查解释器是否属于项目 .venv；返回更新后的 ok。"""
    in_project_venv, prefix = _venv_state()
    if in_project_venv:
        print(f"解释器归属 : 项目 .venv（{prefix}）[OK]")
        return ok
    if allow_system:
        print(f"解释器归属 : 非项目 .venv（sys.prefix={prefix}）[WARN] --allow-system 已放宽")
        return ok
    print(f"解释器归属 : 非项目 .venv（sys.prefix={prefix}）[FAIL]")
    print("  -> 请用项目虚拟环境运行：")
    print(r"     Windows    : .venv\Scripts\python.exe scripts\doctor.py")
    print("     Linux/macOS: .venv/bin/python scripts/doctor.py")
    print("     仅诊断系统解释器时加 --allow-system")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hardware Monitor 环境自检")
    parser.add_argument(
        "--allow-system",
        action="store_true",
        help="允许在非项目 .venv 的解释器下运行（只告警，不判失败）",
    )
    args = parser.parse_args(argv)

    _ensure_utf8_pipe()
    _print_header()
    ok = True

    # 1. 解释器归属
    ok = _report_venv(ok, args.allow_system)

    # 2. Python 版本
    version = sys.version_info
    is_venv = sys.prefix != sys.base_prefix
    py_text = f"{version.major}.{version.minor}.{version.micro}"
    if version[:2] >= MIN_PYTHON:
        py_state = "OK"
    else:
        py_state = f"需要 >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
        ok = False
    print(f"Python     : {py_text} ({'虚拟环境' if is_venv else '系统解释器'}) [{py_state}]")
    print(f"解释器     : {sys.executable}")
    print(f"项目目录   : {PROJECT_ROOT}")

    # 3. 平台
    print(f"平台       : {platform.system()} {platform.release()} ({platform.machine()})")
    if sys.platform == "win32":
        print(f"Windows    : {platform.version()}")

    # 4. 依赖
    print()
    print("关键依赖：")
    missing_required: list[str] = []
    for name, required, note in PACKAGES:
        installed = _package_version(name)
        if installed:
            print(f"  {name:<10} {installed:<14} {note}")
        elif required:
            print(f"  {name:<10} {'缺失':<14} {note}")
            missing_required.append(name)
        else:
            print(f"  {name:<10} {'未安装':<14} {note}（可选）")
    if missing_required:
        ok = False
        print(f"  -> 缺少必需依赖: {', '.join(missing_required)}")
        print("     修复：pip install -r requirements.txt")

    # 5. 端口与服务
    port = _probe_port()
    host = _probe_host()
    print()
    in_use = _port_in_use(host, port)
    print(f"端口 {port}   : {'已被占用' if in_use else '空闲'}")
    print(f"探测地址   : http://{host}:{port}")
    if in_use:
        payload, reason = _fetch_start(host, port)
        if payload is None:
            ok = False
            print(f"服务状态   : 端口被占用，但 /api/start 校验失败（{reason}）")
            print("  -> 端口可能被其他程序占用，或本服务启用了 token 而 MONITOR_TOKEN 未配置")
            print(f"     处理：换端口（MONITOR_PORT=<其它端口>）或关闭占用 {port} 的程序后重试")
        else:
            print("服务状态   : 运行中")
            print(f"  status    = {payload.get('status')}")
            print(f"  ready     = {payload.get('ready')}")
            print(f"  elevated  = {payload.get('elevated')}")
            print(f"  host:port = {payload.get('host')}:{payload.get('port')}")
            if payload.get("platform") is not None:
                print(f"  platform  = {payload.get('platform')}")
            if payload.get("capabilities") is not None:
                print(f"  capabilities = {payload.get('capabilities')}")
    else:
        print("服务状态   : 未运行（启动方式见 README / run.bat / run.sh）")

    # 6. 结论
    print()
    print("-" * 60)
    if ok:
        print("结论: 环境就绪")
        return 0
    print("结论: 环境不完整，请按上面的提示修复后重试")
    return 1


if __name__ == "__main__":
    sys.exit(main())
