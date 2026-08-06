# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Span context management for observability."""

from __future__ import annotations

import threading
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.trace import Span

from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.llm.call_scope import get_current_llm_call_id


def _is_root_span(span: Span, root_span: Span | None) -> bool:
    """Report whether *span* is the trace's root span, which a flush must spare.

    Identity against the resolved root span is the reliable test, and the only
    one that works for a host whose root is not a ``team.<name>`` span (a
    single-agent run names it after the mode and session). The name prefix
    stays as a second test for the flush paths that run with no root span in
    reach — shutdown, or another context's trace.

    Args:
        span: Candidate span from the tracker's active set.
        root_span: Root span resolved for the current context, if any.

    Returns:
        True when the span must be left for its owner to end.
    """
    if root_span is not None and span is root_span:
        return True
    name = getattr(span, "name", "")
    return isinstance(name, str) and name.startswith("team.")


def _is_open_llm_call_of(span: Span, parent_id: int) -> bool:
    """Report whether *span* is a still-open ``llm.call`` span under *parent_id*.

    Args:
        span: Candidate span from the tracker's active set.
        parent_id: Span id of the parent the caller is resolving against.

    Returns:
        True when the span is a recording ``llm.call`` whose parent matches.
    """
    if span.name != "llm.call" or not span.is_recording():
        return False
    return span.parent is not None and span.parent.span_id == parent_id


class ActiveSpanTracker(SpanProcessor):
    """SpanProcessor that tracks all active spans for reliable cleanup.

    Uses strong references (regular ``set``) to ensure spans survive
    asyncio task cancellation.  Properly ended spans are removed in
    ``on_end`` so they don't accumulate; only spans that were never
    explicitly ended remain in the set and are closed by ``flush_all_spans``.
    """

    def __init__(self):
        self._spans_by_trace: dict[int, set[Span]] = {}
        # Open llm.call spans indexed by the id of the LLM request that
        # opened them (see ``openjiuwen.core.foundation.llm.call_scope``).
        # This is the correlation the chunk / usage / completion callbacks
        # resolve against, so a request always writes onto its own span.
        self._llm_spans_by_call_id: dict[str, Span] = {}
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
            team_logger.debug(
                "ActiveSpanTracker state: traces={} total_spans={} team_spans={} "
                "start_calls={} end_calls={}",
                len(self._spans_by_trace), total_spans, team_spans,
                self._on_start_count, self._on_end_count,
            )

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        try:
            if hasattr(span, 'context') and span.context:
                trace_id = span.context.trace_id
                with self._lock:
                    self._spans_by_trace.setdefault(trace_id, set()).add(span)
                self._on_start_count += 1
                self._dump_state()
        except Exception as exc:
            team_logger.warning("ActiveSpanTracker.on_start failed: {}", exc)

    def on_end(self, span: ReadableSpan) -> None:
        """Remove properly ended spans so they don't accumulate."""
        try:
            if hasattr(span, 'context') and span.context:
                trace_id = span.context.trace_id
                state = getattr(span, "otel_llm_state", None)
                call_id = getattr(state, "call_id", "") if state is not None else ""
                with self._lock:
                    trace_set = self._spans_by_trace.get(trace_id)
                    if trace_set is not None:
                        trace_set.discard(cast(Span, span))
                    # An ended span is never a lookup target again; dropping
                    # the index entry here is what keeps the map bounded even
                    # when a close path forgot to pop it.
                    if call_id and self._llm_spans_by_call_id.get(call_id) is span:
                        self._llm_spans_by_call_id.pop(call_id, None)
                self._on_end_count += 1
        except Exception as exc:
            team_logger.warning("ActiveSpanTracker.on_end failed: {}", exc)

    def register_llm_span(self, call_id: str, span: Span) -> None:
        """Index an open llm.call span under the LLM request that opened it.

        Args:
            call_id: Id of the LLM request, from the call scope in effect when
                the span was opened. Empty when the caller reached the callback
                framework without going through ``Model`` — nothing is indexed
                then and lookups fall back to parent matching.
            span: The freshly opened ``llm.call`` span.
        """
        if not call_id:
            return
        with self._lock:
            self._llm_spans_by_call_id[call_id] = span

    def peek_current_llm_span(self) -> Span | None:
        return self._find_llm_span(pop=False)

    def pop_current_llm_span(self) -> Span | None:
        return self._find_llm_span(pop=True)

    def close_llm_spans_by_parent(self, parent_span_id: int) -> int:
        """End recording llm.call spans whose parent matches *parent_span_id*.

        An llm span reaching this path means its normal close callback did
        not fire — logged at error level.
        """
        from opentelemetry.trace import Status, StatusCode

        team_span = _resolve_team_span()
        if team_span is None:
            return 0
        trace_id = team_span.context.trace_id

        closed = 0
        with self._lock:
            spans = list(self._spans_by_trace.get(trace_id, set()))
        for span in spans:
            if span.name != "llm.call" or not span.is_recording():
                continue
            parent = span.parent
            if parent is None or parent.span_id != parent_span_id:
                continue
            try:
                team_logger.warning(
                    "ORPHAN LLM span in cascade-close: span_id={:016x} "
                    "parent_span_id={:016x} — close callback did not fire",
                    span.context.span_id, parent_span_id,
                )
                span.set_status(Status(StatusCode.OK))
                span.end()
                closed += 1
            except Exception as exc:
                team_logger.warning(
                    "ActiveSpanTracker: failed to cascade-close llm span "
                    "span_id={:016x}: {}",
                    span.context.span_id if hasattr(span, 'context') and span.context else 0,
                    exc,
                )
        return closed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_parent_span_id() -> int | None:
        agent_span = _current_agent_span.get()
        if agent_span is not None and agent_span.is_recording():
            return agent_span.context.span_id
        team_span = _resolve_team_span()
        if team_span is not None and team_span.is_recording():
            return team_span.context.span_id
        return None

    def _find_llm_span(self, *, pop: bool) -> Span | None:
        """Find the recording llm.call span the current callback belongs to.

        Resolution is by request identity first: the LLM call scope in effect
        names the request whose callback is firing, and the span it opened is
        indexed under that id.  That is what keeps concurrent requests apart —
        a member's streaming call, another member's call, and a detached
        background request such as the image-modality probe each resolve to
        their own span no matter which task the callback runs in.

        Parent matching is only a fallback, for callers that reach the
        callback framework without a call scope (a model wrapper that does not
        go through ``Model``, or a test triggering the events directly).  It
        answers only when exactly one recording ``llm.call`` span hangs off the
        current agent/team span; an ambiguous or empty match returns None,
        because writing a completion onto someone else's span is worse than
        losing it.

        Args:
            pop: Whether to release the span from the lookup index, marking
                the request as finished.

        Returns:
            The span this callback belongs to, or None when it cannot be
            identified.
        """
        call_id = get_current_llm_call_id()
        if call_id:
            return self._take_llm_span_by_call_id(call_id, pop=pop)
        return self._find_llm_span_by_parent()

    def _take_llm_span_by_call_id(self, call_id: str, *, pop: bool) -> Span | None:
        """Return the span opened by request *call_id*, if it is still open."""
        with self._lock:
            span = self._llm_spans_by_call_id.get(call_id)
            if span is None:
                return None
            if not span.is_recording():
                self._llm_spans_by_call_id.pop(call_id, None)
                return None
            if pop:
                self._llm_spans_by_call_id.pop(call_id, None)
        return span

    def _find_llm_span_by_parent(self) -> Span | None:
        """Return the single recording llm.call span under the current parent.

        Returns:
            The one matching span, or None when the current context has no
            resolvable parent, no candidate, or more than one candidate.
        """
        team_span = _resolve_team_span()
        if team_span is None:
            return None
        parent_id = self._resolve_parent_span_id()
        if parent_id is None:
            return None
        trace_id = team_span.context.trace_id

        with self._lock:
            span_set = self._spans_by_trace.get(trace_id)
            if span_set is None:
                return None
            all_spans = list(span_set)

        exact = [s for s in all_spans if _is_open_llm_call_of(s, parent_id)]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            team_logger.warning(
                "ActiveSpanTracker: {} open llm.call spans share parent_span_id={:016x} "
                "and the callback carries no LLM call id — skipping rather than "
                "guessing which one it belongs to",
                len(exact), parent_id,
            )
        return None

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

        Spans that carry ``otel_llm_state`` are leaked LLM spans whose normal
        close callback never fired — logged at error level.  Other spans
        (tool / task / event) reaching this path are also unexpected and
        logged as errors.

        The trace's root span is spared when *exclude_team_span* is set: it is
        the caller's to end, and closing it here would both steal its end time
        and report it as leaked.
        """
        from opentelemetry.trace import Status, StatusCode

        closed_count = 0
        root_span = _resolve_team_span() if exclude_team_span else None

        with self._lock:
            spans_to_close = list(self._spans_by_trace.pop(trace_id, set()))

        for span in spans_to_close:
            try:
                if not span.is_recording():
                    continue

                if exclude_team_span and _is_root_span(span, root_span):
                    continue

                # Spans with _llm_state are leaked LLM spans — log, don't
                # stamp (silent fixups mask real bugs).
                state = getattr(span, "otel_llm_state", None)
                if state is not None:
                    _log_orphan_llm_span(span, state)
                else:
                    team_logger.warning(
                        "ORPHAN non-LLM span at flush: name={} span_id={:016x} "
                        "— span was never properly closed",
                        span.name if hasattr(span, 'name') else '<no-name>',
                        span.context.span_id if hasattr(span, 'context') and span.context else 0,
                    )
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
        root_span = _resolve_team_span() if exclude_team_span else None

        with self._lock:
            all_traces = list(self._spans_by_trace.items())
            self._spans_by_trace.clear()
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

                    if exclude_team_span and _is_root_span(span, root_span):
                        continue

                    state = getattr(span, "otel_llm_state", None)
                    if state is not None:
                        _log_orphan_llm_span(span, state)
                    else:
                        team_logger.warning(
                            "ORPHAN non-LLM span at flush: name={} span_id={:016x} "
                            "— span was never properly closed",
                            span.name if hasattr(span, 'name') else '<no-name>',
                            span.context.span_id if hasattr(span, 'context') and span.context else 0,
                        )
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

    State is stored on the span object itself (``span.otel_llm_state``),
    not in a ContextVar, so it survives asyncio context switches in
    streaming generators.

    Attributes:
        span: The open OTel span for this LLM call.
        start_ns: Monotonic-ns timestamp of when the span was opened.
        call_id: Id of the LLM request that opened this span, from the call
            scope in effect at the time. Empty when the call reached the
            callback framework without one; the span is then not indexed and
            its later callbacks resolve by parent matching instead.
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
    call_id: str = ""
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

# Process-wide fallback for the trace's root span, used only when the
# ContextVar carries nothing in the calling context.
#
# A team run has no need for it: ``team_runner`` opens the ``team.<name>`` span
# and every member task is created from that context, so the ContextVar is
# visible everywhere the callbacks run. A single-agent host is the opposite
# case — it opens its root span per request, while the agent executes in a
# supervisor task created earlier (at session setup), which therefore never
# inherits the binding. Without a fallback every lookup below returns None in
# that task: llm/tool spans get no parent, the rail skips the agent tier, and
# ``_find_llm_span`` cannot even locate an already-open ``llm.call`` span to
# write its completion onto.
#
# Registered by the host around one run (see ``set_ambient_team_span``); a
# single active run is assumed, matching the one-root-span-per-process shape of
# such hosts.
_ambient_team_span: Span | None = None


def set_ambient_team_span(span: Span | None) -> None:
    """Register the trace's root span as the process-wide fallback.

    For hosts whose root span cannot be reached through the ContextVar from
    the task the agent runs in. Pair with :func:`clear_ambient_team_span`.

    Args:
        span: Root span of the run, or None to clear.
    """
    global _ambient_team_span
    _ambient_team_span = span


def clear_ambient_team_span() -> None:
    """Drop the process-wide root-span fallback registered for a run."""
    global _ambient_team_span
    _ambient_team_span = None


def _resolve_team_span() -> Span | None:
    """Return the trace's root span: context binding first, ambient fallback second."""
    span = _team_span_ctx.get()
    if span is not None:
        return span
    return _ambient_team_span


def get_team_span(team_name: str | None = None) -> Span | None:
    return _resolve_team_span()


def set_team_span(span: Span, team_name: str | None = None) -> None:
    _team_span_ctx.set(span)


def clear_team_span() -> None:
    _team_span_ctx.set(None)


def get_or_create_team_span(team_name: str, tracer) -> Span | None:
    if not team_name:
        return None

    span = _team_span_ctx.get()
    if span is not None:
        team_logger.debug(
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


def _log_orphan_llm_span(span: Span, state: LlmSpanState) -> None:
    """Log and close an orphan LLM span that reached the flush path.

    A span with ``otel_llm_state`` reaching ``flush_spans_for_trace`` or
    ``flush_all_spans`` means it was opened normally but its close
    callback (on_llm_output / on_llm_invoke_output) never fired AND
    cascade-close missed it.  This is a real bug — log at error level
    so the root cause can be investigated.

    Unlike the old ``_finalize_llm_span_from_state``, this does NOT set a
    "stream_finalized" output attribute, because that would mask the fact
    that the span was leaked.
    """
    team_logger.warning(
        "ORPHAN LLM span at flush: span_id={:016x} streaming={} "
        "recording={} first_chunk_ns={} — span was opened but never "
        "properly closed; its normal close callback did not fire",
        span.context.span_id if hasattr(span, 'context') and span.context else 0,
        getattr(state, "is_streaming", None),
        span.is_recording(),
        getattr(state, "first_chunk_ns", None),
    )


def cascade_close_children() -> None:
    """End all open child llm/tool spans on the current context.

    The single source of truth for cascade-close — called from
    ``AgentSpanScope.close`` (rail) and ``close_team_agent_spans`` below.
    Spans reaching this path had their normal close callback fail to fire
    — this is a real bug, logged at error level without any silent stamping.
    """
    for bucket in _tool_span_map.get().values():
        for ts in bucket:
            if ts.is_recording():
                team_logger.warning(
                    "ORPHAN tool span in cascade-close: name={} span_id={:016x} — "
                    "on_tool_call_finished/on_tool_call_error did not fire",
                    ts.name if hasattr(ts, 'name') else '<no-name>',
                    ts.context.span_id if hasattr(ts, 'context') and ts.context else 0,
                )
                ts.end()
    _tool_span_map.set({})

    # Close llm.call spans belonging to the current agent span.
    tracker = get_active_span_tracker()
    agent_span = _current_agent_span.get()
    if tracker is not None and agent_span is not None:
        tracker.close_llm_spans_by_parent(agent_span.context.span_id)


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


def get_current_llm_span() -> Span | None:
    tracker = get_active_span_tracker()
    if tracker is None:
        return None
    return tracker.peek_current_llm_span()


def pop_current_llm_span() -> Span | None:
    tracker = get_active_span_tracker()
    if tracker is None:
        return None
    return tracker.pop_current_llm_span()


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


def get_current_tool_span() -> Span | None:
    """Return the innermost tool span still executing in this context.

    The tool call a sub-agent is dispatched from: the sub-agent runs *inside*
    ``tool.task`` (or a platform's own agent tool), so its agent span belongs
    under that tool span rather than beside it.

    Tool spans are keyed by name with no cross-key ordering, so the innermost
    one is the latest-started still-recording span — tool calls are sequential
    within an agent loop, which makes start time an unambiguous order.

    Returns:
        The innermost open tool span, or None when no tool call is running.
    """
    open_spans: list[Span] = []
    for bucket in _tool_span_map.get().values():
        open_spans.extend(span for span in bucket if span.is_recording())
    if not open_spans:
        return None
    return max(open_spans, key=lambda span: getattr(span, "start_time", 0) or 0)


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
        team_logger.debug(
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

    team_logger.debug("otel: finalize_trace completed for team={}", team_name)


def reset_all() -> None:
    """Reset all per-task span trackers. Used by tests between cases."""
    _team_span_ctx.set(None)
    _current_agent_span.set(None)
    _tool_span_map.set({})
    clear_ambient_team_span()


def flush_child_spans(*, trace_id: int | None = None) -> None:
    """Flush pending child spans for a specific trace.

    When *trace_id* is provided explicitly (the normal path from
    ``finalize_trace``), only that trace's spans are closed — other
    teams' spans are never touched.

    When *trace_id* is ``None``, the call site must be operating while the
    trace's root span is still resolvable — its ContextVar binding, or the
    ambient registration (e.g. ``cascade_close_children`` during an
    agent-span close).  In that case the trace_id is discovered from it.

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
            team_span = _resolve_team_span()
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
