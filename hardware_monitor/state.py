"""内存采样快照与错误隔离。

设计：后台采样线程写入 `_state`；HTTP 请求只读快照。单个采集器失败时只记录
`<key>_error`，不会中断整轮采样，也不清除上一轮的值（对看板而言「最后一次
已知值」比空值有用，排障时可同时看到旧值与错误标记）。
"""

from __future__ import annotations

import threading

_state: dict = {}
_state_lock = threading.Lock()


def put(key, value):
    """写入快照；成功时清除同名的错误标记。"""
    with _state_lock:
        _state.pop(key + "_error", None)
        _state[key] = value


def put_error(key, err):
    """记录采集错误，不改动已有的值。"""
    with _state_lock:
        _state[key + "_error"] = str(err)


def collect(key, fn, *args):
    """执行一个采集函数：失败只记录 <key>_error，不影响其他指标。"""
    try:
        value = fn(*args)
    except Exception as exc:  # noqa: BLE001 - 单个采集失败不应拖垮采样线程
        put_error(key, exc)
        print(f"[WARN] collect {key} failed: {exc}")
        return None
    put(key, value)
    return value


def _snapshot():
    """返回快照副本：过滤下划线开头的内部键，并附加 latency_ms。"""
    with _state_lock:
        data = {k: v for k, v in _state.items() if not k.startswith("_")}
        cycle_ms = _state.get("_cycle_ms", 0)
    data["latency_ms"] = cycle_ms
    return data


def reset():
    """清空快照（主要供测试使用）。"""
    with _state_lock:
        _state.clear()
