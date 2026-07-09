# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Phase tracking + boundary snapshots + cooperative stop for NativeHarness.

``PhaseSnapshotRail`` does three things for the harness's pause/abort/resume
semantics:

- **Phase tracking**: maintains ``ActiveRound.iter_phase`` /
  ``model_call_in_flight`` / ``tool_started`` from the inner ReAct loop's
  model/tool callbacks, so the supervisor knows whether a hard-cancel would land
  in a parked LLM await (safe) or in a running tool (never allowed).
- **Boundary snapshots**: captures ``last_iter_snapshot`` at each inner
  iteration boundary (AFTER_REACT_ITERATION) and ``last_safe_snapshot`` at each
  outer round boundary (AFTER_TASK_ITERATION). These are the rollback targets.
- **Cooperative stop**: when a pause or a graceful abort is armed, requests a
  force-finish at a model-call boundary so the loop always stops at a clean
  iteration boundary, never mid-tool.

The active round is reached through a direct back-reference to the harness
(``harness._st.active``). A ContextVar cannot be used here: the inner loop runs
in the TaskScheduler's exec task, not in the round task that would set it.

``add_rail`` / ``register_rail`` route each hook by event — the model / tool /
react-iteration hooks bridge onto the inner ReActAgent, while
AFTER_TASK_ITERATION stays on the outer DeepAgent.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
)
from openjiuwen.agent_teams.harness.state import (
    ActiveRound,
    RoundPhase,
    SafeStateSnapshot,
)

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent
    from openjiuwen.agent_teams.harness.native_harness import NativeHarness


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

    Shared by PhaseSnapshotRail (iteration / round boundaries) and NativeHarness
    (pre-round baseline) so all snapshots have an identical shape.

    Args:
        deep_agent: Owning DeepAgent (its react_agent holds the context).
        session: Current session, used for context routing + state.
        previous: Prior snapshot whose iteration_index is incremented to label
            this one. Ignored when ``index`` is given.
        index: Explicit iteration_index. NativeHarness passes 0 for the
            pre-round baseline; PhaseSnapshotRail leaves it None to chain off
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


# Force-finish payload used by the cooperative pause / graceful-abort path.
# It ends the inner loop at a clean iteration boundary. ``_on_round_done`` routes
# the finished round by the ``ActiveRound`` flags (``pause_requested`` /
# ``graceful_abort``), so this payload only needs to be recognizable as a
# control stop (not a real answer) by ``_run_round`` so it is not written to the
# output stream.
COOPERATIVE_STOP_TYPE = "cooperative_stop"
COOPERATIVE_STOP_RESULT: dict = {"result_type": COOPERATIVE_STOP_TYPE}


class PhaseSnapshotRail(AgentRail):
    """Track inner-loop phase, snapshot iteration boundaries, and drive the
    cooperative stop for pause / graceful-abort.

    Unlike the legacy ``SnapshotRail`` (which reads the active round via the
    ``_ACTIVE_ROUND`` ContextVar — dead inside the TaskScheduler exec task where
    the loop actually runs), this rail holds a direct back-reference to the
    harness and reads ``harness._st.active``, valid from any task.

    Registered on the harness's DeepAgent via ``add_rail``: the model/tool/
    react-iteration hooks bridge onto the inner ReActAgent (BRIDGE events); the
    task-iteration hook stays on the outer DeepAgent (DEEP event).

    Responsibilities:
    - Maintain ``iter_phase`` / ``model_call_in_flight`` / ``tool_started`` so
      the supervisor picks the right pause/abort strategy (hard-cancel a parked
      model call vs. let a running tool finish).
    - Capture ``last_iter_snapshot`` at each inner boundary
      (AFTER_REACT_ITERATION) and ``last_safe_snapshot`` at each outer boundary
      (AFTER_TASK_ITERATION) — the rollback targets.
    - Cooperative stop: when ``pause_requested`` or ``graceful_abort`` is armed,
      force-finish at a model-call boundary so the loop stops cleanly, never
      interrupting a running tool. The tool-call hook deliberately does NOT
      force-finish — a started iteration must run to completion.
    """

    priority: int = 1000

    def __init__(self, harness: "NativeHarness") -> None:
        self._harness = harness

    def _active(self) -> ActiveRound | None:
        """Return the harness's live round (valid from any task, unlike the
        ContextVar the legacy rail used)."""
        return self._harness._st.active

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Enter MODEL phase; cooperative-stop before the LLM body if armed.

        This is the one stop point both verbs share: the LLM has not started, so
        stopping here leaves context exactly at the previous iteration boundary.
        For pause it realises "stop at the nearest boundary"; for graceful abort
        it realises "if the LLM has not started yet, exit now".
        """
        active = self._active()
        if active is None:
            return
        active.iter_phase = RoundPhase.MODEL
        active.model_call_in_flight = True
        if active.pause_requested or active.graceful_abort:
            # A before-hook force_finish skips the LLM body entirely — the
            # cleanest boundary (no model call, no tool, no message change).
            ctx.request_force_finish(COOPERATIVE_STOP_RESULT)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Leave MODEL phase. Only *pause* stops here; graceful abort must let a
        started iteration run its tools to completion."""
        active = self._active()
        if active is None:
            return
        active.model_call_in_flight = False
        if active.pause_requested:
            # PAUSE semantics: an LLM-phase pause discards the in-flight
            # iteration and rewinds to the previous boundary. react_agent
            # consumes this force_finish BEFORE writing the AssistantMessage or
            # running any tool, so no tool_call ever starts. This also closes
            # the hard-cancel race: if the LLM completed while the supervisor
            # was preparing its cancel, we still stop cleanly here and the late
            # cancel lands on an unwinding coroutine, harmlessly.
            #
            # graceful_abort deliberately does NOT stop here: its contract is
            # "once the LLM has started, finish the whole iteration (tools
            # included), then exit at the next model-call boundary".
            ctx.request_force_finish(COOPERATIVE_STOP_RESULT)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Enter TOOL phase. Never force-finish here: a started iteration's
        tools must run to completion (side effects are irreversible)."""
        active = self._active()
        if active is None:
            return
        active.iter_phase = RoundPhase.TOOL
        active.tool_started = True

    async def after_react_iteration(self, ctx: AgentCallbackContext) -> None:
        """Inner iteration boundary: reset phase + capture last_iter_snapshot."""
        active = self._active()
        if active is None:
            return
        active.iter_phase = RoundPhase.BOUNDARY
        active.tool_started = False
        session = ctx.session
        if session is None:
            logger.warning(
                "[PhaseSnapshotRail] no session on ctx; skipping iteration "
                "snapshot for round_id=%s",
                active.round_id,
            )
            return
        active.last_iter_snapshot = capture_snapshot(
            active.deep_agent,
            session,
            previous=active.last_iter_snapshot,
        )
        logger.debug(
            "[PhaseSnapshotRail] iteration boundary round_id=%s iteration=%s msgs=%s",
            active.round_id,
            active.last_iter_snapshot.iteration_index,
            len(active.last_iter_snapshot.context_messages),
        )

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Outer round boundary: capture last_safe_snapshot (revives the
        previously-dead ContextVar-based capture)."""
        active = self._active()
        if active is None:
            return
        session = ctx.session
        if session is None:
            return
        active.last_safe_snapshot = capture_snapshot(
            active.deep_agent,
            session,
            previous=active.last_safe_snapshot,
        )
