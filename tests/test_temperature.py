"""CPU 温度调度器测试：显式注入来源列表，脱离平台模块验证缓存/降级逻辑。"""

from __future__ import annotations

from hardware_monitor.collectors.temperature import TemperatureReader

PAYLOAD = {"name": "CPU (Fake)", "value": 42.0, "source": "fake"}


def test_uses_injected_sources_in_order():
    calls = []

    def first():
        calls.append("first")
        return None

    def second():
        calls.append("second")
        return PAYLOAD

    reader = TemperatureReader(sources=[first, second])

    assert reader.read() == PAYLOAD
    assert calls == ["first", "second"]


def test_cache_until_interval():
    calls = []

    def source():
        calls.append("call")
        return PAYLOAD

    reader = TemperatureReader(sources=[source], interval=3600)

    assert reader.read() == PAYLOAD
    assert reader.read() == PAYLOAD
    assert calls == ["call"]


def test_empty_result_is_not_cached():
    calls = []

    def source():
        calls.append("call")
        return None

    reader = TemperatureReader(sources=[source], interval=3600)

    assert reader.read() is None
    assert reader.read() is None
    assert len(calls) == 2


def test_source_exception_isolated():
    def boom():
        raise RuntimeError("sensor exploded")

    reader = TemperatureReader(sources=[boom, lambda: PAYLOAD])

    assert reader.read() == PAYLOAD


def test_accepts_factory_returning_sources():
    reader = TemperatureReader(sources=lambda: [lambda: PAYLOAD])
    assert reader.read() == PAYLOAD


def test_default_sources_come_from_platform(monkeypatch):
    import hardware_monitor.platform as platform_info

    monkeypatch.setattr(platform_info, "temperature_sources", lambda: [lambda: PAYLOAD])

    reader = TemperatureReader()
    assert reader.read() == PAYLOAD
