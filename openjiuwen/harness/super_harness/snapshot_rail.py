# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail that bridges SuperHarness into the inner ReActAgent.

Responsibilities:
- On BEFORE_INVOKE: bind the active round's steering queue into the ctx so
  that ``ctx.drain_steering`` will pick up SuperHarness ``send(immediate=True)``
  messages at the next iteration top.
- On AFTER_REACT_ITERATION: capture a SafeStateSnapshot of the current
  context messages + DeepAgentState. If the active round's graceful_abort
  flag is set, request a force_finish so the next iteration top-of-loop
  check breaks the inner loop cleanly.

The active round is located via the ``_ACTIVE_ROUND`` ContextVar that the
supervisor sets before launching ``_run_round``; ContextVar values are
copied per asyncio.Task, so concurrent rounds in unrelated harnesses do
not leak into each other.
"""
from __future__ import annotations

from contextvars import ContextVar

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
)
from openjiuwen.harness.super_harness.state import (
    ActiveRound,
    SafeStateSnapshot,
)


# Per-task storage for the currently active round. Supervisor sets this
# before scheduling ``_run_round``; the rail reads it during ReAct hooks.
_ACTIVE_ROUND: ContextVar[ActiveRound | None] = ContextVar(
    "super_harness_active_round",
    default=None,
)


class SnapshotRail(AgentRail):
    """Bridge rail registered onto the inner ReActAgent by SuperHarness.

    Priority 1000 ensures this rail's hooks run after any other rail's
    same-event hooks, so the snapshot reflects the fully settled iteration
    state (including any context mutations performed by other rails).
    """

    priority: int = 1000

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Bind the active round's steering queue into the ReActAgent ctx.

        ReActAgent calls ``ctx.drain_steering`` at every iteration top, so
        any content pushed into the bound queue from SuperHarness's
        ``send(immediate=True)`` path will be picked up on the next
        iteration boundary.
        """
        active = _ACTIVE_ROUND.get()
        if active is None:
            return
        ctx.bind_steering_queue(active.steering_queue)

    async def after_react_iteration(self, ctx: AgentCallbackContext) -> None:
        """Capture a SafeStateSnapshot; honor graceful_abort if set.

        Two responsibilities are bundled here because they share the same
        trigger point (the end of a fully successful ReAct iteration):
        1. Snapshot capture for potential rollback by immediate abort/pause.
        2. Graceful abort trampoline: if the active round's graceful_abort
           flag is True, request force_finish so the next iteration top
           breaks the inner loop.
        """
        active = _ACTIVE_ROUND.get()
        if active is None:
            return

        session = ctx.session
        if session is None:
            logger.warning(
                "[SuperHarness.SnapshotRail] no session on ctx; "
                "skipping snapshot for round_id=%s",
                active.round_id,
            )
            return

        # ctx.agent is the inner ReActAgent here. DeepAgent (which owns
        # the session-scoped state) is reachable via the active round.
        deep_agent = active.deep_agent
        context = deep_agent.react_agent.context_engine.get_context(
            session_id=session.get_session_id(),
        )
        messages_snapshot = (
            tuple(context.get_messages()) if context is not None else tuple()
        )
        state = deep_agent.load_state(session)
        next_index = (
            active.last_safe_snapshot.iteration_index + 1
            if active.last_safe_snapshot is not None
            else 1
        )
        active.last_safe_snapshot = SafeStateSnapshot(
            context_messages=messages_snapshot,
            deep_agent_state=state.to_session_dict(),
            iteration_index=next_index,
        )
        logger.debug(
            "[SuperHarness.SnapshotRail] snapshot captured round_id=%s iteration=%s msgs=%s",
            active.round_id,
            next_index,
            len(messages_snapshot),
        )

        if active.graceful_abort:
            ctx.request_force_finish(
                {
                    "output": "<graceful_abort>",
                    "result_type": "aborted_graceful",
                },
            )
            logger.info(
                "[SuperHarness.SnapshotRail] graceful_abort honored round_id=%s",
                active.round_id,
            )
