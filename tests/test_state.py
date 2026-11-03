"""state.py 行为测试：错误清除、失败隔离、快照过滤。"""

from __future__ import annotations

from hardware_monitor import state


def test_put_clears_error():
    state.reset()
    state.put_error("cpu", "boom")
    assert state._state["cpu_error"] == "boom"

    state.put("cpu", {"usage": 1.0})
    assert "cpu_error" not in state._state
    assert state._state["cpu"] == {"usage": 1.0}


def test_collect_isolates_failure():
    state.reset()
    state.put("memory", {"usage_percent": 10.0})

    def boom():
        raise RuntimeError("sensor gone")

    result = state.collect("cpu", boom)

    assert result is None
    assert state._state["cpu_error"] == "sensor gone"
    # 其他指标不受影响
    assert state._state["memory"] == {"usage_percent": 10.0}
    assert "cpu" not in state._state


def test_collect_success_clears_previous_error():
    state.reset()
    state.put_error("cpu", "old error")

    value = state.collect("cpu", lambda: {"usage": 2.0})

    assert value == {"usage": 2.0}
    assert "cpu_error" not in state._state


def test_snapshot_filters_internal_keys_and_adds_latency():
    state.reset()
    state.put("cpu", {"usage": 5.0})
    with state._state_lock:
        state._state["_cycle_ms"] = 3.5

    snapshot = state._snapshot()

    assert snapshot["cpu"] == {"usage": 5.0}
    assert snapshot["latency_ms"] == 3.5
    assert not any(key.startswith("_") for key in snapshot)
