# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from contextvars import Context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode, set_span_in_context

from openjiuwen.extensions.observability.semconv import (
    OJ_SPAN_FORCED_CLOSE,
    OJ_SPAN_FORCED_CLOSE_REASON,
    OJ_TRACE_FORCED_CLOSE,
)
from openjiuwen.extensions.observability.span_context import (
    ActiveSpanTracker,
    cascade_close_children,
    clear_root_span,
    clear_current_session_id,
    flush_child_spans,
    get_current_tool_span,
    get_root_span,
    reset_state,
    set_active_span_tracker,
    set_current_agent_span,
    set_current_session_id,
    set_root_span,
    push_tool_span,
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


def test_flush_child_spans_preserves_registered_agent_root() -> None:
    """The safety flush must not end a single-agent root span as a child."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    tracker = ActiveSpanTracker()
    provider.add_span_processor(tracker)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.agent-root-flush")
    root = tracer.start_span("agent.agent.session", kind=SpanKind.SERVER)
    set_root_span(root, session_id="single-agent")
    child = tracer.start_span("tool.call", context=set_span_in_context(root))
    set_active_span_tracker(tracker)
    try:
        flush_child_spans()

        assert root.is_recording()
        assert not child.is_recording()
        assert not any(span.name == "agent.agent.session" for span in exporter.get_finished_spans())
        child_record = next(span for span in exporter.get_finished_spans() if span.name == "tool.call")
        assert child_record.status.status_code is StatusCode.UNSET
        assert child_record.attributes[OJ_SPAN_FORCED_CLOSE] is True
        assert child_record.attributes[OJ_SPAN_FORCED_CLOSE_REASON] == "trace_safety_flush"
        assert root.attributes[OJ_TRACE_FORCED_CLOSE] is True
    finally:
        if root.is_recording():
            root.end()
        clear_root_span(session_id="single-agent", expected_span=root)
        set_active_span_tracker(None)
        reset_state()
        provider.shutdown()


def test_cascade_marks_abandoned_llm_unset_and_surfaces_forced_root() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    tracker = ActiveSpanTracker()
    provider.add_span_processor(tracker)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.agent-llm-cascade")
    root = tracer.start_span("agent.root", kind=SpanKind.SERVER)
    agent = tracer.start_span("agent.solo.step", context=set_span_in_context(root))
    llm = tracer.start_span("llm.call", context=set_span_in_context(agent))
    set_root_span(root, session_id="single-agent")
    set_current_agent_span(agent)
    set_active_span_tracker(tracker)
    try:
        assert cascade_close_children() == 1

        assert root.is_recording()
        assert agent.is_recording()
        assert not llm.is_recording()
        llm_record = next(span for span in exporter.get_finished_spans() if span.name == "llm.call")
        assert llm_record.status.status_code is StatusCode.UNSET
        assert llm_record.attributes[OJ_SPAN_FORCED_CLOSE] is True
        assert llm_record.attributes[OJ_SPAN_FORCED_CLOSE_REASON] == (
            "missing_llm_terminal_callback"
        )
        assert root.attributes[OJ_TRACE_FORCED_CLOSE] is True
    finally:
        if agent.is_recording():
            agent.end()
        if root.is_recording():
            root.end()
        clear_root_span(session_id="single-agent", expected_span=root)
        set_current_agent_span(None)
        set_active_span_tracker(None)
        reset_state()
        provider.shutdown()


def test_subagent_cascade_preserves_dispatching_parent_tool() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.subagent-tool-cascade")
    root = tracer.start_span("agent.main", kind=SpanKind.SERVER)
    main_step = tracer.start_span("agent.main.step", context=set_span_in_context(root))
    dispatch_tool = tracer.start_span("tool.task_tool", context=set_span_in_context(main_step))
    subagent = tracer.start_span("agent.explore", context=set_span_in_context(dispatch_tool))
    leaked_child = tracer.start_span("tool.bash", context=set_span_in_context(subagent))
    push_tool_span("task_tool", dispatch_tool)
    push_tool_span("bash", leaked_child)
    set_current_agent_span(subagent)
    try:
        assert cascade_close_children() == 1

        assert dispatch_tool.is_recording()
        assert not leaked_child.is_recording()
        assert get_current_tool_span() is dispatch_tool
        leaked_record = next(
            span for span in exporter.get_finished_spans() if span.name == "tool.bash"
        )
        assert leaked_record.attributes[OJ_SPAN_FORCED_CLOSE_REASON] == (
            "missing_tool_terminal_callback"
        )
    finally:
        if subagent.is_recording():
            subagent.end()
        if dispatch_tool.is_recording():
            dispatch_tool.end()
        if main_step.is_recording():
            main_step.end()
        if root.is_recording():
            root.end()
        set_current_agent_span(None)
        reset_state()
        provider.shutdown()
