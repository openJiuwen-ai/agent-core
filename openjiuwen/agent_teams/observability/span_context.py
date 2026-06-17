# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Span context management for observability."""

from __future__ import annotations

import threading
import weakref
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.trace import Span, set_span_in_context

from openjiuwen.core.common.logging import team_logger


class ActiveSpanTracker(SpanProcessor):
    """SpanProcessor that tracks all active spans for reliable cleanup.

    Uses WeakSet to avoid memory leaks - spans are automatically removed
    when they are garbage collected.
    """

    def __init__(self):
        self._spans_by_trace: dict[int, weakref.WeakSet[Span]] = {}
        self._lock = threading.Lock()

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        try:
            if hasattr(span, 'context') and span.context:
                trace_id = span.context.trace_id
                with self._lock:
                    if trace_id not in self._spans_by_trace:
                        self._spans_by_trace[trace_id] = weakref.WeakSet()
                    self._spans_by_trace[trace_id].add(span)
        except Exception as exc:
            team_logger.warning("ActiveSpanTracker.on_start failed: {}", exc)

    def on_end(self, span: ReadableSpan) -> None:
        pass

    def _on_ending(self, span: Span) -> None:
        pass

    def shutdown(self) -> None:
        self.flush_all_spans()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self.flush_all_spans()
        return True

    def flush_spans_for_trace(self, trace_id: int, exclude_team_span: bool = True) -> int:
        """Close all active spans for a specific trace (multi-team isolation)."""
        from opentelemetry.trace import Status, StatusCode

        closed_count = 0
        spans_to_close: list[Span] = []

        with self._lock:
            weak_set = self._spans_by_trace.pop(trace_id, None)
            if weak_set is None:
                return 0
            spans_to_close = list(weak_set)

        for span in spans_to_close:
            try:
                if not span.is_recording():
                    continue

                if exclude_team_span and hasattr(span, 'name') and span.name.startswith("team."):
                    continue

                span.set_status(Status(StatusCode.OK))
                span.end()
                closed_count += 1
            except Exception as exc:
                team_logger.warning("ActiveSpanTracker: failed to close span for trace {}: {}", trace_id, exc)

        if closed_count > 0:
            team_logger.info("ActiveSpanTracker: closed {} spans for trace {:016x}", closed_count, trace_id)

        return closed_count

    def flush_all_spans(self, exclude_team_span: bool = True) -> int:
        """Close all active spans (shutdown only)."""
        from opentelemetry.trace import Status, StatusCode

        closed_count = 0

        with self._lock:
            all_traces = list(self._spans_by_trace.items())
            self._spans_by_trace.clear()

        for trace_id, weak_set in all_traces:
            for span in list(weak_set):
                try:
                    if not span.is_recording():
                        continue

                    if exclude_team_span and hasattr(span, 'name') and span.name.startswith("team."):
                        continue

                    span.set_status(Status(StatusCode.OK))
                    span.end()
                    closed_count += 1
                except Exception as exc:
                    team_logger.warning("ActiveSpanTracker: failed to close span: {}", exc)

        if closed_count > 0:
            team_logger.info("ActiveSpanTracker: closed {} active spans", closed_count)

        return closed_count


_active_span_tracker: ActiveSpanTracker | None = None


def get_active_span_tracker() -> ActiveSpanTracker | None:
    return _active_span_tracker


def set_active_span_tracker(tracker: ActiveSpanTracker | None) -> None:
    global _active_span_tracker
    _active_span_tracker = tracker


@dataclass
class LlmSpanState:
    """Per-call state attached to one open LLM span.

    Attributes:
        span: The open OTel span for this LLM call.
        start_ns: Monotonic-ns timestamp of when the span was opened.
        context_token: Token returned by ``context.attach`` when the
            span was attached as the current OTel context. Held so the
            close handler can ``context.detach(token)`` and restore the
            previous parent.
        first_chunk_ns: Monotonic-ns of the first stream chunk; None until
            the first chunk arrives.
        chunk_count: Running count of stream chunks seen so far.
    """

    span: Span
    start_ns: int
    context_token: Any | None = None
    first_chunk_ns: int | None = None
    chunk_count: int = 0
    accumulated_content: str = ""
    accumulated_reasoning: str = ""
    is_streaming: bool = False

    def next_chunk_seq(self) -> int:
        """Increment and return the next chunk sequence number."""
        self.chunk_count += 1
        return self.chunk_count


_team_span_ctx: ContextVar[Span | None] = ContextVar("_team_span_ctx", default=None)


def get_team_span(team_name: str | None = None) -> Span | None:
    return _team_span_ctx.get()


def set_team_span(span: Span, team_name: str | None = None) -> None:
    _team_span_ctx.set(span)


def clear_team_span() -> None:
    _team_span_ctx.set(None)


def get_or_create_team_span(team_name: str, tracer) -> Span | None:
    if not team_name:
        return None

    span = _team_span_ctx.get()
    if span is not None:
        return span

    from opentelemetry.trace import SpanKind
    from openjiuwen.agent_teams.observability.semconv import (
        AT_TEAM_NAME,
        LANGFUSE_TRACE_NAME,
        LANGFUSE_TRACE_TAGS,
    )

    span = tracer.start_span(name=f"team.{team_name}", kind=SpanKind.SERVER)
    span.set_attribute(AT_TEAM_NAME, team_name)
    span.set_attribute(LANGFUSE_TRACE_NAME, f"team.{team_name}")
    span.set_attribute(LANGFUSE_TRACE_TAGS, [team_name])

    _team_span_ctx.set(span)
    team_logger.info("otel: get_or_create_team_span created team span for team_name={}, span_id={}",
                     team_name, span.context.span_id)
    return span


def remove_team_span(team_name: str | None = None) -> Span | None:
    """Remove team span from context and return it."""
    span = _team_span_ctx.get()
    _team_span_ctx.set(None)
    return span


_current_agent_span: ContextVar[Span | None] = ContextVar("_current_agent_span", default=None)


def get_current_agent_span() -> Span | None:
    return _current_agent_span.get()


def set_current_agent_span(span: Span | None) -> None:
    _current_agent_span.set(span)


def close_team_agent_spans(team_name: str) -> None:
    from opentelemetry.trace import Status, StatusCode

    current = _current_agent_span.get()
    if current is not None and current.is_recording():
        team_logger.warning(
            "otel: close_team_agent_spans - closing agent span for team={}, name={}, span_id={:016x}",
            team_name,
            current.name if hasattr(current, 'name') else 'unknown',
            current.context.span_id if hasattr(current, 'context') else 0,
        )
        current.set_status(Status(StatusCode.OK))
        current.end()
        _current_agent_span.set(None)


# LLM spans nest (e.g. an inner agent calls back into another LLM); use a stack.
_llm_span_stack: ContextVar[list[LlmSpanState]] = ContextVar("_otel_llm_span_stack", default=[])


def push_llm_span_state(state: LlmSpanState) -> None:
    """Push a new LLM span state onto the per-task stack."""
    stack = list(_llm_span_stack.get())
    stack.append(state)
    _llm_span_stack.set(stack)


def pop_llm_span_state(*, peek: bool = False) -> LlmSpanState | None:
    """Pop (or peek) the top LLM span state for the current task.

    Args:
        peek: When True, return the top entry without removing it. Used by
            the streaming chunk handler that fires repeatedly between
            input/output events.
    """
    stack = list(_llm_span_stack.get())
    if not stack:
        return None
    if peek:
        return stack[-1]
    state = stack.pop()
    _llm_span_stack.set(stack)
    return state


# Tool spans are keyed by tool_name because the framework triggers
# TOOL_CALL_STARTED and TOOL_CALL_FINISHED with tool_name as the only
# correlation key. Concurrent tools with the same name in the same task
# are assumed not to occur (tool calls are sequential within an agent
# loop iteration); if that ever changes, switch to tool_id.
_tool_span_map: ContextVar[dict[str, list[Span]]] = ContextVar("_otel_tool_span_map", default={})


def push_tool_span(tool_name: str, span: Span) -> None:
    """Push a tool span keyed by tool_name."""
    mapping = dict(_tool_span_map.get())
    bucket = list(mapping.get(tool_name, []))
    bucket.append(span)
    mapping[tool_name] = bucket
    _tool_span_map.set(mapping)


def pop_tool_span(tool_name: str) -> Span | None:
    """Pop the most recent open tool span for tool_name, or None."""
    mapping = dict(_tool_span_map.get())
    bucket = list(mapping.get(tool_name, []))
    if not bucket:
        return None
    span = bucket.pop()
    if bucket:
        mapping[tool_name] = bucket
    else:
        mapping.pop(tool_name, None)
    _tool_span_map.set(mapping)
    return span


def pop_any_tool_span() -> Span | None:
    mapping = dict(_tool_span_map.get())
    if not mapping:
        return None
    tool_name = next(iter(mapping))
    bucket = list(mapping[tool_name])
    if not bucket:
        mapping.pop(tool_name, None)
        _tool_span_map.set(mapping)
        return None
    span = bucket.pop()
    if bucket:
        mapping[tool_name] = bucket
    else:
        mapping.pop(tool_name, None)
    _tool_span_map.set(mapping)
    return span


def finalize_trace(team_name: str) -> None:
    """Finalize all spans for a team trace.

    Order matters: close the team span first (clearing the ContextVar),
    then flush all remaining child spans.  This ensures that
    flush_child_spans falls back to the flush_all_spans path
    (because _team_span_ctx is now None), which catches any spans
    that were started after the first flush — a common race in
    pause / resume cycles where coordination events fire
    asynchronously.

    Called from Runner's finally block to ensure all spans are
    properly closed.
    """
    from opentelemetry.trace import Status, StatusCode

    # Step 1: Close the team span first (clears ContextVar)
    team_span = _team_span_ctx.get()
    if team_span is not None and team_span.is_recording():
        team_logger.info("otel: finalize_trace - closing team span for team={}", team_name)
        team_span.set_status(Status(StatusCode.OK))
        team_span.end()
        _team_span_ctx.set(None)

    # Step 2: Flush all remaining child spans.
    # Because _team_span_ctx is now None, flush_child_spans will
    # call flush_all_spans which catches spans from every trace —
    # including any late spans that arrived after the team span
    # was started (e.g. from shutdown callbacks).
    flush_child_spans()

    team_logger.info("otel: finalize_trace completed for team={}", team_name)


def reset_all() -> None:
    """Reset all per-task span trackers. Used by tests between cases."""
    _team_span_ctx.set(None)
    _current_agent_span.set(None)
    _llm_span_stack.set([])
    _tool_span_map.set({})


def flush_child_spans() -> None:
    """Flush all pending child spans for current trace."""
    tracker = get_active_span_tracker()
    if tracker is None:
        return

    try:
        team_span = _team_span_ctx.get()
        if team_span is not None and hasattr(team_span, 'context') and team_span.context:
            trace_id = team_span.context.trace_id
            closed = tracker.flush_spans_for_trace(trace_id, exclude_team_span=True)
            if closed > 0:
                team_logger.info("flush_child_spans: closed {} spans for trace {:016x}", closed, trace_id)
        else:
            closed = tracker.flush_all_spans(exclude_team_span=True)
            if closed > 0:
                team_logger.info("flush_child_spans: closed {} spans via ActiveSpanTracker", closed)
    except Exception as exc:
        team_logger.warning("flush_child_spans: ActiveSpanTracker failed: {}", exc)
