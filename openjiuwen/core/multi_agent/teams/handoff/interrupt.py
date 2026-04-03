# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TeamInterruptSignal -- unified interrupt signal for HandoffTeam."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

from openjiuwen.core.common.logging import multi_agent_logger as logger


@dataclass
class TeamInterruptSignal:
    """Signal that pauses the handoff chain and persists state for later resumption.

    Attributes:
        result:  Interrupt payload returned to the caller (must have ``result_type='interrupt'``).
        message: Optional human-readable description of the interrupt reason.
    """
    result: Any
    message: Optional[str] = None


def extract_interrupt_signal(result=None, exc=None):
    """Extract a :class:`TeamInterruptSignal` from an agent result or exception.

    Args:
        result: Agent return value to inspect.  Recognised when
                ``result.get('result_type') == 'interrupt'``.
        exc:    Exception to inspect.  Recognised when it is an
                ``AgentInterrupt`` instance.

    Returns:
        :class:`TeamInterruptSignal` if an interrupt is detected; ``None`` otherwise.
    """
    if result is not None and isinstance(result, dict):
        if result.get("result_type") == "interrupt":
            return TeamInterruptSignal(result=result)
    if exc is not None:
        from openjiuwen.core.session.interaction.base import AgentInterrupt
        if isinstance(exc, AgentInterrupt):
            message = getattr(exc, "message", str(exc))
            return TeamInterruptSignal(
                result={"result_type": "interrupt", "message": message},
                message=message,
            )
    return None


async def flush_team_session(session):
    """Flush the team session checkpointer after an interrupt.

    Best-effort: flush failures are logged as warnings and never propagated,
    so that interrupt delivery to the caller is never blocked by storage errors.

    Args:
        session: Team session to flush.  No-op when ``None``.
    """
    if session is None:
        return
    try:
        await session.post_run()
    except Exception as exc:
        logger.warning(
            "[flush_team_session] checkpointer flush failed after interrupt; "
            "interrupt state may not be persisted: %s",
            exc,
            exc_info=True,
        )
