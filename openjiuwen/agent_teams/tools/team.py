# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent Team Module

This module implements Agent Team which manages team members, tasks, and messages.
"""

import asyncio
import shutil
from pathlib import Path
from typing import (
    Awaitable,
    Callable,
    List,
    Optional,
    TYPE_CHECKING,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.team import MemberOpResult, TeamMemberSpec
from openjiuwen.agent_teams.tools.database import (
    TeamDatabase,
    Team,
    TeamMember,
)
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.agent_teams.schema.status import (
    ExecutionStatus,
    MemberStatus,
    MemberMode,
    TaskStatus,
)
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    MemberCanceledEvent,
    MemberShutdownEvent,
    MemberSpawnedEvent,
    PlanApprovalEvent,
    ToolApprovalResultEvent,
    TeamCleanedEvent,
    TeamCreatedEvent,
    TeamTopic,
)
from openjiuwen.agent_teams.spawn.context import get_session_id
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
        model_config_allocator: Optional[Callable[[], Optional[str]]] = None,
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
            model_config_allocator: Callback that returns the next model
                config JSON string for teammate allocation.
        """
        self.team_name = team_name
        self.member_name = member_name
        self.is_leader = is_leader
        self.db = db
        self.messager = messager
        self.teammate_mode = teammate_mode
        self.predefined_members = predefined_members or []
        self._allocate_model_config = model_config_allocator

        self.task_manager = TeamTaskManager(self.team_name, member_name, self.db, messager)
        self.message_manager = TeamMessageManager(self.team_name, member_name, self.db, messager)

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
        self, member_name: str, display_name: str, agent_card: AgentCard, *,
        desc: Optional[str] = None, prompt: Optional[str] = None,
        status: MemberStatus = MemberStatus.UNSTARTED,
        execution_status: ExecutionStatus = ExecutionStatus.IDLE,
        mode: MemberMode = MemberMode.BUILD_MODE,
        member_model: Optional["TeamModelConfig"] = None,
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
            member_model: TeamModelConfig allocated for this member.

        Returns:
            ``MemberOpResult`` describing the outcome. ``__bool__`` falls
            through to ``ok`` so legacy ``if await spawn_member(...): ...``
            patterns keep working.
        """
        existing = await self.db.get_member(member_name, self.team_name)
        if existing is not None:
            return MemberOpResult.fail(
                f"Member {member_name} already exists in team {self.team_name}"
            )

        success = await self.db.create_member(
            member_name=member_name,
            team_name=self.team_name,
            display_name=display_name,
            agent_card=agent_card.model_dump_json(),
            status=status,
            desc=desc,
            execution_status=execution_status,
            mode=mode.value,
            prompt=prompt,
            model_config_json=member_model.model_dump_json() if member_model else None,
        )
        if not success:
            return MemberOpResult.fail(
                f"Database rejected create_member for {member_name} "
                f"in team {self.team_name}"
            )

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
        unstarted = await self.db.get_team_members(self.team_name, status=MemberStatus.UNSTARTED)
        started: list[str] = []
        for member in unstarted:
            member_name = member.member_name

            await on_created(member_name)

            try:
                await self.messager.publish(
                    topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                    message=EventMessage.from_event(MemberSpawnedEvent(
                        team_name=self.team_name,
                        member_name=member_name,
                    )),
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
        member_data = await self.db.get_member(member_name, self.team_name)
        if member_data is None:
            team_logger.error(f"Member {member_name} not found in team {self.team_name}")
            return False

        team_logger.info(f"Approving plan for member {member_name}: {approved}, feedback: {feedback}")

        # Prepare message
        if approved:
            # Approve member's claimed tasks (CLAIMED -> PLAN_APPROVED)
            claimed_tasks = await self.task_manager.get_tasks_by_assignee(
                member_name=member_name,
                status=TaskStatus.CLAIMED.value
            )
            approved_count = 0
            for task in claimed_tasks:
                if await self.task_manager.approve_plan(task.task_id):
                    approved_count += 1

            if approved_count > 0:
                team_logger.info(f"Approved {approved_count} tasks for member {member_name}")

            content = (
                f"Your plan has been APPROVED. {approved_count} task(s) are now approved for completion."
                f"Feedback: {feedback}" if feedback
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
                message=EventMessage.from_event(PlanApprovalEvent(
                    team_name=self.team_name,
                    member_name=member_name,
                    approved=approved
                )),
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
        member_data = await self.db.get_member(member_name, self.team_name)
        if member_data is None:
            team_logger.error(f"Member {member_name} not found in team {self.team_name}")
            return False

        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(ToolApprovalResultEvent(
                    team_name=self.team_name,
                    member_name=member_name,
                    tool_call_id=tool_call_id,
                    approved=approved,
                    feedback=feedback or "",
                    auto_confirm=auto_confirm,
                )),
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
        member_data = await self.db.get_member(member_name, self.team_name)
        if not member_data:
            return MemberOpResult.fail(
                f"Member {member_name} not found in team {self.team_name}"
            )

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
            is_valid_transition,
            MEMBER_TRANSITIONS,
        )

        if not is_valid_transition(current_status, MemberStatus.SHUTDOWN_REQUESTED, MEMBER_TRANSITIONS):
            return MemberOpResult.fail(
                f"Member {member_name} cannot shut down from status "
                f"'{current_status.value}'"
            )

        team_logger.info(
            f"Shutting down member {member_name}: {current_status.value} -> {MemberStatus.SHUTDOWN_REQUESTED.value}"
            f" (force={force})"
        )

        # Update member status in database (team management layer)
        success = await self.db.update_member_status(member_name, self.team_name, MemberStatus.SHUTDOWN_REQUESTED.value)
        if not success:
            return MemberOpResult.fail(
                f"Database rejected status update for member {member_name}"
            )

        # Note: execution_status is managed by member process internally
        # Team leader only sets member status and notifies member via message and event
        msg_id = await self.message_manager.send_message(
            content="当前任务已全部完成，请结束流程",
            to_member_name=member_name,
        )
        if not msg_id:
            team_logger.warning(f"Failed to send shutdown request message to member {member_name}")

        # Publish shutdown event (for cross-process notification to member)
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(MemberShutdownEvent(
                    team_name=self.team_name,
                    member_name=member_name,
                    force=force
                )),
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
        member_data = await self.db.get_member(member_name, self.team_name)
        if not member_data:
            team_logger.error(f"Member {member_name} not found in team {self.team_name}")
            return False

        current_status = MemberStatus(member_data.status)

        # Only send cancel event if member is busy
        if current_status != MemberStatus.BUSY:
            team_logger.info(
                f"Member {member_name} is not busy (status: {current_status.value}), no need to cancel execution")
            return True

        team_logger.info(f"Cancelling execution for member {member_name}")

        # Reset all CLAIMED tasks assigned to this member
        claimed_tasks = await self.task_manager.get_tasks_by_assignee(
            member_name=member_name,
            status=TaskStatus.CLAIMED.value
        )
        reset_count = 0
        for task in claimed_tasks:
            if await self.task_manager.reset(task.task_id):
                reset_count += 1
                team_logger.info(f"Reset task {task.task_id} from member {member_name}")

        if reset_count > 0:
            team_logger.info(f"Reset {reset_count} tasks from member {member_name}")

        success = await self.message_manager.send_message(
            content="当前任务有变动，请停止执行当前任务，重新尝试认领合适任务", to_member_name=member_name)
        if not success:
            team_logger.error(f"Failed to send cancel request message to member {member_name}")
            return False

        # Publish cancel event (for cross-process notification to member)
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_name),
                message=EventMessage.from_event(MemberCanceledEvent(
                    team_name=self.team_name,
                    member_name=member_name
                )),
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
        members = await self.db.get_team_members(self.team_name)
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
        await self.db.delete_team(self.team_name)

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

    async def get_member(self, member_name: str) -> Optional[TeamMember]:
        """Get a member by ID

        Args:
            member_name: Member identifier

        Returns:
            TeamMember info or None
        """
        return await self.db.get_member(member_name, self.team_name)

    async def list_members(self) -> List[TeamMember]:
        """List all team members

        Returns:
            List of TeamMember info
        """
        members = await self.db.get_team_members(self.team_name)
        return [member for member in members if member.member_name != self.member_name]

    async def get_team_info(self) -> Optional[Team]:
        """Get team information

        Returns:
            Team information
        """
        return await self.db.get_team(self.team_name)

    async def get_team_updated_at(self) -> int:
        """Probe ``team_info.updated_at`` for change detection.

        Cheap single-column SELECT used by prompt-section caches to
        decide whether to refetch full team metadata.

        Returns:
            Last update timestamp (ms), or ``0`` when missing.
        """
        return await self.db.get_team_updated_at(self.team_name)

    async def get_members_max_updated_at(self) -> int:
        """Probe MAX(``team_member.updated_at``) for the team.

        Returns:
            Largest member update timestamp (ms), or ``0`` when no
            members exist.  Status / execution_status updates do not
            bump this value -- only roster mutations do.
        """
        return await self.db.get_members_max_updated_at(self.team_name)

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

    async def cancel_all_tasks(self) -> int:
        """Cancel all tasks in team atomically

        Cancels all non-cancelled and non-completed tasks in a single transaction.
        After cancellation, sends a broadcast message to all team members.

        The cancel operation is atomic at the database level via task_manager.cancel_all_tasks().

        Returns:
            Number of tasks cancelled

        Example:
            count = await team.cancel_all_tasks()
            # count = 5
        """
        # Cancel all tasks atomically
        cancelled_tasks = await self.task_manager.cancel_all_tasks()

        if not cancelled_tasks:
            team_logger.info(f"No tasks to cancel in team {self.team_name}")
            return 0

        # Send broadcast message to all team members
        broadcast_content = f"All tasks ({len(cancelled_tasks)}) have been cancelled by team leader."
        await self.message_manager.broadcast_message(content=broadcast_content)

        team_logger.info(f"Cancelled {len(cancelled_tasks)} tasks in team {self.team_name}")
        return len(cancelled_tasks)

    async def build_team(
        self, display_name: str, desc: str,
        leader_display_name: str, leader_desc: str,
    ):
        """Create a team and register the leader as a member.

        Creates team in database, writes the leader into the member table,
        then publishes TeamEvent.Created.

        Args:
            display_name: Human-readable team label.
            desc: Team goal, scope, and directives.
            leader_display_name: Human-readable display label for the leader member.
            leader_desc: Persona description of the leader member.
        """
        # Create team in database
        team_name = self.team_name
        leader_member_name = self.member_name
        success = await self.db.create_team(team_name=team_name,
                                            display_name=display_name,
                                            leader_member_name=leader_member_name,
                                            desc=desc)

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
        )

        # Register predefined teammates (UNSTARTED, launched later via broadcast)
        for member_spec in self.predefined_members:
            member_card_id = f"{team_name}_{member_spec.member_name}"
            member_card = AgentCard(
                id=member_card_id,
                name=member_spec.display_name,
                description=member_spec.persona,
            )
            allocated = self._allocate_model_config() if self._allocate_model_config else None
            await self.spawn_member(
                member_name=member_spec.member_name,
                display_name=member_spec.display_name,
                agent_card=member_card,
                desc=member_spec.persona,
                prompt=member_spec.prompt_hint,
                status=MemberStatus.UNSTARTED,
                execution_status=ExecutionStatus.IDLE,
                mode=self.teammate_mode,
                member_model=allocated,
            )

        # Publish team created event
        session_id = get_session_id()
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(session_id, team_name),
                message=EventMessage.from_event(TeamCreatedEvent(
                    team_name=team_name,
                    display_name=display_name,
                    leader_member_name=leader_member_name,
                    created=TeamDatabase.get_current_time()
                )),
            )
            team_logger.debug(f"Team created event published: {team_name}")
        except Exception as e:
            team_logger.error(f"Failed to publish team created event for {team_name}: {e}")

        team_logger.info(f"Team {team_name} created successfully")
