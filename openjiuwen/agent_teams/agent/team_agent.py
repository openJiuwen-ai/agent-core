# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unified TeamAgent implementation."""

from __future__ import annotations

import asyncio
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Optional,
)

from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
from openjiuwen.agent_teams.agent.coordination import (
    CoordinationKernel,
    EventBus,
)
from openjiuwen.agent_teams.agent.member import TeamMember
from openjiuwen.agent_teams.agent.member_factory import create_member_handle
from openjiuwen.agent_teams.agent.recovery_manager import RecoveryManager
from openjiuwen.agent_teams.agent.session_manager import SessionManager
from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
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
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.spawn.agent_config import SpawnAgentConfig
from openjiuwen.core.runner.spawn.process_manager import SpawnConfig
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.rail.base import AgentRail

if TYPE_CHECKING:
    from openjiuwen.agent_teams.harness import TeamHarness
    from openjiuwen.agent_teams.interaction.payload import DeliverResult
    from openjiuwen.agent_teams.models.allocator import Allocation, ModelAllocator
    from openjiuwen.agent_teams.models.pool import ModelPoolEntry
    from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
    from openjiuwen.harness.tools.worktree import WorktreeManager


# pylint: disable=too-many-public-methods
class TeamAgent(BaseAgent):
    """One implementation that can act as leader or teammate.

    Uses composition: wraps an internal DeepAgent instance instead of
    inheriting from it. Delegates to specialized managers for
    configuration, streaming, spawning, recovery, and session management.
    """

    def __init__(self, card):
        super().__init__(card)
        self._configurator = AgentConfigurator(card)
        self._state = TeamAgentState()

        self._spawn_manager = SpawnManager(
            state=self._state,
            configurator=self._configurator,
            team_agent_getter=lambda: self,
        )
        self._recovery_manager = RecoveryManager(
            configurator=self._configurator,
            spawn_manager=self._spawn_manager,
        )
        self._session_manager = SessionManager(
            state=self._state,
            configurator=self._configurator,
            recovery_manager=self._recovery_manager,
        )
        self._stream_controller = StreamController(
            blueprint_getter=lambda: self._configurator.blueprint,
            state=self._state,
            resources=self._configurator.resources,
            status_updater=self._update_status,
            execution_updater=self._update_execution,
            wake_mailbox_callback=self._wake_mailbox_if_interrupt_cleared,
        )
        self._coordination = CoordinationKernel(self)

    # ------------------------------------------------------------------
    # Properties — delegate to configurator
    # ------------------------------------------------------------------

    @property
    def blueprint(self):
        """Return the static assembly blueprint, or None before configure()."""
        return self._configurator.blueprint

    @property
    def state(self):
        """Return the mutable runtime state container."""
        return self._state

    @property
    def infra(self):
        """Return the per-process team infrastructure container."""
        return self._configurator.infra

    @property
    def resources(self):
        """Return the per-instance runtime resources container."""
        return self._configurator.resources

    @property
    def harness(self) -> Optional["TeamHarness"]:
        """Return the harness owning the underlying DeepAgent runtime.

        All access to the DeepAgent runtime — config, model, workspace,
        rails, streaming — must go through this object. New code should
        not seek out the DeepAgent instance directly.
        """
        return self._configurator.harness

    @property
    def spec(self) -> Optional[TeamAgentSpec]:
        return self._configurator.spec

    @property
    def runtime_context(self) -> Optional[TeamRuntimeContext]:
        return self._configurator.ctx

    @property
    def coordination(self) -> CoordinationKernel:
        """Return the coordination kernel (event bus + dispatcher + lifecycle)."""
        return self._coordination

    @property
    def coordination_loop(self) -> Optional[EventBus]:
        """Return the underlying event bus.

        Kept as a public accessor for tests and legacy callers; new code
        should go through ``self.coordination`` instead.
        """
        return self._coordination.event_bus

    @property
    def role(self) -> TeamRole:
        return self._configurator.role

    @property
    def lifecycle(self) -> str:
        return self._configurator.lifecycle

    @property
    def team_spec(self) -> Optional[TeamSpec]:
        return self._configurator.team_spec

    @property
    def member_name(self) -> Optional[str]:
        return self._configurator.member_name

    @property
    def message_manager(self):
        return self._configurator.message_manager

    @property
    def task_manager(self):
        return self._configurator.task_manager

    @property
    def team_backend(self) -> Optional[TeamBackend]:
        return self._configurator.team_backend

    @property
    def session_id(self) -> Optional[str]:
        """Return the current session ID from the agent_teams contextvar.

        The contextvar is the single source of truth; reading from a cached
        state field would re-introduce double-bookkeeping bugs that the
        contextvar-only design was meant to eliminate.
        """
        from openjiuwen.agent_teams.context import get_session_id

        return get_session_id() or None

    @property
    def session_manager(self) -> SessionManager:
        """Return the session manager."""
        return self._session_manager

    @property
    def recovery_manager(self) -> RecoveryManager:
        """Return the recovery manager."""
        return self._recovery_manager

    @property
    def spawn_manager(self) -> SpawnManager:
        """Return the spawn manager."""
        return self._spawn_manager

    @property
    def stream_controller(self) -> StreamController:
        """Return the stream controller."""
        return self._stream_controller

    @property
    def event_listeners(self) -> list:
        """Return the registered event listeners."""
        return self._state.event_listeners

    @property
    def team_member(self) -> Optional[TeamMember]:
        """Return the TeamMember handle for this agent, if set."""
        return self._state.team_member

    async def is_shutdown_requested(self) -> bool:
        """Whether this teammate has been asked to shut down or already has.

        Leaders never carry a TeamMember handle (only teammates and human
        agents do), so this always returns False for leader agents.
        Includes ``SHUTDOWN`` itself because ``shutdown_self`` writes the
        terminal status directly before tearing down the stream — the
        finalize path must treat that as "already heading out" and not
        flip the status back to READY through a pause decision.
        Consumed by ``TeamRuntimeManager.finalize_member``.
        """
        member = self._state.team_member
        if member is None:
            return False
        status = await member.status()
        return status in (MemberStatus.SHUTDOWN_REQUESTED, MemberStatus.SHUTDOWN)

    @property
    def pending_user_query(self) -> str:
        """Return the pending user query string."""
        return self._state.pending_user_query

    @property
    def team_name(self) -> Optional[str]:
        """Return the team name from the runtime context."""
        return self._configurator.team_name

    async def update_status(self, status: MemberStatus) -> None:
        """Update the member status in the database."""
        await self._update_status(status)

    def persist_allocator_state(self) -> None:
        """Persist the model allocator state to the current session."""
        self._persist_allocator_state()

    # ------------------------------------------------------------------
    # Event listeners
    # ------------------------------------------------------------------

    def add_event_listener(self, handler) -> None:
        self._state.event_listeners.append(handler)

    def remove_event_listener(self, handler) -> None:
        try:
            self._state.event_listeners.remove(handler)
        except ValueError:
            pass

    def lookup_human_agent_runtime(self, member_name: str) -> Optional["TeamAgent"]:
        """Resolve an inprocess-spawned human agent's live ``TeamAgent``.

        Used by ``HumanAgentInbox`` so the leader-side runtime can feed
        user input directly into the avatar's DeepAgent without going
        through the message bus. Returns ``None`` for subprocess
        spawns (cross-process delivery is out of scope for Phase 2)
        or when the avatar has not been spawned yet.
        """
        backend = self._configurator.team_backend
        if backend is None or not backend.is_human_agent(member_name):
            return None
        return self._spawn_manager.lookup_inprocess_agent(member_name)

    def is_agent_ready(self) -> bool:
        return self._configurator.harness is not None

    def is_agent_running(self) -> bool:
        return self._is_agent_running()

    def has_in_flight_round(self) -> bool:
        return self._has_in_flight_round()

    async def deliver_input(self, content: Any, *, use_steer: bool = True) -> None:
        if self._is_agent_running():
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
            self._stream_controller.pending_inputs.append(content)
            return
        await self._start_agent(content)

    def has_pending_interrupt(self) -> bool:
        return self._stream_controller.has_pending_interrupt()

    async def start_agent(self, content: str) -> None:
        await self._start_agent(content)

    async def follow_up(self, content: str) -> None:
        await self._stream_controller.follow_up(content)

    async def cancel_agent(self) -> None:
        team_logger.debug("[{}] cancel_agent requested", self._member_name() or "?")
        await self._cancel_agent()

    async def destroy_team(self, force: bool = True) -> bool:
        # Snapshot session_id BEFORE coordination teardown. ``stop_coordination``
        # triggers ``SessionManager.release_session`` which resets the
        # contextvar; without the snapshot, ``_remove_self_from_pool`` would
        # be unable to identify which pool entry it owns and silently leak it.
        session_id_snapshot = self.session_id

        try:
            await self.cancel_agent()
        except Exception as e:
            team_logger.warning("[{}] cancel_agent during destroy failed: {}", self._member_name() or "?", e)

        try:
            await self._stop_coordination()
        except Exception as e:
            team_logger.warning("[{}] stop coordination during destroy failed: {}", self._member_name() or "?", e)

        # Drop any pool entry for this team so the next ``run_agent_team*``
        # call sees a clean slate. ``destroy_team`` is the leader-level
        # teardown sibling of ``TeamRuntimeManager.stop_team`` / ``delete_team``
        # — invoked directly on the TeamAgent it must still honor the
        # "stop_coordination implies pool.remove" invariant. Best-effort:
        # any failure is logged but does not break the destroy contract.
        await self._remove_self_from_pool(session_id_snapshot)

        if not self._configurator.team_backend:
            return False

        return await self._configurator.team_backend.force_clean_team(shutdown_members=force)

    async def _remove_self_from_pool(self, session_id: Optional[str]) -> None:
        """Best-effort detach from the process-global team runtime pool.

        Takes ``session_id`` as an explicit argument because the caller has
        to snapshot it before coordination teardown resets the contextvar.
        Reaches into ``GLOBAL_RUNNER`` to find the runtime manager rather
        than holding a back reference, because pool ownership is a
        runtime-layer concern that the TeamAgent must not couple to at
        construction time. Idempotent — a missing pool entry, a manager
        that was never lazily created, or any access failure all become
        no-ops with a warning log.
        """
        team_name = self._configurator.team_name
        if not team_name or not session_id:
            return
        try:
            from openjiuwen.core.runner.runner import GLOBAL_RUNNER

            manager = getattr(GLOBAL_RUNNER, "_team_runtime_manager", None)
            if manager is None:
                return
            pool = manager.pool
            entry = await pool.get(team_name)
            if entry is None or entry.current_session_id != session_id:
                return
            await pool.remove(team_name)
        except Exception as exc:
            team_logger.warning(
                "[{}] destroy_team pool cleanup failed: {}",
                self._member_name() or "?",
                exc,
            )

    async def steer(self, content: str) -> None:
        await self._stream_controller.steer(content)

    async def resume_interrupt(self, user_input) -> None:
        if not self._stream_controller.is_valid_interrupt_resume(user_input):
            team_logger.info("[{}] dropping stale interrupt resume input", self._member_name() or "?")
            return
        if self._has_in_flight_round():
            team_logger.info(
                "[{}] queueing interrupt resume until current round completes",
                self._member_name() or "?",
            )
            self._stream_controller.pending_interrupt_resumes.append(user_input)
            return
        await self._start_agent(user_input)

    # ------------------------------------------------------------------
    # BaseAgent abstract method: configure
    # ------------------------------------------------------------------

    # pylint: disable=arguments-differ
    def configure(self, spec: TeamAgentSpec, context: TeamRuntimeContext) -> "TeamAgent":
        self._setup_infra(spec, context)
        self._setup_agent(spec, context)
        return self

    # ------------------------------------------------------------------
    # Team-specific configuration
    # ------------------------------------------------------------------

    def _setup_infra(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> None:
        self._configurator.setup_infra(
            spec,
            ctx,
            on_teammate_created=self._on_teammate_created,
            on_team_cleaned=self._mark_team_cleaned,
        )

    def _setup_agent(self, spec: TeamAgentSpec, ctx: TeamRuntimeContext) -> None:
        self._configurator.setup_agent(spec, ctx)

        # Build the member handle once for every role. ``create_member_handle``
        # is a pure constructor: it only needs the bound ``team_backend``
        # (``setup_infra`` wires that up for all roles before this runs) and
        # never touches the database. The leader's own DB row may not exist
        # yet at this point -- it only materializes when the leader calls
        # ``BuildTeamTool`` mid-round -- but ``TeamMember`` tolerates a missing
        # row, so the handle is created eagerly here just like teammates. This
        # keeps status / execution transitions flowing to the DB for every
        # role, including the leader and cold-recovered agents.
        if ctx.member_name:
            self._state.team_member = create_member_handle(
                member_name=ctx.member_name,
                blueprint=self._configurator.blueprint,
                infra=self._configurator.infra,
                agent_card=self.card,
            )

        self._coordination.setup(role=ctx.role)
        self._register_team_completion_callbacks()

    def _register_team_completion_callbacks(self) -> None:
        """Wire optional team-completion callbacks into the coordination layer.

        Runs once, after the DeepAgent is fully built (rails mounted,
        ``agent_customizer`` applied) and the dispatcher exists. Extracts
        any ``TeamSkillRail`` mounted on the agent and registers its
        ``notify_team_completed`` hook with the ``TeamCompletionHandler``
        so a drained task board triggers skill evolution — no per-event
        rail lookup. No-op when the harness, dispatcher, or rail is absent.
        """
        harness = self._configurator.harness
        dispatcher = self._coordination.dispatcher
        if harness is None or dispatcher is None:
            return
        from openjiuwen.harness.rails import TeamSkillRail

        for rail in harness.find_rails(TeamSkillRail):
            dispatcher.team_completion.register_completion_callback(rail.notify_team_completed)

    def _resolve_agent_spec(
        self,
        spec: TeamAgentSpec,
        role: TeamRole,
        member_name: Optional[str] = None,
    ):
        return self._configurator.resolve_agent_spec(spec, role, member_name)

    def update_model_pool(self, new_pool: "list[ModelPoolEntry]") -> None:
        self._configurator.update_model_pool(new_pool)
        if self._configurator.spec is None or self.role != TeamRole.LEADER:
            return
        team_session = self._session_manager.team_session
        if team_session is None:
            return
        self._recovery_manager.persist_leader_config(team_session)

    def attach_model_allocator(
        self,
        allocator: "ModelAllocator",
        *,
        leader_allocation: Optional["Allocation"] = None,
    ) -> None:
        self._configurator.attach_model_allocator(allocator, leader_allocation=leader_allocation)

    def restore_allocator_state(self, state: dict) -> None:
        self._configurator.restore_allocator_state(state)

    def _create_workspace_manager(
        self,
        spec: TeamAgentSpec,
        ctx: TeamRuntimeContext,
    ) -> "TeamWorkspaceManager":
        return self._configurator.create_workspace_manager(spec, ctx)

    def _create_worktree_manager(self, spec: TeamAgentSpec) -> "WorktreeManager":
        return self._configurator.create_worktree_manager(spec)

    # ------------------------------------------------------------------
    # BaseAgent abstract methods: invoke / stream
    # ------------------------------------------------------------------

    async def invoke(self, inputs, session=None):
        team_logger.info("[{}] invoke start, role={}", self._member_name() or "?", self.role.value)
        self._stream_controller.stream_queue = asyncio.Queue()
        # Cache the user query so CoordinationManager can pass it to the
        # memory pipeline during start().
        self._state.pending_user_query = inputs.get("query", "") if isinstance(inputs, dict) else str(inputs)
        await self._coordination.start(session)
        try:
            await self._coordination.enqueue_user_input(inputs)
            await self._coordination.enqueue_mailbox_after_first_iteration()
            last_result = None
            while True:
                chunk = await self._stream_controller.stream_queue.get()
                if chunk is None:
                    break
                last_result = chunk
            return last_result
        finally:
            await self._coordination.finalize_round()

    async def broadcast(self, content: str) -> "DeliverResult":
        """Broadcast a user-side announcement; returns the delivery result."""
        from openjiuwen.agent_teams.interaction import UserInbox

        if self._configurator.team_backend is None:
            raise RuntimeError("TeamAgent.broadcast requires a configured team backend")
        return await UserInbox(self._configurator.team_backend.message_manager).broadcast(content)

    async def human_agent_say(
        self,
        content: str,
        to: Optional[str] = None,
        *,
        sender: Optional[str] = None,
    ) -> "DeliverResult":
        """Speak as a registered human-agent member; returns the delivery result."""
        from openjiuwen.agent_teams.interaction import HumanAgentInbox

        if self._configurator.team_backend is None:
            raise RuntimeError("TeamAgent.human_agent_say requires a configured team backend")
        return await HumanAgentInbox(
            self._configurator.team_backend,
            self._configurator.team_backend.message_manager,
        ).send(content, to=to, sender=sender)

    async def stream(self, inputs, session=None, stream_modes=None):
        team_logger.info("[{}] stream start, role={}", self._member_name() or "?", self.role.value)
        self._stream_controller.stream_queue = asyncio.Queue()
        self._state.pending_user_query = inputs.get("query", "") if isinstance(inputs, dict) else str(inputs)
        await self._coordination.start(session)
        try:
            await self._coordination.enqueue_user_input(inputs)
            await self._coordination.enqueue_mailbox_after_first_iteration()
            while True:
                chunk = await self._stream_controller.stream_queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            await self._coordination.finalize_round()

    async def interact(self, message: str) -> None:
        await self._coordination.enqueue_user_input(message)

    # ------------------------------------------------------------------
    # Coordination lifecycle (delegates to CoordinationManager; kept as
    # public wrappers because tests drive them by name)
    # ------------------------------------------------------------------

    async def _start_coordination(self, session=None) -> None:
        await self._coordination.start(session)

    async def _pause_coordination(self) -> None:
        await self._coordination.pause()

    async def pause_coordination(self) -> None:
        """Pause coordination without tearing down teammate processes."""
        await self._pause_coordination()

    async def _stop_coordination(self) -> None:
        await self._coordination.stop()

    async def stop_coordination(self) -> None:
        """Stop coordination and shut down all spawned teammates."""
        await self._stop_coordination()

    def _close_stream(self) -> None:
        self._coordination.close_stream()

    @property
    def _subscribed_topics(self) -> list[str]:
        return self._coordination.subscribed_topics

    def _is_agent_running(self) -> bool:
        return self._stream_controller.is_agent_running()

    def _has_in_flight_round(self) -> bool:
        return self._stream_controller.has_in_flight_round()

    async def _cancel_agent(self) -> None:
        await self._stream_controller.cancel_agent()

    async def shutdown_self(self) -> None:
        member_name = self._member_name() or "?"
        team_logger.info("[{}] shutdown_self requested", member_name)
        await self._stream_controller.cooperative_cancel()
        if self._state.team_member is not None:
            try:
                await self._state.team_member.update_status(MemberStatus.SHUTDOWN)
            except Exception as e:
                team_logger.debug(
                    "[{}] post-clean status update failed (expected): {}",
                    member_name,
                    e,
                )
        self._close_stream()

    async def _start_agent(self, initial_message: Any) -> None:
        await self._stream_controller.start_round(initial_message)

    async def _update_status(self, status: MemberStatus) -> None:
        if self._state.team_member:
            await self._state.team_member.update_status(status)

    async def _update_execution(self, status: ExecutionStatus) -> None:
        if self._state.team_member:
            await self._state.team_member.update_execution_status(status)

    async def _wake_mailbox_if_interrupt_cleared(self) -> None:
        await self._coordination.wake_mailbox_if_interrupt_cleared()

    def _member_name(self) -> Optional[str]:
        return self._configurator.member_name

    def _team_name(self) -> Optional[str]:
        return self._configurator.team_name

    # ------------------------------------------------------------------
    # Rail / callback proxies to internal DeepAgent
    # ------------------------------------------------------------------

    async def register_rail(self, rail: AgentRail) -> "TeamAgent":
        harness = self._configurator.harness
        if harness is not None:
            await harness.register_rail(rail)
        return self

    async def unregister_rail(self, rail: AgentRail) -> "TeamAgent":
        harness = self._configurator.harness
        if harness is not None:
            await harness.unregister_rail(rail)
        return self

    # ------------------------------------------------------------------
    # Spawn / clone helpers
    # ------------------------------------------------------------------

    def build_spawn_payload(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._configurator.build_spawn_payload(ctx, initial_message=initial_message)

    def build_member_context(self, member_spec: TeamMemberSpec) -> TeamRuntimeContext:
        return self._configurator.build_member_context(member_spec)

    def build_spawn_config(self, ctx: TeamRuntimeContext) -> SpawnAgentConfig:
        return self._configurator.build_spawn_config(ctx)

    @classmethod
    async def from_spawn_payload(cls, payload: Dict[str, Any]) -> "TeamAgent":
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        spec = TeamAgentSpec.model_validate(payload["spec"])
        context = TeamRuntimeContext.model_validate(payload["context"])

        agent_spec = spec.agents.get(context.role.value) or spec.agents["leader"]
        team_name = (context.team_spec.team_name if context.team_spec else None) or spec.team_name
        card_id = f"{team_name}_{context.member_name}" if context.member_name else "unknown"
        card = agent_spec.card or AgentCard(
            id=card_id,
            name=context.member_name or "unknown",
            description=f"Teammate: {context.persona}" if context.persona else "Teammate",
        )
        agent = cls(card)
        agent.configure(spec, context)
        return agent

    async def _on_teammate_created(self, teammate_id: str):
        team_logger.info("[{}] on_teammate_created: {}", self._member_name() or "?", teammate_id)
        ctx = await self._spawn_manager.build_context_from_db(teammate_id)
        if ctx is None:
            return
        teammate = await self._configurator.team_backend.get_member(teammate_id)
        await self.spawn_teammate(
            ctx,
            initial_message=teammate.prompt if teammate else None,
            session=self.session_id,
            spawn_config=SpawnConfig(health_check_timeout=30, health_check_interval=50),
        )

    async def _mark_team_cleaned(self) -> None:
        """Latch ``state.team_cleaned`` from the ``clean_team`` success path.

        Wired into ``TeamBackend`` via
        ``setup_team_backend(on_team_cleaned=...)``. ``clean_team`` runs
        synchronously inside the leader's DeepAgent round, so setting the
        flag here guarantees it is visible before
        ``StreamController._run_one_round``'s finally block evaluates
        terminal conditions — no reliance on the racy ``TeamCleanedEvent``
        bus handler, which the leader deliberately ignores (see
        ``coordination/handlers/agent_lifecycle.py::on_cleaned``).
        """
        team_logger.info("[{}] clean_team completed; latching team_cleaned", self._member_name() or "?")
        self._state.team_cleaned = True

    async def spawn_teammate(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
        session: Optional[Any] = None,
        spawn_config: Optional[SpawnConfig] = None,
    ):
        return await self._spawn_manager.spawn_teammate(
            ctx,
            initial_message=initial_message,
            session=session,
            spawn_config=spawn_config,
        )

    # ------------------------------------------------------------------
    # Fault tolerance: cleanup, restart, recover
    # ------------------------------------------------------------------

    async def resume_for_new_session(self, session) -> None:
        await self._session_manager.resume_for_new_session(session)

    async def recover_for_existing_session(self, session) -> None:
        """Recover an existing session checkpoint on a running TeamAgent.

        Unlike recover_from_session which constructs a fresh agent, this
        method reuses the current agent and assumes session.pre_run() has
        already restored checkpoint state. Used for session switches that
        should not unwind the entire team.
        """
        await self._stop_coordination()
        await self._session_manager.recover_for_existing_session(session)

    async def recover_team(self) -> list[str]:
        return await self._recovery_manager.recover_team()

    # ------------------------------------------------------------------
    # Leader config persistence / recovery
    # ------------------------------------------------------------------

    def _persist_leader_config(self, session) -> None:
        self._recovery_manager.persist_leader_config(session)

    def _persist_allocator_state(self) -> None:
        self._recovery_manager.persist_allocator_state(self._session_manager.team_session)

    @classmethod
    def recover_from_session(
        cls,
        session,
        team_name: str,
        runtime_spec: TeamAgentSpec | None = None,
    ) -> "TeamAgent":
        """Reconstruct a leader TeamAgent from a session checkpoint.

        Args:
            session: Prepared agent team session whose checkpoint was already
                restored via ``pre_run``.
            team_name: Identifies which team's bucket to load. A session can
                hold state for multiple teams; the caller must specify which.
            runtime_spec: Optional live spec from the current process. Used to
                reinject non-serializable fields (currently ``agent_customizer``,
                which is ``Field(exclude=True)`` and never survives the
                checkpoint round-trip). When omitted the recovered spec is
                used as-is.

        Raises:
            ValueError: When the session has no bucket for ``team_name`` or
                the bucket is missing the leader spec.
        """
        from openjiuwen.agent_teams.runtime.metadata import read_team_namespace
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        bucket = read_team_namespace(session, team_name)
        if bucket is None:
            raise ValueError(f"No persisted state for team '{team_name}' in session")
        spec_data = bucket.get("spec")
        if spec_data is None:
            raise ValueError(f"No leader spec found for team '{team_name}'")
        spec = TeamAgentSpec.model_validate(spec_data)
        # agent_customizer is a Callable marked Field(exclude=True); it is
        # dropped on serialization and always None after model_validate. Cold
        # recover must reinject it from the live runtime spec, otherwise
        # platform adapters that hook rails/tools through this callback get
        # silently disabled across process restarts.
        if runtime_spec is not None and runtime_spec.agent_customizer is not None:
            spec.agent_customizer = runtime_spec.agent_customizer
        context = TeamRuntimeContext.model_validate(bucket["context"])

        agent_spec = spec.agents.get(context.role.value) or spec.agents["leader"]
        card_id = f"{team_name}_{context.member_name}" if context.member_name else "leader"
        card = agent_spec.card or AgentCard(
            id=card_id,
            name=context.member_name or "leader",
        )
        agent = cls(card)
        agent.configure(spec, context)
        allocator_state = bucket.get("model_allocator_state")
        if allocator_state:
            agent.restore_allocator_state(allocator_state)
        # Inject session_id into the agent_teams contextvar so the immediately
        # following ``recover_team`` flow (and its restart_teammate -> spawn
        # chain) can read it via ``get_session_id``. We deliberately do NOT
        # take a Token here: this is a classmethod and the bind / release
        # contract is owned by ``SessionManager``; the caller's context is
        # short-lived (manager._apply_action) and the pool entry that holds
        # the leader will eventually go through bind_session for proper
        # Token-managed lifecycle.
        from openjiuwen.agent_teams.context import set_session_id

        set_session_id(session.get_session_id())
        return agent


__all__ = ["TeamAgent"]
