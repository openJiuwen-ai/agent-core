# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent Team Module

This module implements Agent Team which manages team members, tasks, and messages.
"""

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    List,
    Optional,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.models.allocator import Allocation

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.interaction.bridge_protocol import BridgeProtocolAdapter
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    MemberCanceledEvent,
    MemberShutdownEvent,
    MemberSpawnedEvent,
    TeamCleanedEvent,
    TeamCreatedEvent,
    TeamTopic,
    ToolApprovalResultEvent,
)
from openjiuwen.agent_teams.schema.status import (
    MEMBER_SETTLED_STATUSES,
    ExecutionStatus,
    MemberMode,
    MemberStatus,
    TaskStatus,
)
from openjiuwen.agent_teams.schema.team import (
    BridgeMailboxInjectMode,
    BridgeMemberSpec,
    ExternalCliAgentSpec,
    MemberOpResult,
    MemberRosterEntry,
    TeamCompletionSnapshot,
    TeamMemberSpec,
    TeamRole,
)
from openjiuwen.agent_teams.tools.database import (
    TASK_TERMINAL_STATUSES,
    Team,
    TeamDatabase,
    TeamMember,
)
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@dataclass
class CapabilityOverrides:
    """Runtime capability overrides for a single build_team call.

    Flags default to None, meaning "inherit the spec ceiling". Pass
    True/False to explicitly enable or disable the capability for this run.
    The dispatch mode is NOT overridable here — it is static spec
    configuration (F_62): prompts and tool shapes are assembled per mode at
    build time, so one team instance never changes mode at runtime.
    """

    enable_hitt: bool | None = None
    enable_bridge: bool | None = None
    enable_task_verification: bool | None = None


class TeamBackend:
    """Agent Team Manager

    This class manages an existing team and its members, tasks, and messages.

    Attributes:
        team_name: Team identifier
        member_name: Current member identifier
        is_leader: Whether current member is the leader
        db: Team database instance
        task_manager: Task manager instance
    """

    def __init__(
        self,
        team_name: str,
        member_name: str,
        is_leader: bool,
        db: TeamDatabase,
        messager: Messager,
        teammate_mode: MemberMode = MemberMode.BUILD_MODE,
        predefined_members: list[TeamMemberSpec] | None = None,
        model_config_allocator: Optional[Callable[[Optional[str]], Optional["Allocation"]]] = None,
        leader_allocation: Optional["Allocation"] = None,
        enable_hitt: bool = False,
        enable_bridge: bool = False,
        *,
        dispatch_mode: str = "autonomous",
        enable_task_verification: bool = False,
        enable_fork: bool = False,
        external_cli_agents: list[ExternalCliAgentSpec] | None = None,
        on_before_team_cleaned: Callable[[], Awaitable[None]] | None = None,
        on_team_cleaned: Callable[[], Awaitable[None]] | None = None,
        on_team_built: Callable[[], Awaitable[None]] | None = None,
        plan_storage_dir: str | None = None,
        plan_id: str | None = None,
        leader_member_name: str | None = None,
        leader_prompt: str = "",
    ):
        """Initialize agent team manager.

        Args:
            team_name: Team identifier.
            member_name: Current member identifier.
            is_leader: Whether current member is the leader.
            db: TeamDatabase.
            messager: Messager instance for event publishing.
            teammate_mode: Default execution mode for spawned teammates.
            predefined_members: Pre-configured teammates to register
                during ``build_team``.
            model_config_allocator: Callback that returns the next
                ``Allocation`` for teammate allocation. Receives an
                optional ``model_name`` hint forwarded from the spawn
                site (predefined member spec or ``spawn_member`` tool
                argument); ``RoundRobinModelAllocator`` ignores the
                hint, ``ByModelNameAllocator`` requires it.
            leader_allocation: Pre-allocated ``Allocation`` for the
                leader member. Persisted on the leader's DB row in
                ``build_team`` as ``{model_name, model_index}`` so the
                assignment is auditable and survives full-restart
                recovery via positional lookup against the live pool.
            enable_hitt: Spec-level HITT capability ceiling. When
                False, every human-agent spawn path returns failure;
                when True, the runtime instance flag (mutated by
                ``build_team``) decides whether the capability is
                actually engaged.
            enable_bridge: Spec-level Bridge-Agent capability ceiling.
                Symmetric to ``enable_hitt`` for the bridge feature.
                When False, ``spawn_bridge_agent`` returns failure and
                predefined BRIDGE_AGENT members are skipped at
                ``build_team`` time.
            dispatch_mode: The team's dispatch mode, straight from
                ``TeamAgentSpec.dispatch_mode`` (F_62). Static configuration —
                identical on every member process; selects ``verify_task``'s
                verdict policy through the task manager.
            enable_task_verification: Spec-level "verification expected"
                ceiling (F_62); ``build_team`` may override the runtime
                instance flag, mirroring ``enable_hitt``.
            enable_fork: Spec-level context-inheritance capability gate
                (``TeamAgentSpec.enable_fork``). When False the
                ``checkpoint`` tool is not wired and ``spawn_teammate``
                carries neither the fork properties nor the fork prose.
                Unlike ``enable_hitt``, there is no ``build_team`` override:
                fork is an execution-time optimization, not a team-shape
                decision the leader gets to make per instance.
            external_cli_agents: Static launch configs for external CLI
                agents (``TeamAgentSpec.external_cli_agents``). The
                non-empty set of declared ``cli_agent`` names is the
                capability ceiling for external-CLI members:
                ``spawn_external_cli_agent`` rejects any ``cli_agent`` not
                declared here.
            on_before_team_cleaned: Optional async callback fired on the
                ``clean_team`` SUCCESS path before the team DB row is
                deleted. Use this for cleanup that still needs member rows
                and their metadata, such as session-scoped worktree finalize.
            on_team_cleaned: Optional async callback fired exactly once
                on the ``clean_team`` SUCCESS path. NOT fired on the early
                ``return False`` path (active members remain). The hosting
                ``TeamAgent`` wires this to ``_mark_team_cleaned`` so the
                leader's StreamController can end the round
                deterministically — the racy ``on_cleaned`` bus event is
                deliberately not relied on for the leader.
                The callback is invoked immediately after the team DB row
                is deleted, before best-effort cleanup and event publishing.
            on_team_built: Optional async callback fired exactly once after
                ``build_team`` creates the team row and initial members.
            leader_prompt: The leader's private prompt (``LeaderSpec.prompt``
                via ``ctx.prompt``). Persisted on the leader's DB row at
                ``build_team`` so cold-recovery — which rebuilds the leader
                context from the DB — restores the same private prompt. The
                public ``leader_desc`` is supplied separately by the
                ``build_team`` caller (LLM-filled tool arg).
        """
        self.team_name = team_name
        self.member_name = member_name
        self.is_leader = is_leader
        self.leader_member_name = str(leader_member_name or (member_name if is_leader else "")).strip()
        # Lazily-resolved leader name for members that were not handed one at
        # construction. The leader is fixed for a team's life, so the DB row is
        # queried once and cached. See ``resolve_leader_member_name``.
        self._leader_name_cache: str | None = None
        self.db = db
        self.messager = messager
        self.teammate_mode = teammate_mode
        self.predefined_members = predefined_members or []
        self._allocate_model_config = model_config_allocator
        self.leader_allocation = leader_allocation
        # Leader's private prompt (LeaderSpec.prompt via ctx.prompt). Persisted
        # on the leader's DB row at build_team so cold-recovery, which rebuilds
        # the leader context from the DB, restores the same private prompt.
        self._leader_prompt = leader_prompt
        # HITT capability ceiling (immutable, from spec) and the runtime
        # effective flag that ``build_team`` may downgrade. All human-agent
        # creation paths gate on ``_enable_hitt``; the spec ceiling is
        # consulted only when ``build_team(enable_hitt=True)`` tries to
        # enable beyond it.
        self._spec_enable_hitt: bool = enable_hitt
        self._enable_hitt: bool = enable_hitt
        # Bridge capability ceiling — symmetric to HITT. Predefined
        # BRIDGE_AGENT members are registered at ``build_team`` only
        # when ``_enable_bridge`` is True; ``spawn_bridge_agent`` gates
        # on the same flag for dynamic spawn.
        self._spec_enable_bridge: bool = enable_bridge
        self._enable_bridge: bool = enable_bridge
        # Dispatch mode (F_62): static spec configuration, identical on every
        # member process. Prompts and tool shapes are assembled per mode at
        # build time; nothing flips it at runtime.
        self.dispatch_mode: str = dispatch_mode
        # Verification expectation (F_62): spec ceiling + runtime effective
        # flag, mirroring the ``enable_hitt`` pattern.
        self._spec_enable_task_verification: bool = enable_task_verification
        self._enable_task_verification: bool = enable_task_verification
        # True once build_team took over a team that already existed rather
        # than creating one, so the tool result can say which it was.
        self._team_taken_over: bool = False
        # True when this run restored a conversation that already contains the
        # build_team result -- a cold recovery. Set by
        # ``TeamAgent.recover_from_session``; see ``rejects_rebuild``.
        self._history_restored: bool = False
        # Fired once on the build_team / clean_team success paths so the
        # hosting TeamAgent can persist DB lifecycle state and latch
        # state.team_cleaned deterministically inside the leader's round.
        self._on_before_team_cleaned = on_before_team_cleaned
        self._on_team_cleaned = on_team_cleaned
        self._on_team_built = on_team_built

        self.task_manager = TeamTaskManager(
            self.team_name,
            member_name,
            self.db,
            messager,
            plans_dir=plan_storage_dir,
            team_plan_id=plan_id,
            leader_member_name=self.leader_member_name,
            dispatch_mode=dispatch_mode,
        )
        # Per-human-agent callback fired by the leader's dispatcher when
        # a team-side message reaches the avatar — see
        # ``register_human_agent_inbound`` for the registration surface.
        # Holds raw callables (not wrapped) so the dispatcher can decide
        # async vs sync invocation at call time.
        self._human_agent_inbound_callbacks: dict[str, Any] = {}
        # Bridge-agent registry. ``_bridge_member_specs`` indexes the
        # ``BridgeMemberSpec`` rows by member_name so the coordination
        # message handler can read ``mailbox_inject_mode`` /
        # ``protocol`` / ``adapter_config`` at deliver time without
        # re-walking the predefined list. Seeded from
        # ``predefined_members`` so restart paths reconstruct the
        # index without replaying spawn.
        self._bridge_member_specs: dict[str, BridgeMemberSpec] = {
            m.member_name: m for m in self.predefined_members if isinstance(m, BridgeMemberSpec)
        }
        # Concrete protocol adapter per bridge member. Phase-1 stays
        # empty; SDK injects via ``set_bridge_adapter`` when an adapter
        # implementation lands. ``None`` is allowed and means "no
        # adapter wired" — the auto-forward path then substitutes
        # ``REMOTE_UNAVAILABLE_SENTINEL`` so the bridge degrades to a
        # normal teammate.
        self._bridge_adapters: dict[str, BridgeProtocolAdapter] = {}
        # External-CLI member registry: member_name -> cli_agent backend name.
        # A member listed here is driven by an external backend instead of a
        # local DeepAgent. Runtime recovery restores this process-local index
        # from ``TeamMember.options["cli_agent"]``.
        self._external_cli_specs: dict[str, str] = {}
        # Static per-CLI launch configs from the spec, keyed by cli_agent
        # name. The non-empty key set is the capability ceiling: spawning an
        # external-CLI member requires a matching config here. The spawn path
        # reads the matched config (command / cwd / mcp injection / env) to
        # launch the subprocess.
        self._external_cli_configs: dict[str, ExternalCliAgentSpec] = {
            c.cli_agent: c for c in (external_cli_agents or [])
        }
        self.message_manager = TeamMessageManager(
            self.team_name,
            member_name,
            self.db,
            messager,
        )

        # Filesystem paths to remove when the team is cleaned.
        # Populated by the hosting TeamAgent once the actual (possibly
        # user-customized) workspace / member-workspace directories are
        # resolved, so ``clean_team`` wipes the real locations instead of
        # only the default ones.
        self._cleanup_paths: set[str] = set()

        # Fork / checkpoint support. ``_enable_fork`` is the capability gate:
        # the tool factory reads it to decide whether ``checkpoint`` is wired
        # and whether ``spawn_teammate`` exposes the fork properties at all,
        # so everything below stays dormant when fork is off.
        self._enable_fork: bool = enable_fork
        self._pending_forks: dict[str, dict] = {}    # member_name → {fork, since, source}
        self._checkpoints: dict[str, int] = {}       # name → message_count
        self._snapshot_length: Callable[[], int] | None = None
        self._store_checkpoint_fn: Callable[[str, int], None] | None = None

        team_logger.info(f"AgentTeam manager initialized for {team_name}, member={member_name}")

    def register_cleanup_path(self, path: Optional[str]) -> None:
        """Register a filesystem path to remove on ``clean_team``.

        Accepts absolute directory paths. No-ops for empty or None input.
        Idempotent: the same path is only stored once.
        """
        if not path:
            return
        self._cleanup_paths.add(str(Path(path).expanduser()))

    # ------------------------------------------------------------------
    # Fork / checkpoint support
    # ------------------------------------------------------------------

    def fork_enabled(self) -> bool:
        """Whether context inheritance (fork) is open for this team.

        Straight from ``TeamAgentSpec.enable_fork`` — no ``build_team``
        override, unlike ``hitt_enabled`` / ``bridge_enabled``. Single gate
        signal for all three fork surfaces: the ``checkpoint`` tool, the
        fork properties on ``spawn_teammate``'s schema, and the fork section
        of its description.
        """
        return self._enable_fork

    def set_snapshot_length(self, fn) -> None:
        """Register the callback that returns this member's message count."""
        self._snapshot_length = fn

    def set_store_checkpoint_fn(self, fn) -> None:
        """Register the callback for persisting a named checkpoint."""
        self._store_checkpoint_fn = fn

    def mark_fork_on_spawn(
        self,
        member: str,
        fork_value,
        *,
        fork_source: str | None = None,
        compact: bool = False,
    ) -> None:
        self._pending_forks[member] = {
            "fork": fork_value,
            "since": None,
            "source": fork_source,
            "compact": compact,
        }
        team_logger.debug(
            "[fork] mark_fork_on_spawn: member=%s fork=%s source=%s "
            "compact=%s team_name=%s pending_keys=%s",
            member, fork_value, fork_source, compact,
            self.team_name, list(self._pending_forks.keys()),
        )

    def consume_fork_on_spawn(self, member: str) -> dict | None:
        result = self._pending_forks.pop(member, None)
        team_logger.debug(
            "[fork] consume_fork_on_spawn: member=%s result=%s "
            "remaining_pending=%s team_name=%s",
            member, result, list(self._pending_forks.keys()),
            self.team_name,
        )
        return result

    def snapshot_context_length(self) -> int:
        if self._snapshot_length is not None:
            result = self._snapshot_length()
            team_logger.debug(
                "[fork] snapshot_context_length: member=%s len=%d",
                self.member_name, result,
            )
            return result
        team_logger.debug(
            "[fork] snapshot_context_length: member=%s NO callback",
            self.member_name,
        )
        return 0

    def store_checkpoint(self, name: str, count: int) -> None:
        team_logger.debug(
            "[fork] store_checkpoint: member=%s name=%s count=%d "
            "has_store_fn=%s",
            self.member_name, name, count,
            self._store_checkpoint_fn is not None,
        )
        if self._store_checkpoint_fn is not None:
            self._store_checkpoint_fn(name, count)
        else:
            self._checkpoints[name] = count

    def get_checkpoints(self) -> dict[str, int]:
        return dict(self._checkpoints)

    # ------------------------------------------------------------------

    async def _remove_cleanup_paths(self) -> None:
        """Remove every registered cleanup path with ``shutil.rmtree``.

        Sorts paths by depth (deepest first) so that a parent directory
        and its descendants both get removed cleanly even if the caller
        registered overlapping entries.  Failures are logged and do not
        abort the overall cleanup.
        """
        if not self._cleanup_paths:
            return

        ordered = sorted(
            self._cleanup_paths,
            key=lambda p: len(Path(p).parts),
            reverse=True,
        )
        for raw in ordered:
            target = Path(raw)
            if not target.is_dir():
                continue
            try:
                await asyncio.to_thread(shutil.rmtree, str(target))
                team_logger.info(f"Removed team filesystem path: {target}")
            except Exception as e:
                team_logger.error(f"Failed to remove path {target}: {e}")

    async def spawn_member(
        self,
        member_name: str,
        display_name: str,
        agent_card: AgentCard,
        *,
        desc: Optional[str] = None,
        prompt: Optional[str] = None,
        status: MemberStatus = MemberStatus.UNSTARTED,
        execution_status: ExecutionStatus = ExecutionStatus.IDLE,
        mode: MemberMode = MemberMode.BUILD_MODE,
        allocation: Optional["Allocation"] = None,
        role: TeamRole = TeamRole.TEAMMATE,
        isolation: Optional[str] = None,
        cli_agent: Optional[str] = None,
        permissions_override: Optional[dict[str, str]] = None,
    ) -> MemberOpResult:
        """Create a team member record in the database.

        Only persists the member data — does NOT start the member.
        Call ``startup`` to launch all unstarted members.

        Args:
            member_name: Unique member identifier (semantic slug, e.g. "backend-dev-1").
            display_name: Human-readable display label for the member.
            agent_card: Agent card defining the agent.
            desc: Public member description.
            prompt: Startup instruction for the member.
            status: Initial member status.
            execution_status: Initial execution status.
            mode: Member mode (BUILD_MODE or PLAN_MODE).
            allocation: Pool allocation for this member; persisted as a
                ``{model_name, model_index}`` reference so credentials
                can refresh in-place via the live session pool. ``None``
                when the team is not configured with a pool, in which
                case the member uses its per-agent default model.
            role: ``TeamRole`` enum value persisted on the member row.
                Defaults to ``TEAMMATE`` for the ordinary teammate
                spawn paths; ``spawn_human_agent`` overrides with
                ``HUMAN_AGENT`` so the role survives cold recovery.
            cli_agent: External CLI backend name for external members.
                Persisted so stopped or cold-recovered teams can rebuild
                member runtime routing without relying on process memory.
            permissions_override: Flat ``{tool_name: level_string}`` dict
                from ``spawn_teammate.permissions``.  Only tightening
                rules are valid (see ``narrow_permissions``).  Persisted
                as JSON on the member row so it survives process restarts.

        Returns:
            ``MemberOpResult`` describing the outcome. ``__bool__`` falls
            through to ``ok`` so legacy ``if await spawn_member(...): ...``
            patterns keep working.
        """
        existing = await self.db.member.get_member(member_name, self.team_name)
        if existing is not None:
            return MemberOpResult.fail(f"Member {member_name} already exists in team {self.team_name}")
        if isolation is not None and isolation != "worktree":
            return MemberOpResult.fail("Invalid isolation: expected 'worktree' or None")

        if not await self.db.team.team_exists(self.team_name):
            return MemberOpResult.fail(
                f"Team {self.team_name} does not exist; call build_team first"
            )

        from openjiuwen.agent_teams.tools.member_options import build_member_options

        options = build_member_options(
            model_ref=allocation.to_db_ref() if allocation is not None else None,
            cli_agent=cli_agent,
            worktree_isolation=isolation,
            permissions_override=permissions_override,
        )

        success = await self.db.member.create_member(
            member_name=member_name,
            team_name=self.team_name,
            display_name=display_name,
            agent_card=agent_card.model_dump_json(),
            status=status,
            role=role.value,
            desc=desc,
            execution_status=execution_status,
            mode=mode.value,
            prompt=prompt,
            options=options,
        )
        if not success:
            return MemberOpResult.fail(f"Database rejected create_member for {member_name} in team {self.team_name}")

        team_logger.info(f"Member {member_name} created successfully")
        return MemberOpResult.success()

    async def _spawn_and_publish(
        self,
        member_name: str,
        on_created: Callable[[str], Awaitable[None]],
    ) -> None:
        """Spawn a member agent and publish MemberSpawnedEvent.

        Shared helper for startup() and startup_member().
        Event publish failure is logged but does not raise.
        """
        await on_created(member_name)

        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(
                    MemberSpawnedEvent(
                        team_name=self.team_name,
                        member_name=member_name,
                    ),
                ),
            )
            team_logger.debug("Member spawned event published: {}", member_name)
        except Exception as e:
            team_logger.error("Failed to publish member spawned event for {}: {}", member_name, e)

        team_logger.info("Member {} started", member_name)

    async def startup(
        self,
        on_created: Callable[[str], Awaitable[None]],
    ) -> list[str]:
        """Start all unstarted members.

        Finds every member whose status is UNSTARTED and starts
        each via startup_member (which uses STARTING CAS guard).
        On spawn failure, startup_member rolls back STARTING→UNSTARTED
        and re-raises.

        Args:
            on_created: Callback that receives a member_name and
                launches the corresponding agent process.

        Returns:
            List of member_names that were started.
        """
        unstarted = await self.db.member.get_team_members(self.team_name, status=MemberStatus.UNSTARTED)
        started: list[str] = []
        for member in unstarted:
            await self.startup_member(member.member_name, on_created)
            started.append(member.member_name)
        return started

    async def startup_member(
        self,
        member_name: str,
        on_created: Callable[[str], Awaitable[None]],
    ) -> bool:
        """Start a single UNSTARTED member.

        Atomically transitions UNSTARTED→STARTING in DB first (CAS
        guard), then invokes on_created to spawn the agent. If the
        transition fails (member not found, not UNSTARTED, or already
        STARTING/READY), returns False immediately — a concurrent
        startup path already owns the spawn. If on_created raises,
        rolls back STARTING→UNSTARTED so the member can be retried.

        Args:
            member_name: The member to start.
            on_created: Callback that launches the agent process.

        Returns:
            True if the member was started, False otherwise.
        """
        transitioned = await self.db.member.try_transition_member_status(
            member_name, self.team_name, MemberStatus.UNSTARTED, MemberStatus.STARTING,
        )
        if not transitioned:
            return False

        try:
            await self._spawn_and_publish(member_name, on_created)
        except Exception:
            await self.db.member.try_transition_member_status(
                member_name, self.team_name, MemberStatus.STARTING, MemberStatus.UNSTARTED,
            )
            raise

        return True

    async def approve_plan(
        self,
        plan_id: str,
        approved: bool = True,
        feedback: Optional[str] = None,
    ) -> bool:
        """Approve or reject a member's submitted task plan.

        Args:
            plan_id: Exact member plan submission identifier to review.
            approved: True to approve, False to reject
            feedback: Optional feedback message
        Returns:
            True if successful, False otherwise

        Example:
            success = team.approve_plan(
                plan_id="plan123",
                approved=True,
                feedback="Plan looks good"
            )
        """
        if not plan_id:
            team_logger.error("approve_plan requires plan_id")
            return False

        plan_record = self.task_manager.get_plan_record(plan_id)
        if not plan_record:
            team_logger.error("Plan %s not found", plan_id)
            return False
        member_name = str(plan_record.get("member_name") or "")
        task_id = str(plan_record.get("task_id") or "")
        if not member_name:
            team_logger.error("Plan %s has no member_name", plan_id)
            return False
        member_data = await self.db.member.get_member(member_name, self.team_name)
        if member_data is None:
            team_logger.error(f"Member {member_name} not found in team {self.team_name}")
            return False

        team_logger.info(
            "Approving plan for member {}: approved={}, task_id={}, plan_id={}, feedback={}",
            member_name,
            approved,
            task_id,
            plan_id,
            feedback,
        )
        result = await self.task_manager.approve_plan(
            plan_id=plan_id,
            approved=approved,
            feedback=feedback or "",
            leader_name=self.member_name,
        )
        if not result.ok:
            team_logger.error("Failed to approve/reject plan {}: {}", plan_id, result.reason)
            return False

        team_logger.info(
            "Plan approval state updated for member {}, approved={}, task_id={}",
            member_name,
            approved,
            task_id,
        )
        return True

    async def approve_tool(
        self,
        member_name: str,
        tool_call_id: str,
        approved: bool,
        feedback: Optional[str] = None,
        auto_confirm: bool = False,
    ) -> bool:
        """Approve or reject one interrupted teammate tool call."""
        member_data = await self.db.member.get_member(member_name, self.team_name)
        if member_data is None:
            team_logger.error(f"Member {member_name} not found in team {self.team_name}")
            return False

        # DB message (protocol=json): carries detailed approval data for
        # teammate to read when resuming from interrupt.  This is the
        # fallback delivery path if the pub-sub event is lost.
        approval_payload = json.dumps({
            "type": "tool_approval_result",
            "tool_call_id": tool_call_id,
            "approved": approved,
            "feedback": feedback or "",
            "auto_confirm": auto_confirm,
        })
        await self.message_manager.send_message(
            content=approval_payload,
            to_member_name=member_name,
            protocol="json",
        )

        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(
                    ToolApprovalResultEvent(
                        team_name=self.team_name,
                        member_name=member_name,
                        tool_call_id=tool_call_id,
                        approved=approved,
                        feedback=feedback or "",
                        auto_confirm=auto_confirm,
                    )
                ),
            )
            team_logger.debug(
                "Tool approval result event published for member {}, tool_call_id={}",
                member_name,
                tool_call_id,
            )
        except Exception as e:
            team_logger.error(
                "Failed to publish tool approval result event for {} / {}: {}",
                member_name,
                tool_call_id,
                e,
            )

        team_logger.info(
            "Tool approval event sent to member {} for tool_call_id={}, approved={}, auto_confirm={}",
            member_name,
            tool_call_id,
            approved,
            auto_confirm,
        )
        return True

    async def shutdown_member(self, member_name: str, force: bool = False) -> MemberOpResult:
        """Shutdown a member.

        Sends a shutdown request to member. Supports interrupting
        member's current execution.

        Team leader calls this to shutdown a member running in a separate process.
        This method:
        1. Updates member status in database (team management layer)
        2. Does NOT update execution_status (managed by member process internally)
        3. Publishes SHUTDOWN event for cross-process notification
        4. Member process receives event and handles its own shutdown sequence

        Args:
            member_name: Member identifier.
            force: Whether to force shutdown (bypass normal shutdown sequence).

        Returns:
            ``MemberOpResult`` describing the outcome. ``__bool__`` falls
            through to ``ok`` so legacy truthy call sites keep working.
        """
        # Check if member exists in database
        member_data = await self.db.member.get_member(member_name, self.team_name)
        if not member_data:
            return MemberOpResult.fail(f"Member {member_name} not found in team {self.team_name}")

        current_status = MemberStatus(member_data.status)

        # Check if already shutdown — idempotent success path
        if current_status == MemberStatus.SHUTDOWN or current_status == MemberStatus.SHUTDOWN_REQUESTED:
            team_logger.debug(
                f"Member {member_name} already shutdown"
                if current_status == MemberStatus.SHUTDOWN
                else f"Member {member_name} is shutting down"
            )
            return MemberOpResult.success()

        # Guard: reject shutdown of a live human agent who still holds active
        # tasks, unless force=True. Models sometimes confuse "cancel this
        # human's task" with "shutdown the human" — without this guard the
        # mistake silently succeeds and the tasks are orphaned.  Regular
        # teammates are intentionally not gated here: their shutdown is a
        # normal lifecycle operation and the leader already expects to
        # manage any leftover tasks afterwards.
        if not force and await self.is_live_human_agent(member_name):
            active_statuses = {
                TaskStatus.PLANNING.value,
                TaskStatus.IN_PROGRESS.value,
                TaskStatus.IN_REVIEW.value,
            }
            owned_tasks = await self.task_manager.get_tasks_by_assignee(member_name=member_name)
            active_tasks = [t for t in owned_tasks if t.status in active_statuses]
            if active_tasks:
                task_ids = ", ".join(t.task_id for t in active_tasks)
                return MemberOpResult.fail(
                    t("team.shutdown_human_active_tasks",
                      member_name=member_name, count=str(len(active_tasks)), task_ids=task_ids)
                )

        # Validate state transition
        from openjiuwen.agent_teams.schema.status import (
            MEMBER_TRANSITIONS,
            is_valid_transition,
        )

        if not is_valid_transition(current_status, MemberStatus.SHUTDOWN_REQUESTED, MEMBER_TRANSITIONS):
            return MemberOpResult.fail(f"Member {member_name} cannot shut down from status '{current_status.value}'")

        team_logger.info(
            f"Shutting down member {member_name}: {current_status.value} -> {MemberStatus.SHUTDOWN_REQUESTED.value}"
            f" (force={force})"
        )

        # Update member status in database (team management layer)
        success = await self.db.member.update_member_status(
            member_name, self.team_name, MemberStatus.SHUTDOWN_REQUESTED.value
        )
        if not success:
            return MemberOpResult.fail(f"Database rejected status update for member {member_name}")

        # Note: execution_status is managed by member process internally
        # Team leader only sets member status and notifies member via message and event
        msg_id = await self.message_manager.send_message(
            content=t("team.shutdown_request_content"),
            to_member_name=member_name,
        )
        if not msg_id:
            team_logger.warning(f"Failed to send shutdown request message to member {member_name}")

        # Publish shutdown event (for cross-process notification to member)
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(
                    MemberShutdownEvent(
                        team_name=self.team_name,
                        member_name=member_name,
                        force=force,
                    )
                ),
            )
            team_logger.debug(f"Member shutdown event published: {member_name}")
        except Exception as e:
            team_logger.error(f"Failed to publish member shutdown event for {member_name}: {e}")

        team_logger.info(f"Shutdown request sent to member {member_name}")
        return MemberOpResult.success()

    async def cancel_member(self, member_name: str) -> bool:
        """Cancel member execution

        Sends a cancellation request to a member who is
        currently executing.

        Args:
            member_name: Member identifier

        Returns:
            True if successful, False otherwise

        Example:
            success = team.cancel_member(member_name="member123")
        """
        # Check if member exists in database
        member_data = await self.db.member.get_member(member_name, self.team_name)
        if not member_data:
            team_logger.error(f"Member {member_name} not found in team {self.team_name}")
            return False

        current_status = MemberStatus(member_data.status)

        # Only send cancel event if member is busy
        if current_status != MemberStatus.BUSY:
            team_logger.info(
                f"Member {member_name} is not busy (status: {current_status.value}), no need to cancel execution"
            )
            return True

        team_logger.info(f"Cancelling execution for member {member_name}")

        # Reset all active (PLANNING / IN_PROGRESS / IN_REVIEW) tasks held by
        # this member back into the claimable pool.
        active_statuses = {
            TaskStatus.PLANNING.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.IN_REVIEW.value,
        }
        owned_tasks = await self.task_manager.get_tasks_by_assignee(member_name=member_name)
        reset_count = 0
        for task in owned_tasks:
            if task.status not in active_statuses:
                continue
            if await self.task_manager.reset(task.task_id):
                reset_count += 1
                team_logger.info(f"Reset task {task.task_id} from member {member_name}")

        if reset_count > 0:
            team_logger.info(f"Reset {reset_count} tasks from member {member_name}")

        success = await self.message_manager.send_message(
            content=t("team.cancel_request_content"), to_member_name=member_name
        )
        if not success:
            team_logger.error(f"Failed to send cancel request message to member {member_name}")
            return False

        # Publish cancel event (for cross-process notification to member)
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(MemberCanceledEvent(team_name=self.team_name, member_name=member_name)),
            )
            team_logger.debug(f"Member canceled event published: {member_name}")
        except Exception as e:
            team_logger.error(f"Failed to publish member canceled event for {member_name}: {e}")

        team_logger.info(f"Cancel request sent to member {member_name}")
        return True

    async def clean_team(self) -> bool:
        """Clean up team (Team.cleanup)

        When all team members are in SHUTDOWN status, remove team
        from team_info table (cascade delete will remove related records).
        Publishes TeamEvent.Cleaned.

        Returns:
            True if successful, False otherwise

        Example:
            success = team.clean_team()
        """
        # Check if all members are shutdown
        all_shutdown = True
        members = await self.db.member.get_team_members(self.team_name)
        for member_data in members:
            if member_data.member_name == self.member_name:
                continue
            if member_data.status != MemberStatus.SHUTDOWN.value:
                member_name = member_data.member_name
                team_logger.info(f"Member {member_name} is not shutdown (status: {member_data.status})")
                all_shutdown = False
                break

        if not all_shutdown:
            team_logger.error(f"Cannot clean team {self.team_name}: not all members are shutdown")
            return False

        if self._on_before_team_cleaned is not None:
            try:
                await self._on_before_team_cleaned()
            except Exception as e:
                team_logger.error(f"on_before_team_cleaned callback failed for team {self.team_name}: {e}")
                return False

        # Delete team from database
        await self.db.team.delete_team(self.team_name)

        # Notify the hosting TeamAgent as soon as the DB row is gone so
        # the checkpoint mirrors the durable source of truth before any
        # best-effort filesystem cleanup or event publishing.
        if self._on_team_cleaned is not None:
            try:
                await self._on_team_cleaned()
            except Exception as e:
                team_logger.error(f"on_team_cleaned callback failed for team {self.team_name}: {e}")

        # Remove registered filesystem paths for the team.  TeamAgent
        # registers actual resolved workspace/output paths, not the whole
        # team_home parent: team_home contains per-session state such as
        # session-scoped worktrees, and deleting the parent would cross
        # session boundaries.  ``shutil.rmtree`` does not follow symlinks,
        # so independent member workspaces linked in from
        # ``~/.openjiuwen/{member_name}_workspace/`` are preserved.
        await self._remove_cleanup_paths()

        # Publish team cleaned event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(TeamCleanedEvent(team_name=self.team_name)),
            )
            team_logger.debug(f"Team cleaned event published: {self.team_name}")
        except Exception as e:
            team_logger.error(f"Failed to publish team cleaned event for {self.team_name}: {e}")

        team_logger.info(f"Team {self.team_name} cleaned successfully")

        return True

    async def force_clean_team(self, shutdown_members: bool = True) -> bool:
        """Force cleanup for the current session's team state.

        Unlike ``clean_team()``, this method does not wait for every
        member to reach SHUTDOWN. It can be used during session
        switching to aggressively discard the old team's runtime and
        persisted session data.
        """
        if shutdown_members:
            members = await self.db.member.get_team_members(self.team_name)
            for member_data in members:
                if member_data.member_name == self.member_name:
                    continue
                try:
                    await self.shutdown_member(member_data.member_name, force=True)
                except Exception as e:
                    team_logger.warning(
                        "Failed to request shutdown for member {} during force cleanup: {}",
                        member_data.member_name,
                        e,
                    )

        success = await self.db.force_delete_team_session(self.team_name)

        try:
            await self._remove_cleanup_paths()
        except Exception as e:
            team_logger.error("Failed to remove cleanup paths for {}: {}", self.team_name, e)
            success = False

        if success:
            team_logger.info(f"Team {self.team_name} force cleaned successfully")
        return success

    async def get_member(self, member_name: str) -> Optional[TeamMember]:
        """Get a member by ID

        Args:
            member_name: Member identifier

        Returns:
            TeamMember info or None
        """
        return await self.db.member.get_member(member_name, self.team_name)

    async def member_exists(self, member_name: str) -> bool:
        """Check whether a member exists without loading its full row.

        For callers that only need presence (recipient validation, roster
        membership tests). Avoids ``get_member`` pulling ``agent_card`` /
        ``prompt`` / ``options`` just to test for ``None``.

        Args:
            member_name: Member identifier.

        Returns:
            True when the member exists in this team.
        """
        return await self.db.member.member_exists(member_name, self.team_name)

    async def resolve_leader_member_name(self) -> str:
        """Return the team's leader member_name, from the DB row when needed.

        The leader is a persistent property of the team — ``build_team`` writes
        it to the ``team_info`` row — so it is the single source of truth and
        does not need threading through spawn / descriptor plumbing. A leader
        (or a backend handed the name at construction) already knows it and
        skips the query; a plain member resolves it from the row and caches the
        result, since the leader never changes for the life of a team.

        Returns:
            The leader member_name, or ``""`` when the team row is absent.
        """
        if self.leader_member_name:
            return self.leader_member_name
        if self._leader_name_cache is None:
            team = await self.db.team.get_team(self.team_name)
            self._leader_name_cache = (team.leader_member_name if team else "") or ""
        return self._leader_name_cache

    async def list_members(self) -> List[TeamMember]:
        """List all team members

        Returns:
            List of TeamMember info
        """
        members = await self.db.member.get_team_members(self.team_name)
        return [member for member in members if member.member_name != self.member_name]

    async def list_member_roster(self) -> List[MemberRosterEntry]:
        """List the roster (name / display name / status) excluding self.

        Backs the ``list_members`` tool. Uses a narrow column projection
        (``member.get_member_roster``) instead of loading full
        ``TeamMember`` rows, since the roster view only needs these three
        fields — avoids pulling every member's ``agent_card`` / ``prompt`` /
        ``options`` out of the DB on each call.

        Returns:
            One ``MemberRosterEntry`` per member, the calling member removed.
        """
        rows = await self.db.member.get_member_roster(self.team_name)
        return [
            MemberRosterEntry(member_name=name, display_name=display_name, status=status)
            for name, display_name, status in rows
            if name != self.member_name
        ]

    async def get_team_info(self) -> Optional[Team]:
        """Get team information

        Returns:
            Team information
        """
        return await self.db.team.get_team(self.team_name)

    async def is_team_completed(self) -> Optional[TeamCompletionSnapshot]:
        """Evaluate whether the whole team has reached a completed state.

        Returns a snapshot only when all three conditions hold at once,
        checked in order task -> member -> message:
            1. At least one task exists and every task is terminal
               (``TASK_TERMINAL_STATUSES``).
            2. Every member -- including the leader -- is in a settled
               status (``MEMBER_SETTLED_STATUSES``).
            3. No message is left unread by any member, broadcasts
               included. Completion is judged strictly: any undelivered
               message -- direct or fan-out broadcast -- blocks the team
               from concluding.

        Read-only; safe to call repeatedly. Queries the member DAO directly
        so the leader itself is part of the roster check (``list_members``
        excludes the calling member).

        Returns:
            A ``TeamCompletionSnapshot`` when the team is complete,
            otherwise ``None``.
        """
        tasks = await self.task_manager.list_tasks()
        if not tasks:
            return None
        if any(task.status not in TASK_TERMINAL_STATUSES for task in tasks):
            return None

        members = await self.db.member.get_team_members(self.team_name)
        if not members:
            return None
        if any(member.status not in MEMBER_SETTLED_STATUSES for member in members):
            return None

        if await self.message_manager.has_unread_messages(include_broadcast=True):
            return None

        return TeamCompletionSnapshot(member_count=len(members), task_count=len(tasks))

    async def get_team_updated_at(self) -> int:
        """Probe ``team_info.updated_at`` for change detection.

        Cheap single-column SELECT used by prompt-section caches to
        decide whether to refetch full team metadata.

        Returns:
            Last update timestamp (ms), or ``0`` when missing.
        """
        return await self.db.team.get_team_updated_at(self.team_name)

    async def get_members_max_updated_at(self) -> int:
        """Probe MAX(``team_member.updated_at``) for the team.

        Returns:
            Largest member update timestamp (ms), or ``0`` when no
            members exist.  Status / execution_status updates do not
            bump this value -- only roster mutations do.
        """
        return await self.db.member.get_members_max_updated_at(self.team_name)

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a task and notify assignee if claimed

        Cancels a task in the team. If the task has been claimed by a member,
        sends a notification message to the assignee.

        Args:
            task_id: Task identifier

        Returns:
            True if successful, False otherwise

        Example:
            success = team.cancel_task(task_id="task123")
        """
        # Get task information before cancellation
        task = await self.task_manager.get(task_id)
        if not task:
            team_logger.error(f"Task {task_id} not found")
            return False

        # Check if task is already cancelled
        if task.status == TaskStatus.CANCELLED.value:
            team_logger.info(f"Task {task_id} is already cancelled")
            return True

        # Cancel the task
        cancelled_task = await self.task_manager.cancel(task_id)
        if not cancelled_task:
            team_logger.error(f"Failed to cancel task {task_id}")
            return False

        # Send notification message to assignee if task was claimed
        if task.assignee:
            content = f"Task '{task.title}' (ID: {task_id}) has been cancelled by the team leader."
            success = await self.message_manager.send_message(
                content=content,
                to_member_name=task.assignee,
            )
            if not success:
                team_logger.warning(f"Failed to send cancellation notification to assignee {task.assignee}")
            else:
                team_logger.info(f"Cancellation notification sent to assignee {task.assignee}")

        team_logger.info(f"Task {task_id} cancelled successfully")
        return True

    async def cancel_all_tasks(
        self,
        skip_assignees: Optional[set[str]] = None,
    ) -> int:
        """Cancel all tasks in team atomically

        Cancels all non-cancelled and non-completed tasks in a single transaction.
        After cancellation, sends a broadcast message to all team members.

        The cancel operation is atomic at the database level via task_manager.cancel_all_tasks().

        Args:
            skip_assignees: Member names whose claimed tasks must NOT be
                cancelled. Used to honor HITT's "human_agent-locked"
                guarantee even during batch cancels.

        Returns:
            Number of tasks cancelled

        Example:
            count = await team.cancel_all_tasks()
            # count = 5
        """
        # Cancel all tasks atomically
        cancelled_tasks = await self.task_manager.cancel_all_tasks(
            skip_assignees=skip_assignees,
        )

        if not cancelled_tasks:
            team_logger.info(f"No tasks to cancel in team {self.team_name}")
            return 0

        # Send broadcast message to all team members
        broadcast_content = f"All tasks ({len(cancelled_tasks)}) have been cancelled by team leader."
        await self.message_manager.broadcast_message(content=broadcast_content)

        team_logger.info(f"Cancelled {len(cancelled_tasks)} tasks in team {self.team_name}")
        return len(cancelled_tasks)

    async def _reattach_team(self, existing: Any) -> None:
        """Take over a team that already exists instead of creating one.

        Reached when the leader calls ``build_team`` against a team whose row is
        already in the static table — in practice a fresh session inheriting a
        running team (``NEW_TEAM_IN_SESSION``). The row, the roster and the
        members' own configuration are all standing facts by then — the
        teammates are re-spawned from that roster by ``recover_team``, not from
        anything decided here — so this path writes no rows and re-registers
        nobody.

        **The capability arguments of this call do not apply.** The team was
        configured when it was built, its members already run under that
        configuration, and its tasks may already be shaped by it (reviewers on a
        verify-gated board). So the effective verification flag is read back off
        the row rather than recomputed, and the leader learns what it actually
        got from the tool result — the same way it learns a narrowed ceiling on
        the create path.

        ``on_team_built`` still fires: the checkpoint of *this* session has to
        record that the team DB row exists, or a later run would dispatch on a
        state that does not match the database. No ``TeamCreated`` event is
        published — nothing was created, and members that acted on it would be
        reacting to an event about a team they are already part of.

        Args:
            existing: The team row read from the static table.
        """
        self._enable_task_verification = bool(existing.enable_task_verification)
        self._team_taken_over = True
        team_logger.info(
            "Team %s already exists; taking it over (task_verification=%s)",
            self.team_name,
            self._enable_task_verification,
        )

        if self._on_team_built is not None:
            try:
                await self._on_team_built()
            except Exception as e:
                team_logger.error(f"on_team_built callback failed for team {self.team_name}: {e}")

    async def build_team(
        self,
        display_name: str,
        desc: str,
        leader_display_name: str,
        leader_desc: str,
        overrides: Optional[CapabilityOverrides] = None,
    ):
        """Create a team and register the leader as a member.

        Creates team in database, writes the leader into the member table,
        then publishes TeamEvent.Created.

        **Idempotent against an existing team**: when the row is already there
        the call takes the team over (see :meth:`_reattach_team`) instead of
        failing, because this is also the one call that hands the leader its
        collaboration policy — and a leader inheriting a team needs that policy
        at least as much as one that just built it.

        Args:
            display_name: Human-readable team label.
            desc: Team goal, scope, and directives.
            leader_display_name: Human-readable display label for the leader member.
            leader_desc: Public description of the leader member (shown in
                peers' roster; the private prompt is passed via the
                ``leader_prompt`` constructor arg, not here).
            overrides: Optional runtime capability overrides. Use
                ``CapabilityOverrides(enable_hitt=True/False)`` to override
                the HITT or bridge capability ceiling for this run; None means
                each flag inherits its spec ceiling. ``dispatch_mode`` follows
                the F_62 rule instead: None -> "autonomous", "scheduled" must
                be explicit and requires the spec ceiling.
        """
        # Step 0: a team that already exists is taken over, not rebuilt. The
        # case this exists for is NEW_TEAM_IN_SESSION -- a fresh session
        # inheriting a team that is already running. A child agent session
        # shares the team session id, so a new session means an empty history:
        # the build_team tool result that carries the collaboration policy is
        # gone, while the team, its roster and its tasks are all still there.
        # Since that call is the single place the policy is handed over (F_76),
        # failing here would leave that leader with the bootstrap alone.
        # (COLD_RECOVER keeps its history and normally does not call build_team
        # again; idempotence is just harmless insurance for it.)
        existing = await self.db.team.get_team(self.team_name)
        if existing is not None:
            await self._reattach_team(existing)
            return

        enable_hitt = overrides.enable_hitt if overrides is not None else None
        enable_bridge = overrides.enable_bridge if overrides is not None else None
        enable_task_verification = overrides.enable_task_verification if overrides is not None else None
        # Step A: enforce spec ceiling
        if enable_hitt is True and not self._spec_enable_hitt:
            from openjiuwen.core.common.exception.codes import StatusCode
            from openjiuwen.core.common.exception.errors import raise_error

            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason=(
                    "build_team(enable_hitt=True) requires TeamAgentSpec.enable_hitt=True "
                    "(capability ceiling). Spec has enable_hitt=False — cannot enable HITT "
                    "at build_team time."
                ),
            )
        if enable_bridge is True and not self._spec_enable_bridge:
            from openjiuwen.core.common.exception.codes import StatusCode
            from openjiuwen.core.common.exception.errors import raise_error

            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason=(
                    "build_team(enable_bridge=True) requires TeamAgentSpec.enable_bridge=True "
                    "(capability ceiling). Spec has enable_bridge=False — cannot enable Bridge "
                    "at build_team time."
                ),
            )

        # Step B: compute effective flag and persist on backend so all
        # downstream spawn paths see a single source of truth.
        effective_enable_hitt = self._spec_enable_hitt if enable_hitt is None else enable_hitt
        self._enable_hitt = effective_enable_hitt
        effective_enable_bridge = self._spec_enable_bridge if enable_bridge is None else enable_bridge
        self._enable_bridge = effective_enable_bridge
        effective_task_verification = (
            self._spec_enable_task_verification
            and (enable_task_verification if enable_task_verification is not None else True)
        )
        self._enable_task_verification = effective_task_verification

        # Create team in database. dispatch_mode is recorded from the spec
        # (informational — the spec is the runtime source of truth, F_62);
        # the effective verification flag is persisted alongside.
        team_name = self.team_name
        leader_member_name = self.member_name
        success = await self.db.team.create_team(
            team_name=team_name,
            display_name=display_name,
            leader_member_name=leader_member_name,
            desc=desc,
            dispatch_mode=self.dispatch_mode,
            enable_task_verification=effective_task_verification,
        )

        if not success:
            raise RuntimeError(f"Failed to create team {team_name}")

        # Register leader as a member — starts busy/running immediately
        leader_card_id = f"{team_name}_{leader_member_name}"
        leader_card = AgentCard(
            id=leader_card_id,
            name=leader_display_name,
            description=leader_desc,
        )
        await self.spawn_member(
            member_name=leader_member_name,
            display_name=leader_display_name,
            agent_card=leader_card,
            desc=leader_desc,
            prompt=self._leader_prompt,
            status=MemberStatus.BUSY,
            execution_status=ExecutionStatus.RUNNING,
            mode=MemberMode.BUILD_MODE,
            allocation=self.leader_allocation,
        )

        # Register predefined teammates (UNSTARTED, launched later via broadcast).
        # Human agents are filtered out and handled by
        # ``_spawn_human_agents`` so they never enter the startup loop.
        # Bridge agents share the teammate registration path (they are
        # full teammates locally) but are skipped if ``enable_bridge``
        # is disabled on this run.
        skipped_bridge_specs: list[BridgeMemberSpec] = []
        for member_spec in self.predefined_members:
            if member_spec.role_type == TeamRole.HUMAN_AGENT:
                continue
            if isinstance(member_spec, BridgeMemberSpec) and not effective_enable_bridge:
                skipped_bridge_specs.append(member_spec)
                # Drop the index entry as well so downstream code does
                # not treat it as a bridge when ``enable_bridge`` is off.
                self._bridge_member_specs.pop(member_spec.member_name, None)
                continue
            member_card_id = f"{team_name}_{member_spec.member_name}"
            member_card = AgentCard(
                id=member_card_id,
                name=member_spec.display_name,
                description=member_spec.desc,
            )
            allocation = self._allocate_model_config(member_spec.model_name) if self._allocate_model_config else None
            await self.spawn_member(
                member_name=member_spec.member_name,
                display_name=member_spec.display_name,
                agent_card=member_card,
                desc=member_spec.desc,
                prompt=member_spec.prompt,
                status=MemberStatus.UNSTARTED,
                execution_status=ExecutionStatus.IDLE,
                mode=self.teammate_mode,
                allocation=allocation,
                role=member_spec.role_type,
            )
        if skipped_bridge_specs:
            team_logger.warning(
                "Skipped %d predefined BRIDGE_AGENT(s) for team %s because "
                "build_team(enable_bridge=False) overrode the spec capability",
                len(skipped_bridge_specs),
                team_name,
            )

        # HITT: register every declared human member when the effective
        # capability is on. When the leader passed enable_hitt=False at
        # build_team time, all predefined HUMAN_AGENT specs are skipped
        # (the ceiling itself stays open per the spec, but this run
        # declined to engage HITT).
        human_specs = [m for m in self.predefined_members if m.role_type == TeamRole.HUMAN_AGENT]
        if effective_enable_hitt:
            for human_spec in human_specs:
                await self.spawn_human_agent(
                    member_name=human_spec.member_name,
                    display_name=human_spec.display_name,
                    desc=human_spec.desc,
                    prompt=human_spec.prompt,
                )
        elif human_specs:
            team_logger.warning(
                "Skipped %d predefined HUMAN_AGENT(s) for team %s because "
                "build_team(enable_hitt=False) overrode the spec capability",
                len(human_specs),
                team_name,
            )

        if self._on_team_built is not None:
            try:
                await self._on_team_built()
            except Exception as e:
                team_logger.error(f"on_team_built callback failed for team {team_name}: {e}")

        # Publish team created event
        session_id = get_session_id()
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(session_id, team_name),
                message=EventMessage.from_event(
                    TeamCreatedEvent(
                        team_name=team_name,
                        display_name=display_name,
                        leader_member_name=leader_member_name,
                        created=TeamDatabase.get_current_time(),
                    )
                ),
            )
            team_logger.debug(f"Team created event published: {team_name}")
        except Exception as e:
            team_logger.error(f"Failed to publish team created event for {team_name}: {e}")

        team_logger.info(f"Team {team_name} created successfully")

    async def spawn_human_agent(
        self,
        *,
        member_name: str,
        display_name: Optional[str] = None,
        desc: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> MemberOpResult:
        """Register a human-agent member as an UNSTARTED team member.

        Public method called by ``build_team`` (for predefined HUMAN_AGENT
        specs) and ``SpawnMemberTool`` (when ``role_type='human_agent'``).
        Human agents share the standard spawn path with teammates so they
        get a real DeepAgent runtime (LLM + tools) the user can drive
        through ``HumanAgentInbox``. Status starts at UNSTARTED so
        ``startup()`` picks them up and the leader's
        ``_on_teammate_created`` callback spawns them just like any other
        member; role-aware rail filtering inside the configurator then
        strips the team-coordination rails (FirstIterationGate /
        TeamToolApprovalRail) and swaps the autonomous-claim path
        (``claim_task``) for a self-only completion tool. The shared
        ``send_message`` is still attached so the user can ask the
        avatar to relay outbound messages; the HITT prompt section
        binds it to user-driven instructions only.

        Args:
            member_name: Unique member identifier for the human.
            display_name: Optional display label; falls back to the
                framework-managed default when omitted.
            desc: Optional member description; falls back to the
                framework default.
            prompt: Optional startup hint forwarded to the avatar.

        Returns:
            ``MemberOpResult``. Returns failure when HITT is disabled
            (``MemberOpResult.fail``) or the underlying member create
            fails. Caller (tool layer) propagates ``reason`` to the LLM.
        """
        if not self._enable_hitt:
            return MemberOpResult.fail(
                "Cannot spawn human agent: HITT capability is disabled "
                "(enable_hitt=False on TeamAgentSpec or build_team)"
            )

        resolved_display_name = display_name or t("hitt.human_agent_display_name")
        resolved_desc = desc or t("hitt.human_agent_default_desc")
        member_card = AgentCard(
            id=f"{self.team_name}_{member_name}",
            name=resolved_display_name,
            description=resolved_desc,
        )
        result = await self.spawn_member(
            member_name=member_name,
            display_name=resolved_display_name,
            agent_card=member_card,
            desc=resolved_desc,
            prompt=prompt,
            status=MemberStatus.UNSTARTED,
            execution_status=ExecutionStatus.IDLE,
            mode=MemberMode.BUILD_MODE,
            role=TeamRole.HUMAN_AGENT,
        )
        if not result.ok:
            team_logger.warning(
                "Failed to register human agent '%s' for team %s: %s",
                member_name,
                self.team_name,
                result.reason,
            )
        return result

    async def is_human_agent(self, member_name: Optional[str]) -> bool:
        """Whether ``member_name`` is a registered human-agent member.

        Queries ``team_member.role`` from DB on every call — no in-memory
        cache, so the answer is always current regardless of when the
        member was spawned.
        """
        if not member_name:
            return False
        member_dao = self.db.member
        if member_dao is None:
            return False
        return await member_dao.is_human_agent(self.team_name, member_name)

    async def is_live_human_agent(self, member_name: str | None) -> bool:
        """Whether ``member_name`` is a human-agent member still on the team.

        Narrower than :meth:`is_human_agent`: a member whose status is in
        ``MEMBER_DEPARTED_STATUSES`` (shutdown requested / shut down) answers
        False. The HITT task lock in ``UpdateTaskTool`` keys on this, so
        shutting a human down releases the tasks it still holds back to the
        leader instead of stranding them.
        """
        if not member_name:
            return False
        member_dao = self.db.member
        if member_dao is None:
            return False
        return await member_dao.is_live_human_agent(self.team_name, member_name)

    async def is_reachable_human_agent(self, member_name: str | None) -> bool:
        """Whether ``member_name`` is a human-agent member that can still be delivered to.

        Looser than :meth:`is_live_human_agent`: only a fully SHUTDOWN member is
        excluded. A member with shutdown merely *requested* is still reachable,
        and has to be — ``shutdown_member`` flips the status before it sends the
        notice, so cutting delivery at the request would drop the very message
        telling that member's controller it was removed.
        """
        if not member_name:
            return False
        member_dao = self.db.member
        if member_dao is None:
            return False
        return await member_dao.is_reachable_human_agent(self.team_name, member_name)

    async def get_member_status(self, member_name: str) -> str | None:
        """Current persisted status of ``member_name``, or None if unknown.

        Narrow projection used by the coordination layer's harness-input gate.
        """
        member_dao = self.db.member
        if member_dao is None:
            return None
        return await member_dao.get_member_status(self.team_name, member_name)

    async def register_human_agent_inbound(
        self,
        member_name: str,
        callback: Optional[Any],
    ) -> None:
        """Register / clear a team→user notification callback for a human agent.

        Phase-2 HITT does not let a human agent's LLM consume team-side
        messages; instead the runtime forwards them to the SDK / business
        layer via this callback. ``callback=None`` removes a prior
        registration. Unknown member names raise ``KeyError`` so typos
        surface immediately rather than silently dropping notifications.
        """
        if not await self.is_human_agent(member_name):
            names = await self.human_agent_names()
            raise KeyError(
                f"'{member_name}' is not a registered human-agent member; "
                f"registered members: {sorted(names)}"
            )
        if callback is None:
            self._human_agent_inbound_callbacks.pop(member_name, None)
        else:
            self._human_agent_inbound_callbacks[member_name] = callback

    def get_human_agent_inbound(self, member_name: str) -> Optional[Any]:
        """Return the inbound callback registered for ``member_name``, if any."""
        return self._human_agent_inbound_callbacks.get(member_name)

    async def human_agent_names(self) -> frozenset[str]:
        """Snapshot of currently registered human-agent member names.

        Queries ``team_member.role`` from DB — no in-memory cache, so
        the answer always reflects the current roster.
        """
        member_dao = self.db.member
        if member_dao is None:
            return frozenset()
        names = await member_dao.list_human_agent_names(self.team_name)
        return frozenset(names)

    async def live_human_agent_names(self) -> frozenset[str]:
        """Snapshot of human-agent members that have not left the team.

        Batch counterpart of :meth:`is_live_human_agent`; members that have
        been shut down are excluded. Used by the cancel-all path so the
        leftovers of a departed human are cancelled like any other member's.
        """
        member_dao = self.db.member
        if member_dao is None:
            return frozenset()
        names = await member_dao.list_live_human_agent_names(self.team_name)
        return frozenset(names)

    async def reachable_human_agent_names(self) -> frozenset[str]:
        """Snapshot of human-agent members that can still be delivered to.

        Batch counterpart of :meth:`is_reachable_human_agent`; only fully
        SHUTDOWN members are excluded. Used to fan a broadcast out to human
        controllers.
        """
        member_dao = self.db.member
        if member_dao is None:
            return frozenset()
        names = await member_dao.list_reachable_human_agent_names(self.team_name)
        return frozenset(names)

    def hitt_enabled(self) -> bool:
        """Whether the HITT capability is currently engaged for this team.

        Reflects the runtime effective flag (set by ``TeamAgentSpec`` and
        possibly downgraded by ``build_team(enable_hitt=False)``), not
        the live roster. Used by tools and rails to decide whether
        human-agent operations are admissible at all — gating on this
        flag avoids the chicken-and-egg of "no humans yet, so HITT looks
        off" while ``spawn_human_agent`` waits to be called.
        """
        return self._enable_hitt

    def mark_history_restored(self) -> None:
        """Record that this run restored a conversation containing the policy.

        Called on the cold-recovery path (``TeamAgent.recover_from_session``),
        which continues the *same* session: the child agent session shares the
        team session id, so the leader's history comes back with the original
        ``build_team`` result -- and therefore the collaboration policy -- still
        in it. See :meth:`rejects_rebuild`.
        """
        self._history_restored = True

    async def rejects_rebuild(self) -> bool:
        """Whether ``build_team`` must refuse to run on this team.

        A cold recovery already holds the policy in its restored history, and
        that result is never compacted away, so calling ``build_team`` again
        buys nothing and costs a round -- and a leader that does it is usually
        confused about whether its team exists, which the refusal corrects.

        Both conditions are required. ``_history_restored`` alone is not
        enough: a recovered leader whose team was disbanded mid-run (the
        all-teammates-SHUTDOWN path in ``CoordinationKernel.start`` calls
        ``clean_team``) has no team row left and genuinely does need to build
        one. The team row is what says a team is there to be rejoined.

        Returns:
            True when the leader is already attached, with history, to a team
            that still exists.
        """
        if not self._history_restored:
            return False
        return await self.db.team.team_exists(self.team_name)

    def team_taken_over(self) -> bool:
        """Whether ``build_team`` took over an existing team instead of creating one.

        Read by ``BuildTeamTool`` so the result says which happened: a leader
        that inherited a running team must not go on to spawn the members that
        are already on its roster.
        """
        return self._team_taken_over

    def task_verification_enabled(self) -> bool:
        """Whether task verification is expected for this team instance.

        Prompt-level guidance flag (F_62): the leader is asked to assign
        reviewers when creating tasks. The reviewer machinery itself works
        regardless of this flag.
        """
        return self._enable_task_verification

    # ------------------------------------------------------------------
    # Bridge-agent surface
    # ------------------------------------------------------------------

    def bridge_enabled(self) -> bool:
        """Whether the Bridge capability is currently engaged.

        Symmetric to ``hitt_enabled``. Tools / rails / coordination
        handlers gate on this — it's True when both the spec ceiling
        and the ``build_team`` runtime switch allow bridges.
        """
        return self._enable_bridge

    def is_bridge_agent(self, member_name: Optional[str]) -> bool:
        """Whether ``member_name`` is a registered bridge-agent member."""
        if not member_name:
            return False
        return member_name in self._bridge_member_specs

    def bridge_agent_names(self) -> frozenset[str]:
        """Snapshot of currently registered bridge-agent member names."""
        return frozenset(self._bridge_member_specs.keys())

    def get_bridge_member_spec(self, member_name: str) -> Optional[BridgeMemberSpec]:
        """Return the ``BridgeMemberSpec`` for ``member_name``, or None.

        Returned spec carries ``mailbox_inject_mode`` / ``protocol`` /
        ``adapter_config`` — single source of truth for the mailbox
        auto-forward path.
        """
        return self._bridge_member_specs.get(member_name)

    def set_bridge_adapter(
        self,
        member_name: str,
        adapter: Optional[BridgeProtocolAdapter],
    ) -> None:
        """Register / clear the protocol adapter for a bridge member.

        SDK / business layer calls this after spawn to wire a concrete
        adapter instance. ``adapter=None`` removes a prior registration
        (the bridge then falls back to ``REMOTE_UNAVAILABLE_SENTINEL``).
        Unknown member names raise ``KeyError`` so typos surface
        immediately instead of silently dropping the relay.
        """
        if member_name not in self._bridge_member_specs:
            raise KeyError(
                f"'{member_name}' is not a registered bridge-agent member; "
                f"registered members: {sorted(self._bridge_member_specs.keys())}"
            )
        if adapter is None:
            self._bridge_adapters.pop(member_name, None)
        else:
            self._bridge_adapters[member_name] = adapter

    def get_bridge_adapter(self, member_name: str) -> Optional[BridgeProtocolAdapter]:
        """Return the adapter registered for ``member_name``, or None."""
        return self._bridge_adapters.get(member_name)

    async def spawn_bridge_agent(
        self,
        *,
        member_name: str,
        display_name: str,
        desc: str = "",
        prompt: str,
        model_name: Optional[str] = None,
        mailbox_inject_mode: BridgeMailboxInjectMode = BridgeMailboxInjectMode.PASSTHROUGH,
        protocol: str = "",
        adapter_config: Optional[dict[str, Any]] = None,
    ) -> MemberOpResult:
        """Register a bridge-agent member dynamically.

        Used by ``SpawnMemberTool`` when ``role_type='bridge_agent'``.
        Predefined bridge members are registered inline in
        ``build_team`` and reach this method only via the dynamic path.

        Bridge members share the standard teammate DB row (so they
        appear in the roster, accept tasks, send messages exactly like
        a teammate) and additionally index into
        ``_bridge_member_specs`` so the coordination message handler
        can find their mailbox configuration at deliver time.

        Args:
            member_name: Unique member identifier.
            display_name: Human-readable label.
            desc: Public description — the field peers see in the roster
                and ``list_members``. Optional; defaults to empty.
            prompt: Private briefing the remote agent adopts as its own
                system prompt via ``adapter.connect``. Required — it is
                what makes the remote act as this member.
            model_name: Optional model pool hint forwarded to the
                allocator (``None`` falls back to per-agent default).
            mailbox_inject_mode: Outbound wrap format for inbound
                messages relayed to the remote.
            protocol: Adapter lookup key. Empty string in Phase-1.
            adapter_config: Free-form adapter parameters (timeout,
                endpoint, ...). Passed verbatim to ``adapter.connect``.

        Returns:
            ``MemberOpResult``. Returns failure when Bridge capability
            is disabled or the underlying ``spawn_member`` rejects the
            registration.
        """
        if not self._enable_bridge:
            return MemberOpResult.fail(
                "Cannot spawn bridge agent: Bridge capability is disabled "
                "(enable_bridge=False on TeamAgentSpec or build_team)"
            )

        if not prompt:
            return MemberOpResult.fail(
                "spawn_bridge_agent requires non-empty 'prompt' — it is the "
                "briefing the remote agent adopts via adapter.connect"
            )

        member_card = AgentCard(
            id=f"{self.team_name}_{member_name}",
            name=display_name,
            description=desc,
        )
        allocation = self._allocate_model_config(model_name) if self._allocate_model_config else None
        result = await self.spawn_member(
            member_name=member_name,
            display_name=display_name,
            agent_card=member_card,
            desc=desc,
            prompt=prompt,
            status=MemberStatus.UNSTARTED,
            execution_status=ExecutionStatus.IDLE,
            mode=self.teammate_mode,
            allocation=allocation,
            role=TeamRole.BRIDGE_AGENT,
        )
        if not result.ok:
            team_logger.warning(
                "Failed to register bridge agent '%s' for team %s: %s",
                member_name,
                self.team_name,
                result.reason,
            )
            return result

        self._bridge_member_specs[member_name] = BridgeMemberSpec(
            member_name=member_name,
            display_name=display_name,
            desc=desc,
            prompt=prompt,
            model_name=model_name,
            mailbox_inject_mode=mailbox_inject_mode,
            protocol=protocol,
            adapter_config=adapter_config or {},
        )
        return result

    # ------------------------------------------------------------------
    # External-CLI member support
    # ------------------------------------------------------------------

    async def restore_external_cli_specs_from_db(self) -> None:
        """Restore external CLI member routing from persisted member options."""
        from openjiuwen.agent_teams.tools.member_options import get_member_cli_agent

        members = await self.db.member.get_team_members(self.team_name)
        restored: dict[str, str] = {}
        for member in members:
            cli_agent = get_member_cli_agent(member)
            if cli_agent:
                restored[member.member_name] = cli_agent
        self._external_cli_specs.update(restored)

    def is_external_cli_agent(self, member_name: str) -> bool:
        """Return whether ``member_name`` is driven by an external CLI."""
        return member_name in self._external_cli_specs

    def get_external_cli_agent(self, member_name: str) -> Optional[str]:
        """Return the cli_agent backend name for a member, or ``None``."""
        return self._external_cli_specs.get(member_name)

    def external_cli_agent_names(self) -> frozenset[str]:
        """Return a snapshot of all registered external-CLI member names."""
        return frozenset(self._external_cli_specs)

    def external_cli_config(self, cli_agent: str) -> Optional[ExternalCliAgentSpec]:
        """Return the static launch config for a ``cli_agent`` kind, or None."""
        return self._external_cli_configs.get(cli_agent)

    def external_cli_kinds(self) -> frozenset[str]:
        """Return the set of ``cli_agent`` kinds declared in the spec."""
        return frozenset(self._external_cli_configs)

    async def spawn_external_cli_agent(
        self,
        *,
        member_name: str,
        display_name: str,
        cli_agent: str,
        desc: str = "",
        prompt: str,
        model_name: Optional[str] = None,
    ) -> MemberOpResult:
        """Register an external-CLI teammate dynamically.

        The member shares the standard teammate DB row (it appears in the
        roster, claims tasks and sends messages like any teammate) but is
        recorded in ``_external_cli_specs`` so the spawn path drives it with
        an ``ExternalCliRuntime`` over the named CLI subprocess instead of a
        local DeepAgent. Registration happens before ``startup`` triggers
        the spawn, so ``build_context_from_db`` sees the mapping in time.

        Args:
            member_name: Unique member identifier.
            display_name: Human-readable label.
            cli_agent: External CLI backend name (``"claude"`` / ``"codex"`` / ...); see
                ``agent_teams/external/cli_agent/adapters.py``.
            desc: Public description stored on the member row (roster view).
                Optional; defaults to empty.
            prompt: Private system prompt the CLI adopts to act as this
                member. Required.
            model_name: Ignored for external-CLI members (the model lives in
                the external CLI); accepted for signature symmetry.

        Returns:
            ``MemberOpResult`` — failure if the backend name is unknown or the
            underlying ``spawn_member`` rejects the registration.
        """
        from openjiuwen.agent_teams.external.cli_agent.backends import available_backends, is_known_backend

        if not prompt:
            return MemberOpResult.fail("spawn_external_cli_agent requires non-empty 'prompt'")
        # Capability ceiling: the CLI kind must be pre-declared in
        # ``TeamAgentSpec.external_cli_agents`` (all launch knowledge is
        # static there; the spawn call only names the kind).
        if cli_agent not in self._external_cli_configs:
            declared = ", ".join(sorted(self._external_cli_configs)) or "<none>"
            return MemberOpResult.fail(
                f"cli_agent '{cli_agent}' is not declared in TeamAgentSpec.external_cli_agents "
                f"(declared: {declared}); add a static config entry for it first"
            )
        if not is_known_backend(cli_agent):
            return MemberOpResult.fail(f"Unknown cli_agent '{cli_agent}'; known: {', '.join(available_backends())}")

        member_card = AgentCard(
            id=f"{self.team_name}_{member_name}",
            name=display_name,
            description=desc,
        )
        # Record the mapping before spawn_member so the later startup ->
        # build_context_from_db pass routes this member to the CLI path.
        self._external_cli_specs[member_name] = cli_agent
        result = await self.spawn_member(
            member_name=member_name,
            display_name=display_name,
            agent_card=member_card,
            desc=desc,
            prompt=prompt,
            status=MemberStatus.UNSTARTED,
            execution_status=ExecutionStatus.IDLE,
            mode=self.teammate_mode,
            role=TeamRole.TEAMMATE,
            cli_agent=cli_agent,
        )
        if not result.ok:
            self._external_cli_specs.pop(member_name, None)
            team_logger.warning(
                "Failed to register external-cli agent '%s' for team %s: %s",
                member_name,
                self.team_name,
                result.reason,
            )
        return result
