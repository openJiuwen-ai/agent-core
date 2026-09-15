# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Root span opened around a single-agent run."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.trace import StatusCode, set_span_in_context
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.extensions.observability import setup as shared_setup
from openjiuwen.extensions.observability import span_context as shared_span_context
from openjiuwen.extensions.observability.semconv import (
    ERROR_TYPE,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_OPERATION_NAME,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_SESSION_ID,
    OJ_AGENT_MODE,
    OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
    OJ_EXECUTION_SUBJECT_ID,
    OJ_EXECUTION_SUBJECT_KIND,
    OJ_EXECUTION_SUBJECT_SESSION_ID,
    OJ_REQUEST_ID,
    OJ_RUN_ID,
    OJ_SESSION_ID,
    OJ_SPAN_FORCED_CLOSE,
    OJ_SPAN_FORCED_CLOSE_REASON,
    OJ_TRACE_COMPLETE,
    OJ_TRACE_FORCED_CLOSE,
    OJ_TRACE_ROOT,
    OJ_TRACE_SCHEMA_VERSION,
    OJ_TRAJECTORY_RECORD_KIND,
    OJ_TURN_ID,
    OJ_TURN_NUMBER,
)
from openjiuwen.harness.observability import span_context as agent_span_context
from openjiuwen.harness.observability import setup as agent_setup
from openjiuwen.harness.execution_subject import ExecutionSubject
from openjiuwen.harness.observability.run_span import (
    build_run_span_name,
    close_agent_run_span,
    open_agent_run_span,
    stamp_run_output,
)


@pytest.fixture
def exporter(monkeypatch) -> InMemorySpanExporter:
    """Serve a real tracer over an in-memory exporter with tracing enabled."""
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    monkeypatch.setattr(shared_setup, "is_initialized", lambda: True)
    monkeypatch.setattr(shared_setup, "get_tracer", provider.get_tracer)
    monkeypatch.setattr(shared_setup, "get_config", lambda: None)
    monkeypatch.setattr(agent_setup, "is_tracing_enabled", lambda: True)
    shared_span_context.reset_state()
    agent_span_context.reset_run_root_spans()
    yield memory
    shared_span_context.reset_state()
    agent_span_context.reset_run_root_spans()


def test_span_name_carries_the_mode_hierarchy_and_degrades_gracefully() -> None:
    assert build_run_span_name(mode="code.normal", session_id="s1") == "agent.code.normal.s1"
    assert build_run_span_name(mode="agent.plan", session_id="") == "agent.agent.plan.run"
    assert build_run_span_name(mode="", session_id="s1") == "agent.run.s1"
    assert build_run_span_name(mode="", session_id="") == "agent.run"


def test_open_registers_the_root_so_child_spans_find_a_parent(exporter) -> None:
    """Without a registered root, the callback handler creates no llm/tool span."""
    handle = open_agent_run_span(session_id="sess-A", mode="agent.fast")
    try:
        assert handle is not None
        assert shared_span_context.get_root_span(session_id="sess-A") is handle
        assert agent_span_context.resolve_run_root_span() is handle
    finally:
        close_agent_run_span(handle, session_id="sess-A")


def test_root_routing_attributes_exist_during_processor_on_start(monkeypatch) -> None:
    started_attributes: list[dict[str, object]] = []

    class _StartCapture(SpanProcessor):
        def on_start(self, span, parent_context=None) -> None:
            del parent_context
            started_attributes.append(dict(span.attributes))

        def on_end(self, span) -> None:
            del span

    provider = TracerProvider()
    provider.add_span_processor(_StartCapture())
    monkeypatch.setattr(shared_setup, "is_initialized", lambda: True)
    monkeypatch.setattr(shared_setup, "get_tracer", provider.get_tracer)
    monkeypatch.setattr(agent_setup, "is_tracing_enabled", lambda: True)
    shared_span_context.reset_state()
    agent_span_context.reset_run_root_spans()

    handle = open_agent_run_span(
        session_id="sess-live",
        mode="agent.fast",
        request_id="request-live",
        run_id="run-live",
        turn_id="turn-live",
        turn_number=3,
    )
    try:
        assert started_attributes == [
            {
                LANGFUSE_SESSION_ID: "sess-live",
                OJ_AGENT_MODE: "agent.fast",
                OJ_TRACE_ROOT: True,
                OJ_TRACE_SCHEMA_VERSION: "1",
                GEN_AI_OPERATION_NAME: "invoke_agent",
                OJ_TRAJECTORY_RECORD_KIND: "turn",
                LANGFUSE_OBSERVATION_TYPE: "agent",
                OJ_EXECUTION_SUBJECT_ID: "main",
                OJ_EXECUTION_SUBJECT_DISPLAY_NAME: "Main Agent",
                OJ_EXECUTION_SUBJECT_KIND: "main_agent",
                OJ_EXECUTION_SUBJECT_SESSION_ID: "sess-live",
                GEN_AI_CONVERSATION_ID: "sess-live",
                OJ_SESSION_ID: "sess-live",
                OJ_REQUEST_ID: "request-live",
                OJ_RUN_ID: "run-live",
                OJ_TURN_ID: "turn-live",
                OJ_TURN_NUMBER: 3,
            }
        ]
    finally:
        close_agent_run_span(handle, session_id="sess-live")
        shared_span_context.reset_state()
        agent_span_context.reset_run_root_spans()


def test_root_uses_explicit_execution_subject_for_out_of_turn_team_work(exporter) -> None:
    subject = ExecutionSubject(
        subject_id="team-member:sess-team:demo:leader",
        display_name="Leader",
        kind="team_leader",
        session_id="sess-team",
    )

    handle = open_agent_run_span(
        session_id="sess-team",
        mode="team.work.normal",
        execution_subject=subject,
    )
    close_agent_run_span(handle, session_id="sess-team")

    root = exporter.get_finished_spans()[0]
    assert root.attributes[OJ_EXECUTION_SUBJECT_ID] == subject.subject_id
    assert root.attributes[OJ_EXECUTION_SUBJECT_DISPLAY_NAME] == "Leader"
    assert root.attributes[OJ_EXECUTION_SUBJECT_KIND] == "team_leader"
    assert root.attributes[OJ_EXECUTION_SUBJECT_SESSION_ID] == "sess-team"


def test_close_ends_the_span_stamps_the_output_and_clears_the_root(exporter) -> None:
    handle = open_agent_run_span(
        session_id="sess-A",
        mode="agent.fast",
        request_id="request-A",
        run_id="run-A",
        turn_id="turn-A",
        turn_number=2,
    )

    close_agent_run_span(handle, session_id="sess-A", output="final answer")

    finished = exporter.get_finished_spans()
    assert [span.name for span in finished] == ["agent.agent.fast.sess-A"]
    assert finished[0].attributes[LANGFUSE_SESSION_ID] == "sess-A"
    assert finished[0].attributes[LANGFUSE_OBSERVATION_OUTPUT] == "final answer"
    assert finished[0].attributes[GEN_AI_CONVERSATION_ID] == "sess-A"
    assert finished[0].attributes[GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert finished[0].attributes[OJ_SESSION_ID] == "sess-A"
    assert finished[0].attributes[OJ_REQUEST_ID] == "request-A"
    assert finished[0].attributes[OJ_RUN_ID] == "run-A"
    assert finished[0].attributes[OJ_TURN_ID] == "turn-A"
    assert finished[0].attributes[OJ_TURN_NUMBER] == 2
    assert finished[0].attributes[OJ_AGENT_MODE] == "agent.fast"
    assert finished[0].attributes[OJ_TRACE_ROOT] is True
    assert finished[0].attributes[OJ_TRACE_SCHEMA_VERSION] == "1"
    assert finished[0].attributes[OJ_TRACE_COMPLETE] is True
    assert shared_span_context.get_root_span(session_id="sess-A") is None
    assert agent_span_context.resolve_run_root_span() is None


def test_no_span_is_opened_while_single_agent_tracing_is_off(exporter, monkeypatch) -> None:
    """The Team subsystem may hold the provider up while agent tracing is off."""
    monkeypatch.setattr(agent_setup, "is_tracing_enabled", lambda: False)

    handle = open_agent_run_span(session_id="sess-A", mode="agent.fast")

    assert handle is None
    close_agent_run_span(handle, session_id="sess-A")  # no-op, must not raise
    assert exporter.get_finished_spans() == ()


def test_an_aborted_run_leaves_the_output_attribute_unset() -> None:
    """Empty output means nothing to stamp — not an empty answer."""

    def _fail(key, value):
        raise AssertionError(f"must not stamp {key}={value}")

    stamp_run_output(SimpleNamespace(set_attribute=_fail), "")


def test_explicit_empty_output_is_preserved_by_close(exporter) -> None:
    handle = open_agent_run_span(session_id="sess-A", mode="agent.fast")

    close_agent_run_span(handle, session_id="sess-A", output="")

    finished = exporter.get_finished_spans()[0]
    assert LANGFUSE_OBSERVATION_OUTPUT in finished.attributes
    assert finished.attributes[LANGFUSE_OBSERVATION_OUTPUT] == ""


def test_structured_in_band_failure_marks_root_error_without_fake_exception(exporter) -> None:
    handle = open_agent_run_span(session_id="sess-A", mode="agent.fast")

    close_agent_run_span(
        handle,
        session_id="sess-A",
        error_type="provider.rate_limit",
        error_message="quota exhausted",
    )

    finished = exporter.get_finished_spans()[0]
    assert finished.attributes[ERROR_TYPE] == "provider.rate_limit"
    assert finished.status.status_code is StatusCode.ERROR
    assert finished.status.description == "quota exhausted"
    assert not [event for event in finished.events if event.name == "exception"]


def test_real_exception_is_authoritative_over_structured_failure(exporter) -> None:
    handle = open_agent_run_span(session_id="sess-A", mode="agent.fast")

    close_agent_run_span(
        handle,
        session_id="sess-A",
        exception=RuntimeError("transport wrapper"),
        error_type="round.execution.error",
        error_message="tool process exited",
    )

    finished = exporter.get_finished_spans()[0]
    assert finished.attributes[ERROR_TYPE] == "RuntimeError"
    assert finished.status.status_code is StatusCode.ERROR
    assert finished.status.description == "transport wrapper"
    assert [event for event in finished.events if event.name == "exception"]


def test_exception_only_failure_keeps_its_concrete_type(exporter) -> None:
    handle = open_agent_run_span(session_id="sess-A", mode="agent.fast")

    close_agent_run_span(
        handle,
        session_id="sess-A",
        exception=TimeoutError("provider timed out"),
    )

    finished = exporter.get_finished_spans()[0]
    assert finished.attributes[ERROR_TYPE] == "TimeoutError"
    assert finished.status.status_code is StatusCode.ERROR
    assert finished.status.description == "provider timed out"


def test_leaked_child_spans_are_flushed_against_the_run_trace(exporter, monkeypatch) -> None:
    """The safety net must still know which trace to sweep after the root ends.

    ``flush_child_spans`` resolves the trace from the root ContextVar when no
    trace id is given, and an ended root is no longer resolvable — so the flush
    would silently skip and a leaked llm/tool span would never be closed.
    """
    flushed: list[int | None] = []
    monkeypatch.setattr(
        shared_span_context,
        "flush_child_spans",
        lambda *, trace_id=None: flushed.append(trace_id),
    )
    handle = open_agent_run_span(session_id="sess-A", mode="agent.fast")
    expected_trace_id = handle.context.trace_id

    close_agent_run_span(handle, session_id="sess-A")

    assert flushed == [expected_trace_id]


def test_cascade_forced_tool_is_unset_and_marks_root_before_root_ends(exporter) -> None:
    handle = open_agent_run_span(session_id="sess-A", mode="agent.fast")
    child = shared_setup.get_tracer("forced-close-test").start_span(
        "tool.pending",
        context=set_span_in_context(handle),
    )
    shared_span_context.push_tool_span("pending", child)

    close_agent_run_span(handle, session_id="sess-A")

    finished = exporter.get_finished_spans()
    assert [span.name for span in finished] == ["tool.pending", "agent.agent.fast.sess-A"]
    child_record, root_record = finished
    assert child_record.status.status_code is StatusCode.UNSET
    assert child_record.attributes[OJ_SPAN_FORCED_CLOSE] is True
    assert child_record.attributes[OJ_SPAN_FORCED_CLOSE_REASON] == (
        "missing_tool_terminal_callback"
    )
    assert root_record.attributes[OJ_TRACE_FORCED_CLOSE] is True
    assert root_record.attributes[OJ_TRACE_COMPLETE] is True
