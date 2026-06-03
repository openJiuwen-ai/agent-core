# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent Team Module

This module implements Agent Team which manages team members, tasks, and messages.
"""

import asyncio
import shutil
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
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    MemberCanceledEvent,
    MemberShutdownEvent,
    MemberSpawnedEvent,
    PlanApprovalEvent,
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
    MemberOpResult,
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
        *,
        on_team_cleaned: Callable[[], Awaitable[None]] | None = None,
        on_team_built: Callable[[], Awaitable[None]] | None = None,
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
        """
        self.team_name = team_name
        self.member_name = member_name
        self.is_leader = is_leader
        self.db = db
        self.messager = messager
        self.teammate_mode = teammate_mode
        self.predefined_members = predefined_members or []
        self._allocate_model_config = model_config_allocator
        self.leader_allocation = leader_allocation
        # HITT capability ceiling (immutable, from spec) and the runtime
        # effective flag that ``build_team`` may downgrade. All human-agent
        # creation paths gate on ``_enable_hitt``; the spec ceiling is
        # consulted only when ``build_team(enable_hitt=True)`` tries to
        # enable beyond it.
        self._spec_enable_hitt: bool = enable_hitt
        self._enable_hitt: bool = enable_hitt
        # Fired once on the build_team / clean_team success paths so the
        # hosting TeamAgent can persist DB lifecycle state and latch
        # state.team_cleaned deterministically inside the leader's round.
        self._on_team_cleaned = on_team_cleaned
        self._on_team_built = on_team_built

        self.task_manager = TeamTaskManager(self.team_name, member_name, self.db, messager)
        # Roster of human-collaborator members. Sync in-memory cache so
        # the many sync callers (coordination handlers, rails, prompt
        # sections) can consult it cheaply. **DB is the source of truth**:
        # this set is empty at construction time and rebuilt from
        # ``team_member.role`` by ``refresh_human_agent_roster()`` at
        # backend bootstrap. ``spawn_member`` also writes through to the
        # set when it persists a HUMAN_AGENT row, so the cache and DB
        # never diverge for the lifetime of this backend.
        self._human_agent_names: set[str] = set()
        # Per-human-agent callback fired by the leader's dispatcher when
        # a team-side message reaches the avatar — see
        # ``register_human_agent_inbound`` for the registration surface.
        # Holds raw callables (not wrapped) so the dispatcher can decide
        # async vs sync invocation at call time.
        self._human_agent_inbound_callbacks: dict[str, Any] = {}
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

        team_logger.info(f"AgentTeam manager initialized for {team_name}, member={member_name}")

    def register_cleanup_path(self, path: Optional[str]) -> None:
        """Register a filesystem path to remove on ``clean_team``.

        Accepts absolute directory paths. No-ops for empty or None input.
        Idempotent: the same path is only stored once.
        """
        if not path:
            return
        self._cleanup_paths.add(str(Path(path).expanduser()))

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
    ) -> MemberOpResult:
        """Create a team member record in the database.

        Only persists the member data — does NOT start the member.
        Call ``startup`` to launch all unstarted members.

        Args:
            member_name: Unique member identifier (semantic slug, e.g. "backend-dev-1").
            display_name: Human-readable display label for the member.
            agent_card: Agent card defining the agent.
            desc: Member persona description.
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

        Returns:
            ``MemberOpResult`` describing the outcome. ``__bool__`` falls
            through to ``ok`` so legacy ``if await spawn_member(...): ...``
            patterns keep working.
        """
        existing = await self.db.member.get_member(member_name, self.team_name)
        if existing is not None:
            return MemberOpResult.fail(f"Member {member_name} already exists in team {self.team_name}")

        import json as _json

        model_ref_json: Optional[str] = _json.dumps(allocation.to_db_ref()) if allocation is not None else None

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
            model_ref_json=model_ref_json,
        )
        if not success:
            return MemberOpResult.fail(f"Database rejected create_member for {member_name} in team {self.team_name}")

        # Write through to the in-memory HITT roster cache so sync
        # callers (coordination handlers, rails) see the new human
        # immediately, without waiting for the next ``refresh_human_agent_roster``.
        if role == TeamRole.HUMAN_AGENT:
            self._human_agent_names.add(member_name)

        team_logger.info(f"Member {member_name} created successfully")
        return MemberOpResult.success()

    async def startup(
        self,
        on_created: Callable[[str], Awaitable[None]],
    ) -> list[str]:
        """Start all unstarted members.

        Finds every member whose status is UNSTARTED, invokes
        ``on_created`` to spin up the agent, and publishes a
        MemberSpawnedEvent for each.

        Args:
            on_created: Callback that receives a member_name and
                launches the corresponding agent process.

        Returns:
            List of member_names that were started.
        """
        unstarted = await self.db.member.get_team_members(self.team_name, status=MemberStatus.UNSTARTED)
        started: list[str] = []
        for member in unstarted:
            member_name = member.member_name

            await on_created(member_name)

            try:
                await self.messager.publish(
                    topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                    message=EventMessage.from_event(
                        MemberSpawnedEvent(
                            team_name=self.team_name,
                            member_name=member_name,
                        )
                    ),
                )
                team_logger.debug(f"Member spawned event published: {member_name}")
            except Exception as e:
                team_logger.error(f"Failed to publish member spawned event for {member_name}: {e}")

            started.append(member_name)
            team_logger.info(f"Member {member_name} started")

        return started

    async def approve_plan(self, member_name: str, approved: bool, feedback: Optional[str] = None) -> bool:
        """Approve or reject a member's plan

        If approved, approve member's claimed tasks (CLAIMED -> PLAN_APPROVED).
        If rejected, send feedback to member.

        Args:
            member_name: Member identifier
            approved: True to approve, False to reject
            feedback: Optional feedback message

        Returns:
            True if successful, False otherwise

        Example:
            success = team.approve_plan(
                member_name="member123",
                approved=True,
                feedback="Plan looks good"
            )
        """
        member_data = await self.db.member.get_member(member_name, self.team_name)
        if member_data is None:
            team_logger.error(f"Member {member_name} not found in team {self.team_name}")
            return False

        team_logger.info(f"Approving plan for member {member_name}: {approved}, feedback: {feedback}")

        # Prepare message
        if approved:
            # Approve member's claimed tasks (CLAIMED -> PLAN_APPROVED)
            claimed_tasks = await self.task_manager.get_tasks_by_assignee(
                member_name=member_name, status=TaskStatus.CLAIMED.value
            )
            approved_count = 0
            for task in claimed_tasks:
                if await self.task_manager.approve_plan(task.task_id):
                    approved_count += 1

            if approved_count > 0:
                team_logger.info(f"Approved {approved_count} tasks for member {member_name}")

            content = (
                f"Your plan has been APPROVED. {approved_count} task(s) are now approved for completion."
                f"Feedback: {feedback}"
                if feedback
                else f"Your plan has been APPROVED. {approved_count} task(s) are now approved for completion."
            )
        else:
            content = (
                f"Your plan has been REJECTED. Please revise and resubmit. "
                f"Feedback: {feedback if feedback else 'No specific feedback provided.'}"
            )

        # Send message via TeamMessageManager
        message_id = await self.message_manager.send_message(
            content=content,
            to_member_name=member_name,
        )

        if not message_id:
            team_logger.error(f"Failed to send approval message to member {member_name}")
            return False

        # Publish plan approval event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(
                    PlanApprovalEvent(
                        team_name=self.team_name,
                        member_name=member_name,
                        approved=approved,
                    )
                ),
            )
            team_logger.debug(f"Plan approval event published for member: {member_name}")
        except Exception as e:
            team_logger.error(f"Failed to publish plan approval event for {member_name}: {e}")

        team_logger.info(f"Plan approval sent to member {member_name}")
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

        # Reset all CLAIMED tasks assigned to this member
        claimed_tasks = await self.task_manager.get_tasks_by_assignee(
            member_name=member_name, status=TaskStatus.CLAIMED.value
        )
        reset_count = 0
        for task in claimed_tasks:
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
        # registers the actual resolved locations of the team shared
        # workspace, member workspaces, and the team-named parent
        # directory via ``register_cleanup_path``.  ``shutil.rmtree``
        # does not follow symlinks, so independent member workspaces
        # linked in from ``~/.openjiuwen/{member_name}_workspace/`` are
        # preserved.
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

    async def list_members(self) -> List[TeamMember]:
        """List all team members

        Returns:
            List of TeamMember info
        """
        members = await self.db.member.get_team_members(self.team_name)
        return [member for member in members if member.member_name != self.member_name]

    async def get_team_info(self) -> Optional[Team]:
        """Get team information

        Returns:
            Team information
        """
        return await self.db.team.get_team(self.team_name)

    async def is_team_completed(self) -> Optional[TeamCompletionSnapshot]:
        """Evaluate whether the whole team has reached a completed state.

        Returns a snapshot only when all three conditions hold at once:
            1. Every member -- including the leader -- is in a settled
               status (``MEMBER_SETTLED_STATUSES``).
            2. At least one task exists and every task is terminal
               (``TASK_TERMINAL_STATUSES``).
            3. No direct or broadcast message is left unread by any member.

        Read-only; safe to call repeatedly. Queries the member DAO directly
        so the leader itself is part of the roster check (``list_members``
        excludes the calling member).

        Returns:
            A ``TeamCompletionSnapshot`` when the team is complete,
            otherwise ``None``.
        """
        members = await self.db.member.get_team_members(self.team_name)
        if not members:
            return None
        if any(member.status not in MEMBER_SETTLED_STATUSES for member in members):
            return None

        tasks = await self.task_manager.list_tasks()
        if not tasks:
            return None
        if any(task.status not in TASK_TERMINAL_STATUSES for task in tasks):
            return None

        if await self.message_manager.has_unread_messages():
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

    async def build_team(
        self,
        display_name: str,
        desc: str,
        leader_display_name: str,
        leader_desc: str,
        enable_hitt: Optional[bool] = None,
    ):
        """Create a team and register the leader as a member.

        Creates team in database, writes the leader into the member table,
        then publishes TeamEvent.Created.

        Args:
            display_name: Human-readable team label.
            desc: Team goal, scope, and directives.
            leader_display_name: Human-readable display label for the leader member.
            leader_desc: Persona description of the leader member.
            enable_hitt: Runtime instance HITT switch (None inherits the
                spec ceiling, True enables, False disables). Cannot exceed
                ``TeamAgentSpec.enable_hitt`` — passing True when the spec
                ceiling is False is a config error and raises. When the
                effective flag is False, predefined HUMAN_AGENT members
                are skipped (with a warning).
        """
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

        # Step B: compute effective flag and persist on backend so all
        # downstream spawn paths see a single source of truth.
        effective_enable_hitt = self._spec_enable_hitt if enable_hitt is None else enable_hitt
        self._enable_hitt = effective_enable_hitt

        # Create team in database
        team_name = self.team_name
        leader_member_name = self.member_name
        success = await self.db.team.create_team(
            team_name=team_name,
            display_name=display_name,
            leader_member_name=leader_member_name,
            desc=desc,
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
            status=MemberStatus.BUSY,
            execution_status=ExecutionStatus.RUNNING,
            mode=MemberMode.BUILD_MODE,
            allocation=self.leader_allocation,
        )

        # Register predefined teammates (UNSTARTED, launched later via broadcast).
        # Human agents are filtered out and handled by
        # ``_spawn_human_agents`` so they never enter the startup loop.
        for member_spec in self.predefined_members:
            if member_spec.role_type == TeamRole.HUMAN_AGENT:
                continue
            member_card_id = f"{team_name}_{member_spec.member_name}"
            member_card = AgentCard(
                id=member_card_id,
                name=member_spec.display_name,
                description=member_spec.persona,
            )
            allocation = self._allocate_model_config(member_spec.model_name) if self._allocate_model_config else None
            await self.spawn_member(
                member_name=member_spec.member_name,
                display_name=member_spec.display_name,
                agent_card=member_card,
                desc=member_spec.persona,
                prompt=member_spec.prompt_hint,
                status=MemberStatus.UNSTARTED,
                execution_status=ExecutionStatus.IDLE,
                mode=self.teammate_mode,
                allocation=allocation,
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
                    desc=human_spec.persona,
                    prompt=human_spec.prompt_hint,
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
            desc: Optional persona description; falls back to the
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
        resolved_desc = desc or t("hitt.human_agent_default_persona")
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

    async def refresh_human_agent_roster(self) -> None:
        """Rebuild the in-memory HITT roster from ``team_member.role``.

        Cold-recovery entry points (leader ``recover_team``, teammate
        ``from_spawn_payload``) call this before the backend serves any
        sync ``is_human_agent`` / ``human_agent_names()`` lookups so the
        cache picks up dynamically-spawned humans that were never in
        ``predefined_members``. Idempotent — replaces the cache wholesale
        with the DB snapshot.

        Calls ``db.initialize()`` first so callers can drive the refresh
        before any other DB-touching method has lazily warmed the DAOs.
        Test setups that build a half-wired backend (no engine, no
        session) survive as a no-op rather than crashing.
        """
        initializer = getattr(self.db, "initialize", None)
        if initializer is not None:
            await initializer()
        member_dao = getattr(self.db, "member", None)
        if member_dao is None:
            team_logger.debug(
                "Skipping human-agent roster refresh for team %s: member DAO unavailable",
                self.team_name,
            )
            return
        names = await member_dao.list_human_agent_names(self.team_name)
        self._human_agent_names.clear()
        self._human_agent_names.update(names)
        team_logger.debug(
            "Refreshed human-agent roster for team %s from DB: %s",
            self.team_name,
            sorted(self._human_agent_names),
        )

    def is_human_agent(self, member_name: Optional[str]) -> bool:
        """Whether ``member_name`` is a registered human-agent member."""
        if not member_name:
            return False
        return member_name in self._human_agent_names

    def register_human_agent_inbound(
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
        if member_name not in self._human_agent_names:
            raise KeyError(
                f"'{member_name}' is not a registered human-agent member; "
                f"registered members: {sorted(self._human_agent_names)}"
            )
        if callback is None:
            self._human_agent_inbound_callbacks.pop(member_name, None)
        else:
            self._human_agent_inbound_callbacks[member_name] = callback

    def get_human_agent_inbound(self, member_name: str) -> Optional[Any]:
        """Return the inbound callback registered for ``member_name``, if any."""
        return self._human_agent_inbound_callbacks.get(member_name)

    def human_agent_names(self) -> frozenset[str]:
        """Snapshot of currently registered human-agent member names."""
        return frozenset(self._human_agent_names)

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
