# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.core.runner.callback.events import LLMCallEvents
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime


class _Framework:
    def __init__(self, *, fail_event: str | None = None) -> None:
        self.fail_event = fail_event
        self.registered: list[tuple[str, object]] = []
        self.unregistered: list[tuple[str, object]] = []

    def register_sync(self, event: str, callback: object, **_: object) -> None:
        if event == self.fail_event:
            raise RuntimeError("register failed")
        self.registered.append((event, callback))

    def unregister_sync(self, event: str, callback: object) -> None:
        self.unregistered.append((event, callback))


def _config() -> ObservabilityConfig:
    return ObservabilityConfig(enabled=True, service_name="extension-runtime-test", sample_rate=1.0)


def test_callback_registration_rolls_back_partial_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = ObservabilityRuntime()
    framework = _Framework(fail_event=LLMCallEvents.LLM_STREAM_INPUT)
    monkeypatch.setattr(runtime, "_resolve_callback_framework", lambda: framework)

    with pytest.raises(RuntimeError, match="register failed"):
        runtime.initialize(_config(), span_exporter_override=InMemorySpanExporter())

    assert framework.registered
    assert framework.unregistered == framework.registered
    assert not runtime.is_initialized()


def test_missing_callback_framework_keeps_provider_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = ObservabilityRuntime()
    monkeypatch.setattr(runtime, "_resolve_callback_framework", lambda: None)
    runtime.initialize(_config(), span_exporter_override=InMemorySpanExporter())
    try:
        assert runtime.is_initialized()
    finally:
        runtime.shutdown()
