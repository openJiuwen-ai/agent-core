# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamHarness: sole adapter between TeamAgent and the underlying DeepAgent.

All construction, rail mounting, runtime calls and state queries that touch
DeepAgent flow through this class. Replacing DeepAgent with a remote /
distributed scheduling resource only requires re-implementing this module;
business code in ``agent_teams`` keeps the same call surface.
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

from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY

if TYPE_CHECKING:
    from openjiuwen.agent_teams.rails import (
        FirstIterationGate,
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
    """Handles to the team-side rails mounted onto DeepAgent.

    Kept as a dataclass to make the rail lineup (and which ones are
    optional) explicit to readers and tests. Order of the fields mirrors
    the order rails are mounted in :meth:`TeamHarness.build`.
    """

    team_tool: "TeamToolRail"
    team_policy: "TeamPolicyRail"
    first_iter_gate: Optional["FirstIterationGate"] = None
    team_workspace: Optional["TeamWorkspaceRail"] = None
    tool_approval: Optional["TeamToolApprovalRail"] = None
    team_plan_mode: Optional["TeamPlanModeRail"] = None


class TeamHarness:
    """Sole adapter between TeamAgent and the underlying DeepAgent runtime."""

    def __init__(
        self,
        deep_agent: "DeepAgent",
        rails: _MountedRails,
        *,
        role: "TeamRole",
        member_name: Optional[str],
        initial_plan_mode: bool = False,
    ) -> None:
        self._deep_agent = deep_agent
        self._rails = rails
        self._role = role
        self._member_name = member_name
        self._initial_plan_mode = initial_plan_mode
        self._initial_plan_mode_seeded = False
        self._active_agent_session: Optional[Any] = None

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
        first_iter_gate: Optional["FirstIterationGate"] = None,
        team_workspace_rail: Optional["TeamWorkspaceRail"] = None,
        tool_approval_rail: Optional["TeamToolApprovalRail"] = None,
        team_plan_mode_rail: Optional["TeamPlanModeRail"] = None,
        initial_plan_mode: bool = False,
    ) -> "TeamHarness":
        """Materialize a DeepAgent from the spec and mount all team rails.

        Mount order is load-bearing: TeamToolRail must be mounted and
        eagerly initialized before TeamPolicyRail so the ability snapshot
        the LLM sees matches the snapshot tests observe between
        ``configure()`` and the first ``invoke()``.

        ``agent_customizer`` is intentionally NOT a parameter: callers
        run it via :meth:`run_agent_customizer` after constructing
        dependencies (e.g., team memory manager) that the customizer may
        rely on.
        """
        deep_agent = agent_spec.build()

        deep_agent.add_rail(team_tool_rail)
        # Eager init: the rail's lazy init runs at first invoke, but tool
        # snapshots taken before that point would otherwise miss team
        # tools. ``init`` is idempotent so the lazy pass becomes a no-op.
        team_tool_rail.set_sys_operation(deep_agent.deep_config.sys_operation)
        team_tool_rail.set_workspace(deep_agent.deep_config.workspace)
        team_tool_rail.init(deep_agent)

        deep_agent.add_rail(team_policy_rail)

        if first_iter_gate is not None:
            deep_agent.add_rail(first_iter_gate)

        if team_workspace_rail is not None:
            deep_agent.add_rail(team_workspace_rail)

        if tool_approval_rail is not None:
            deep_agent.add_rail(tool_approval_rail)

        if team_plan_mode_rail is not None:
            deep_agent.add_rail(team_plan_mode_rail)

        rails = _MountedRails(
            team_tool=team_tool_rail,
            team_policy=team_policy_rail,
            first_iter_gate=first_iter_gate,
            team_workspace=team_workspace_rail,
            tool_approval=tool_approval_rail,
            team_plan_mode=team_plan_mode_rail,
        )
        return cls(
            deep_agent,
            rails,
            role=role,
            member_name=member_name,
            initial_plan_mode=initial_plan_mode,
        )

    def run_agent_customizer(self, customizer: AgentCustomizer) -> None:
        """Invoke a user-supplied customizer hook on the underlying agent.

        Called by the configurator after rail mount and any dependency
        wiring (memory manager, etc.) so the customizer sees a
        fully-prepared environment. Swallows exceptions to keep a
        broken hook from killing team setup; failures are logged.
        """
        try:
            customizer(self._deep_agent, self._member_name, self._role.value)
        except Exception as exc:
            team_logger.warning(
                "[{}] agent_customizer failed: {}",
                self._member_name or "?",
                exc,
            )

    # ------------------------------------------------------------------
    # State / config snapshots
    # ------------------------------------------------------------------

    @property
    def deep_config(self) -> "DeepAgentConfig":
        """Return the live DeepAgentConfig snapshot."""
        return self._deep_agent.deep_config

    @property
    def workspace(self) -> Optional[Any]:
        """Return the workspace bound to the underlying agent, if any."""
        return self._deep_agent.deep_config.workspace if self._deep_agent.deep_config else None

    @property
    def sys_operation(self) -> Optional[Any]:
        """Return the sys_operation bound to the underlying agent."""
        return self._deep_agent.deep_config.sys_operation if self._deep_agent.deep_config else None

    @property
    def model(self) -> Any:
        """Return the model used by the underlying agent."""
        return self._deep_agent.deep_config.model

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
        return self._deep_agent.loop_session or self._active_agent_session

    def init_cwd_for_round(self) -> None:
        """Initialize the per-round cwd from the workspace root.

        Wrapping ``init_cwd`` here keeps stream_controller from reaching
        into ``deep_config.workspace.root_path`` directly.
        """
        workspace = self.workspace
        if workspace is None:
            return
        from openjiuwen.core.sys_operation.cwd import init_cwd

        init_root = workspace.root_path
        init_cwd(init_root, workspace=init_root)

    # ------------------------------------------------------------------
    # Runtime call surface
    # ------------------------------------------------------------------

    async def steer(self, content: str) -> None:
        """Forward a steer instruction into the underlying agent."""
        team_logger.debug("[{}] steer: {:.120}", self._member_name or "?", content)
        await self._deep_agent.steer(content)

    async def follow_up(self, content: str) -> None:
        """Forward a follow-up message into the underlying agent."""
        team_logger.debug("[{}] follow_up: {:.120}", self._member_name or "?", content)
        await self._deep_agent.follow_up(content)

    async def abort(self) -> None:
        """Request the underlying task loop to abort cooperatively.

        The agent's coordinator sets an abort flag and runs ``on_abort``
        so the in-flight iteration exits at the next safe point. The
        outer streaming iterator then terminates naturally — no
        ``CancelledError`` is raised. Callers that need a hard deadline
        should pair this with ``asyncio.wait_for`` plus ``Task.cancel``
        as a fallback.
        """
        await self._deep_agent.abort()

    async def run_streaming(
        self,
        inputs: dict[str, Any],
        *,
        session_id: Optional[str],
        team_session: Optional[Any] = None,
    ) -> AsyncIterator[Any]:
        """Stream chunks from the underlying agent.

        Prepares the child AgentSession explicitly so team.plan can seed the
        real Leader DeepAgent into plan mode before its normal stream starts.
        The returned chunks still come from ``Runner.run_agent_streaming``.
        """
        if team_session is None and not self._initial_plan_mode:
            async for chunk in Runner.run_agent_streaming(
                self._deep_agent,
                inputs,
                session=session_id,
            ):
                yield chunk
            return

        agent_session = await self._prepare_agent_session(
            inputs=inputs,
            session_id=session_id,
            team_session=team_session,
        )
        async for chunk in Runner.run_agent_streaming(
            self._deep_agent,
            inputs,
            session=agent_session,
        ):
            yield chunk

    async def _prepare_agent_session(
        self,
        *,
        inputs: dict[str, Any],
        session_id: Optional[str],
        team_session: Optional[Any],
    ):
        card = getattr(self._deep_agent, "card", None)
        if team_session is not None and hasattr(team_session, "create_agent_session"):
            agent_session = team_session.create_agent_session(
                card=card,
                share_stream_writer=False,
            )
        else:
            from openjiuwen.core.session.agent import create_agent_session

            agent_session = create_agent_session(session_id=session_id, card=card)
        await agent_session.pre_run(inputs=inputs)
        self._active_agent_session = agent_session
        self._ensure_initial_plan_mode(agent_session)
        return agent_session

    def _ensure_initial_plan_mode(self, session: Any) -> None:
        if not self._is_initial_team_plan_leader():
            return
        if self._initial_plan_mode_seeded:
            return
        state = self._deep_agent.load_state(session)
        if state.plan_mode.mode != "plan":
            self._deep_agent.switch_mode(session, "plan")
        self._initial_plan_mode_seeded = True

    def _is_initial_team_plan_leader(self) -> bool:
        return self._initial_plan_mode and getattr(self._role, "value", self._role) == "leader"

    # ------------------------------------------------------------------
    # Rail / tool registration
    # ------------------------------------------------------------------

    def find_rails(self, rail_type: type) -> list["AgentRail"]:
        """Return rails of ``rail_type`` mounted on the underlying agent.

        Used once after construction to wire optional rails (e.g. a
        ``TeamSkillRail`` mounted by the user's ``agent_customizer``) into
        the coordination layer, instead of looking them up per event.
        """
        return self._deep_agent.find_rails_by_type((rail_type,))

    async def register_rail(self, rail: "AgentRail") -> None:
        """Register an additional rail on the running agent."""
        await self._deep_agent.register_rail(rail)

    async def unregister_rail(self, rail: "AgentRail") -> None:
        """Unregister a previously registered rail."""
        await self._deep_agent.unregister_rail(rail)

    def register_member_tools(self, memory_manager: Any) -> None:
        """Register the team memory toolkit on the underlying agent.

        Wraps ``memory_manager.register_tools(deep_agent)`` so callers
        never see the DeepAgent reference. Memory manager itself still
        receives a DeepAgent — that is a known leak slated for cleanup
        when memory_manager is refactored.
        """
        memory_manager.register_tools(self._deep_agent)

    async def inject_member_memory(self, memory_manager: Any, query: str) -> None:
        """Inject loaded memory into the agent's system prompt.

        Wraps ``memory_manager.load_and_inject(deep_agent, query=...)``
        for the same reason as :meth:`register_member_tools`.
        """
        await memory_manager.load_and_inject(self._deep_agent, query=query)

    # ------------------------------------------------------------------
    # Internal access
    # ------------------------------------------------------------------

    @property
    def inner_agent(self) -> "DeepAgent":
        """Return the underlying DeepAgent instance.

        Production code MUST NOT use this. It exists for tests and a few
        narrow migration helpers (e.g., ``setup_agent`` returning a
        DeepAgent for legacy callers). Reach-throughs should be tracked
        and removed.
        """
        return self._deep_agent

    @property
    def rails(self) -> _MountedRails:
        """Return handles to the team-side rails mounted on the agent."""
        return self._rails


__all__ = ["AgentCustomizer", "TeamHarness"]
