# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from contextvars import Context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.extensions.observability.span_context import (
    clear_root_span,
    clear_current_session_id,
    get_root_span,
    reset_state,
    set_current_session_id,
    set_root_span,
)


def _provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_session_roots_are_isolated_and_ambiguous_fallback_is_safe() -> None:
    provider, _ = _provider()
    tracer = provider.get_tracer("root-test")
    first = tracer.start_span("root.first")
    second = tracer.start_span("root.second")
    try:
        set_root_span(first, session_id="session-a")
        set_root_span(second, session_id="session-b")
        set_current_session_id("session-a")
        assert get_root_span() is first
        assert get_root_span(session_id="session-a") is first
        assert get_root_span(session_id="session-b") is second

        # A supervisor task can lose the bound ContextVar while preserving
        # the session identity; the current session must win over ambiguity.
        supervisor_context = Context()
        assert supervisor_context.run(set_current_session_id, "session-a") is None
        assert supervisor_context.run(get_root_span) is first

        # With no bound root or session identity, two live registry roots are
        # intentionally ambiguous and must not be guessed.
        clear_current_session_id()
        assert Context().run(get_root_span) is None
    finally:
        clear_root_span(session_id="session-a", expected_span=first)
        clear_root_span(session_id="session-b", expected_span=second)
        first.end()
        second.end()
        reset_state()
        provider.shutdown()


def test_clear_root_span_expected_identity_does_not_remove_replacement() -> None:
    provider, _ = _provider()
    tracer = provider.get_tracer("root-race-test")
    old = tracer.start_span("root.old")
    new = tracer.start_span("root.new")
    try:
        set_root_span(old, session_id="session")
        set_root_span(new, session_id="session")
        clear_root_span(session_id="session", expected_span=old)
        assert get_root_span(session_id="session") is new
        clear_root_span(session_id="session", expected_span=new)
        assert get_root_span(session_id="session") is None
    finally:
        if old.is_recording():
            old.end()
        if new.is_recording():
            new.end()
        reset_state()
        provider.shutdown()
