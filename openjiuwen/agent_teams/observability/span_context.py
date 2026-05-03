# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-task span tracking via contextvars.

Callback events for LLM / Tool / Agent come in pairs (input/output) but
the framework does not pass identifiers that link them. We use
``contextvars.ContextVar`` to maintain per-asyncio-task stacks of open
spans so that the matching close handler can find its span.

Why per-task contextvars rather than a global dict keyed by id():
- ``contextvars`` is propagated correctly across ``asyncio.Task``s, which
  is the model the LLM/Tool callbacks run under.
- A global dict would leak across concurrent agents.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from opentelemetry.trace import Span


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

    def next_chunk_seq(self) -> int:
        """Increment and return the next chunk sequence number."""
        self.chunk_count += 1
        return self.chunk_count


# LLM spans nest (e.g. an inner agent calls back into another LLM); use a stack.
_llm_span_stack: ContextVar[list[LlmSpanState]] = ContextVar(
    "_otel_llm_span_stack",
    default=[],
)

# Tool spans are keyed by tool_name because the framework triggers
# TOOL_CALL_STARTED and TOOL_CALL_FINISHED with tool_name as the only
# correlation key. Concurrent tools with the same name in the same task
# are assumed not to occur (tool calls are sequential within an agent
# loop iteration); if that ever changes, switch to tool_id.
_tool_span_map: ContextVar[dict[str, list[Span]]] = ContextVar(
    "_otel_tool_span_map",
    default={},
)

# Agent spans, keyed by agent identifier.
_agent_span_map: ContextVar[dict[str, list[Span]]] = ContextVar(
    "_otel_agent_span_map",
    default={},
)


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


def push_agent_span(agent_id: str, span: Span) -> None:
    """Push an agent span keyed by agent_id."""
    mapping = dict(_agent_span_map.get())
    bucket = list(mapping.get(agent_id, []))
    bucket.append(span)
    mapping[agent_id] = bucket
    _agent_span_map.set(mapping)


def pop_agent_span(agent_id: str) -> Span | None:
    """Pop the most recent open agent span for agent_id, or None."""
    mapping = dict(_agent_span_map.get())
    bucket = list(mapping.get(agent_id, []))
    if not bucket:
        return None
    span = bucket.pop()
    if bucket:
        mapping[agent_id] = bucket
    else:
        mapping.pop(agent_id, None)
    _agent_span_map.set(mapping)
    return span


def reset_all() -> None:
    """Reset all per-task span trackers. Used by tests between cases."""
    _llm_span_stack.set([])
    _tool_span_map.set({})
    _agent_span_map.set({})
