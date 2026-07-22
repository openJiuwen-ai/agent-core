# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Span context management for observability."""

from __future__ import annotations

import asyncio
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.trace import Span

from openjiuwen.core.common.logging import team_logger


class ActiveSpanTracker(SpanProcessor):
    """SpanProcessor that tracks all active spans for reliable cleanup.

    Uses strong references (regular ``set``) to ensure spans survive
    asyncio task cancellation.  Properly ended spans are removed in
    ``on_end`` so they don't accumulate; only spans that were never
    explicitly ended (e.g. the owning task was cancelled) remain in
    the set and are closed by ``flush_all_spans``.

    Also indexes spans by asyncio task (``_spans_by_task``) so that
    the current recording llm.call span can be retrieved without a
    ContextVar (which is unreliable across streaming async generator
    yield/resume context switches).
    """

    def __init__(self):
        self._spans_by_trace: dict[int, set[Span]] = {}
        self._spans_by_task: dict[int, list[Span]] = {}
        self._lock = threading.Lock()
        self._on_start_count = 0
        self._on_end_count = 0

    def _dump_state(self, *, force: bool = False) -> None:
        """Log current tracking state.  Only dumps every 50 starts or when forced."""
        if not force and self._on_start_count % 50 != 0:
            return
        with self._lock:
            total_spans = 0
            team_spans = 0
            for s_set in self._spans_by_trace.values():
                total_spans += len(s_set)
                for s in s_set:
                    if hasattr(s, 'name') and s.name.startswith("team."):
                        team_spans += 1
            team_logger.info(
                "ActiveSpanTracker state: traces={} total_spans={} team_spans={} "
                "start_calls={} end_calls={}",
                len(self._spans_by_trace), total_spans, team_spans,
                self._on_start_count, self._on_end_count,
            )

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        try:
            if hasattr(span, 'context') and span.context:
                trace_id = span.context.trace_id
                try:
                    tid = id(asyncio.current_task())  # 0 if no task (sync context)
                except Exception:
                    tid = 0
                with self._lock:
                    self._spans_by_trace.setdefault(trace_id, set()).add(span)
                    # per-task stack of spans (most recent last)
                    self._spans_by_task.setdefault(tid, []).append(span)
                self._on_start_count += 1
                self._dump_state()
        except Exception as exc:
            team_logger.warning("ActiveSpanTracker.on_start failed: {}", exc)

    def on_end(self, span: ReadableSpan) -> None:
        """Remove properly ended spans so they don't accumulate."""
        try:
            if hasattr(span, 'context') and span.context:
                trace_id = span.context.trace_id
                with self._lock:
                    trace_set = self._spans_by_trace.get(trace_id)
                    if trace_set is not None:
                        trace_set.discard(cast(Span, span))
                    # Remove from all task stacks
                    for tid, stack in self._spans_by_task.items():
                        try:
                            stack.remove(cast(Span, span))
                        except ValueError:
                            pass
                self._on_end_count += 1
        except Exception as exc:
            team_logger.warning("ActiveSpanTracker.on_end failed: {}", exc)

    def peek_current_llm_span(self) -> Span | None:
        """Return the most recent recording llm.call span on the current asyncio task, or None."""
        try:
            tid = id(asyncio.current_task())
        except Exception:
            return None
        if tid == 0:
            return None
        with self._lock:
            stack = self._spans_by_task.get(tid, [])
            # return topmost recording llm.call span
            for span in reversed(stack):
                if hasattr(span, 'name') and span.name == "llm.call" and span.is_recording():
                    return span
        return None

    def pop_current_llm_span(self) -> Span | None:
        """Pop the topmost recording llm.call span from the current task stack."""
        try:
            tid = id(asyncio.current_task())
        except Exception:
            return None
        if tid == 0:
            return None
        with self._lock:
            stack = self._spans_by_task.get(tid, [])
            for i in range(len(stack) - 1, -1, -1):
                span = stack[i]
                if hasattr(span, 'name') and span.name == "llm.call" and span.is_recording():
                    stack.pop(i)
                    return span
        return None

    def clear_task_llm_spans(self, tid: int) -> None:
        """End and remove all llm.call spans for a given task (cascade-close path)."""
        with self._lock:
            stack = self._spans_by_task.pop(tid, [])
            for span in stack:
                if hasattr(span, 'name') and span.name == "llm.call" and span.is_recording():
                    _stamp_cancelled_if_empty(span)
                    span.end()

    def _on_ending(self, span: Span) -> None:
        pass

    def shutdown(self) -> None:
        self.flush_all_spans()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        # Do NOT call flush_all_spans here — force_flush is called by
        # TracerProvider.force_flush which is triggered after every
        # close_team_spans / close_all_spans.  Closing spans prematurely
        # would steal them from their owning trace's finalize_trace.
        return True

    def flush_spans_for_trace(self, trace_id: int, exclude_team_span: bool = True) -> int:
        """Close all active spans for a specific trace (multi-team isolation).

        Spans that carry ``_llm_state`` are finalized with a proper
        output attribute (from the state object) instead of being
        stamped ``cancelled``.
        """
        from opentelemetry.trace import Status, StatusCode

        closed_count = 0

        with self._lock:
            spans_to_close = list(self._spans_by_trace.pop(trace_id, set()))

        for span in spans_to_close:
            try:
                if not span.is_recording():
                    continue

                if exclude_team_span and hasattr(span, 'name') and span.name.startswith("team."):
                    continue

                # Spans with _llm_state get proper finalization
                state = getattr(span, "_llm_state", None)
                if state is not None:
                    _finalize_llm_span_from_state(span, state)
                else:
                    _stamp_cancelled_if_empty(span)
                span.set_status(Status(StatusCode.OK))
                span.end()
                closed_count += 1
            except Exception as exc:
                team_logger.warning("ActiveSpanTracker: failed to close span for trace {}: {}", trace_id, exc)

        if closed_count > 0:
            team_logger.info("ActiveSpanTracker: closed {} spans for trace {:032x}", closed_count, trace_id)

        return closed_count

    def flush_all_spans(self, exclude_team_span: bool = True) -> int:
        """Close all remaining active spans (finalize / shutdown)."""
        from opentelemetry.trace import Status, StatusCode

        closed_count = 0

        with self._lock:
            all_traces = list(self._spans_by_trace.items())
            self._spans_by_trace.clear()
            self._spans_by_task.clear()
            team_logger.info(
                "ActiveSpanTracker.flush_all_spans BEFORE: traces={} "
                "trace_ids=[{}]",
                len(all_traces),
                ", ".join("{:032x}".format(tid) for tid, _ in all_traces),
            )

        for trace_id, span_set in all_traces:
            for span in list(span_set):
                try:
                    if not span.is_recording():
                        continue

                    if exclude_team_span and hasattr(span, 'name') and span.name.startswith("team."):
                        continue

                    state = getattr(span, "_llm_state", None)
                    if state is not None:
                        _finalize_llm_span_from_state(span, state)
                    else:
                        _stamp_cancelled_if_empty(span)
                    span.set_status(Status(StatusCode.OK))
                    span.end()
                    closed_count += 1
                except Exception as exc:
                    team_logger.warning("ActiveSpanTracker: failed to close span: {}", exc)

        if closed_count > 0:
            team_logger.info("ActiveSpanTracker.flush_all_spans: closed {} spans across {} traces",
                           closed_count, len(all_traces))

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

    State is stored on the span object itself (``span._llm_state``),
    not in a ContextVar, so it survives asyncio context switches in
    streaming generators.

    Attributes:
        span: The open OTel span for this LLM call.
        start_ns: Monotonic-ns timestamp of when the span was opened.
        is_streaming: Whether this is a streaming (chunk-by-chunk) call.
        first_chunk_ns: Monotonic-ns of the first stream chunk; None until
            the first chunk arrives.
        reasoning_first_ns: Monotonic-ns of the first reasoning chunk.
        reasoning_last_ns: Monotonic-ns of the last reasoning chunk.
        reasoning_start_wall_ns: Wall-clock epoch (time.time_ns) captured
            at the first reasoning chunk.
    """

    span: Span
    start_ns: int
    is_streaming: bool = False
    first_chunk_ns: int | None = None
    reasoning_first_ns: int | None = None
    reasoning_last_ns: int | None = None
    # Wall-clock epoch (time.time_ns) captured at the first reasoning chunk.
    # Span start/end must be wall-clock timestamps, but the measured duration
    # is a monotonic delta (reasoning_last_ns - reasoning_first_ns). end_time
    # is set to start + that delta so the UI span duration equals reasoning time.
    reasoning_start_wall_ns: int | None = None


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
        team_logger.info(
            "otel: get_or_create_team_span REUSE existing span={} is_recording={} "
            "trace_id={:032x} span_id={:016x}",
            span.name, span.is_recording(),
            span.context.trace_id, span.context.span_id,
        )
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
    team_logger.info(
        "otel: get_or_create_team_span CREATE new team span team_name={} "
        "trace_id={:032x} span_id={:016x}",
        team_name, span.context.trace_id, span.context.span_id,
    )
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


def _stamp_cancelled_if_empty(span: Span) -> None:
    """Set a cancelled marker on a span that was never given proper output.

    Spans reaching the cascade-close paths had their normal close callback
    interrupted (e.g. task cancelled mid-LLM-call).
    """
    from openjiuwen.agent_teams.observability.semconv import LANGFUSE_OBSERVATION_OUTPUT

    if not span.attributes.get(LANGFUSE_OBSERVATION_OUTPUT):
        span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, "cancelled")


def _finalize_llm_span_from_state(span: Span, state: LlmSpanState) -> None:
    """Finalize a llm.call span using its attached state (flush path).

    Used by ``flush_spans_for_trace`` and ``flush_all_spans`` when a span
    still has ``_llm_state`` attached. Sets a minimal output attribute so
    the span is not stamped ``cancelled``, even though the final response
    payload is unavailable at flush time.
    """
    from openjiuwen.agent_teams.observability.semconv import (
        LANGFUSE_OBSERVATION_OUTPUT,
        LANGFUSE_OBSERVATION_TYPE,
    )

    # State gives us the observation type; set output to indicate
    # the span was finalized (not cancelled — it had a real open state).
    span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, "stream_finalized")


def cascade_close_children() -> None:
    """End all open child llm/tool spans on the current context.

    The single source of truth for cascade-close — called from
    ``AgentSpanScope.close`` (rail) and ``close_team_agent_spans`` below.
    Replaces the triplicated loop that used to live in after_task_iteration
    / after_invoke / close_team_agent_spans. Do NOT set Status(OK) here:
    these spans only reach this path when their normal close callback did
    not fire, so leaving status UNSET makes them stand out.
    """
    for bucket in _tool_span_map.get().values():
        for ts in bucket:
            if ts.is_recording():
                _stamp_cancelled_if_empty(ts)
                ts.end()
    _tool_span_map.set({})

    # Close llm.call spans for the current task via the tracker.
    tracker = get_active_span_tracker()
    if tracker is not None:
        try:
            tid = id(asyncio.current_task())
        except Exception:
            tid = 0
        if tid != 0:
            tracker.clear_task_llm_spans(tid)


def close_team_agent_spans(team_name: str) -> None:
    from opentelemetry.trace import Status, StatusCode

    # Drain child LLM / tool spans before closing the parent agent span.
    cascade_close_children()

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

    Captures the trace_id *before* closing the team span and passes it
    explicitly to ``flush_child_spans``.  This prevents the flush from
    falling through to ``flush_all_spans`` (which would steal spans
    belonging to other still-running teams) when ``_team_span_ctx`` is
    cleared by the close above.

    Called from Runner's finally block to ensure all spans are
    properly closed.
    """
    from opentelemetry.trace import Status, StatusCode

    # Capture trace_id BEFORE closing the team span — once the span ends,
    # _team_span_ctx is None and flush_child_spans cannot discover which
    # trace to target.
    team_span = _team_span_ctx.get()
    trace_id_for_flush: int | None = None
    if team_span is not None:
        if hasattr(team_span, 'context') and team_span.context:
            trace_id_for_flush = team_span.context.trace_id

    # Step 1: Close the team span (clears ContextVar for the next team).
    if team_span is not None and team_span.is_recording():
        team_logger.info(
            "otel: finalize_trace - closing team span team={} name={} "
            "is_recording={} trace_id={:032x} span_id={:016x}",
            team_name, team_span.name, team_span.is_recording(),
            team_span.context.trace_id, team_span.context.span_id,
        )
        team_span.set_status(Status(StatusCode.OK))
        team_span.end()
        _team_span_ctx.set(None)
    elif team_span is not None:
        team_logger.warning(
            "otel: finalize_trace - team span EXISTS but NOT recording team={} "
            "name={} is_recording={} trace_id={:032x} span_id={:016x}",
            team_name, team_span.name, team_span.is_recording(),
            team_span.context.trace_id, team_span.context.span_id,
        )
    else:
        team_logger.warning(
            "otel: finalize_trace - NO team span in ContextVar for team={}",
            team_name,
        )

    # Step 2: Flush remaining child spans for THIS trace only.
    flush_child_spans(trace_id=trace_id_for_flush)

    team_logger.info("otel: finalize_trace completed for team={}", team_name)


def reset_all() -> None:
    """Reset all per-task span trackers. Used by tests between cases."""
    _team_span_ctx.set(None)
    _current_agent_span.set(None)
    _tool_span_map.set({})


def flush_child_spans(*, trace_id: int | None = None) -> None:
    """Flush pending child spans for a specific trace.

    When *trace_id* is provided explicitly (the normal path from
    ``finalize_trace``), only that trace's spans are closed — other
    teams' spans are never touched.

    When *trace_id* is ``None``, the call site must be operating while
    ``_team_span_ctx`` is still valid (e.g. ``cascade_close_children``
    during an agent-span close).  In that case the trace_id is
    discovered from the current team span.

    The previous ``flush_all_spans`` fallback (when ``_team_span_ctx``
    was ``None``) has been **removed** — it caused one team's finalize
    to steal spans belonging to other still-running teams.
    """
    tracker = get_active_span_tracker()
    if tracker is None:
        return

    try:
        effective_trace_id: int | None = trace_id
        if effective_trace_id is None:
            team_span = _team_span_ctx.get()
            if team_span is not None and hasattr(team_span, 'context') and team_span.context:
                effective_trace_id = team_span.context.trace_id

        if effective_trace_id is not None:
            closed = tracker.flush_spans_for_trace(effective_trace_id, exclude_team_span=True)
            if closed > 0:
                team_logger.info(
                    "flush_child_spans: closed {} spans for trace {:032x}",
                    closed, effective_trace_id,
                )
        else:
            team_logger.warning(
                "flush_child_spans: cannot determine trace_id — no team span in "
                "ContextVar and no explicit trace_id provided; skipping flush"
            )
    except Exception as exc:
        team_logger.warning("flush_child_spans: ActiveSpanTracker failed: {}", exc)
