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


def get_team_span(team_name: str | None = None) -> Span | None:
    """Resolve the current root through the historical Team-facing accessor."""
    del team_name
    return get_root_span()


def set_team_span(span: Span, team_name: str | None = None) -> None:
    """Bind the Team root without using its display name as a session key."""
    del team_name
    set_root_span(span)


def clear_team_span() -> None:
    clear_root_span()


def get_or_create_team_span(team_name: str, tracer) -> Span | None:
    if not team_name:
        return None
    span = get_bound_root_span()
    if span is not None:
        return span

    from opentelemetry.trace import SpanKind
    from openjiuwen.extensions.observability.semconv import (
        AT_TEAM_NAME,
        LANGFUSE_TRACE_NAME,
        LANGFUSE_TRACE_TAGS,
    )

    span = tracer.start_span(name=f"team.{team_name}", kind=SpanKind.SERVER)
    span.set_attribute(AT_TEAM_NAME, team_name)
    span.set_attribute(LANGFUSE_TRACE_NAME, f"team.{team_name}")
    span.set_attribute(LANGFUSE_TRACE_TAGS, [team_name])
    set_root_span(span)
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
