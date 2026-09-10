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
    OJ_REQUEST_ID,
    OJ_RUN_ID,
    OJ_SESSION_ID,
    OJ_STEP_ID,
    OJ_STEP_NUMBER,
    OJ_AGENT_MODE,
    OJ_TRAJECTORY_EVENT_ID,
    OJ_TRAJECTORY_EVENT_KIND,
    OJ_TRAJECTORY_PAYLOAD,
    OJ_TRAJECTORY_RECORDED_AT_UNIX_NANO,
    OJ_TRAJECTORY_RECORD_KIND,
    OJ_TRAJECTORY_REQUEST_ID,
    OJ_TRAJECTORY_SCHEMA_VERSION,
    OJ_TRAJECTORY_SEQUENCE_EPOCH,
    OJ_TRAJECTORY_SESSION_ID,
    OJ_TRAJECTORY_STEP_ID,
    OJ_TRAJECTORY_SUBJECT_ID,
    OJ_TRAJECTORY_SUBJECT_SEQUENCE,
    OJ_TRAJECTORY_TURN_ID,
    OJ_TRACE_SCHEMA_VERSION,
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
    consume_context_window_compaction,
    next_trajectory_subject_position,
)


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
    if not parent_span.is_recording():
        return None
    session_id = str(parent_span.attributes.get(OJ_SESSION_ID) or "")
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
        OJ_TRAJECTORY_SCHEMA_VERSION: "2",
        OJ_TRAJECTORY_EVENT_ID: event_id,
        OJ_TRAJECTORY_EVENT_KIND: event_kind,
        OJ_TRAJECTORY_SUBJECT_ID: subject_id,
        OJ_TRAJECTORY_SEQUENCE_EPOCH: resolved_epoch,
        OJ_TRAJECTORY_SUBJECT_SEQUENCE: sequence,
        OJ_TRAJECTORY_SESSION_ID: session_id,
        OJ_TRAJECTORY_RECORDED_AT_UNIX_NANO: recorded_at,
        OJ_TRAJECTORY_PAYLOAD: json.dumps(payload, ensure_ascii=False, default=str),
        OJ_TRAJECTORY_RECORD_KIND: "event",
        OJ_TRACE_SCHEMA_VERSION: "2",
    }
    for source_key, target_key in (
        (OJ_TURN_ID, OJ_TRAJECTORY_TURN_ID),
        (OJ_STEP_ID, OJ_TRAJECTORY_STEP_ID),
        (OJ_REQUEST_ID, OJ_TRAJECTORY_REQUEST_ID),
    ):
        value = parent_span.attributes.get(source_key)
        if value not in (None, ""):
            attributes[target_key] = str(value)
    for routing_key in (
        OJ_SESSION_ID,
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
    if not parent_span.is_recording():
        return False
    session_id = str(parent_span.attributes.get(OJ_SESSION_ID) or "")
    subject_id = str(parent_span.attributes.get(OJ_EXECUTION_SUBJECT_ID) or "main")
    sequence_epoch, sequence = next_trajectory_subject_position(
        session_id=session_id,
        subject_id=subject_id,
    )
    recorded_at = time.time_ns()
    attributes: dict[str, Any] = {
        OJ_TRAJECTORY_SCHEMA_VERSION: "2",
        OJ_TRAJECTORY_EVENT_ID: uuid.uuid4().hex,
        OJ_TRAJECTORY_EVENT_KIND: event_kind,
        OJ_TRAJECTORY_SUBJECT_ID: subject_id,
        OJ_TRAJECTORY_SEQUENCE_EPOCH: sequence_epoch,
        OJ_TRAJECTORY_SUBJECT_SEQUENCE: sequence,
        OJ_TRAJECTORY_SESSION_ID: session_id,
        OJ_TRAJECTORY_RECORDED_AT_UNIX_NANO: recorded_at,
        OJ_TRAJECTORY_PAYLOAD: json.dumps(payload, ensure_ascii=False, default=str),
    }
    for source_key, target_key in (
        (OJ_TURN_ID, OJ_TRAJECTORY_TURN_ID),
        (OJ_STEP_ID, OJ_TRAJECTORY_STEP_ID),
        (OJ_REQUEST_ID, OJ_TRAJECTORY_REQUEST_ID),
    ):
        value = parent_span.attributes.get(source_key)
        if value not in (None, ""):
            attributes[target_key] = str(value)
    parent_span.add_event(event_kind, attributes=attributes, timestamp=recorded_at)
    return True


def emit_context_window_commit(
    *,
    tracer: Tracer,
    llm_span: Span,
    messages: list[dict[str, Any]],
    request_purpose: str,
) -> Span | None:
    """Emit one ended context.window.commit child span."""
    session_id = str(llm_span.attributes.get(OJ_SESSION_ID) or "")
    subject_id = str(llm_span.attributes.get(OJ_EXECUTION_SUBJECT_ID) or "main")
    window_id = uuid.uuid4().hex
    sequence_epoch, sequence, base_window_id, delta, is_epoch_baseline = advance_context_window(
        session_id=session_id,
        subject_id=subject_id,
        window_id=window_id,
        messages=messages,
    )
    payload = {
        "window_id": window_id,
        "base_window_id": base_window_id,
        "complete": True,
        "messages": messages,
        "delta": delta,
        "request_purpose": request_purpose,
    }
    if is_epoch_baseline:
        payload.update({
            "transition_kind": "epoch_baseline",
            "baseline_reason": "runtime_epoch_start",
        })
    caused_by_operation_id = consume_context_window_compaction(
        session_id=session_id,
        subject_id=subject_id,
        request_id=str(llm_span.attributes.get(OJ_REQUEST_ID) or ""),
        step_id=str(llm_span.attributes.get(OJ_STEP_ID) or ""),
    )
    if caused_by_operation_id is not None:
        payload.update({
            "caused_by_operation_id": caused_by_operation_id,
            "input_window_id": base_window_id,
            "output_window_id": window_id,
        })
        if is_epoch_baseline:
            payload["correlation_kind"] = "compaction"
        else:
            payload["transition_kind"] = "compaction"
    return emit_native_trajectory_event(
        tracer=tracer,
        parent_span=llm_span,
        event_kind="context.window.commit",
        payload=payload,
        subject_sequence=sequence,
        sequence_epoch=sequence_epoch,
    )


__all__ = [
    "emit_context_window_commit",
    "emit_native_trajectory_event",
    "record_native_trajectory_log_event",
]
