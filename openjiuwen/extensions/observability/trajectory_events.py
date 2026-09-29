# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable native trajectory event emission."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer, set_span_in_context

from openjiuwen.extensions.observability.semconv import (
    GEN_AI_CONVERSATION_ID,
    OJ_REQUEST_ID,
    OJ_RUN_ID,
    OJ_STEP_ID,
    OJ_STEP_NUMBER,
    OJ_AGENT_MODE,
    OJ_TRAJECTORY_EVENT_ID,
    OJ_TRAJECTORY_EVENT_KIND,
    OJ_TRAJECTORY_PAYLOAD,
    OJ_TRAJECTORY_RECORDED_AT_UNIX_NANO,
    OJ_TRAJECTORY_RECORD_KIND,
    OJ_TRAJECTORY_SCHEMA_VERSION,
    OJ_TRAJECTORY_SEQUENCE_EPOCH,
    OJ_TRAJECTORY_SUBJECT_ID,
    OJ_TRAJECTORY_SUBJECT_SEQUENCE,
    TRAJECTORY_EVENT_KINDS,
    TRAJECTORY_SPAN_SCHEMA_VERSION,
    OJ_TURN_ID,
    OJ_TURN_NUMBER,
    OJ_EXECUTION_SUBJECT_ID,
    OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
    OJ_EXECUTION_SUBJECT_KIND,
    OJ_EXECUTION_SUBJECT_PARENT_ID,
    OJ_EXECUTION_SUBJECT_SESSION_ID,
)
from openjiuwen.extensions.observability.span_context import (
    advance_context_window,
    current_context_window_messages,
    next_trajectory_subject_position,
)

# Occurrence ids of the per-request system slot a window carries at its head.
# The slot is request material, not conversation: a compaction rewrites the
# conversation and leaves the slot as it was, so the window it states keeps
# the slot from the window before it.
REQUEST_SYSTEM_SLOT_PREFIX = "openjiuwen:request-system-slot:"


def _require_known_event_kind(event_kind: str) -> None:
    # Readers treat the event-kind set as closed; an unknown kind would be
    # dropped by every one of them, so refuse it where it is written.
    if event_kind not in TRAJECTORY_EVENT_KINDS:
        raise ValueError(f"unknown trajectory event kind: {event_kind!r}")


def emit_native_trajectory_event(
    *,
    tracer: Tracer,
    parent_span: Span,
    event_kind: str,
    payload: dict[str, Any],
    subject_sequence: int | None = None,
    sequence_epoch: str | None = None,
) -> Span | None:
    """Emit one immutable v2 event using the parent's concrete owner."""
    _require_known_event_kind(event_kind)
    if not parent_span.is_recording():
        return None
    session_id = str(parent_span.attributes.get(GEN_AI_CONVERSATION_ID) or "")
    subject_id = str(parent_span.attributes.get(OJ_EXECUTION_SUBJECT_ID) or "main")
    if subject_sequence is None and sequence_epoch is None:
        resolved_epoch, sequence = next_trajectory_subject_position(
            session_id=session_id,
            subject_id=subject_id,
        )
    elif subject_sequence is not None and sequence_epoch is not None:
        resolved_epoch = sequence_epoch
        sequence = subject_sequence
    else:
        raise ValueError("subject_sequence and sequence_epoch must be provided together")
    event_id = uuid.uuid4().hex
    parent_context = set_span_in_context(parent_span, otel_context.get_current())
    span = tracer.start_span(name=event_kind, context=parent_context, kind=SpanKind.INTERNAL)
    recorded_at = time.time_ns()
    attributes: dict[str, Any] = {
        OJ_TRAJECTORY_SCHEMA_VERSION: TRAJECTORY_SPAN_SCHEMA_VERSION,
        OJ_TRAJECTORY_EVENT_ID: event_id,
        OJ_TRAJECTORY_EVENT_KIND: event_kind,
        OJ_TRAJECTORY_SUBJECT_ID: subject_id,
        OJ_TRAJECTORY_SEQUENCE_EPOCH: resolved_epoch,
        OJ_TRAJECTORY_SUBJECT_SEQUENCE: sequence,
        GEN_AI_CONVERSATION_ID: session_id,
        OJ_TRAJECTORY_RECORDED_AT_UNIX_NANO: recorded_at,
        OJ_TRAJECTORY_PAYLOAD: json.dumps(payload, ensure_ascii=False, default=str),
        OJ_TRAJECTORY_RECORD_KIND: "event",
    }
    for routing_key in (
        OJ_TURN_ID,
        OJ_STEP_ID,
        OJ_REQUEST_ID,
        OJ_RUN_ID,
        OJ_AGENT_MODE,
        OJ_TURN_NUMBER,
        OJ_STEP_NUMBER,
        OJ_EXECUTION_SUBJECT_ID,
        OJ_EXECUTION_SUBJECT_DISPLAY_NAME,
        OJ_EXECUTION_SUBJECT_KIND,
        OJ_EXECUTION_SUBJECT_PARENT_ID,
        OJ_EXECUTION_SUBJECT_SESSION_ID,
    ):
        value = parent_span.attributes.get(routing_key)
        if value not in (None, ""):
            attributes[routing_key] = value
    for key, value in attributes.items():
        span.set_attribute(key, value)
    span.set_status(Status(StatusCode.OK))
    span.end()
    return span


def record_native_trajectory_log_event(
    *,
    parent_span: Span,
    event_kind: str,
    payload: dict[str, Any],
) -> bool:
    """Record one immutable trajectory event on the current short-lived Span."""
    _require_known_event_kind(event_kind)
    if not parent_span.is_recording():
        return False
    session_id = str(parent_span.attributes.get(GEN_AI_CONVERSATION_ID) or "")
    subject_id = str(parent_span.attributes.get(OJ_EXECUTION_SUBJECT_ID) or "main")
    sequence_epoch, sequence = next_trajectory_subject_position(
        session_id=session_id,
        subject_id=subject_id,
    )
    recorded_at = time.time_ns()
    attributes: dict[str, Any] = {
        OJ_TRAJECTORY_SCHEMA_VERSION: TRAJECTORY_SPAN_SCHEMA_VERSION,
        OJ_TRAJECTORY_EVENT_ID: uuid.uuid4().hex,
        OJ_TRAJECTORY_EVENT_KIND: event_kind,
        OJ_TRAJECTORY_SUBJECT_ID: subject_id,
        OJ_TRAJECTORY_SEQUENCE_EPOCH: sequence_epoch,
        OJ_TRAJECTORY_SUBJECT_SEQUENCE: sequence,
        GEN_AI_CONVERSATION_ID: session_id,
        OJ_TRAJECTORY_RECORDED_AT_UNIX_NANO: recorded_at,
        OJ_TRAJECTORY_PAYLOAD: json.dumps(payload, ensure_ascii=False, default=str),
    }
    for correlation_key in (OJ_TURN_ID, OJ_STEP_ID, OJ_REQUEST_ID):
        value = parent_span.attributes.get(correlation_key)
        if value not in (None, ""):
            attributes[correlation_key] = str(value)
    parent_span.add_event(event_kind, attributes=attributes, timestamp=recorded_at)
    return True


def emit_context_window_commit(
    *,
    tracer: Tracer,
    llm_span: Span,
    messages: list[dict[str, Any]],
    request_purpose: str,
) -> Span | None:
    """Emit one ended context.window.commit child span.

    Only a request that carries the conversation forward advances the chain.
    A compaction asks the model to summarize the conversation, so its prompt
    is *about* the context rather than part of it; committing it would splice
    a foreign window into the chain a reader replays. The window a compaction
    produces is committed by :func:`emit_compaction_window_commit` instead.

    Returns:
        The emitted span, or None when this request does not advance the
        chain or the owning span is no longer recording.
    """
    if request_purpose == "compaction":
        # Return before advancing: the advance is what rewrites the subject's
        # canonical state, so letting a compaction reach it would both corrupt
        # the chain and make the next real turn's delta a near-full window.
        # The compaction's own prompt stays on its LLM span, and its operation
        # is already recorded by the compaction.completed event.
        return None
    session_id = str(llm_span.attributes.get(GEN_AI_CONVERSATION_ID) or "")
    subject_id = str(llm_span.attributes.get(OJ_EXECUTION_SUBJECT_ID) or "main")
    sequence_epoch, sequence, payload = _context_window_commit_payload(
        session_id=session_id,
        subject_id=subject_id,
        messages=messages,
        request_purpose=request_purpose,
    )
    return emit_native_trajectory_event(
        tracer=tracer,
        parent_span=llm_span,
        event_kind="context.window.commit",
        payload=payload,
        subject_sequence=sequence,
        sequence_epoch=sequence_epoch,
    )


def emit_compaction_window_commit(
    *,
    tracer: Tracer,
    parent_span: Span,
    messages: list[dict[str, Any]],
    operation_id: str,
    model_requests: list[dict[str, str]],
) -> Span | None:
    """Commit the context window a completed compaction produced.

    A compaction is a turn of its own: its instruction is the input, the
    summary request is its model call, and the rewritten conversation is the
    context every later turn starts from. The window therefore changes when
    the compaction completes, and the compaction states that change itself
    rather than leaving the next model call to guess which compaction it
    follows. Every later commit is a plain delta on top of this window.

    The compaction's own model call has ended by the time its result is
    known, so the commit hangs off the live agent or run span and names its
    physical request through ``model_requests`` instead, the way the
    compaction.completed event does. An empty list is a model-free
    compaction.

    Args:
        tracer: Tracer that emits the commit span.
        parent_span: Live span the commit is parented to; it names the
            session and execution subject whose window changed.
        messages: The conversation after compaction, in canonical trajectory
            form. The per-request system slot of the previous window is kept
            at its head, because a compaction rewrites the conversation and
            not the request material around it.
        operation_id: The compaction operation that caused this window.
        model_requests: ``{"request_id", "inference_id"}`` of each model call
            the compaction made.

    Returns:
        The emitted span, or None when the parent is no longer recording.
    """
    session_id = str(parent_span.attributes.get(GEN_AI_CONVERSATION_ID) or "")
    subject_id = str(parent_span.attributes.get(OJ_EXECUTION_SUBJECT_ID) or "main")
    previous = current_context_window_messages(session_id=session_id, subject_id=subject_id)
    sequence_epoch, sequence, payload = _context_window_commit_payload(
        session_id=session_id,
        subject_id=subject_id,
        messages=_compacted_window(previous or [], messages),
        request_purpose="compaction",
    )
    payload.update({
        "caused_by_operation_id": str(operation_id).strip(),
        "input_window_id": payload["base_window_id"],
        "output_window_id": payload["window_id"],
        "model_requests": list(model_requests),
    })
    if payload.get("transition_kind") == "epoch_baseline":
        payload["correlation_kind"] = "compaction"
    else:
        payload["transition_kind"] = "compaction"
    return emit_native_trajectory_event(
        tracer=tracer,
        parent_span=parent_span,
        event_kind="context.window.commit",
        payload=payload,
        subject_sequence=sequence,
        sequence_epoch=sequence_epoch,
    )


def _compacted_window(
    previous: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the window a compaction leaves behind.

    The previous window was stated from a model request's view of the
    messages; *messages* is the context engine's view of what survived. The
    two spell one message differently in places that are not content (the
    shape of tool calls, provider-normalized parts), and such a difference
    must not read as the compaction having rewritten a message it kept. A
    survivor whose role and content are unchanged is therefore restated
    exactly as the previous window had it; one whose content did change (a
    model-free processor trimming a tool result, say) is stated anew and
    reads as the replacement it is.

    The per-request system slot at the head of the previous window is kept:
    a compaction rewrites the conversation, not the request material.
    """
    previous_by_id = {
        str(message.get("message_id", "")): message
        for message in previous
    }
    window = [
        message
        for message in previous
        if str(message.get("message_id", "")).startswith(REQUEST_SYSTEM_SLOT_PREFIX)
    ]
    for message in messages:
        prior = previous_by_id.get(str(message.get("message_id", "")))
        unchanged = (
            prior is not None
            and prior.get("role") == message.get("role")
            and prior.get("content") == message.get("content")
        )
        window.append(prior if unchanged else message)
    return window


def _context_window_commit_payload(
    *,
    session_id: str,
    subject_id: str,
    messages: list[dict[str, Any]],
    request_purpose: str,
) -> tuple[str, int, dict[str, Any]]:
    """Advance one subject's window and build the commit that states it.

    Returns:
        The sequence epoch, the subject sequence, and the commit payload.
    """
    window_id = uuid.uuid4().hex
    sequence_epoch, sequence, base_window_id, delta, is_epoch_baseline = advance_context_window(
        session_id=session_id,
        subject_id=subject_id,
        window_id=window_id,
        messages=messages,
    )
    # Only a baseline carries the complete window. Every later commit is the
    # delta against the one before it, which a consumer applies onto the chain
    # it has already read. Repeating the whole window on each commit made a
    # streaming turn's storage grow with the square of its length: measured on
    # one real session, 173 commits carried 121.7 MB of windows to express
    # 0.5 MB of actual change.
    payload: dict[str, Any] = {
        "window_id": window_id,
        "base_window_id": base_window_id,
        "complete": True,
        "delta": delta,
        "request_purpose": request_purpose,
    }
    if is_epoch_baseline:
        payload.update({
            "messages": messages,
            "transition_kind": "epoch_baseline",
            "baseline_reason": "runtime_epoch_start",
        })
    return sequence_epoch, sequence, payload


__all__ = [
    "REQUEST_SYSTEM_SLOT_PREFIX",
    "emit_compaction_window_commit",
    "emit_context_window_commit",
    "emit_native_trajectory_event",
    "record_native_trajectory_log_event",
]
