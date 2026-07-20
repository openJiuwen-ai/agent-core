# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session write_stream observer for Agent RAS stream detection.

Registers on ``{session_id}write_stream`` (fired by ``Session.write_stream``
*before* the payload is written to the stream writer). Truncation by
``Monitor.on_stream_chunk`` therefore still affects what the frontend sees.

Same-session sequential rebinding is allowed (``before_invoke`` →
``before_model_call`` may use different ctx objects). Cross-session rebind
while attached is rejected.
"""
from __future__ import annotations

from typing import Any, Callable, Awaitable

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

# Visible assistant token streams scanned by thinking-loop detectors.
_LLM_STREAM_TYPES = frozenset({"llm_output", "llm_reasoning"})

OnChunk = Callable[
    [str, str, Any, AgentCallbackContext],
    Awaitable[None],
]


def _session_id_from_ctx(ctx: AgentCallbackContext) -> str | None:
    """Best-effort session id from callback ctx (session or inputs)."""
    session = getattr(ctx, "session", None)
    getter = getattr(session, "get_session_id", None)
    if callable(getter):
        try:
            sid = str(getter() or "").strip()
            if sid:
                return sid
        except Exception:
            logger.debug(
                "StreamObserver session id lookup failed",
                exc_info=True,
            )
    inputs = getattr(ctx, "inputs", None)
    cid = getattr(inputs, "conversation_id", None)
    if cid is not None:
        sid = str(cid).strip()
        if sid:
            return sid
    return None


class StreamObserver:
    """Bind one session's write_stream events to an on_chunk coroutine."""

    def __init__(self, on_chunk: OnChunk) -> None:
        self._on_chunk = on_chunk
        self._ctx: AgentCallbackContext | None = None
        self._event: str | None = None
        self._session_id: str | None = None
        self._attached = False

    def bind_ctx(self, ctx: AgentCallbackContext | None) -> bool:
        """Bind the invoke callback context forwarded to Monitor.

        Same-session rebinding is allowed so ``before_invoke`` and
        ``before_model_call`` may use different ctx objects. While attached,
        a ctx whose session_id differs from this observer's session is
        rejected.

        Returns:
            ``True`` if the binding was applied, ``False`` if rejected.
        """
        if ctx is None:
            self._ctx = None
            return True
        if self._attached and self._session_id is not None:
            ctx_sid = _session_id_from_ctx(ctx)
            if ctx_sid is not None and ctx_sid != self._session_id:
                logger.warning(
                    "StreamObserver reject bind_ctx: ctx session=%s != "
                    "observer session=%s",
                    ctx_sid,
                    self._session_id,
                )
                return False
        self._ctx = ctx
        return True

    @property
    def attached(self) -> bool:
        return self._attached

    async def attach(self, session_id: str) -> None:
        """Register the write_stream callback for ``session_id``."""
        if self._attached:
            return
        sid = str(session_id or "").strip() or "default"
        event = f"{sid}write_stream"
        from openjiuwen.core.runner import Runner

        await Runner.callback_framework.register(
            event,
            self._on_write_stream,
            priority=100,
            namespace="agent_ras",
            tags={"agent_ras_stream"},
        )
        self._event = event
        self._session_id = sid
        self._attached = True

    async def detach(self) -> None:
        """Unregister the write_stream callback if attached."""
        if not self._attached or not self._event:
            self._ctx = None
            self._session_id = None
            return
        from openjiuwen.core.runner import Runner

        try:
            await Runner.callback_framework.unregister(
                self._event, self._on_write_stream,
            )
        except Exception:
            logger.debug(
                "StreamObserver detach failed event=%s",
                self._event,
                exc_info=True,
            )
        self._event = None
        self._attached = False
        self._session_id = None
        self._ctx = None

    async def _on_write_stream(self, *, data: Any = None, **_: Any) -> None:
        """Callback Framework entry: inspect/truncate llm_* payloads."""
        ctx = self._ctx
        if ctx is None or data is None:
            return
        chunk_type, chunk_text = _extract_chunk(data)
        if chunk_type not in _LLM_STREAM_TYPES:
            return
        try:
            await self._on_chunk(chunk_type, chunk_text, data, ctx)
        except Exception:
            logger.warning(
                "StreamObserver on_chunk failed type=%s",
                chunk_type,
                exc_info=True,
            )


def _extract_chunk(data: Any) -> tuple[str, str]:
    """Return (type, content text) from an OutputSchema or dict."""
    if isinstance(data, dict):
        chunk_type = str(data.get("type") or "")
        payload = data.get("payload")
    else:
        chunk_type = str(getattr(data, "type", "") or "")
        payload = getattr(data, "payload", None)
    text = ""
    if isinstance(payload, dict):
        text = str(payload.get("content") or "")
    elif payload is not None:
        text = str(getattr(payload, "content", "") or "")
    return chunk_type, text
