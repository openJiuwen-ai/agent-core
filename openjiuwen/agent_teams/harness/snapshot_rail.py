# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail that bridges NativeHarness into the inner ReActAgent.

On AFTER_REACT_ITERATION (fired at the end of a fully successful ReAct
iteration) this rail:
1. Captures a SafeStateSnapshot of the current-round context messages +
   DeepAgentState, for potential rollback by immediate abort / pause.
2. If the active round's graceful_abort flag is set, requests a force_finish
   so the next iteration top-of-loop check breaks the inner loop cleanly.

The active round is located via the ``_ACTIVE_ROUND`` ContextVar that
NativeHarness sets inside the round task before driving ``invoke``; ContextVar
values are copied per asyncio.Task, so concurrent rounds do not leak into
each other.

Steering injection is NOT handled here: NativeHarness passes the round's
steering queue directly through ``invoke`` inputs (``_steering_queue``), which
ReActAgent binds into the ctx natively. This rail therefore only needs the
single AFTER_REACT_ITERATION hook.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
)
from openjiuwen.agent_teams.harness.state import (
    ActiveRound,
    SafeStateSnapshot,
)

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


# Per-task storage for the currently active round. NativeHarness sets this
# inside the round task before driving invoke; the rail reads it during the
# AFTER_REACT_ITERATION hook (which runs in the same task context).
_ACTIVE_ROUND: ContextVar[ActiveRound | None] = ContextVar(
    "native_harness_active_round",
    default=None,
)


def capture_snapshot(
    deep_agent: "DeepAgent",
    session: Session,
    *,
    previous: SafeStateSnapshot | None = None,
    index: int | None = None,
) -> SafeStateSnapshot:
    """Capture current-round context messages + DeepAgentState.

    ``with_history=False`` captures only the current-round message segment —
    the same segment ``NativeHarness._rollback_to_snapshot`` restores with
    ``with_history=False``. Using the default (with_history=True) here would
    let rollback duplicate the persisted history segment on every abort/pause.

    Shared by SnapshotRail (per-iteration) and NativeHarness (pre-round
    baseline) so both produce identical snapshot shapes.

    Args:
        deep_agent: Owning DeepAgent (its react_agent holds the context).
        session: Current session, used for context routing + state.
        previous: Prior snapshot whose iteration_index is incremented to label
            this one. Ignored when ``index`` is given.
        index: Explicit iteration_index. NativeHarness passes 0 for the
            pre-round baseline; SnapshotRail leaves it None to chain off
            ``previous``.

    Returns:
        A new SafeStateSnapshot.
    """
    context = deep_agent.react_agent.context_engine.get_context(
        session_id=session.get_session_id(),
    )
    messages_snapshot = (
        tuple(context.get_messages(with_history=False))
        if context is not None
        else tuple()
    )
    state = deep_agent.load_state(session)
    if index is None:
        index = previous.iteration_index + 1 if previous is not None else 1
    return SafeStateSnapshot(
        context_messages=messages_snapshot,
        deep_agent_state=state.to_session_dict(),
        iteration_index=index,
    )


class SnapshotRail(AgentRail):
    """Bridge rail registered onto the inner ReActAgent by NativeHarness.

    Priority 1000 ensures this rail's hook runs after any other rail's
    same-event hook, so the snapshot reflects the fully settled iteration
    state (including any context mutations performed by other rails).
    """

    priority: int = 1000

    async def after_react_iteration(self, ctx: AgentCallbackContext) -> None:
        """Capture a SafeStateSnapshot; honor graceful_abort if set."""
        active = _ACTIVE_ROUND.get()
        if active is None:
            return

        session = ctx.session
        if session is None:
            logger.warning(
                "[NativeHarness.SnapshotRail] no session on ctx; "
                "skipping snapshot for round_id=%s",
                active.round_id,
            )
            return

        active.last_safe_snapshot = capture_snapshot(
            active.deep_agent,
            session,
            previous=active.last_safe_snapshot,
        )
        logger.debug(
            "[NativeHarness.SnapshotRail] snapshot captured round_id=%s iteration=%s msgs=%s",
            active.round_id,
            active.last_safe_snapshot.iteration_index,
            len(active.last_safe_snapshot.context_messages),
        )

        if active.graceful_abort:
            ctx.request_force_finish(
                {
                    "output": "<graceful_abort>",
                    "result_type": "aborted_graceful",
                },
            )
            logger.info(
                "[NativeHarness.SnapshotRail] graceful_abort honored round_id=%s",
                active.round_id,
            )
