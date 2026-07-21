# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Atomic recovery side effects for stream suppress / flush / steering / terminate.

Prompt text for thinking-loop recovery notices and steering comes from
``robustness_prompt``.
"""
from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.agent_ras.models import Anomaly
from openjiuwen.harness.agent_ras.recovery.robustness_prompt import (
    format_steering,
    recovery_steering_on_abnormal,
    recovery_user_notice_for,
)
from openjiuwen.harness.agent_ras.recovery.state import (
    PendingRecovery,
    SuppressFlushState,
)

LLM_STREAM_TYPES = frozenset({"llm_output", "llm_reasoning"})
_USER_NOTICE_STREAM_TYPE = "retry_notification"


def truncate_chunk_payload(chunk: Any, content: str) -> None:
    """In-place truncate stream chunk payload content."""
    if isinstance(chunk, dict):
        payload = chunk.get("payload")
        if isinstance(payload, dict):
            payload["content"] = content
        return
    payload = getattr(chunk, "payload", None)
    if payload is None:
        return
    if isinstance(payload, dict):
        payload["content"] = content
    else:
        try:
            setattr(payload, "content", content)
        except Exception:
            logger.debug(
                "truncate_chunk_payload: could not set payload.content",
                exc_info=True,
            )


def truncate_chunk_on_hit(
    chunk: Any,
    chunk_text: str,
    *,
    keep_len: int | None = None,
) -> None:
    """Truncate hitting chunk: keep prefix before repeat or clear entirely."""
    if keep_len is not None and keep_len > 0:
        truncate_chunk_payload(chunk, chunk_text[:keep_len])
    else:
        truncate_chunk_payload(chunk, "")


def suppress_and_buffer(
    state: SuppressFlushState,
    chunk_type: str,
    text: str,
    chunk: Any,
) -> None:
    """Buffer suppressed text and truncate outgoing chunk payload."""
    state.record_suppressed(chunk_type, text)
    truncate_chunk_payload(chunk, "")


async def flush_suppressed_stream(
    ctx: AgentCallbackContext,
    chunk_type: str,
    content: str,
) -> None:
    """Write buffered suppressed text back to the session stream (normal path)."""
    session = getattr(ctx, "session", None)
    if session is None or not content:
        return
    try:
        from openjiuwen.core.session.stream import OutputSchema

        await session.write_stream(
            OutputSchema(
                type=chunk_type,
                index=0,
                payload={"content": content},
            )
        )
    except Exception:
        logger.warning("flush_suppressed_stream failed", exc_info=True)


async def inject_steering(ctx: AgentCallbackContext, message: str) -> None:
    """Push a self-correction steering message onto the agent context.

    Applies the standard ``<system-reminder>`` envelope via ``format_steering``.
    """
    try:
        text = format_steering(message or "")
        queue = getattr(ctx, "steering_queue", None)
        if queue is None:
            logger.warning(
                "inject_steering skipped: steering_queue is None "
                "chars=%s prefix=%r",
                len(text),
                text[:80],
            )
            return
        ctx.push_steering(text)
        logger.info(
            "inject_steering pushed chars=%s prefix=%r",
            len(text),
            text[:80],
        )
    except Exception:
        logger.error("inject_steering failed", exc_info=True)


async def emit_user_notice(ctx: AgentCallbackContext, message: str) -> None:
    """Emit a user-visible recovery / warning notice on the session stream."""
    session = getattr(ctx, "session", None)
    if session is None:
        return
    try:
        from openjiuwen.core.session.stream import OutputSchema

        notice_text = f"\n\n⚠️ {message}\n\n"
        await session.write_stream(
            OutputSchema(
                type=_USER_NOTICE_STREAM_TYPE,
                index=-1,
                payload={
                    "output": {
                        "output": notice_text,
                        "result_type": "text",
                    },
                },
            )
        )
    except Exception:
        logger.warning("emit_user_notice failed", exc_info=True)


async def emit_stream_error(ctx: AgentCallbackContext, message: str) -> None:
    """Write an error-typed stream event (critical terminate path)."""
    session = getattr(ctx, "session", None)
    if session is None:
        return
    try:
        from openjiuwen.core.session.stream import OutputSchema

        await session.write_stream(
            OutputSchema(
                type="error",
                index=0,
                payload={"error": message, "message": message},
            )
        )
    except Exception:
        logger.warning("emit_stream_error failed", exc_info=True)


async def terminate(
    ctx: AgentCallbackContext,
    message: str,
    *,
    write_error_stream: bool,
) -> None:
    """Force-finish the invoke; optionally emit an error stream event first."""
    if write_error_stream:
        await emit_stream_error(ctx, message)
    try:
        ctx.request_force_finish({"output": message, "result_type": "error"})
    except Exception:
        logger.error("terminate failed", exc_info=True)


def pending_from_anomaly(anomaly: Anomaly) -> PendingRecovery:
    """Alias for ``PendingRecovery.from_anomaly`` (executor / Monitor use)."""
    return PendingRecovery.from_anomaly(anomaly)


async def apply_recovery_normal(
    ctx: AgentCallbackContext,
    suppress: SuppressFlushState,
    pending: PendingRecovery,
    *,
    locale: str = "cn",
) -> None:
    """Normal / fail-open: flush suppressed buffers and stop suppressing."""
    _ = locale
    chunk_type = pending.chunk_type
    suppress.mark_resolved_normal()
    flushed = suppress.flush_suppressed(chunk_type)
    if flushed:
        await flush_suppressed_stream(ctx, chunk_type, flushed)


async def apply_recovery_abnormal(
    ctx: AgentCallbackContext,
    pending: PendingRecovery,
    *,
    locale: str = "cn",
) -> str:
    """Abnormal path: inject recovery steering and return notice text."""
    steering = recovery_steering_on_abnormal(pending, locale=locale)
    logger.info(
        "apply_recovery_abnormal source=%s profile=%s primary_fault=%s "
        "steering_chars=%s",
        pending.source,
        pending.recovery_profile,
        pending.extra.get("primary_fault") or "",
        len(steering),
    )
    await inject_steering(ctx, steering)
    return recovery_user_notice_for(pending, locale=locale)
