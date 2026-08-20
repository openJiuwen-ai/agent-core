# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Behavior tests for the shared OpenTelemetry provider runtime."""

from __future__ import annotations

import gc
from typing import Any
from unittest.mock import MagicMock
from weakref import ref

import pytest
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime


class _RecordingProcessor(SpanProcessor):
    def __init__(self, events: list[str] | None = None) -> None:
        self.spans: list[ReadableSpan] = []
        self.events = events

    def on_end(self, span: ReadableSpan) -> None:
        self.spans.append(span)
        if self.events is not None:
            self.events.append("extra")


class _FailingProcessor(SpanProcessor):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        self.events.append("failing-start")
        raise RuntimeError("additional on_start failed")

    def on_end(self, span: ReadableSpan) -> None:
        self.events.append("failing-end")
        raise RuntimeError("additional on_end failed")

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self.events.append("failing-flush")
        raise RuntimeError("additional force_flush failed")

    def shutdown(self) -> None:
        self.events.append("failing-shutdown")
        raise RuntimeError("additional shutdown failed")


class _EqualProcessor(_RecordingProcessor):
    def __eq__(self, other: object) -> bool:
        return isinstance(other, _EqualProcessor)


class _RecordingExporter(InMemorySpanExporter):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def export(self, spans: Any) -> Any:
        self.events.append("exporter")
        return super().export(spans)


def _config(name: str) -> ObservabilityConfig:
    return ObservabilityConfig(enabled=True, service_name=name, sample_rate=1.0)


def test_exporter_runs_before_additional_processor() -> None:
    events: list[str] = []
    exporter = _RecordingExporter(events)
    processor = _RecordingProcessor(events)
    runtime = ObservabilityRuntime()
    try:
        runtime.initialize(
            _config("processor-order-test"),
            span_exporter_override=exporter,
            additional_span_processors=(processor,),
        )

        with runtime.get_tracer("processor-order-test").start_as_current_span("captured"):
            pass

        assert list(exporter.get_finished_spans()) == processor.spans
        assert events == ["exporter", "extra"]
    finally:
        runtime.shutdown()


def test_initialize_attaches_override_exporter_to_existing_provider() -> None:
    runtime = ObservabilityRuntime()
    first_exporter = InMemorySpanExporter()
    second_exporter = InMemorySpanExporter()
    try:
        runtime.initialize(
            _config("late-exporter-test"),
            span_exporter_override=first_exporter,
        )
        with runtime.get_tracer("late-exporter-test").start_as_current_span("before"):
            pass

        runtime.initialize(
            _config("late-exporter-test"),
            span_exporter_override=second_exporter,
        )
        with runtime.get_tracer("late-exporter-test").start_as_current_span("after"):
            pass

        assert [span.name for span in first_exporter.get_finished_spans()] == ["before", "after"]
        assert [span.name for span in second_exporter.get_finished_spans()] == ["after"]
    finally:
        runtime.shutdown()


def test_processors_are_identity_deduped_and_retained() -> None:
    runtime = ObservabilityRuntime()
    exporter = InMemorySpanExporter()
    first = _EqualProcessor()
    equal_but_distinct = _EqualProcessor()
    first_ref = ref(first)
    try:
        runtime.initialize(
            _config("identity-test"),
            span_exporter_override=exporter,
        )
        runtime.add_span_processors((first, first, equal_but_distinct))
        del first
        gc.collect()

        assert first_ref() is not None
        with runtime.get_tracer("identity-test").start_as_current_span("captured"):
            pass

        assert [span.name for span in first_ref().spans] == ["captured"]
        assert [span.name for span in equal_but_distinct.spans] == ["captured"]
    finally:
        runtime.shutdown()


def test_processor_failures_do_not_stop_export_or_following_processor() -> None:
    events: list[str] = []
    exporter = _RecordingExporter(events)
    failing = _FailingProcessor(events)
    following = _RecordingProcessor(events)
    runtime = ObservabilityRuntime()
    try:
        runtime.initialize(
            _config("safe-adapter-test"),
            span_exporter_override=exporter,
            additional_span_processors=(failing, following),
        )
        with runtime.get_tracer("safe-adapter-test").start_as_current_span("captured"):
            pass

        runtime.force_flush()

        assert [span.name for span in exporter.get_finished_spans()] == ["captured"]
        assert [span.name for span in following.spans] == ["captured"]
        assert events[:3] == ["failing-start", "exporter", "failing-end"]
        assert "extra" in events
        assert "failing-flush" in events
    finally:
        runtime.shutdown()
    assert "failing-shutdown" in events


def test_initialization_failure_clears_runtime_state(monkeypatch: Any) -> None:
    import openjiuwen.extensions.observability.runtime as runtime_module

    runtime = ObservabilityRuntime()
    monkeypatch.setattr(
        runtime_module,
        "build_span_exporter",
        MagicMock(side_effect=RuntimeError("exporter failed")),
    )

    with pytest.raises(RuntimeError, match="exporter failed"):
        runtime.initialize(_config("rollback-test"))

    assert not runtime.is_initialized()
    assert runtime.get_config() is None
