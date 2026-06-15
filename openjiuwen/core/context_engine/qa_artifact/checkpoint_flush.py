# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import time
from typing import Any

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.observability import write_context_trace

_FLUSH_THROTTLE_S = 0.5
_last_flush_monotonic: dict[str, float] = {}


def _resolve_checkpointer_session(session: Any) -> Any | None:
    if session is None:
        return None
    actual = getattr(session, "_parent", None) or session
    return getattr(actual, "_inner", None) or actual


async def maybe_persist_qa_memory_state(session: Any, *, qa_id: str | None = None) -> bool:
    """Lightweight checkpoint flush after async artifact production.

    Persists the full agent state (including ``__qa_memory__``) via checkpointer
    ``post_agent_execute``, throttled per session to avoid concurrent trigger storms.
    """
    inner = _resolve_checkpointer_session(session)
    if inner is None:
        return False

    session_id = ""
    if hasattr(inner, "session_id"):
        session_id = inner.session_id() or ""
    elif hasattr(session, "get_session_id"):
        session_id = session.get_session_id() or ""

    now = time.monotonic()
    last = _last_flush_monotonic.get(session_id, 0.0)
    if session_id and (now - last) < _FLUSH_THROTTLE_S:
        return False

    try:
        from openjiuwen.core.session.checkpointer import CheckpointerFactory

        await CheckpointerFactory.get_checkpointer().post_agent_execute(inner)
        if session_id:
            _last_flush_monotonic[session_id] = now
        logger.info(
            "[QAArtifactCheckpoint] flushed qa_memory session_id=%s qa_id=%s",
            session_id,
            qa_id,
        )
        write_context_trace(
            "qa_artifact.checkpoint_flush",
            {"session_id": session_id, "qa_id": qa_id},
        )
        return True
    except Exception as exc:
        logger.warning(
            "[QAArtifactCheckpoint] flush failed session_id=%s qa_id=%s error=%s",
            session_id,
            qa_id,
            exc,
        )
        return False
