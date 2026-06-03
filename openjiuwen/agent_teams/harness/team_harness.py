# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamHarness: sole adapter between TeamAgent and a NativeHarness brain.

TeamHarness composes a single :class:`NativeHarness` (which IS-A DeepAgent and
drives the full task loop under one supervisor) and exposes the concurrent-safe
interaction surface (``start`` / ``stop`` / ``outputs`` / ``send`` / ``abort`` /
``pause`` / ``on_state_changed`` / ``on_round``) plus the team capability hooks
the configurator / coordination need. Replacing DeepAgent with a remote
scheduling resource only requires re-implementing this module; business code in
``agent_teams`` keeps the same call surface.

Lifecycle: the underlying DeepAgent is *configured* at build time
(``native.prepare_config``) so the configurator can run an agent_customizer and
read workspace/sys_operation before any run cycle. ``start(team_session)`` then
binds a child agent session (sharing the team session id, so DeepAgentState
persists) and spins up the supervisor; the same native instance is reused across
run cycles on the same session. A session switch tears the native down and
rebuilds it for the new session (re-running the cached customizer); cross-cycle
state is recovered from the persisted session id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Callable,
    Optional,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.agent_teams.harness.state import HarnessState

if TYPE_CHECKING:
    from openjiuwen.agent_teams.rails import (
        TeamPolicyRail,
        TeamPlanModeRail,
        TeamToolApprovalRail,
        TeamToolRail,
    )
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.agent_teams.team_workspace.rails import TeamWorkspaceRail
    from openjiuwen.core.single_agent.rail.base import AgentRail
    from openjiuwen.harness.deep_agent import DeepAgent
    from openjiuwen.harness.schema.config import DeepAgentConfig


# Public type alias for the agent_customizer hook signature. Stays
# ``(DeepAgent, member_name, role_value)`` because it is a user-facing
# extension point; this alias documents that contract in one place.
AgentCustomizer = Callable[[Any, Optional[str], str], None]


@dataclass
class _MountedRails:
    """Handles to the team-side rails mounted onto the DeepAgent.

    Kept as a dataclass to make the rail lineup (and which ones are optional)
    explicit to readers and tests. Order of the fields mirrors the order rails
    are mounted in :meth:`TeamHarness.build`.
    """

    team_tool: "TeamToolRail"
    team_policy: "TeamPolicyRail"
    team_workspace: Optional["TeamWorkspaceRail"] = None
    tool_approval: Optional["TeamToolApprovalRail"] = None
    team_plan_mode: Optional["TeamPlanModeRail"] = None


class TeamHarness:
    """Sole adapter between TeamAgent and a NativeHarness-backed DeepAgent."""

    def __init__(
        self,
        deep_provider: Callable[[], "DeepAgent"],
        native: NativeHarness,
        rails: _MountedRails,
        *,
        role: "TeamRole",
        member_name: Optional[str],
        initial_plan_mode: bool = False,
    ) -> None:
        self._deep_provider = deep_provider
        self._native: Optional[NativeHarness] = native
        self._rails = rails
        self._role = role
        self._member_name = member_name
        self._initial_plan_mode = initial_plan_mode
        self._initial_plan_mode_seeded = False
        self._active_agent_session: Optional[Any] = None
        self._native_session_id: Optional[str] = None
        # Cached so a session-switch rebuild can re-run it on the new native.
        self._customizer: Optional[AgentCustomizer] = None

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
        team_tool_rail: "TeamToolRail",
        team_policy_rail: "TeamPolicyRail",
        team_workspace_rail: Optional["TeamWorkspaceRail"] = None,
        tool_approval_rail: Optional["TeamToolApprovalRail"] = None,
        team_plan_mode_rail: Optional["TeamPlanModeRail"] = None,
        initial_plan_mode: bool = False,
    ) -> "TeamHarness":
        """Build the deep-agent provider, construct the native, and configure it.

        The provider materializes a DeepAgent and mounts all team rails on every
        call (mount order load-bearing: TeamToolRail eagerly initialized before
        TeamPolicyRail so the ability snapshot the LLM sees matches what tests
        observe). ``native.prepare_config()`` runs the provider once at build
        time so the configurator can run an ``agent_customizer`` and read
        ``workspace`` / ``sys_operation`` before any run cycle.

        ``agent_customizer`` is intentionally NOT a parameter: callers run it
        via :meth:`run_agent_customizer` after constructing dependencies (e.g.,
        team memory manager) the customizer may rely on.
        """
        def _deep_provider() -> "DeepAgent":
            deep_agent = agent_spec.build()
            deep_agent.add_rail(team_tool_rail)
            deep_agent.add_rail(team_policy_rail)
            if team_workspace_rail is not None:
                deep_agent.add_rail(team_workspace_rail)
            if tool_approval_rail is not None:
                deep_agent.add_rail(tool_approval_rail)
            if team_plan_mode_rail is not None:
                deep_agent.add_rail(team_plan_mode_rail)
            return deep_agent

        rails = _MountedRails(
            team_tool=team_tool_rail,
            team_policy=team_policy_rail,
            team_workspace=team_workspace_rail,
            tool_approval=tool_approval_rail,
            team_plan_mode=team_plan_mode_rail,
        )
        native = NativeHarness(_deep_provider)
        # Sync configure so the configurator's customizer / workspace read work
        # before any async init or run cycle.
        native.prepare_config()
        # Eager-init the tool rail on the *configured native* (not the discarded
        # provider template): configure copies deep_config, not the template's
        # runtime ability registry, so the team tool snapshot must be registered
        # here to be visible before any run (mirrors the pre-adoption build).
        team_tool_rail.set_sys_operation(native.deep_config.sys_operation)
        team_tool_rail.set_workspace(native.deep_config.workspace)
        team_tool_rail.init(native)
        return cls(
            _deep_provider,
            native,
            rails,
            role=role,
            member_name=member_name,
            initial_plan_mode=initial_plan_mode,
        )

    def run_agent_customizer(self, customizer: AgentCustomizer) -> None:
        """Invoke a user-supplied customizer hook on the underlying agent.

        Runs immediately on the configured native and caches the hook so a
        session-switch rebuild can re-apply it. Swallows exceptions to keep a
        broken hook from killing team setup; failures are logged.
        """
        self._customizer = customizer
        self._apply_customizer(self._native)

    def _apply_customizer(self, native: Optional[NativeHarness]) -> None:
        """Run the cached customizer on ``native`` (no-op when either is unset)."""
        if native is None or self._customizer is None:
            return
        try:
            self._customizer(native, self._member_name, self._role.value)
        except Exception as exc:
            team_logger.warning(
                "[{}] agent_customizer failed: {}",
                self._member_name or "?",
                exc,
            )

    # ------------------------------------------------------------------
    # Lifecycle (HarnessProtocol-aligned, one cycle per coordination.start)
    # ------------------------------------------------------------------

    async def start(self, *, team_session: Optional[Any] = None) -> None:
        """Bind a child session and start the supervisor for one run cycle.

        The native is torn down at ``stop`` (round-end) and rebuilt here for the
        next cycle — re-running the cached customizer — because a stopped native
        is terminal. Cross-cycle state (task plan, history, plan mode) recovers
        from the persisted session id shared by the child session.
        """
        if self._native is None or self._native.state is HarnessState.TERMINATED:
            self._native = NativeHarness(self._deep_provider)
            self._native.prepare_config()
            self._apply_customizer(self._native)
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

    async def on_state_changed(self, callback: Callable[..., Any]) -> None:
        """Register a phase-transition callback on the native."""
        if self._native is not None:
            await self._native.on_state_changed(callback)

    async def on_round(self, callback: Callable[..., Any]) -> None:
        """Register a round-lifecycle callback on the native."""
        if self._native is not None:
            await self._native.on_round(callback)

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

    def register_member_tools(self, memory_manager: Any) -> None:
        """Register the team memory toolkit on the underlying agent."""
        if self._native is not None:
            memory_manager.register_tools(self._native)

    async def inject_member_memory(self, memory_manager: Any, query: str) -> None:
        """Inject loaded memory into the agent's system prompt."""
        if self._native is not None:
            await memory_manager.load_and_inject(self._native, query=query)

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

    @property
    def rails(self) -> _MountedRails:
        """Return handles to the team-side rails mounted on the agent."""
        return self._rails


__all__ = ["AgentCustomizer", "TeamHarness"]
