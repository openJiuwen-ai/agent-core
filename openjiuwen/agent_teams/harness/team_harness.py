# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamHarness: sole adapter between TeamAgent and a NativeHarness brain.

TeamHarness composes a single :class:`NativeHarness` (which IS-A DeepAgent and
drives the full task loop under one supervisor) and exposes the concurrent-safe
interaction surface (``start`` / ``stop`` / ``outputs`` / ``send`` / ``abort`` /
``pause`` / ``subscribe``) plus the team capability hooks the configurator /
coordination need. Replacing DeepAgent with a remote scheduling resource only
requires re-implementing this module; business code in ``agent_teams`` keeps the
same call surface.

Lifecycle: the underlying DeepAgent *configures itself* synchronously in its
constructor so the configurator can read workspace/sys_operation before any run
cycle. ``start(team_session)`` then
binds a child agent session (sharing the team session id, so DeepAgentState
persists) and spins up the supervisor; the same native instance is reused across
run cycles on the same session. A session switch tears the native down and
rebuilds it for the new session; cross-cycle state is recovered from the
persisted session id.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Callable,
    Optional,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.agent_teams.harness.state import HarnessState

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.build_context import BuildContext
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.single_agent.rail.base import AgentRail
    from openjiuwen.harness.deep_agent import DeepAgent
    from openjiuwen.harness.schema.config import DeepAgentConfig


class TeamHarness:
    """Sole adapter between TeamAgent and a NativeHarness-backed DeepAgent."""

    def __init__(
        self,
        agent_spec: "DeepAgentSpec",
        build_context: "BuildContext | None",
        native: NativeHarness,
        *,
        role: "TeamRole",
        member_name: Optional[str],
        initial_plan_mode: bool = False,
    ) -> None:
        self._agent_spec = agent_spec
        self._build_context = build_context
        self._native: Optional[NativeHarness] = native
        self._role = role
        self._member_name = member_name
        self._initial_plan_mode = initial_plan_mode
        self._initial_plan_mode_seeded = False
        self._active_agent_session: Optional[Any] = None
        self._native_session_id: Optional[str] = None
        self._bg_controller: Optional[Any] = None

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        agent_spec: "DeepAgentSpec",
        role: "TeamRole",
        member_name: Optional[str],
        initial_plan_mode: bool = False,
        build_context: "BuildContext | None" = None,
    ) -> "TeamHarness":
        """Construct the native via forward construction and configure it.

        The native materializes itself directly from ``agent_spec`` (no
        throwaway template). The team rails are declared in ``agent_spec.rails``
        (provider-resolved from the build context's extras), so they mount and
        initialize through the spec build + ``ensure_initialized`` path like
        every other rail — no hand-mounting here. The native configures itself
        synchronously in its constructor, so the configurator can read
        ``workspace`` / ``sys_operation`` right after construction.
        """
        native = NativeHarness(agent_spec, build_context)
        return cls(
            agent_spec,
            build_context,
            native,
            role=role,
            member_name=member_name,
            initial_plan_mode=initial_plan_mode,
        )

    # ------------------------------------------------------------------
    # Lifecycle (HarnessProtocol-aligned, one cycle per coordination.start)
    # ------------------------------------------------------------------

    async def start(self, *, team_session: Optional[Any] = None) -> None:
        """Bind a child session and start the supervisor for one run cycle.

        The native is torn down at ``stop`` (round-end) and rebuilt here for the
        next cycle because a stopped native is terminal. Cross-cycle state (task
        plan, history, plan mode) recovers from the persisted session id shared
        by the child session.
        """
        if self._native is None or self._native.state is HarnessState.TERMINATED:
            self._native = NativeHarness(self._agent_spec, self._build_context)
        if self._bg_controller is not None:
            self._native.background_task_controller = self._bg_controller
        child = self._make_child_session(team_session)
        await child.pre_run()
        await self._native.start(session=child)
        self._native_session_id = self._session_id_of(team_session)
        self._active_agent_session = child
        self._seed_initial_plan_mode(child)

    async def stop(self) -> None:
        """Stop the native supervisor; keep the (terminated) native for config reads.

        The terminated native still answers ``workspace`` / ``sys_operation`` /
        ``deep_config``; :meth:`start` rebuilds a fresh one next cycle.
        """
        if self._native is not None and self._native.state is not HarnessState.TERMINATED:
            await self._native.stop()
        self._native_session_id = None
        self._active_agent_session = None

    async def run_once(self, content: Any, *, team_session: Optional[Any] = None) -> dict[str, Any]:
        """Run one non-streaming execution; returns the ``Runner.run_agent`` dict.

        Single-shot counterpart to the streaming ``start`` / ``send`` / ``outputs``
        cycle: builds (or reuses) the native, binds a child session, and runs one
        ``NativeHarness.run_once`` (DeepAgent invoke — task loop preserved, no
        supervisor / steer). Used by swarmflow workers so each worker is a
        teammate-equivalent harness driven for exactly one execution.

        Args:
            content: The query for this execution.
            team_session: Optional team session to derive the child session from;
                when omitted a standalone child session is created.

        Returns:
            The invoke result dict (same shape as ``Runner.run_agent``).
        """
        if self._native is None or self._native.state is HarnessState.TERMINATED:
            self._native = NativeHarness(self._agent_spec, self._build_context)
        child = self._make_child_session(team_session)
        await child.pre_run()
        self._active_agent_session = child
        try:
            return await self._native.run_once(content, session=child)
        finally:
            try:
                await child.post_run()
            except Exception:
                logger.debug("[TeamHarness] post_run raised during teardown, ignoring", exc_info=True)
            self._active_agent_session = None

    async def dispose(self) -> None:
        """Permanently destroy the native and release its process-global resources.

        Called on permanent teardown (coordination stop / session discard /
        member shutdown), not on round-end :meth:`stop`: it stops the native
        (idempotent) and drops its ``sys_operation`` so a discarded
        member/session does not leak it. No-op when no native was ever built.
        """
        if self._native is not None:
            await self._native.dispose()

    @staticmethod
    def _session_id_of(team_session: Optional[Any]) -> Optional[str]:
        """Extract the session id from a team session, or None."""
        if team_session is not None and hasattr(team_session, "get_session_id"):
            return team_session.get_session_id()
        return None

    def _make_child_session(self, team_session: Optional[Any]) -> Any:
        """Create the child agent session the native runs on for this cycle.

        Derives from the team session (sharing its id, so DeepAgentState
        persists) when available; otherwise creates a standalone session.
        """
        card = self._native.card if self._native is not None else None
        if team_session is not None and hasattr(team_session, "create_agent_session"):
            return team_session.create_agent_session(card=card, share_stream_writer=False)
        from openjiuwen.core.session.agent import create_agent_session

        return create_agent_session(card=card)

    def _seed_initial_plan_mode(self, session: Any) -> None:
        """Seed the leader into plan mode on the first cycle when configured."""
        if not self._is_initial_team_plan_leader():
            return
        if self._initial_plan_mode_seeded or self._native is None:
            return
        state = self._native.load_state(session)
        if state.plan_mode.mode != "plan":
            self._native.switch_mode(session, "plan")
        self._initial_plan_mode_seeded = True

    def _is_initial_team_plan_leader(self) -> bool:
        return self._initial_plan_mode and getattr(self._role, "value", self._role) == "leader"

    # ------------------------------------------------------------------
    # Interaction surface (forwarded to the native)
    # ------------------------------------------------------------------

    @property
    def state(self) -> HarnessState:
        """Return the native's lifecycle phase, or IDLE when no cycle is live."""
        return self._native.state if self._native is not None else HarnessState.IDLE

    @property
    def session_id(self) -> Optional[str]:
        """Return the native's session id, or None when no cycle is live."""
        return self._native.session_id if self._native is not None else None

    def outputs(self) -> AsyncIterator[Any]:
        """Return the native's output chunk iterator for the current cycle."""
        if self._native is None:
            raise_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg="TeamHarness.outputs() before start().",
            )
        return self._native.outputs()

    async def send(self, content: Any, *, immediate: bool = False) -> Any:
        """Submit input to the native; ``immediate`` steers the active round."""
        if self._native is None:
            raise_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg="TeamHarness.send() before start().",
            )
        return await self._native.send(content, immediate=immediate)

    async def abort(self, *, immediate: bool = False) -> None:
        """Abort the active round: graceful (False) or hard+rollback (True).

        A no-op when no run cycle is live (the native was never started, or was
        already stopped): teardown paths (``drain_agent_task`` → ``cancel_agent``)
        reach here even when coordination started session-less, and the native
        rejects abort before ``start``.
        """
        if self._is_cycle_active():
            await self._native.abort(immediate=immediate)

    async def pause(self) -> None:
        """Pause the active round; the next send restarts it (no-op when idle)."""
        if self._is_cycle_active():
            await self._native.pause()

    def _is_cycle_active(self) -> bool:
        """Return whether a run cycle is live (native started, not yet stopped).

        ``_active_agent_session`` is bound in :meth:`start` and cleared in
        :meth:`stop`, so it is the single signal distinguishing a started native
        (which accepts abort/pause) from one that was never started or already
        torn down (which would raise in ``_require_alive``).
        """
        return self._native is not None and self._active_agent_session is not None

    async def subscribe(
        self,
        *,
        on_state: Callable[..., Any] | None = None,
        on_round: Callable[..., Any] | None = None,
    ) -> None:
        """Register phase/round callbacks on the native (no-op before start)."""
        if self._native is not None:
            await self._native.subscribe(on_state=on_state, on_round=on_round)

    # ------------------------------------------------------------------
    # Interrupt-resume helpers
    # ------------------------------------------------------------------

    def has_pending_interrupt(self) -> bool:
        """Return True if the agent has an interruption state to resume."""
        session = self._interrupt_session()
        if session is None:
            return False
        return session.get_state(INTERRUPTION_KEY) is not None

    def is_pending_interrupt_resume_valid(self, user_input: Any) -> bool:
        """Return True if ``user_input`` matches the pending interrupt requests."""
        if not isinstance(user_input, InteractiveInput):
            return False
        session = self._interrupt_session()
        if session is None:
            return False
        state = session.get_state(INTERRUPTION_KEY)
        if state is None:
            return False
        interrupted = getattr(state, "interrupted_tools", {}) or {}
        pending_ids: set = set()
        for entry in interrupted.values():
            requests = getattr(entry, "interrupt_requests", {}) or {}
            pending_ids.update(requests.keys())
        if not pending_ids:
            return False
        resume_ids = set(user_input.user_inputs.keys())
        return bool(resume_ids) and resume_ids.issubset(pending_ids)

    def _interrupt_session(self) -> Optional[Any]:
        if self._native is None:
            return None
        return self._native.loop_session or self._active_agent_session

    def init_cwd_for_round(self) -> None:
        """Initialize the per-round cwd from the workspace root."""
        workspace = self.workspace
        if workspace is None:
            return
        from openjiuwen.core.sys_operation.cwd import init_cwd

        init_root = workspace.root_path
        init_cwd(init_root, workspace=init_root)

    # ------------------------------------------------------------------
    # Config snapshots (read off the configured native's deep_config)
    # ------------------------------------------------------------------

    @property
    def deep_config(self) -> Optional["DeepAgentConfig"]:
        """Return the live DeepAgentConfig snapshot."""
        return self._native.deep_config if self._native is not None else None

    @property
    def workspace(self) -> Optional[Any]:
        """Return the workspace bound to the underlying agent, if any."""
        config = self.deep_config
        return config.workspace if config is not None else None

    @property
    def sys_operation(self) -> Optional[Any]:
        """Return the sys_operation bound to the underlying agent."""
        config = self.deep_config
        return config.sys_operation if config is not None else None

    @property
    def model(self) -> Any:
        """Return the model used by the underlying agent."""
        config = self.deep_config
        return config.model if config is not None else None

    # ------------------------------------------------------------------
    # Rail / tool registration (forwarded to the native)
    # ------------------------------------------------------------------

    def find_rails(self, rail_type: type) -> list["AgentRail"]:
        """Return rails of ``rail_type`` mounted on the underlying agent."""
        if self._native is None:
            return []
        return self._native.find_rails_by_type((rail_type,))

    async def register_rail(self, rail: "AgentRail") -> None:
        """Register an additional rail on the running agent."""
        if self._native is not None:
            await self._native.register_rail(rail)

    async def unregister_rail(self, rail: "AgentRail") -> None:
        """Unregister a previously registered rail."""
        if self._native is not None:
            await self._native.unregister_rail(rail)

    def add_tool(self, tool: Any) -> None:
        """Register one extra tool instance on the running native (idempotent per id).

        Used for per-turn tools a long-lived session needs transiently — e.g. the
        ``structured_output`` tool an ``agent_session`` turn mounts only while a
        schema is requested. The ability manager re-qualifies the id per owner, so
        concurrent sessions never collide; pair with :meth:`remove_tool` at turn end.
        """
        if self._native is not None:
            self._native.ability_manager.add_ability(tool.card, tool)

    def remove_tool(self, name: str) -> None:
        """Drop a previously :meth:`add_tool`-ed tool by its (unqualified) name."""
        if self._native is not None:
            self._native.ability_manager.remove_ability(name)

    def add_rail(self, rail: Any) -> None:
        """Queue an extra rail on the native brain (registered on next invoke/start).

        Mirrors :meth:`add_tool` for behavioural constraints — e.g. swarmflow
        mounts a rail that force-finishes a round once ``structured_output`` is
        captured. Must be called before the round/turn that should honour it
        (queued into the native's pending rails, registered at init).
        """
        if self._native is not None:
            self._native.add_rail(rail)

    def register_member_tools(self, memory_manager: Any) -> None:
        """Register the team memory toolkit on the underlying agent."""
        if self._native is not None:
            memory_manager.register_tools(self._native)

    async def inject_member_memory(self, memory_manager: Any, query: str) -> None:
        """Inject loaded memory into the agent's system prompt."""
        if self._native is not None:
            await memory_manager.load_and_inject(self._native, query=query)

    def set_background_task_controller(self, controller: Any) -> None:
        """Attach the embedder's background task controller (pause/resume surface).

        Stored on the adapter so it survives native rebuilds across run cycles
        (``start`` re-pushes it to the freshly built native); also pushed to the
        current native immediately so a controller attached after start takes
        effect without waiting for the next cycle.
        """
        self._bg_controller = controller
        if self._native is not None:
            self._native.background_task_controller = controller

    # ------------------------------------------------------------------
    # Internal access
    # ------------------------------------------------------------------

    @property
    def inner_agent(self) -> Optional["DeepAgent"]:
        """Return the underlying NativeHarness (a DeepAgent).

        Production code MUST NOT use this. It exists for tests and a few narrow
        migration helpers. Reach-throughs should be tracked and removed.
        """
        return self._native


__all__ = ["TeamHarness"]
