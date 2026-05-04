# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HandoffSignal -- handoff directive emitted by agents and consumed by HandoffOrchestrator."""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from typing import Any, Optional

from openjiuwen.core.common.logging import multi_agent_logger as logger

_DEFAULT_CONTEXT_ID = "default_context_id"

# Keys written by HandoffTool.invoke() and read by extract_handoff_signal.
HANDOFF_TARGET_KEY = "__handoff_to__"
HANDOFF_MESSAGE_KEY = "__handoff_message__"
HANDOFF_REASON_KEY = "__handoff_reason__"


@dataclass(frozen=True)
class HandoffSignal:
    """Immutable handoff directive produced by :func:`extract_handoff_signal`.

    Attributes:
        target:  ID of the target agent.
        message: Optional context message forwarded to the target agent.
        reason:  Optional human-readable reason for the handoff.
    """
    target: str
    message: Optional[str] = None
    reason: Optional[str] = None


def _find_handoff_payload(result: Any) -> Optional[dict]:
    """Search *result* for a dict that contains HANDOFF_TARGET_KEY."""
    if isinstance(result, dict):
        if HANDOFF_TARGET_KEY in result:
            return result
        for key in ("output", "result", "content"):
            sub = result.get(key)
            if isinstance(sub, dict) and HANDOFF_TARGET_KEY in sub:
                return sub
    return None


def extract_handoff_signal(result: Any, agent_session=None) -> Optional[HandoffSignal]:
    """Return :class:`HandoffSignal` if *result* contains a handoff directive, else ``None``.

    First checks the top-level result dict. If not found and *agent_session* is provided,
    also searches the session's message history for handoff tool results that may have been
    overwritten by subsequent LLM output.

    Args:
        result: Agent return value to inspect.
        agent_session: Optional agent session whose message history may contain handoff signals.

    Returns:
        :class:`HandoffSignal` when ``__handoff_to__`` is found; ``None`` otherwise.
    """
    payload = _find_handoff_payload(result)
    if payload is None and agent_session is not None:
        payload = _find_handoff_from_session(agent_session)
    if payload is None:
        return None
    target = payload.get(HANDOFF_TARGET_KEY)
    if not target or not isinstance(target, str):
        return None
    return HandoffSignal(
        target=target,
        message=payload.get(HANDOFF_MESSAGE_KEY) or None,
        reason=payload.get(HANDOFF_REASON_KEY) or None,
    )


def _find_handoff_from_session(agent_session) -> Optional[dict]:
    """Search agent_session message history for a handoff tool result.

    When a handoff tool is executed, the LLM may generate a follow-up response that
    overwrites the handoff signal in the final result dict. This function recovers the
    handoff signal by scanning tool messages in the session context.

    Args:
        agent_session: Agent session whose message history to search.

    Returns:
        Handoff payload dict if found, else None.
    """
    if agent_session is None:
        return None
    ctx_state = agent_session.get_state("context")
    if not ctx_state or not isinstance(ctx_state, dict):
        return None
    default_ctx = ctx_state.get(_DEFAULT_CONTEXT_ID, {})
    messages = default_ctx.get("messages", []) if isinstance(default_ctx, dict) else []
    for msg in reversed(messages):
        role = getattr(msg, "role", "")
        if role != "tool":
            continue
        content = getattr(msg, "content", "")
        if not content:
            continue
        parsed = None
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            pass
        if parsed is None:
            try:
                parsed = ast.literal_eval(content)
            except (ValueError, SyntaxError, TypeError) as exc:
                logger.debug(
                    f"[_find_handoff_from_session] ast.literal_eval failed: {exc}"
                )
                pass
        if isinstance(parsed, dict) and HANDOFF_TARGET_KEY in parsed:
            return parsed
    return None
