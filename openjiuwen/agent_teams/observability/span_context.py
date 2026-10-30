# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-facing root-span facade over shared observability state."""

from __future__ import annotations

from opentelemetry.trace import Span

from openjiuwen.core.common.logging import team_logger
from openjiuwen.extensions.observability.span_context import (
    ActiveSpanTracker,
    LlmSpanState,
    cascade_close_children,
    clear_ambient_root_span,
    clear_current_session_id,
    clear_root_span,
    close_current_agent_span,
    flush_child_spans,
    get_active_span_tracker,
    get_current_agent_span,
    get_current_llm_span,
    get_current_session_id,
    get_current_tool_span,
    get_bound_root_span,
    get_root_span,
    get_session_root_span,
    pop_any_tool_span,
    pop_current_llm_span,
    pop_tool_span,
    push_tool_span,
    reset_state,
    set_active_span_tracker,
    set_ambient_root_span,
    set_current_agent_span,
    set_current_session_id,
    set_root_span,
)


def _resolve_team_session_id(session_id: str | None = None) -> str:
    """Return the Team session a root lookup is scoped to, or "" when none is known.

    An explicit *session_id* wins; otherwise the Team runtime's own session
    ContextVar, then the observability session published around execution.
    """
    if session_id:
        return str(session_id)
    from openjiuwen.agent_teams.context import get_session_id

    return get_session_id() or get_current_session_id() or ""


def get_team_span(team_name: str | None = None, *, session_id: str | None = None) -> Span | None:
    """Resolve the Team root of the calling session, never another session's.

    A Team root is looked up by its own session only. Without a session in
    reach, only a root bound to the current execution context answers; the
    process-wide fallbacks are never consulted, because the one live root in
    the process may belong to a concurrent session of another mode (for
    example a single-agent run) and adopting it silently drops this Team's
    whole trace.

    Args:
        team_name: Historical parameter, unused; roots are keyed by session.
        session_id: Session the Team runs in; defaults to the ambient one.

    Returns:
        The session's recording Team root, or None.
    """
    del team_name
    sid = _resolve_team_session_id(session_id)
    if sid:
        return get_session_root_span(sid)
    return get_bound_root_span()


def set_team_span(span: Span, team_name: str | None = None) -> None:
    """Bind the Team root without using its display name as a session key."""
    del team_name
    set_root_span(span)


def clear_team_span() -> None:
    clear_root_span()


def get_or_create_team_span(team_name: str, tracer, *, session_id: str | None = None) -> Span | None:
    """Return the team's root span, creating and registering it when absent.

    Args:
        team_name: The team the root stands for.
        tracer: Tracer to open the root with.
        session_id: The session the root belongs to. A caller that knows it
            states it here; the context vars are only a fallback, and an
            unregistered root is invisible to a teammate running in a task
            of its own.
    """
    if not team_name:
        return None
    session_id = _resolve_team_session_id(session_id)
    # Only this session's own root is reused: a root bound in this context for
    # a different session is not ours to extend.
    span = get_session_root_span(session_id) if session_id else get_bound_root_span()
    if span is not None:
        return span

    from opentelemetry.trace import SpanKind
    from openjiuwen.extensions.observability.semconv import (
        AT_TEAM_ID,
        AT_TEAM_NAME,
        GEN_AI_CONVERSATION_ID,
        OJ_AGENT_MODE,
    )

    span = tracer.start_span(name=f"team.{team_name}", kind=SpanKind.SERVER)
    span.set_attribute(AT_TEAM_NAME, team_name)
    span.set_attribute(OJ_AGENT_MODE, "team")
    span.set_attribute(AT_TEAM_ID, team_name)
    if session_id:
        span.set_attribute(GEN_AI_CONVERSATION_ID, session_id)
    # Registered under the session, not only in this task's ContextVar: an
    # in-process teammate runs in a task of its own and looks the root up by
    # the session its callback carries. Left unregistered, that lookup finds
    # nothing and the teammate's whole round goes unrecorded.
    set_root_span(span, session_id=session_id or None)
    team_logger.info(
        "otel: get_or_create_team_span CREATE new team span team_name={} "
        "trace_id={:032x} span_id={:016x}",
        team_name,
        span.context.trace_id,
        span.context.span_id,
    )
    return span


def remove_team_span(team_name: str | None = None) -> Span | None:
    """Remove and return the Team root span without ending it."""
    del team_name
    span = get_bound_root_span()
    clear_root_span(expected_span=span)
    return span


def close_team_agent_spans(team_name: str = "") -> None:
    """Compatibility facade for closing the current agent's child spans."""
    del team_name
    close_current_agent_span()


def finalize_trace(team_name: str) -> None:
    """Close the Team root and flush only its trace's child spans."""
    from opentelemetry.trace import Status, StatusCode

    del team_name
    team_span = get_bound_root_span()
    trace_id = getattr(getattr(team_span, "context", None), "trace_id", None)
    if team_span is not None and team_span.is_recording():
        team_span.set_status(Status(StatusCode.OK))
        team_span.end()
    if team_span is not None:
        clear_root_span(expected_span=team_span)
    flush_child_spans(trace_id=trace_id)


# Preserve the historical Team names while keeping the implementation in the
# extension-owned generic state module.
set_ambient_team_span = set_ambient_root_span
clear_ambient_team_span = clear_ambient_root_span
reset_all = reset_state


__all__ = [
    "ActiveSpanTracker",
    "LlmSpanState",
    "cascade_close_children",
    "clear_ambient_team_span",
    "clear_current_session_id",
    "clear_root_span",
    "clear_team_span",
    "close_team_agent_spans",
    "finalize_trace",
    "flush_child_spans",
    "get_active_span_tracker",
    "get_current_agent_span",
    "get_current_llm_span",
    "get_current_session_id",
    "get_current_tool_span",
    "get_bound_root_span",
    "get_or_create_team_span",
    "get_root_span",
    "get_session_root_span",
    "get_team_span",
    "pop_any_tool_span",
    "pop_current_llm_span",
    "pop_tool_span",
    "push_tool_span",
    "remove_team_span",
    "reset_all",
    "set_active_span_tracker",
    "set_ambient_team_span",
    "set_current_agent_span",
    "set_current_session_id",
    "set_root_span",
    "set_team_span",
]
