"""Focused contract tests for :class:`TrajectorySpanProcessor`."""

from __future__ import annotations

from contextvars import copy_context

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags, TraceState

from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor


def _span(
    name: str,
    *,
    trace_id: int = 1,
    span_id: int = 1,
    end_time: int = 2,
    session_id: str = "session-1",
) -> ReadableSpan:
    context = SpanContext(
        trace_id=trace_id,
        span_id=span_id,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState(),
    )
    return ReadableSpan(
        name=name,
        context=context,
        resource=Resource.create({"openjiuwen.session_id": session_id}),
        kind=SpanKind.INTERNAL,
        attributes={"answer": "ok"},
        status=Status(StatusCode.OK),
        start_time=end_time - 1,
        end_time=end_time,
    )


def _span_names(trajectory) -> list[str]:
    payload = trajectory.to_otlp()
    return [
        span["name"]
        for resource_span in payload["resourceSpans"]
        for scope_span in resource_span.get("scopeSpans", ())
        for span in scope_span.get("spans", ())
    ]


class _MalformedSpan:
    name = "llm.call"

    @property
    def context(self):
        raise RuntimeError("broken span context")


def test_on_end_records_stable_conversion_issue_without_raising() -> None:
    processor = TrajectorySpanProcessor()
    subscription = processor.subscribe(include_span_categories={"llm"})

    processor.on_end(_MalformedSpan())

    trajectory, issues = processor.drain(subscription)
    assert trajectory is None
    assert len(issues) == 1
    assert issues[0]["code"] == "span_conversion_error"


def test_on_end_swallows_issue_recording_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = TrajectorySpanProcessor()
    processor.subscribe(include_span_categories={"llm"})

    def _raise_issue(*args, **kwargs):
        raise RuntimeError("issue recorder failed")

    monkeypatch.setattr(processor, "_record_capture_issue", _raise_issue)

    # A conversion failure enters the issue path, whose own failure must not
    # escape the synchronous OTel ``span.end`` caller.
    processor.on_end(_MalformedSpan())


def test_on_end_swallows_routing_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = TrajectorySpanProcessor(max_pending_spans=1)
    processor.subscribe(include_span_categories={"llm"})
    processor.on_end(_span("llm.call", span_id=1))

    def _raise_route(*args, **kwargs):
        raise RuntimeError("route failed")

    monkeypatch.setattr(processor, "_append_issue", _raise_route)

    processor.on_end(_span("llm.call", span_id=2))


def test_subscription_fans_out_and_drain_is_non_repeating() -> None:
    processor = TrajectorySpanProcessor()
    first = processor.subscribe(include_span_categories={"llm", "tool"})
    second = processor.subscribe(include_span_categories={"llm", "tool"})

    processor.on_end(_span("llm.call", span_id=1))

    first_trajectory, first_issues = processor.drain(first)
    second_trajectory, second_issues = processor.drain(second)
    assert first_issues == second_issues == ()
    assert first_trajectory.trajectory_id == "00000000000000000000000000000001"
    assert _span_names(first_trajectory) == ["llm.call"]
    assert _span_names(second_trajectory) == ["llm.call"]
    assert processor.drain(first) == (None, ())
    assert processor.drain(second) == (None, ())


def test_category_and_trace_routing_are_independent() -> None:
    processor = TrajectorySpanProcessor()
    local = processor.subscribe(include_span_categories={"llm"})
    traced = processor.subscribe(include_span_categories={"team"}, trace_id="1")

    processor.on_end(_span("tool.lookup", trace_id=1, span_id=2))
    processor.on_end(_span("team.run", trace_id=1, span_id=3))
    processor.on_end(_span("team.run", trace_id=2, span_id=4))

    assert processor.drain(local) == (None, ())
    trajectory, issues = processor.drain(traced)
    assert issues == ()
    assert _span_names(trajectory) == ["team.run"]


def test_contextvar_fanout_does_not_leak_to_child_after_unsubscribe() -> None:
    processor = TrajectorySpanProcessor()
    subscription = processor.subscribe(include_span_categories={"llm"})
    child_context = copy_context()
    processor.unsubscribe(subscription)

    child_context.run(processor.on_end, _span("llm.call"))
    assert processor.drain(subscription) == (None, ())


def test_suppression_is_nested_and_restored_after_exception() -> None:
    processor = TrajectorySpanProcessor()
    subscription = processor.subscribe(include_span_categories={"llm"})

    with pytest.raises(RuntimeError):
        with processor.suppress():
            processor.on_end(_span("llm.call", span_id=1))
            with processor.suppress():
                processor.on_end(_span("llm.call", span_id=2))
            raise RuntimeError("stop")

    processor.on_end(_span("llm.call", span_id=3))
    trajectory, issues = processor.drain(subscription)
    assert issues == ()
    assert _span_names(trajectory) == ["llm.call"]


def test_unsubscribe_and_shutdown_are_idempotent() -> None:
    processor = TrajectorySpanProcessor()
    subscription = processor.subscribe(include_span_categories={"llm"})
    processor.unsubscribe(subscription)
    processor.unsubscribe(subscription)
    assert processor.drain(subscription) == (None, ())
    processor.shutdown()
    processor.shutdown()
    assert processor.force_flush() is True

    # Observability may rebuild its provider after a configuration toggle and
    # reattach the same process-level processor instance.
    replacement = processor.subscribe(include_span_categories={"llm"})
    processor.on_end(_span("llm.call", span_id=2))
    assert _span_names(processor.drain(replacement)[0]) == ["llm.call"]
