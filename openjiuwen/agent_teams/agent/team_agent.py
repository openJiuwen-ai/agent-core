# coding: utf-8
"""Unified TeamAgent implementation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import traceback
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
)

from openjiuwen.agent_teams.agent.coordinator import (
    CoordinatorLoop,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.member import TeamMember
from openjiuwen.agent_teams.agent.policy import role_policy
from openjiuwen.agent_teams.agent.team_rail import TeamRail
from openjiuwen.agent_teams.messager import (
    Messager,
    create_messager,
)
from openjiuwen.agent_teams.paths import (
    independent_member_workspace,
    team_home,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import SysOperationSpec
from openjiuwen.agent_teams.schema.status import (
    ExecutionStatus,
    MemberStatus,
)
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.runner.spawn.agent_config import (
    SpawnAgentConfig,
    SpawnAgentKind,
    serialize_runner_config,
)
from openjiuwen.core.runner.spawn.process_manager import (
    SpawnConfig,
    SpawnedProcessHandle,
)
from openjiuwen.core.session.agent_team import Session as AgentTeamSession
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.prompts import resolve_language as _resolve_language


def _resolve_team_mode(spec: TeamAgentSpec) -> str:
    """Return the effective team mode for the given spec.

    Honors an explicit ``spec.team_mode``; otherwise derives from
    ``predefined_members`` for backwards compatibility.
    """
    if spec.team_mode is not None:
        return spec.team_mode
    return "predefined" if spec.predefined_members else "default"


_MAX_RETRY_ATTEMPTS = 10
_RETRYABLE_ERROR_CODES = {181001}
_RETRY_QUERY = "刚才有异常状况，继续执行"
_TASK_FAILED_PAYLOAD_TYPE = "task_failed"
_ERROR_CODE_PATTERN = re.compile(r"^\[(\d+)\]")


def _detect_task_failed(chunk: Any) -> Optional[Tuple[Optional[int], str]]:
    """Detect a TASK_FAILED chunk emitted by the task-loop executor.

    The task-loop executor wraps a BaseError into
    ``ControllerOutputChunk(payload.type='task_failed',
    data=[TextDataFrame(text='[code] message')])``. BaseError's ``__str__``
    guarantees the leading ``[code]`` prefix so the error code can be parsed
    out of the text reliably.

    Args:
        chunk: Stream chunk produced by ``Runner.run_agent_streaming``.

    Returns:
        ``(error_code, error_text)`` when the chunk is a TASK_FAILED frame;
        ``error_code`` is ``None`` if the text lacks the ``[code]`` prefix
        (treated as non-retryable). Returns ``None`` for non-error chunks.
    """
    payload = getattr(chunk, "payload", None)
    if payload is None:
        return None
    if getattr(payload, "type", None) != _TASK_FAILED_PAYLOAD_TYPE:
        return None

    text = ""
    data = getattr(payload, "data", None) or []
    if data:
        text = getattr(data[0], "text", "") or ""

    code: Optional[int] = None
    match = _ERROR_CODE_PATTERN.match(text)
    if match:
        try:
            code = int(match.group(1))
        except ValueError:
            code = None
    return code, text


class TeamAgent(BaseAgent):
    """One implementation that can act as leader or teammate.

    Uses composition: wraps an internal DeepAgent instance instead of
    inheriting from it.
    """

    def __init__(self, card):
        super().__init__(card)
        self._deep_agent: Optional[DeepAgent] = None
        self._spec: Optional[TeamAgentSpec] = None
        self._ctx: Optional[TeamRuntimeContext] = None
        self._coordination_loop: Optional[CoordinatorLoop] = None
        self._role_policy: str = ""
        self._messager: Optional[Messager] = None
        self._subscribed_topics: list[str] = []
        self._team_backend: Optional[TeamBackend] = None
        self._task_manager = None
        self._message_manager = None
        self._session_id: Optional[str] = None
        self._team_member: Optional[TeamMember] = None
        self._stream_queue: Optional[asyncio.Queue] = None
        self._agent_task: Optional[asyncio.Task] = None
        # True only while the ``async for`` over ``Runner.run_agent_streaming``
        # is actively pumping chunks — the exact window where
        # ``steer``/``follow_up`` are guaranteed to reach the task loop.
        # ``_agent_task`` alone is too loose: it stays live through pre-stream
        # status writes (READY→BUSY→STARTING→RUNNING) and the post-stream
        # finalize tail, neither of which can accept steer events.
        self._streaming_active: bool = False
        self._dispatcher = None
        self._teammate_port_counter: int = 0
        self._spawned_handles: dict[str, SpawnedProcessHandle] = {}
        # Strong refs for fire-and-forget teammate-recovery tasks. Health-check
        # callbacks are invoked synchronously from process_manager, so the
        # coroutine they schedule has no other owner — without a set here, the
        # event loop's weak reference lets the GC reap it mid-restart.
        self._recovery_tasks: set[asyncio.Task] = set()
        self._member_port_map: dict[str, int] = {}
        self._first_iter_gate: Optional["FirstIterationGate"] = None
        self._pending_interrupt_resumes: list[InteractiveInput] = []
        # Inputs queued during a pre-stream / finalize-tail window —
        # drained in the ``_run_one_round`` finally so delivery is never
        # lost to the two races where neither ``steer`` (no controller yet /
        # already torn down) nor ``_start_agent`` (would overwrite the live
        # ``_agent_task``) is safe. Public callers should go through
        # ``deliver_input`` rather than reaching for these paths directly.
        self._pending_inputs: list[Any] = []
        self._event_listeners: list = []
        self._model_allocator: Optional["ModelAllocator"] = None
        self._leader_allocation: Optional["Allocation"] = None
        self._workspace_manager: Optional["TeamWorkspaceManager"] = None
        self._workspace_initialized: bool = False
        self._worktree_manager: Optional["WorktreeManager"] = None
        self._team_session: Optional[AgentTeamSession] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def deep_agent(self) -> Optional[DeepAgent]:
        """Return the internal DeepAgent instance."""
        return self._deep_agent

    @property
    def deep_config(self) -> Optional["DeepAgentConfig"]:
        """Proxy: return the DeepAgent's config."""
        if self._deep_agent is None:
            return None
        return self._deep_agent.deep_config

    @property
    def spec(self) -> Optional[TeamAgentSpec]:
        """Return the team agent spec."""
        return self._spec

    @property
    def runtime_context(self) -> Optional[TeamRuntimeContext]:
        """Return the runtime context."""
        return self._ctx

    @property
    def coordination_loop(self) -> Optional[CoordinatorLoop]:
        """Return the shared coordination loop."""
        return self._coordination_loop

    @property
    def role(self) -> TeamRole:
        """Return the configured team role."""
        if self._ctx is None:
            return TeamRole.LEADER
        return self._ctx.role

    @property
    def lifecycle(self) -> str:
        """Return the team lifecycle mode."""
        if self._spec is None:
            return "temporary"
        return self._spec.lifecycle

    @property
    def role_prompt_policy(self) -> str:
        """Return the active base role policy."""
        return self._role_policy

    @property
    def mailbox_transport(self) -> Optional[Messager]:
        """Return the configured mailbox transport, if any."""
        return self._messager

    @property
    def team_spec(self) -> Optional[TeamSpec]:
        """Return the bound team spec."""
        if self._ctx is None:
            return None
        return self._ctx.team_spec

    @property
    def member_name(self) -> Optional[str]:
        """Return the current agent's member_name."""
        return self._member_name()

    @property
    def message_manager(self):
        """Return the message manager, if configured."""
        return self._message_manager

    @property
    def task_manager(self):
        """Return the task manager, if configured."""
        return self._task_manager

    @property
    def team_backend(self) -> Optional[TeamBackend]:
        """Return the team backend, if configured."""
        return self._team_backend

    def add_event_listener(self, handler) -> None:
        """Register an external event listener.

        Listeners receive every EventMessage from the transport,
        including self-published events, before any filtering.

        Args:
            handler: Async callable accepting an EventMessage.
        """
        self._event_listeners.append(handler)

    def remove_event_listener(self, handler) -> None:
        """Remove a previously registered event listener.

        Args:
            handler: The handler to remove.
        """
        try:
            self._event_listeners.remove(handler)
        except ValueError:
            pass

    async def has_team_member(self, member_name: str) -> bool:
        """Check whether a team member exists in the database."""
        if self._team_backend is None:
            return False
        return await self._team_backend.get_member(member_name) is not None

    def is_agent_ready(self) -> bool:
        """Whether the agent has been fully initialized."""
        return self._deep_agent is not None

    def is_agent_running(self) -> bool:
        """Whether the agent is in an active round."""
        return self._is_agent_running()

    def has_in_flight_round(self) -> bool:
        """Whether ``_agent_task`` is scheduled and not yet finalized.

        Looser than ``is_agent_running`` — returns True across the pre-stream
        status writes, the streaming window, and the finalize tail. Use this
        to decide whether ``_start_agent`` would be safe (it is not while a
        round is still in flight). For "can I steer right now?" use
        ``is_agent_running``.
        """
        return self._has_in_flight_round()

    async def deliver_input(self, content: Any, *, use_steer: bool = True) -> None:
        """Guarantee that ``content`` reaches the DeepAgent.

        Chooses the right path for the current state:
        - Streaming active → ``steer`` (or ``follow_up`` when ``use_steer``
          is False) so the content lands in the live task loop.
        - No round in flight → start a fresh round with ``content`` as the
          initial message.
        - Transition window (pre-stream or finalize tail) → append to
          ``_pending_inputs``; the ``_run_one_round`` finally drains the
          queue by launching the next round, so delivery is never dropped.
        """
        if self._streaming_active:
            if use_steer:
                await self.steer(content)
            else:
                await self.follow_up(content)
            return
        if self._has_in_flight_round():
            preview = content if isinstance(content, str) else type(content).__name__
            team_logger.info(
                "[{}] queueing input for next round (transition window): {:.60}",
                self._member_name() or "?",
                str(preview),
            )
            self._pending_inputs.append(content)
            return
        await self._start_agent(content)

    def has_pending_interrupt(self) -> bool:
        """Whether the current session still has an unresolved tool interrupt."""
        session = self._deep_agent.loop_session if self._deep_agent else None
        if session is None:
            return False
        return session.get_state(INTERRUPTION_KEY) is not None

    async def start_agent(self, content: str) -> None:
        """Start a new agent round with the given content."""
        await self._start_agent(content)

    async def follow_up(self, content: str) -> None:
        """Feed content to the currently running agent."""
        if self._deep_agent is not None:
            team_logger.debug("[{}] follow_up: {:.120}", self._member_name() or "?", content)
            await self._deep_agent.follow_up(content)

    async def cancel_agent(self) -> None:
        """Cancel the running agent task."""
        team_logger.debug("[{}] cancel_agent requested", self._member_name() or "?")
        await self._cancel_agent()

    async def destroy_team(self, force: bool = True) -> bool:
        """Destroy this team's runtime and persisted current-session state."""
        try:
            await self.cancel_agent()
        except Exception as e:
            team_logger.warning("[{}] cancel_agent during destroy failed: {}", self._member_name() or "?", e)

        try:
            await self._stop_coordination()
        except Exception as e:
            team_logger.warning("[{}] stop coordination during destroy failed: {}", self._member_name() or "?", e)

        if not self._team_backend:
            return False

        return await self._team_backend.force_clean_team(shutdown_members=force)

    async def pause_polls(self) -> None:
        """Pause periodic polling in the coordination loop."""
        if self._coordination_loop:
            await self._coordination_loop.pause_polls()

    async def resume_polls(self) -> None:
        """Resume periodic polling in the coordination loop."""
        if self._coordination_loop:
            await self._coordination_loop.resume_polls()

    async def steer(self, content: str) -> None:
        """Steer instruction into the running agent."""
        if self._deep_agent is not None:
            team_logger.debug("[{}] steer: {:.120}", self._member_name() or "?", content)
            await self._deep_agent.steer(content)

    async def resume_interrupt(self, user_input) -> None:
        """Resume a pending HITL interrupt with structured input."""
        if not self._is_valid_interrupt_resume(user_input):
            team_logger.info("[{}] dropping stale interrupt resume input", self._member_name() or "?")
            return
        # Use the task-level check here: if a round is still in flight — even
        # in the pre-stream or finalize tail — queue the resume so the finally
        # block in ``_run_one_round`` can drain it via
        # ``_dequeue_valid_interrupt_resume``. A new ``_start_agent`` right
        # now would overwrite the live ``_agent_task`` reference and orphan it.
        if self._has_in_flight_round():
            team_logger.info("[{}] queueing interrupt resume until current round completes", self._member_name() or "?")
            self._pending_interrupt_resumes.append(user_input)
            return
        await self._start_agent(user_input)

    # ------------------------------------------------------------------
    # BaseAgent abstract method: configure
    # ------------------------------------------------------------------

    def configure(self, spec: TeamAgentSpec, context: TeamRuntimeContext) -> "TeamAgent":
        """Satisfy BaseAgent.configure (sync, leader-only path)."""
        self._setup_infra(spec, context)
        self._setup_agent(spec, context)
        return self

    async def configure_team(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> "TeamAgent":
        """Configure the team agent.

        Team metadata and member roster are no longer pre-fetched
        here -- ``TeamRail`` reads them from the DB on demand via
        ``MtimeSectionCache`` so newly spawned members appear in the
        prompt without recreating the rail.
        """
        self._setup_infra(spec, ctx)
        self._setup_agent(spec, ctx)
        return self

    # ------------------------------------------------------------------
    # Team-specific configuration
    # ------------------------------------------------------------------

    def _resolve_agent_spec(
        self,
        spec: TeamAgentSpec,
        role: TeamRole,
        member_name: Optional[str] = None,
    ):
        """Return the DeepAgentSpec for the given member/role, falling back appropriately.

        Lookup order:
        1. agents[member_name] if member_name exists in agents dict (custom per-member spec)
        2. agents[role.value] ("teammate" for teammates)
        3. agents["leader"] as final fallback
        """
        if member_name and member_name in spec.agents:
            return spec.agents[member_name]
        return spec.agents.get(role.value) or spec.agents.get("teammate") or spec.agents["leader"]

    def update_model_pool(self, new_pool: "list[ModelPoolEntry]") -> None:
        """Replace the team's model pool and reset the allocator.

        Use this when credentials rotate, endpoints migrate, or pool
        composition changes during a running session. Existing members
        keep working: their next resolution reads the new pool, picks
        up refreshed credentials in-place, and re-resolves positionally
        if their group composition changed. The allocator counters
        reset so subsequent ``allocate`` calls start fresh against the
        new layout.

        Runtime ``model_id`` values are carried over from the current
        pool to matching new entries (matched by
        ``(model_name, api_base_url)`` and position within that
        bucket) so the foundation's HTTP client cache stays warm
        across credential rotations. Truly new endpoints keep their
        own auto-generated id.

        Persists the new pool + zeroed allocator state to the team
        session so a subsequent ``recover_from_session`` rehydrates the
        same view.

        Args:
            new_pool: Replacement pool entries. Empty list disables the
                allocator entirely (members fall back to per-agent specs).
        """
        if self._ctx is None or self._ctx.team_spec is None:
            return
        from openjiuwen.agent_teams.agent.model_allocator import build_model_allocator
        from openjiuwen.agent_teams.schema.team import inherit_pool_ids

        merged = inherit_pool_ids(self._ctx.team_spec.model_pool, list(new_pool))
        self._ctx.team_spec.model_pool = merged
        self._model_allocator = build_model_allocator(self._spec, self._ctx.team_spec)
        if self._team_session is not None and self._spec is not None and self.role == TeamRole.LEADER:
            self._persist_leader_config(self._team_session)

    def attach_model_allocator(
        self,
        allocator: "ModelAllocator",
        *,
        leader_allocation: Optional["Allocation"] = None,
    ) -> None:
        """Pre-attach a model allocator built outside ``configure``.

        ``TeamAgentSpec.build()`` constructs the allocator before runtime
        context assembly so it can pre-allocate the leader's model from
        the pool. Calling this before ``configure`` lets ``_setup_infra``
        reuse the same rotation state instead of constructing a fresh
        instance and double-counting allocations.

        Args:
            allocator: Already-built allocator instance to reuse.
            leader_allocation: Pre-allocated leader assignment. The DB
                ref portion is forwarded to ``TeamBackend`` and persisted
                on the leader's row so full-restart recovery resolves
                the leader's model the same way as teammates.
        """
        self._model_allocator = allocator
        self._leader_allocation = leader_allocation

    def restore_allocator_state(self, state: dict) -> None:
        """Restore model allocator counters from a persisted state dict.

        No-op when no allocator is attached (e.g. teammate role).
        """
        if self._model_allocator is not None:
            self._model_allocator.load_state_dict(state)

    def _setup_infra(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> None:
        """Phase 1: set spec/context, create messager, workspace manager, register team tools."""
        self._spec = spec
        self._ctx = ctx

        messager_config = ctx.messager_config
        member_name = ctx.member_name
        if member_name and messager_config and messager_config.node_id != member_name:
            messager_config = messager_config.model_copy(update={"node_id": member_name})

        self._messager = create_messager(messager_config) if messager_config else None

        # Team shared workspace — create manager and ensure directory exists.
        if spec.workspace and spec.workspace.enabled:
            self._workspace_manager = self._create_workspace_manager(spec, ctx)

        # Build the allocator only when build() didn't pre-attach one.
        # The pre-attached path keeps a single rotation state across
        # leader pre-allocation (in build()) and teammate spawns; the
        # fallback covers direct ``configure()`` callers (recovery,
        # tests) that bypass build().
        if ctx.role == TeamRole.LEADER and self._model_allocator is None:
            from openjiuwen.agent_teams.agent.model_allocator import (
                build_model_allocator,
            )

            self._model_allocator = build_model_allocator(spec, ctx.team_spec)

        self._tool_cards = self._register_team_tools(spec, ctx, self._messager)

    def _create_workspace_manager(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
    ) -> "TeamWorkspaceManager":
        """Create TeamWorkspaceManager and ensure the workspace directory exists.

        Args:
            spec: Team agent specification containing workspace config.
            ctx: Runtime context for resolving team_name.

        Returns:
            Configured TeamWorkspaceManager instance.
        """
        from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager

        ws_config = spec.workspace
        team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
        ws_path = ws_config.root_path or str(team_home(team_name) / "team-workspace")
        os.makedirs(ws_path, exist_ok=True)
        team_logger.info("Team workspace directory ensured at {}", ws_path)
        return TeamWorkspaceManager(
            config=ws_config,
            workspace_path=ws_path,
            team_name=team_name,
        )

    def _create_worktree_manager(self, spec: TeamAgentSpec) -> "WorktreeManager":
        """Create WorktreeManager for worktree isolation.

        Args:
            spec: Team agent specification containing worktree config.

        Returns:
            Configured WorktreeManager instance.
        """
        from openjiuwen.agent_teams.worktree.manager import WorktreeManager

        ws_root = self._workspace_manager.workspace_path if self._workspace_manager else None
        return WorktreeManager(
            config=spec.worktree,
            workspace_root=ws_root,
        )

    def _setup_agent(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
    ) -> None:
        """Phase 2: build prompt, create DeepAgent, set up coordination."""
        # Lookup agent spec by member_name first, then role fallback chain
        agent_spec = self._resolve_agent_spec(spec, ctx.role, ctx.member_name)
        resolved_language = _resolve_language(agent_spec.language)
        self._role_policy = role_policy(ctx.role, language=resolved_language)
        member_name = ctx.member_name

        # Resolve workspace: fallback to leader's, adjust stable_base path.
        # Stable workspace lives under
        # ``team_home(team_name)/workspaces/{member_name}_workspace``.
        #
        # If the member is a predefined independent DeepAgent whose workspace
        # already exists at ``independent_member_workspace(member_name)``,
        # create a symlink instead of a new directory so the agent keeps
        # its identity.
        ws_spec = agent_spec.workspace or spec.agents.get("leader", agent_spec).workspace
        if ws_spec and ws_spec.stable_base:
            team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
            base = team_home(team_name) / "workspaces"
            team_ws_path = base / f"{member_name}_workspace"
            independent_ws = independent_member_workspace(member_name)
            if independent_ws.is_dir() and not team_ws_path.exists():
                base.mkdir(parents=True, exist_ok=True)
                os.symlink(str(independent_ws), str(team_ws_path), target_is_directory=True)
            ws_spec = ws_spec.model_copy(update={"root_path": str(team_ws_path)})

        # Record the resolved workspace path so clean_team can remove it.
        if ws_spec and ws_spec.root_path and self._team_backend:
            self._team_backend.register_cleanup_path(ws_spec.root_path)

        # Pre-mount the team shared workspace into the agent workspace
        # BEFORE building the DeepAgent, so the factory's SkillUseRail
        # can aggregate ``.team/{team_name}/skills`` via
        # ``Workspace.list_team_links``.
        if self._workspace_manager and ws_spec and ws_spec.root_path:
            self._workspace_manager.mount_into_workspace(ws_spec.root_path)

        # Resolve model: member_model (allocated by leader) takes priority.
        model_config = ctx.member_model or agent_spec.model

        # Merge tools: team management tools + user-defined spec tools.
        merged_tools = list(self._tool_cards)
        if agent_spec.tools:
            merged_tools.extend(agent_spec.tools)

        # Build DeepAgent via DeepAgentSpec.build() with team overrides.
        # ``system_prompt`` is intentionally left untouched so DeepAgent's
        # default identity section stays in place; team-specific content
        # is injected later by ``TeamRail`` as discrete PromptSections.
        sys_operation_spec = agent_spec.sys_operation or SysOperationSpec(
            id=f"{self.card.id}.sys_operation",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(shell_allowlist=None),
        )
        build_spec = agent_spec.model_copy(
            update={
                "card": self.card,
                "model": model_config,
                "workspace": ws_spec,
                "sys_operation": sys_operation_spec,
                "tools": merged_tools,
                "enable_skill_discovery": True,
                "enable_task_loop": True,
            }
        )
        self._deep_agent = build_spec.build()

        # Decompose team policy into ordered PromptSections via TeamRail.
        team_workspace_mount: str | None = None
        team_workspace_path: str | None = None
        if self._workspace_manager:
            resolved_team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
            team_workspace_mount = f".team/{resolved_team_name}/"
            team_workspace_path = self._workspace_manager.workspace_path

        self._deep_agent.add_rail(
            TeamRail(
                role=ctx.role,
                persona=ctx.persona,
                member_name=member_name,
                lifecycle=spec.lifecycle,
                teammate_mode=spec.teammate_mode,
                language=resolved_language,
                team_mode=_resolve_team_mode(spec),
                base_prompt=agent_spec.system_prompt,
                team_workspace_mount=team_workspace_mount,
                team_workspace_path=team_workspace_path,
                team_backend=self._team_backend,
            )
        )

        from openjiuwen.agent_teams.agent.rails import FirstIterationGate

        self._first_iter_gate = FirstIterationGate()
        self._deep_agent.add_rail(self._first_iter_gate)

        # Register the transparent version-control rail. The ``.team/``
        # symlink is mounted earlier (pre-build) so SkillUseRail can
        # discover team-shared skills.
        if self._workspace_manager:
            from openjiuwen.agent_teams.team_workspace.rails import TeamWorkspaceRail

            self._deep_agent.add_rail(
                TeamWorkspaceRail(self._workspace_manager, member_name or ""),
            )

        is_coordinated_teammate = ctx.role == TeamRole.TEAMMATE and ctx.team_spec
        if is_coordinated_teammate and self._team_backend and self._messager:
            from openjiuwen.agent_teams.agent.rails import TeamToolApprovalRail

            approval_tools = agent_spec.approval_required_tools or []
            if approval_tools:
                self._deep_agent.add_rail(
                    TeamToolApprovalRail(
                        team_name=ctx.team_spec.team_name,
                        member_name=member_name or "",
                        db=self._team_backend.db,
                        messager=self._messager,
                        leader_member_name=ctx.team_spec.leader_member_name or "",
                        tool_names=approval_tools,
                    )
                )

        # Platform customizer: inject additional rails & tools (e.g. Claw adapter).
        if spec.agent_customizer and self._deep_agent:
            try:
                spec.agent_customizer(self._deep_agent, member_name, ctx.role.value)
            except Exception as exc:
                team_logger.warning(
                    "[{}] agent_customizer failed: {}",
                    self._member_name() or "?",
                    exc,
                )

        # Teammate: member already exists in DB, create TeamMember now.
        # Leader: TeamMember is created in _on_teammate_created callback.
        if ctx.role == TeamRole.TEAMMATE and member_name and self._team_backend:
            self._team_member = TeamMember(
                member_name=member_name,
                team_name=self._team_backend.team_name,
                agent_card=self.card,
                db=self._team_backend.db,
                messager=self._messager,
                desc=ctx.persona,
            )

        from openjiuwen.agent_teams.agent.dispatcher import EventDispatcher

        self._dispatcher = EventDispatcher(self)
        self._coordination_loop = CoordinatorLoop(
            role=ctx.role,
            wake_callback=self._dispatcher.dispatch,
        )

    # ------------------------------------------------------------------
    # Role-based tool registration
    # ------------------------------------------------------------------

    def _register_team_tools(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
        messager: Messager,
    ) -> List[ToolCard]:
        """Register role-appropriate team tools driven by permission sets."""
        from openjiuwen.agent_teams.schema.status import MemberMode
        from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db
        from openjiuwen.agent_teams.tools.team_tools import create_team_tools

        team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or "default"
        db = get_shared_db(ctx.db_config)

        is_leader = ctx.role == TeamRole.LEADER
        current_member_name = ctx.member_name or (ctx.team_spec.leader_member_name if ctx.team_spec else "")

        agent_team = TeamBackend(
            team_name=team_name,
            member_name=current_member_name,
            is_leader=is_leader,
            db=db,
            messager=messager,
            teammate_mode=MemberMode(spec.teammate_mode),
            predefined_members=spec.predefined_members or None,
            model_config_allocator=self._model_allocator.allocate if self._model_allocator else None,
            leader_allocation=self._leader_allocation if is_leader else None,
        )
        self._team_backend = agent_team
        self._task_manager = agent_team.task_manager
        self._message_manager = agent_team.message_manager

        # Record the team shared workspace path (possibly user-customized)
        # so clean_team removes the real directory, not the default one.
        if self._workspace_manager:
            agent_team.register_cleanup_path(self._workspace_manager.workspace_path)

        # Record the team-named parent directory (``team_home``) that
        # this module uses as the root for ``stable_base`` member
        # workspaces (see ``_setup_agent``) and the default team shared
        # workspace (see ``_create_workspace_manager``).  Registering it
        # also catches teammate workspace dirs the leader never saw
        # (teammates run in separate processes).
        agent_team.register_cleanup_path(str(team_home(team_name)))

        exclude = {"spawn_member"} if _resolve_team_mode(spec) == "predefined" else None
        lang = _resolve_language(ctx.team_spec.language if ctx.team_spec else None)
        team_tools = create_team_tools(
            role=ctx.role.value,
            agent_team=agent_team,
            teammate_mode=spec.teammate_mode,
            on_teammate_created=self._on_teammate_created,
            model_config_allocator=self._model_allocator.allocate if self._model_allocator else None,
            exclude_tools=exclude,
            lang=lang,
        )
        # Workspace metadata tool (lock management, version history).
        if self._workspace_manager:
            from openjiuwen.agent_teams.team_workspace.tools import WorkspaceMetaTool
            from openjiuwen.agent_teams.tools.locales import make_translator

            ws_t = make_translator(lang)
            team_tools.append(WorkspaceMetaTool(self._workspace_manager, ws_t))

        # Worktree isolation tools — teammate only.
        if not is_leader and spec.worktree and spec.worktree.enabled:
            from openjiuwen.agent_teams.tools.locales import make_translator
            from openjiuwen.agent_teams.worktree.tools import EnterWorktreeTool, ExitWorktreeTool

            self._worktree_manager = self._create_worktree_manager(spec)
            wt_t = make_translator(lang)
            team_tools.append(EnterWorktreeTool(self._worktree_manager, wt_t))
            team_tools.append(ExitWorktreeTool(self._worktree_manager, wt_t))
            # Eagerly create session state holder so asyncio.gather
            # tool calls share the same mutable object.
            from openjiuwen.agent_teams.worktree.session import init_session_state

            init_session_state()

        # Only in-process teammates share one global resource manager.
        if spec.spawn_mode == "inprocess":
            self._qualify_team_tool_ids(team_tools, team_name=team_name, member_name=current_member_name)

        # Best-effort registration with Runner's
        # resource manager.  When Runner has not been
        # bootstrapped (e.g. unit tests) we skip
        # silently -- the cards are still in
        # ability_manager for schema generation.
        try:
            Runner.resource_mgr.add_tool(team_tools)
        except Exception:
            team_logger.debug("Runner.resource_mgr not available, skipping tool registration")

        return [t.card for t in team_tools]

    @staticmethod
    def _qualify_team_tool_ids(team_tools: list[Tool], *, team_name: str, member_name: str) -> None:
        """Qualify team tool ids so each member gets distinct resource ids.

        Team tools share public names such as ``send_message`` across leader and
        teammates, but only in-process mode shares one global
        ``Runner.resource_mgr``. If the ids stay as ``team.send_message`` and
        similar, members can resolve another agent's tool instance. Qualifying
        ids keeps model-facing tool names stable while isolating the backing
        instances.
        """
        team_key = team_name or "default"
        member_key = member_name or "unknown"
        for tool in team_tools:
            if tool.card is None or not tool.card.id:
                continue
            qualified_id = f"{tool.card.id}.{team_key}.{member_key}"
            if tool.card.id != qualified_id:
                tool.card.id = qualified_id

    # ------------------------------------------------------------------
    # BaseAgent abstract methods: invoke / stream
    # ------------------------------------------------------------------

    async def invoke(self, inputs, session=None):
        """Execute via CoordinatorLoop-driven rounds.

        Feeds initial query as USER_INPUT event, collects
        all chunks, returns the last result.
        """
        team_logger.info("[{}] invoke start, role={}", self._member_name() or "?", self.role.value)
        self._stream_queue = asyncio.Queue()
        await self._start_coordination(session)
        try:
            await self._enqueue_user_input(inputs)
            await self._enqueue_mailbox_after_first_iteration()
            last_result = None
            while True:
                chunk = await self._stream_queue.get()
                if chunk is None:
                    break
                last_result = chunk
            return last_result
        finally:
            await self._finalize_round()

    async def broadcast(self, content: str) -> Optional[str]:
        """Post a user-originated broadcast to the whole team.

        Leader wakes up as a broadcast recipient like any other member;
        teammates see the announcement through their mailbox sweep. The
        sender is the "user" pseudo-member so recipients can distinguish
        external directives from leader-issued broadcasts.

        Returns the broadcast message id on success.
        """
        from openjiuwen.agent_teams.interaction import UserInbox

        if self._team_backend is None:
            raise RuntimeError("TeamAgent.broadcast requires a configured team backend")
        return await UserInbox(self._team_backend.message_manager).broadcast(content)

    async def human_agent_say(
        self,
        content: str,
        to: Optional[str] = None,
        *,
        sender: Optional[str] = None,
    ) -> Optional[str]:
        """Speak as a registered human-agent member.

        Available only when HITT is enabled on this team. Maps to a
        ``send_message`` (point-to-point when ``to`` is provided, a
        broadcast otherwise) so the human collaborator's speech flows
        through the same message bus as teammate traffic.

        Args:
            content: Message body.
            to: Optional recipient member name; None broadcasts.
            sender: Optional speaker name. Omit on single-human teams
                to use the default; required when the team declares
                multiple human-agent members. Raises
                ``UnknownHumanAgentError`` if ``sender`` is not a
                registered human-agent member.
        """
        from openjiuwen.agent_teams.interaction import HumanAgentInbox

        if self._team_backend is None:
            raise RuntimeError("TeamAgent.human_agent_say requires a configured team backend")
        return await HumanAgentInbox(
            self._team_backend,
            self._team_backend.message_manager,
        ).send(content, to=to, sender=sender)

    async def stream(self, inputs, session=None, stream_modes=None):
        """Stream via CoordinatorLoop-driven rounds.

        Feeds initial query as USER_INPUT event, yields
        chunks from unified queue until sentinel (None).
        """
        team_logger.info("[{}] stream start, role={}", self._member_name() or "?", self.role.value)
        self._stream_queue = asyncio.Queue()
        await self._start_coordination(session)
        try:
            await self._enqueue_user_input(inputs)
            await self._enqueue_mailbox_after_first_iteration()
            while True:
                chunk = await self._stream_queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            await self._finalize_round()

    async def _finalize_round(self) -> None:
        """Tear down a finished invoke/stream round.

        Routes by (lifecycle, shutdown_requested):
        * shutdown requested → fully stop coordination and transition the
          member to SHUTDOWN, regardless of lifecycle. SHUTDOWN_REQUESTED
          can only legally transition to SHUTDOWN/ERROR, so the persistent
          path must NOT push the member back to READY here.
        * persistent + no shutdown → pause coordination, mark READY.
        * temporary → fully stop coordination, mark SHUTDOWN.
        """
        shutdown_requested = (
            self._team_member is not None and await self._team_member.status() == MemberStatus.SHUTDOWN_REQUESTED
        )
        if self.lifecycle == "persistent" and not shutdown_requested:
            await self._pause_coordination()
            if self._team_member:
                await self._team_member.update_status(MemberStatus.READY)
        else:
            await self._stop_coordination()
            if self._team_member:
                await self._team_member.update_status(MemberStatus.SHUTDOWN)
        self._stream_queue = None

    async def interact(self, message: str) -> None:
        """Inject user input into CoordinatorLoop as USER_INPUT event."""
        if self._coordination_loop is None:
            return
        await self._coordination_loop.enqueue(
            InnerEventMessage(
                event_type=InnerEventType.USER_INPUT,
                payload={"content": message},
            )
        )

    # ------------------------------------------------------------------
    # Coordination lifecycle helpers
    # ------------------------------------------------------------------

    async def _start_coordination(
        self,
        session=None,
    ) -> None:
        """Start the coordination loop."""
        if self._coordination_loop is None:
            return
        member_name = self._member_name() or "?"
        team_logger.info("[{}] coordination starting", member_name)
        self._session_id = session.get_session_id() if session else None
        if self._session_id:
            from openjiuwen.agent_teams.spawn.context import set_session_id

            set_session_id(self._session_id)
        from openjiuwen.core.common.logging.utils import set_member_id

        set_member_id(member_name)
        if session is not None:
            if isinstance(session, AgentTeamSession):
                self._team_session = session
            else:
                team_logger.warning(
                    "[{}] TeamAgent expects AgentTeamSession; got {}. "
                    "Please invoke via Runner.run_agent_team_streaming.",
                    member_name,
                    type(session).__name__,
                )
        # Persist leader config to session for full-restart recovery
        if session and self._spec and self.role == TeamRole.LEADER:
            self._persist_leader_config(session)
        # Eagerly initialize the DB so TeamRail's before_model_call probe
        # functions never hit an uninitialized database.  Must run after
        # set_session_id() so create_cur_session_tables uses the right names.
        if self._team_backend:
            await self._team_backend.db.initialize()
            await self._team_backend.db.create_cur_session_tables()

        # Leader-startup recovery: if the team is already persisted in the
        # DB (e.g. a previous run with the same team_name), decide between
        # finalizing a stalled cleanup and re-launching all teammates.
        # On a fresh team build, get_team() returns None and this whole
        # block is a no-op until build_team runs.
        if self.role == TeamRole.LEADER and self._team_backend:
            existing = await self._team_backend.db.get_team(self._team_backend.team_name)
            if existing is not None:
                non_leader_members = await self._team_backend.list_members()
                # Stale shutdown: every teammate is already SHUTDOWN, which
                # only happens when a previous run finished shutdown_member
                # for everyone but never reached clean_team. Finalize the
                # cleanup ourselves so the leader doesn't restart corpses
                # and so the same team_name can be re-built cleanly.
                if non_leader_members and all(m.status == MemberStatus.SHUTDOWN.value for m in non_leader_members):
                    team_logger.warning(
                        "[{}] team {} found with all teammates in SHUTDOWN — finalizing prior incomplete cleanup",
                        self._member_name() or "?",
                        self._team_backend.team_name,
                    )
                    await self._team_backend.clean_team()
                else:
                    await self.recover_team()

        # Async workspace initialization (git init + artifact dirs), idempotent.
        if self._workspace_manager and not self._workspace_initialized:
            await self._workspace_manager.initialize(
                remote_url=self._spec.workspace.remote_url if self._spec and self._spec.workspace else None,
            )
            self._workspace_initialized = True

        await self._update_status(MemberStatus.READY)
        if not self._coordination_loop.is_running:
            await self._coordination_loop.start()
        if self._messager:
            team_name = self._team_name()
            if team_name and not self._subscribed_topics:
                await self._subscribe_transport(team_name)

    async def _enqueue_mailbox_after_first_iteration(self) -> None:
        """Wait for agent's first iteration, then enqueue POLL_MAILBOX.

        Skipped for leader — leader has no pre-existing unread messages at startup.
        """
        if self.role == TeamRole.LEADER:
            return
        if self._first_iter_gate is None or self._coordination_loop is None:
            return
        await self._first_iter_gate.wait()
        await self._coordination_loop.enqueue(
            InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX),
        )

    async def _enqueue_user_input(self, inputs: Any) -> None:
        """Extract query from inputs and enqueue as USER_INPUT event."""
        query = inputs.get("query", "") if isinstance(inputs, dict) else inputs
        if self._coordination_loop is None:
            return
        await self._coordination_loop.enqueue(
            InnerEventMessage(
                event_type=InnerEventType.USER_INPUT,
                payload={"content": query},
            )
        )

    async def _drain_agent_task(self) -> None:
        """Cancel the in-flight agent round and wait for it to exit.

        Called from the pause/stop paths so an agent task whose stream
        consumer has already detached (early ``break`` / ``GeneratorExit``)
        cannot keep producing chunks against a queue that nobody is
        draining. Clears ``_pending_inputs`` and
        ``_pending_interrupt_resumes`` first because ``_run_one_round``'s
        ``finally`` block would otherwise relaunch a fresh round mid
        teardown.
        """
        task = self._agent_task
        if task is None or task.done():
            return
        self._pending_inputs.clear()
        self._pending_interrupt_resumes.clear()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _pause_coordination(self) -> None:
        """Pause coordination for persistent teams.

        Publishes TEAM_STANDBY so teammates pause their polls,
        then stops the leader's own loop without killing
        teammate processes.
        """
        team_logger.info("[{}] coordination pausing (persistent)", self._member_name() or "?")
        await self._drain_agent_task()
        # Flush allocator counters into the session before it is
        # released so the next ``recover_from_session`` /
        # ``resume_for_new_session`` resumes the rotation instead of
        # restarting at index 0.
        self._persist_allocator_state()
        # Signal teammates to pause polls
        if self._messager and self.role == TeamRole.LEADER:
            from openjiuwen.agent_teams.schema.events import (
                EventMessage,
                TeamStandbyEvent,
                TeamTopic,
            )
            from openjiuwen.agent_teams.spawn.context import get_session_id

            team_name = self._team_name()
            if team_name:
                try:
                    await self._messager.publish(
                        topic_id=TeamTopic.TEAM.build(get_session_id(), team_name),
                        message=EventMessage.from_event(TeamStandbyEvent(team_name=team_name)),
                    )
                except Exception as e:
                    team_logger.error("Failed to publish TEAM_STANDBY: {}", e)
        await self._unsubscribe_transport()
        if self._coordination_loop:
            await self._coordination_loop.stop()
        self._close_stream()
        self._team_session = None

    async def _stop_coordination(self) -> None:
        """Stop the coordination loop, send sentinel, and unsubscribe."""
        team_logger.info("[{}] coordination stopping", self._member_name() or "?")
        await self._drain_agent_task()
        # Flush allocator counters before tearing down the session: a
        # temporary team that nevertheless saw spawns during this round
        # should still leave a coherent snapshot behind for any tooling
        # that inspects the session post-mortem.
        self._persist_allocator_state()
        await self._unsubscribe_transport()
        # Cancel any in-flight teammate-recovery tasks before tearing down
        # the spawn handles they would operate on. Snapshot the set because
        # done_callback mutates it.
        if self._recovery_tasks:
            pending = list(self._recovery_tasks)
            for task in pending:
                if not task.done():
                    task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._recovery_tasks.clear()
        # Shut down all spawned teammate processes
        for mid, handle in list(self._spawned_handles.items()):
            try:
                await handle.shutdown()
            except Exception as e:
                team_logger.error("Error shutting down teammate {}: {}", mid, e)
        self._spawned_handles.clear()
        if self._coordination_loop is None:
            return
        await self._coordination_loop.stop()
        self._close_stream()
        self._team_session = None

    def _close_stream(self) -> None:
        """Send sentinel to signal stream consumers that no more data is coming."""
        if self._stream_queue is not None:
            self._stream_queue.put_nowait(None)

    async def _subscribe_transport(self, team_name: str) -> None:
        """Subscribe to all TeamTopic channels on the transport."""
        if not self._messager or not self._coordination_loop:
            return
        from openjiuwen.agent_teams.schema.events import EventMessage, TeamTopic
        from openjiuwen.agent_teams.spawn.context import get_session_id

        local_member_name = self._member_name() or ""

        async def _filter_self(event: EventMessage) -> None:
            for listener in self._event_listeners:
                try:
                    await listener(event)
                except Exception as e:
                    team_logger.error("Event listener error: {}", e)
            if local_member_name and event.sender_id == local_member_name:
                team_logger.debug("ignoring self-published event: {}", event.event_type)
                return
            await self._coordination_loop.enqueue(event)

        session_id = get_session_id()
        await self._messager.register_direct_message_handler(
            self._coordination_loop.enqueue,
        )
        for topic in TeamTopic:
            topic_str = topic.build(session_id, team_name)
            await self._messager.subscribe(
                topic_str,
                _filter_self,
            )
            self._subscribed_topics.append(topic_str)

    async def _unsubscribe_transport(self) -> None:
        """Unsubscribe from all topics and unregister P2P handler."""
        if not self._messager:
            return
        try:
            await self._messager.unregister_direct_message_handler()
        except Exception:
            team_logger.debug("failed to unregister direct message handler during cleanup")
        for topic in self._subscribed_topics:
            try:
                await self._messager.unsubscribe(topic)
            except Exception:
                team_logger.debug("failed to unsubscribe topic {} during cleanup", topic)
        self._subscribed_topics.clear()

    def _is_agent_running(self) -> bool:
        """Return True only while ``Runner.run_agent_streaming`` is pumping.

        Tight window check: matches exactly the span where ``steer`` and
        ``follow_up`` are guaranteed to reach the outer task loop through
        ``DeepAgent._loop_controller``. Pre-stream status writes and the
        post-stream finalize tail deliberately return False — outside the
        streaming window those calls would either no-op or race.
        """
        return self._streaming_active

    def _has_in_flight_round(self) -> bool:
        """Return True while ``_agent_task`` is scheduled but not yet finalized."""
        return self._agent_task is not None and not self._agent_task.done()

    async def _cancel_agent(self) -> None:
        """Cancel the running agent task and update execution status."""
        await self._update_execution(ExecutionStatus.CANCEL_REQUESTED)
        if self._agent_task and not self._agent_task.done():
            await self._update_execution(ExecutionStatus.CANCELLING)
            self._agent_task.cancel()

    async def shutdown_self(self) -> None:
        """Force-shutdown self in response to TEAM_CLEANED.

        Called when the leader has dissolved the team and the database
        team/member rows are gone. Cancels any in-flight round and closes
        the stream so the natural ``stream() → _finalize_round →
        _stop_coordination`` path tears down the coordination loop and
        the coroutine exits. DB status writes are best-effort because the
        backing rows may already have been cascade-deleted.
        """
        member_name = self._member_name() or "?"
        team_logger.info("[{}] shutdown_self requested", member_name)
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
        if self._team_member is not None:
            try:
                await self._team_member.update_status(MemberStatus.SHUTDOWN)
            except Exception as e:
                team_logger.debug(
                    "[{}] post-clean status update failed (expected): {}",
                    member_name,
                    e,
                )
        self._close_stream()

    async def _start_agent(
        self,
        initial_message: Any,
    ) -> None:
        """Run one round of DeepAgent via Runner in background.

        Chunks are pushed to _stream_queue for the outer
        stream()/invoke() to yield.
        """
        if self._deep_agent is None or self._stream_queue is None:
            return
        preview = initial_message if isinstance(initial_message, str) else type(initial_message).__name__
        team_logger.info("[{}] start_agent: {:.120}", self._member_name() or "?", str(preview))
        self._agent_task = asyncio.create_task(
            self._run_one_round(initial_message),
        )
        # Nothing awaits ``_agent_task``: an exception raised in the
        # pre-try head of ``_run_one_round`` (init_cwd, early status
        # writes) would otherwise only surface as asyncio's GC-time
        # "Task exception was never retrieved" on the root logger,
        # invisible to team_logger. Promote it so the crash is visible.
        self._agent_task.add_done_callback(self._log_agent_task_exception)

    def _log_agent_task_exception(self, task: asyncio.Task) -> None:
        """Surface silent crashes of the background agent round task.

        done_callback fires outside any ``except`` block, so
        ``traceback.format_exc()`` is empty. Format the traceback from
        the exception's own ``__traceback__`` and pass it via the
        ``stacktrace`` kwarg, which ``exception()`` honors verbatim.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        team_logger.exception(
            "[{}] _run_one_round task crashed silently",
            self._member_name() or "?",
            stacktrace="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )

    async def _update_status(self, status: MemberStatus) -> None:
        """Update member status if this agent belongs to a team."""
        if self._team_member:
            await self._team_member.update_status(status)

    async def _update_execution(self, status: ExecutionStatus) -> None:
        """Update member execution status if this agent belongs to a team."""
        if self._team_member:
            await self._team_member.update_execution_status(status)

    async def _run_one_round(
        self,
        message: Any,
    ) -> None:
        """Execute one DeepAgent stream round via Runner."""
        if self._deep_agent and self._deep_agent.deep_config and self._deep_agent.deep_config.workspace:
            from openjiuwen.core.sys_operation.cwd import init_cwd

            init_root = self._deep_agent.deep_config.workspace.root_path
            init_cwd(
                init_root,
                workspace=init_root,
            )
        # Pull the member back to READY before transitioning to BUSY so a
        # still-live member that ended its previous round in ERROR can
        # recover. READY → READY is a no-op at the member layer.
        await self._update_status(MemberStatus.READY)
        await self._update_status(MemberStatus.BUSY)
        try:
            await self._execute_round(message)
            # If a shutdown request landed mid-round, leave the status in
            # SHUTDOWN_REQUESTED — the state machine forbids
            # SHUTDOWN_REQUESTED → READY, and ``_finalize_round`` will
            # transition it to SHUTDOWN once the round loop unwinds.
            if self._team_member is None or await self._team_member.status() != MemberStatus.SHUTDOWN_REQUESTED:
                await self._update_status(MemberStatus.READY)
        except BaseException as e:
            team_logger.error("Failed to execute deep agent, {}", e, exc_info=True)
            await self._update_status(MemberStatus.ERROR)
        finally:
            self._agent_task = None
            next_resume = self._dequeue_valid_interrupt_resume()
            if next_resume is not None and self._stream_queue is not None:
                await self._start_agent(next_resume)
            elif self._pending_inputs and self._stream_queue is not None:
                # Drain the entire queue into one round. Combining avoids
                # an O(N) chain of round-restarts when several inputs land
                # in the same transition window; anything arriving after
                # the new round begins streaming goes directly through
                # ``steer`` and never re-enters the queue, so it converges.
                drained = self._pending_inputs
                self._pending_inputs = []
                if len(drained) == 1:
                    combined = drained[0]
                else:
                    combined = "\n\n---\n\n".join(item if isinstance(item, str) else str(item) for item in drained)
                await self._start_agent(combined)
            else:
                await self._wake_mailbox_if_interrupt_cleared()
                if self._team_member and await self._team_member.status() == MemberStatus.SHUTDOWN_REQUESTED:
                    self._close_stream()

    async def _stream_one_round(
        self,
        query: Any,
    ) -> Optional[Tuple[Optional[int], str]]:
        """Pump exactly one streaming round of the inner DeepAgent.

        Forwards non-error chunks to ``self._stream_queue``. Once a TASK_FAILED
        frame appears, every subsequent frame (the blank answer chunk plus the
        END_FRAME from ``session.close_stream``) is swallowed so the downstream
        queue only sees coherent rounds.

        Args:
            query: Query string / payload for this round.

        Returns:
            ``None`` if the round completed without a TASK_FAILED frame.
            Otherwise ``(error_code, error_text)``; ``error_code`` is ``None``
            when the frame text lacks the ``[code]`` prefix.
        """
        inputs = {"query": query}
        error_seen = False
        error_code: Optional[int] = None
        error_text: str = ""
        self._streaming_active = True
        try:
            async for chunk in Runner.run_agent_streaming(
                self._deep_agent,
                inputs,
                session=self._session_id,
            ):
                if error_seen:
                    continue
                detected = _detect_task_failed(chunk)
                if detected is not None:
                    error_seen = True
                    error_code, error_text = detected
                    continue
                if self._stream_queue is not None:
                    await self._stream_queue.put(chunk)
        finally:
            self._streaming_active = False

        if not error_seen:
            return None
        return error_code, error_text

    async def _run_retrying_stream(
        self,
        initial_query: Any,
    ) -> None:
        """Drive ``_stream_one_round`` with the 181001 retry policy.

        Retries up to ``_MAX_RETRY_ATTEMPTS`` times when the round fails with
        a retryable code, substituting ``_RETRY_QUERY`` on each retry. Any
        non-retryable code or exhausted retries raise AGENT_TEAM_EXECUTION_ERROR
        so the caller's ``except Exception`` can flip the status to FAILED.
        """
        current_query: Any = initial_query
        attempt = 0
        while True:
            outcome = await self._stream_one_round(current_query)
            if outcome is None:
                return

            error_code, error_text = outcome
            if (
                error_code in _RETRYABLE_ERROR_CODES
                and attempt < _MAX_RETRY_ATTEMPTS
            ):
                attempt += 1
                team_logger.warning(
                    "DeepAgent round transient error "
                    "(code=%s, attempt=%d/%d): %s",
                    error_code,
                    attempt,
                    _MAX_RETRY_ATTEMPTS,
                    error_text,
                )
                current_query = _RETRY_QUERY
                continue

            team_logger.error(
                "DeepAgent round failed (code=%s, attempts=%d): %s",
                error_code,
                attempt,
                error_text,
            )
            raise build_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg=(
                    f"streaming task failed after {attempt} retries, "
                    f"last error code={error_code}: {error_text}"
                ),
            )

    async def _execute_round(
        self,
        message: Any,
    ) -> None:
        """Execute the agent invocation via Runner.

        Derives a fresh AgentSession from the team session each round so that
        pre_run/post_run lifecycle (checkpoint recover/save) fires correctly.
        Leader uses token-level streaming; teammates use invoke for simpler execution.
        """
        await self._update_execution(ExecutionStatus.STARTING)
        await self._update_execution(ExecutionStatus.RUNNING)
        try:
            await self._run_retrying_stream(message)
            await self._update_execution(ExecutionStatus.COMPLETING)
            await self._update_execution(ExecutionStatus.COMPLETED)
        except asyncio.CancelledError:
            await self._update_execution(ExecutionStatus.CANCELLED)
            raise
        except asyncio.TimeoutError:
            await self._update_execution(ExecutionStatus.TIMED_OUT)
            raise
        except Exception as e:
            team_logger.error("DeepAgent round error: %s", e)
            await self._update_execution(ExecutionStatus.FAILED)
            raise
        finally:
            await self._update_execution(ExecutionStatus.IDLE)

    def _is_valid_interrupt_resume(self, user_input: Any) -> bool:
        """Return True when the supplied InteractiveInput still targets a pending interrupt."""
        if not isinstance(user_input, InteractiveInput):
            return False
        session = self._deep_agent.loop_session if self._deep_agent else None
        if session is None:
            return False
        state = session.get_state(INTERRUPTION_KEY)
        if state is None:
            return False
        interrupted = getattr(state, "interrupted_tools", {}) or {}
        pending_ids = set()
        for entry in interrupted.values():
            requests = getattr(entry, "interrupt_requests", {}) or {}
            pending_ids.update(requests.keys())
        if not pending_ids:
            return False
        resume_ids = set(user_input.user_inputs.keys())
        return bool(resume_ids) and resume_ids.issubset(pending_ids)

    def _dequeue_valid_interrupt_resume(self) -> Optional[InteractiveInput]:
        """Pop the next still-valid queued interrupt resume input."""
        while self._pending_interrupt_resumes:
            candidate = self._pending_interrupt_resumes.pop(0)
            if self._is_valid_interrupt_resume(candidate):
                return candidate
        return None

    async def _wake_mailbox_if_interrupt_cleared(self) -> None:
        """Nudge mailbox processing once an interrupt gate has been cleared."""
        if self.role != TeamRole.TEAMMATE:
            return
        if self.has_pending_interrupt():
            return
        if self._coordination_loop is None:
            return
        await self._coordination_loop.enqueue(
            InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX),
        )

    def _member_name(self) -> Optional[str]:
        """Return the current agent's member_name."""
        return self._ctx.member_name if self._ctx else None

    def _team_name(self) -> Optional[str]:
        """Return the current team_name."""
        if self._ctx and self._ctx.team_spec:
            return self._ctx.team_spec.team_name
        return None

    # ------------------------------------------------------------------
    # Rail / callback proxies to internal DeepAgent
    # ------------------------------------------------------------------

    async def register_rail(self, rail: AgentRail) -> "TeamAgent":
        """Proxy rail registration to the internal DeepAgent."""
        if self._deep_agent is not None:
            await self._deep_agent.register_rail(rail)
        return self

    async def unregister_rail(self, rail: AgentRail) -> "TeamAgent":
        """Proxy rail unregistration to the internal DeepAgent."""
        if self._deep_agent is not None:
            await self._deep_agent.unregister_rail(rail)
        return self

    # ------------------------------------------------------------------
    # Spawn / clone helpers (unchanged logic)
    # ------------------------------------------------------------------

    def build_spawn_payload(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build the payload used to bootstrap one teammate."""
        team_spec = self.team_spec
        member_transport = self._build_member_messager_config(ctx.member_name)
        return {
            "coordination": {
                "team_name": team_spec.team_name if team_spec else "",
                "display_name": team_spec.display_name if team_spec else "",
                "leader_member_name": team_spec.leader_member_name if team_spec else None,
                "member_name": ctx.member_name,
                "role": ctx.role.value,
                "persona": ctx.persona,
                "transport": (member_transport.model_dump(mode="json") if member_transport is not None else None),
            },
            "query": initial_message or "Join the team and wait for your first assignment.",
        }

    def build_member_context(self, member_spec: TeamMemberSpec) -> TeamRuntimeContext:
        """Build runtime context for one teammate from a predefined member spec."""
        return TeamRuntimeContext(
            role=member_spec.role_type,
            member_name=member_spec.member_name,
            persona=member_spec.persona,
            team_spec=self._ctx.team_spec,
            messager_config=self._build_member_messager_config(member_spec.member_name),
            db_config=self._ctx.db_config,
        )

    def _build_member_messager_config(self, member_name: str):
        if self._ctx is None or self._ctx.messager_config is None:
            return None
        leader_cfg = self._ctx.messager_config
        meta = self._spec.metadata if self._spec else {}
        base_port = meta.get("teammate_base_port", 16000)
        port_offset = meta.get("teammate_port_offset", 10)

        # Reuse cached port on restart; assign new port on first spawn
        mid = member_name
        if mid in self._member_port_map:
            port_base = self._member_port_map[mid]
        else:
            port_base = base_port + self._teammate_port_counter * port_offset
            self._teammate_port_counter += 1
            self._member_port_map[mid] = port_base

        updates: Dict[str, Any] = {
            "node_id": member_name,
            "direct_addr": f"tcp://127.0.0.1:{port_base}",
            "pubsub_publish_addr": leader_cfg.pubsub_publish_addr,
            "pubsub_subscribe_addr": leader_cfg.pubsub_subscribe_addr,
        }
        # Teammates never run the pubsub proxy — only connect to the leader's.
        metadata = dict(leader_cfg.metadata)
        metadata.pop("pubsub_bind", None)
        updates["metadata"] = metadata
        return leader_cfg.model_copy(update=updates)

    def build_spawn_config(self, ctx: TeamRuntimeContext) -> SpawnAgentConfig:
        """Build JSON-safe spawn config for one teammate process."""
        context = ctx
        logging_config = self._build_member_logging_config(ctx.member_name or "", ctx.member_name or "")
        return SpawnAgentConfig(
            agent_kind=SpawnAgentKind.TEAM_AGENT,
            runner_config=serialize_runner_config(Runner.get_config()),
            logging_config=logging_config,
            session_id=None,
            payload={
                "spec": self._spec.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
            },
        )

    @staticmethod
    def _build_member_logging_config(member_name: str, name: str) -> dict[str, Any]:
        """Build a logging config with member-specific log file paths to avoid overwrites."""
        from openjiuwen.core.common.logging.log_config import get_log_config_snapshot

        config = get_log_config_snapshot()
        member_tag = member_name or name
        sinks = config.get("sinks", {})
        for sink in sinks.values():
            target = sink.get("target")
            if not isinstance(target, str) or target in ("stdout", "stderr"):
                continue
            # Insert member tag into file path: ./logs/run/jiuwen.log -> ./logs/run/teammates/{tag}/jiuwen.log
            parts = target.rsplit("/", 1)
            if len(parts) == 2:
                sink["target"] = f"{parts[0]}/teammates/{member_tag}/{parts[1]}"
            else:
                sink["target"] = f"teammates/{member_tag}/{target}"
        return config

    @classmethod
    async def from_spawn_payload(cls, payload: Dict[str, Any]) -> "TeamAgent":
        """Rebuild a TeamAgent from JSON-safe spawn payload dict."""
        from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec as _Spec
        from openjiuwen.agent_teams.schema.team import TeamRuntimeContext as _Ctx
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        spec = _Spec.model_validate(payload["spec"])
        context = _Ctx.model_validate(payload["context"])

        agent_spec = spec.agents.get(context.role.value) or spec.agents["leader"]
        team_name = (context.team_spec.team_name if context.team_spec else None) or spec.team_name
        card_id = f"{team_name}_{context.member_name}" if context.member_name else "unknown"
        card = agent_spec.card or AgentCard(
            id=card_id,
            name=context.member_name or "unknown",
            description=f"Teammate: {context.persona}" if context.persona else "Teammate",
        )
        agent = cls(card)
        await agent.configure_team(spec, context)
        return agent

    def _init_leader_member(self, member_name: str) -> None:
        """Initialize TeamMember for the leader after DB registration."""
        self._team_member = TeamMember(
            member_name=member_name,
            team_name=self._team_backend.team_name,
            agent_card=self.card,
            db=self._team_backend.db,
            messager=self._messager,
            desc=self._ctx.persona,
        )

    async def _on_teammate_created(self, teammate_id: str):
        team_logger.info("[{}] on_teammate_created: {}", self._member_name() or "?", teammate_id)
        if teammate_id == self._member_name():
            self._init_leader_member(teammate_id)
            return
        ctx = await self._build_context_from_db(teammate_id)
        if ctx is None:
            return
        teammate = await self._team_backend.get_member(teammate_id)
        await self.spawn_teammate(
            ctx,
            initial_message=teammate.prompt if teammate else None,
            session=self._session_id,
            spawn_config=SpawnConfig(health_check_timeout=30, health_check_interval=50),
        )

    async def _build_context_from_db(self, member_name: str) -> Optional[TeamRuntimeContext]:
        """Build a TeamRuntimeContext directly from DB record."""
        teammate = await self._team_backend.get_member(member_name)
        if teammate is None:
            team_logger.error("Teammate {} not found in database", member_name)
            return None

        member_model = self._resolve_member_model(teammate.model_ref_json)

        return TeamRuntimeContext(
            role=TeamRole.TEAMMATE,
            member_name=teammate.member_name,
            persona=teammate.desc or "",
            team_spec=self._ctx.team_spec,
            messager_config=self._build_member_messager_config(teammate.member_name),
            db_config=self._ctx.db_config,
            member_model=member_model,
        )

    def _resolve_member_model(self, ref_json: Optional[str]) -> Optional["TeamModelConfig"]:
        """Resolve a member's TeamModelConfig from a stored DB reference.

        The DB only carries a ``{"model_name", "model_index"}``
        reference; the live config (credentials, endpoint, request
        knobs) is rebuilt from ``team_spec.model_pool`` so pool updates
        propagate on the next teammate spawn / restart. Resolution is
        purely positional — does not advance any allocator counter, so
        restarts never bias rotation.
        """
        if not ref_json:
            return None
        from openjiuwen.agent_teams.agent.model_allocator import resolve_member_model

        try:
            ref = json.loads(ref_json)
        except (json.JSONDecodeError, TypeError) as e:
            team_logger.warning(
                "[{}] malformed model_ref_json on DB record; ignoring: {}",
                self._member_name() or "?",
                e,
            )
            return None

        if not isinstance(ref, dict):
            return None

        team_spec = self._ctx.team_spec if self._ctx else None
        if team_spec is None:
            return None

        return resolve_member_model(
            team_spec,
            model_name=ref.get("model_name"),
            model_index=ref.get("model_index"),
        )

    async def spawn_teammate(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
        session: Optional[Any] = None,
        spawn_config: Optional[SpawnConfig] = None,
    ):
        """Spawn one teammate via subprocess or in-process coroutine.

        The returned handle is tracked internally and an on_unhealthy
        callback is registered so the leader can auto-restart the
        teammate when consecutive health checks fail.
        """
        member_name = ctx.member_name
        team_logger.info("[{}] spawning teammate: {}", self._member_name() or "?", member_name)

        if self._spec and self._spec.spawn_mode == "inprocess":
            from openjiuwen.agent_teams.spawn.inprocess_spawn import inprocess_spawn

            handle = await inprocess_spawn(
                team_agent=self,
                ctx=ctx,
                initial_message=initial_message,
                session_id=self._session_id or session,
            )
        else:
            handle = await Runner.spawn_agent(
                self.build_spawn_config(ctx),
                self.build_spawn_payload(
                    ctx,
                    initial_message=initial_message,
                ),
                session=session,
                spawn_config=spawn_config,
            )

        self._spawned_handles[member_name] = handle

        def _trigger_unhealthy_recovery() -> asyncio.Task:
            task = asyncio.ensure_future(self._on_teammate_unhealthy(member_name))
            self._recovery_tasks.add(task)
            task.add_done_callback(self._recovery_tasks.discard)
            return task

        handle.on_unhealthy = _trigger_unhealthy_recovery
        return handle

    # ------------------------------------------------------------------
    # Fault tolerance: cleanup, restart, recover
    # ------------------------------------------------------------------

    async def _on_teammate_unhealthy(self, member_name: str) -> None:
        """Handle a teammate whose process has become unhealthy.

        Cleans up the dead process, marks the member as RESTARTING
        in the database, and attempts to re-spawn.
        """
        team_logger.warning("Teammate {} detected as unhealthy, initiating restart", member_name)
        await self._cleanup_teammate(member_name)
        if self._team_backend:
            await self._team_backend.db.update_member_status(
                member_name,
                self._team_name(),
                MemberStatus.RESTARTING.value,
            )
        await self._restart_teammate(member_name)

    async def _cleanup_teammate(self, member_name: str) -> None:
        """Clean up resources for a dead/dying teammate process."""
        handle = self._spawned_handles.pop(member_name, None)
        if handle is None:
            return
        try:
            await handle.stop_health_check()
            if handle.is_alive:
                await handle.force_kill()
        except Exception as e:
            team_logger.error("Error cleaning up teammate {}: {}", member_name, e)

    async def _restart_teammate(self, member_name: str, max_retries: int = 3) -> bool:
        """Restart a teammate process, recovering config from DB.

        Retries with exponential backoff. Publishes MemberRestartedEvent
        on success; marks ERROR on exhaustion. Any prior spawn handle for
        ``member_name`` is force-killed first so callers (recover_team,
        unhealthy detection, leader-startup recovery) can invoke this
        idempotently without leaking processes.
        """
        await self._cleanup_teammate(member_name)

        ctx = await self._build_context_from_db(member_name)
        if ctx is None:
            team_logger.error("Cannot recover spawn config for {}", member_name)
            return False

        teammate = await self._team_backend.get_member(member_name)
        initial_message = teammate.prompt if teammate else None
        spawn_config = SpawnConfig(health_check_timeout=30, health_check_interval=50)

        for attempt in range(1, max_retries + 1):
            try:
                team_logger.info("Restarting teammate {} (attempt {}/{})", member_name, attempt, max_retries)
                await self.spawn_teammate(
                    ctx,
                    initial_message=initial_message,
                    spawn_config=spawn_config,
                )
                await self._publish_restart_event(member_name, attempt)
                team_logger.info("Teammate {} restarted successfully", member_name)
                return True
            except Exception as e:
                team_logger.error("Restart attempt {} for {} failed: {}", attempt, member_name, e)
                if attempt < max_retries:
                    await asyncio.sleep(2**attempt)

        # All retries exhausted
        if self._team_backend:
            await self._team_backend.db.update_member_status(member_name, self._team_name(), MemberStatus.ERROR.value)
        return False

    async def _publish_restart_event(self, member_name: str, restart_count: int) -> None:
        """Publish MemberRestartedEvent on the team topic."""
        if not self._messager or not self._team_backend:
            return
        from openjiuwen.agent_teams.schema.events import (
            EventMessage,
            MemberRestartedEvent,
            TeamTopic,
        )
        from openjiuwen.agent_teams.spawn.context import get_session_id

        try:
            await self._messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self._team_backend.team_name),
                message=EventMessage.from_event(
                    MemberRestartedEvent(
                        team_name=self._team_backend.team_name,
                        member_name=member_name,
                        restart_count=restart_count,
                    )
                ),
            )
        except Exception as e:
            team_logger.error("Failed to publish restart event for {}: {}", member_name, e)

    async def resume_for_new_session(self, session) -> None:
        """Prepare a persistent team for a new session.

        Switches the session context, creates new dynamic tables for
        the new session (tasks, messages), and persists leader config.
        Existing teammate processes and DB member records are retained.

        Args:
            session: The new session to attach to.
        """
        from openjiuwen.agent_teams.spawn.context import set_session_id

        self._session_id = session.get_session_id()
        set_session_id(self._session_id)
        self._team_session = session if isinstance(session, AgentTeamSession) else None

        if self._team_backend:
            await self._team_backend.db.create_cur_session_tables()

        if self._spec and self.role == TeamRole.LEADER:
            self._persist_leader_config(session)

    async def recover_team(self) -> list[str]:
        """Re-launch every teammate from database state.

        Called after the leader has been reconstructed (e.g. via
        ``recover_from_session``) or whenever the leader starts on a
        team that already exists in the DB. All non-leader members are
        re-spawned regardless of their last persisted status — any prior
        process is assumed dead and replaced with a fresh one.
        """
        if not self._team_backend:
            return []

        team_logger.info("[{}] recovering team", self._member_name() or "?")
        all_members = await self._team_backend.list_members()
        leader_member_name = self._member_name()
        restarted: list[str] = []

        for member in all_members:
            if member.member_name == leader_member_name:
                continue
            await self._team_backend.db.update_member_status(
                member.member_name,
                self._team_name(),
                MemberStatus.RESTARTING.value,
            )
            if await self._restart_teammate(member.member_name):
                restarted.append(member.member_name)

        return restarted

    # ------------------------------------------------------------------
    # Leader config persistence / recovery
    # ------------------------------------------------------------------

    def _persist_leader_config(self, session) -> None:
        """Persist leader's spec + context + allocator state for recovery.

        Allocator state rides alongside spec so the rotation counters
        survive a full-restart. The pool itself is rebuilt from
        ``team_spec.model_pool`` on recovery — only the volatile
        counters need persistence. ``model_allocator_state`` is omitted
        when no pool is configured.
        """
        payload: dict[str, Any] = {
            "spec": self._spec.model_dump(mode="json"),
            "context": self._ctx.model_dump(mode="json"),
            "team_name": self._team_name(),
        }
        if self._model_allocator is not None:
            payload["model_allocator_state"] = self._model_allocator.state_dict()
        session.update_state(payload)

    def _persist_allocator_state(self) -> None:
        """Flush in-memory allocator counters into the active session.

        Called at end-of-session boundaries (pause / stop) so any
        rotation that happened during the session is captured before
        the session reference is dropped. No-op outside the leader,
        without an allocator, or without an active team session.
        """
        if self._team_session is None or self._model_allocator is None:
            return
        try:
            self._team_session.update_state(
                {"model_allocator_state": self._model_allocator.state_dict()},
            )
        except Exception as e:
            team_logger.error(
                "[{}] failed to persist allocator state: {}",
                self._member_name() or "?",
                e,
            )

    @classmethod
    def recover_from_session(cls, session) -> "TeamAgent":
        """Recover a leader TeamAgent from a persisted session.

        Used in full-restart scenario: all processes exited, the caller
        re-creates the leader from session state.

        Args:
            session: AgentTeamSession with persisted leader config in state.
        """
        from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec as _Spec
        from openjiuwen.agent_teams.schema.team import TeamRuntimeContext as _Ctx
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        state = session.get_state()
        spec_data = state.get("spec")
        if spec_data is None:
            raise ValueError("No leader spec found in session state")
        spec = _Spec.model_validate(spec_data)
        context = _Ctx.model_validate(state["context"])

        agent_spec = spec.agents.get(context.role.value) or spec.agents["leader"]
        team_name = (context.team_spec.team_name if context.team_spec else None) or spec.team_name
        card_id = f"{team_name}_{context.member_name}" if context.member_name else "leader"
        card = agent_spec.card or AgentCard(
            id=card_id,
            name=context.member_name or "leader",
        )
        agent = cls(card)
        agent.configure(spec, context)
        # Restore allocator counters so the rotation continues from
        # where the previous session left off rather than restarting
        # at index 0 and re-handing-out the head of the pool.
        allocator_state = state.get("model_allocator_state")
        if allocator_state:
            agent.restore_allocator_state(allocator_state)
        return agent


__all__ = ["TeamAgent"]
