#!/usr/bin/env python3
"""Codex CLI 状态行脚本（T5.4）。

输出单行硬件摘要，供 Codex/终端状态行使用：

    CPU 12% | MEM 57% | GPU 7% | 58°C

用法：
    python statusline.py
    python statusline.py --base-url http://127.0.0.1:5678 --token 你的口令
    python statusline.py --format "CPU {cpu}% MEM {mem}% {temp}C"

约定：
    - 服务不可用时输出 `--` 并返回非零退出码（状态行可据此标红）；
    - 只依赖标准库（urllib），无需安装任何第三方包；
    - 温度优先取 CPU，其次 GPU；缺失的字段输出 `--`。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_FORMAT = "CPU {cpu}% | MEM {mem}% | GPU {gpu}% | {temp}°C"
DEFAULT_BASE_URL = "http://127.0.0.1:5678"
TIMEOUT_SECONDS = 2.0


def fetch_snapshot(base_url, token, timeout=TIMEOUT_SECONDS):
    """请求 /api/all；失败抛异常。"""
    url = base_url.rstrip("/") + "/api/all"
    request = urllib.request.Request(url)
    if token:
        request.add_header("X-Monitor-Token", token)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _number(value, digits=0):
    if value is None:
        return None
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return None


def extract_values(data):
    """把 /api/all 简化成 (cpu, mem, gpu, temp)；缺失为 None。"""
    data = data or {}
    cpu = (data.get("cpu") or {}).get("usage")
    mem = (data.get("memory") or {}).get("usage_percent")
    gpu = (data.get("gpu") or {}).get("usage")

    temp = None
    temperature = data.get("temperature") or {}
    for key in ("cpu", "gpu"):
        entry = temperature.get(key)
        if entry and entry.get("value") is not None:
            temp = entry.get("value")
            break
    return cpu, mem, gpu, temp


def build_line(data, fmt=DEFAULT_FORMAT):
    cpu, mem, gpu, temp = extract_values(data)
    text = fmt.format(
        cpu=_number(cpu) if _number(cpu) is not None else "--",
        mem=_number(mem) if _number(mem) is not None else "--",
        gpu=_number(gpu) if _number(gpu) is not None else "--",
        temp=_number(temp, 1) if _number(temp, 1) is not None else "--",
    )
    # 缺失值不保留单位，避免出现 "--%" / "--°C"
    return text.replace("--%", "--").replace("--°C", "--").replace("-- °C", "--")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hardware Monitor 状态行")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="服务地址")
    parser.add_argument("--token", default="", help="MONITOR_TOKEN（可选）")
    parser.add_argument("--format", default=DEFAULT_FORMAT, help="输出模板，占位符: {cpu} {mem} {gpu} {temp}")
    args = parser.parse_args(argv)

    try:
        data = fetch_snapshot(args.base_url, args.token)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        print(f"[hardware-monitor] statusline unavailable: {exc}", file=sys.stderr)
        print("--")
        return 1

    try:
        print(build_line(data, args.format))
    except (KeyError, IndexError, ValueError) as exc:
        print(f"[hardware-monitor] bad format: {exc}", file=sys.stderr)
        print("--")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
