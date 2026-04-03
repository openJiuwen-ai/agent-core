# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent Team Module

This module implements Agent Team which manages team members, tasks, and messages.
"""

from typing import (
    Awaitable,
    Callable,
    List,
    Optional,
)

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.team import TeamMemberSpec
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
        team_id: Team identifier
        member_id: Current member identifier
        is_leader: Whether current member is the leader
        db: Team database instance
        task_manager: Task manager instance
    """

    def __init__(
        self,
        team_id: str,
        member_id: str,
        is_leader: bool,
        db: TeamDatabase,
        messager: Messager,
        teammate_mode: MemberMode = MemberMode.PLAN_MODE,
        predefined_members: list[TeamMemberSpec] | None = None,
    ):
        """Initialize agent team manager.

        Args:
            team_id: Team identifier.
            member_id: Current member identifier.
            is_leader: Whether current member is the leader.
            db: TeamDatabase.
            messager: Messager instance for event publishing.
            teammate_mode: Default execution mode for spawned teammates.
            predefined_members: Pre-configured teammates to register
                during ``build_team``.
        """
        self.team_id = team_id
        self.member_id = member_id
        self.is_leader = is_leader
        self.db = db
        self.messager = messager
        self.teammate_mode = teammate_mode
        self.predefined_members = predefined_members or []

        self.task_manager = TeamTaskManager(self.team_id, member_id, self.db, messager)
        self.message_manager = TeamMessageManager(self.team_id, member_id, self.db, messager)

        team_logger.info(f"AgentTeam manager initialized for {team_id}, member={member_id}")

    async def spawn_member(
        self, member_id: str, name: str, agent_card: AgentCard, *,
        desc: Optional[str] = None, prompt: Optional[str] = None,
        status: MemberStatus = MemberStatus.UNSTARTED,
        execution_status: ExecutionStatus = ExecutionStatus.IDLE,
        mode: MemberMode = MemberMode.PLAN_MODE,
    ) -> bool:
        """Create a team member record in the database.

        Only persists the member data — does NOT start the member.
        Call ``startup`` to launch all unstarted members.

        Args:
            member_id: Unique member identifier.
            name: Member name.
            agent_card: Agent card defining the agent.
            desc: Member persona description.
            prompt: Startup instruction for the member.
            status: Initial member status.
            execution_status: Initial execution status.
            mode: Member mode (PLAN_MODE or BUILD_MODE).
        """
        success = await self.db.create_member(
            member_id=member_id,
            team_id=self.team_id,
            name=name,
            agent_card=agent_card.model_dump_json(),
            status=status,
            desc=desc,
            execution_status=execution_status,
            mode=mode.value,
            prompt=prompt,
        )
        if not success:
            team_logger.error(f"Failed to create member {member_id}")
            return False

        team_logger.info(f"Member {member_id} created successfully")
        return True

    async def startup(
        self,
        on_created: Callable[[str], Awaitable[None]],
    ) -> list[str]:
        """Start all unstarted members.

        Finds every member whose status is UNSTARTED, invokes
        ``on_created`` to spin up the agent, and publishes a
        MemberSpawnedEvent for each.

        Args:
            on_created: Callback that receives a member_id and
                launches the corresponding agent process.

        Returns:
            List of member_ids that were started.
        """
        unstarted = await self.db.get_team_members(self.team_id, status=MemberStatus.UNSTARTED)
        started: list[str] = []
        for member in unstarted:
            member_id = member.member_id

            await on_created(member_id)

            try:
                await self.messager.publish(
                    topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_id),
                    message=EventMessage.from_event(MemberSpawnedEvent(
                        team_id=self.team_id,
                        member_id=member_id,
                    )),
                )
                team_logger.debug(f"Member spawned event published: {member_id}")
            except Exception as e:
                team_logger.error(f"Failed to publish member spawned event for {member_id}: {e}")

            started.append(member_id)
            team_logger.info(f"Member {member_id} started")

        return started

    async def approve_plan(self, member_id: str, approved: bool, feedback: Optional[str] = None) -> bool:
        """Approve or reject a member's plan

        If approved, approve member's claimed tasks (CLAIMED -> PLAN_APPROVED).
        If rejected, send feedback to member.

        Args:
            member_id: Member identifier
            approved: True to approve, False to reject
            feedback: Optional feedback message

        Returns:
            True if successful, False otherwise

        Example:
            success = team.approve_plan(
                member_id="member123",
                approved=True,
                feedback="Plan looks good"
            )
        """
        member_data = await self.db.get_member(member_id)
        if member_data is None:
            team_logger.error(f"Member {member_id} not found")
            return False

        team_logger.info(f"Approving plan for member {member_id}: {approved}, feedback: {feedback}")

        # Prepare message
        if approved:
            # Approve member's claimed tasks (CLAIMED -> PLAN_APPROVED)
            claimed_tasks = await self.task_manager.get_tasks_by_assignee(
                member_id=member_id,
                status=TaskStatus.CLAIMED.value
            )
            approved_count = 0
            for task in claimed_tasks:
                if await self.task_manager.approve_plan(task.task_id):
                    approved_count += 1

            if approved_count > 0:
                team_logger.info(f"Approved {approved_count} tasks for member {member_id}")

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
            to_member=member_id,
        )

        if not message_id:
            team_logger.error(f"Failed to send approval message to member {member_id}")
            return False

        # Publish plan approval event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_id),
                message=EventMessage.from_event(PlanApprovalEvent(
                    team_id=self.team_id,
                    member_id=member_id,
                    approved=approved
                )),
            )
            team_logger.debug(f"Plan approval event published for member: {member_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish plan approval event for {member_id}: {e}")

        team_logger.info(f"Plan approval sent to member {member_id}")
        return True

    async def approve_tool(
        self,
        member_id: str,
        tool_call_id: str,
        approved: bool,
        feedback: Optional[str] = None,
        auto_confirm: bool = False,
    ) -> bool:
        """Approve or reject one interrupted teammate tool call."""
        member_data = await self.db.get_member(member_id)
        if member_data is None:
            team_logger.error(f"Member {member_id} not found")
            return False

        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_id),
                message=EventMessage.from_event(ToolApprovalResultEvent(
                    team_id=self.team_id,
                    member_id=member_id,
                    tool_call_id=tool_call_id,
                    approved=approved,
                    feedback=feedback or "",
                    auto_confirm=auto_confirm,
                )),
            )
            team_logger.debug(
                "Tool approval result event published for member {}, tool_call_id={}",
                member_id,
                tool_call_id,
            )
        except Exception as e:
            team_logger.error(
                "Failed to publish tool approval result event for {} / {}: {}",
                member_id,
                tool_call_id,
                e,
            )

        team_logger.info(
            "Tool approval event sent to member {} for tool_call_id={}, approved={}, auto_confirm={}",
            member_id,
            tool_call_id,
            approved,
            auto_confirm,
        )
        return True

    async def shutdown_member(self, member_id: str, force: bool = False) -> bool:
        """Shutdown a member

        Sends a shutdown request to member. Supports interrupting
        member's current execution.

        Team leader calls this to shutdown a member running in a separate process.
        This method:
        1. Updates member status in database (team management layer)
        2. Does NOT update execution_status (managed by member process internally)
        3. Publishes SHUTDOWN event for cross-process notification
        4. Member process receives event and handles its own shutdown sequence

        Args:
            member_id: Member identifier
            force: Whether to force shutdown (bypass normal shutdown sequence)

        Returns:
            True if successful, False otherwise

        Example:
            success = team.shutdown_member(member_id="member123", force=True)
        """
        # Check if member exists in database
        member_data = await self.db.get_member(member_id)
        if not member_data:
            team_logger.error(f"Member {member_id} not found")
            return False

        current_status = MemberStatus(member_data.status)

        # Check if already shutdown
        if current_status == MemberStatus.SHUTDOWN or current_status == MemberStatus.SHUTDOWN_REQUESTED:
            team_logger.debug(
                f"Member {member_id} already shutdown"
                if current_status == MemberStatus.SHUTDOWN
                else f"Member {member_id} is shutting down"
            )
            return True

        # Validate state transition
        from openjiuwen.agent_teams.schema.status import (
            is_valid_transition,
            MEMBER_TRANSITIONS,
        )

        if not is_valid_transition(current_status, MemberStatus.SHUTDOWN_REQUESTED, MEMBER_TRANSITIONS):
            team_logger.error(
                f"Invalid status transition for member {member_id}: "
                f"{current_status.value} -> {MemberStatus.SHUTDOWN_REQUESTED.value}"
            )
            return False

        team_logger.info(
            f"Shutting down member {member_id}: {current_status.value} -> {MemberStatus.SHUTDOWN_REQUESTED.value}"
            f" (force={force})"
        )

        # Update member status in database (team management layer)
        success = await self.db.update_member_status(member_id, MemberStatus.SHUTDOWN_REQUESTED.value)
        if not success:
            team_logger.error(f"Failed to update member {member_id} status")
            return False

        # Note: execution_status is managed by member process internally
        # Team leader only sets member status and notifies member via message and event
        success = await self.message_manager.send_message(content="当前任务已全部完成，请结束流程", to_member=member_id)
        if not success:
            team_logger.warning(f"Failed to send shutdown request message to member {member_id}")

        # Publish shutdown event (for cross-process notification to member)
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_id),
                message=EventMessage.from_event(MemberShutdownEvent(
                    team_id=self.team_id,
                    member_id=member_id,
                    force=force
                )),
            )
            team_logger.debug(f"Member shutdown event published: {member_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish member shutdown event for {member_id}: {e}")

        team_logger.info(f"Shutdown request sent to member {member_id}")
        return True

    async def cancel_member(self, member_id: str) -> bool:
        """Cancel member execution

        Sends a cancellation request to a member who is
        currently executing.

        Args:
            member_id: Member identifier

        Returns:
            True if successful, False otherwise

        Example:
            success = team.cancel_member(member_id="member123")
        """
        # Check if member exists in database
        member_data = await self.db.get_member(member_id)
        if not member_data:
            team_logger.error(f"Member {member_id} not found")
            return False

        current_status = MemberStatus(member_data.status)

        # Only send cancel event if member is busy
        if current_status != MemberStatus.BUSY:
            team_logger.info(
                f"Member {member_id} is not busy (status: {current_status.value}), no need to cancel execution")
            return True

        team_logger.info(f"Cancelling execution for member {member_id}")

        # Reset all CLAIMED tasks assigned to this member
        claimed_tasks = await self.task_manager.get_tasks_by_assignee(
            member_id=member_id,
            status=TaskStatus.CLAIMED.value
        )
        reset_count = 0
        for task in claimed_tasks:
            if await self.task_manager.reset(task.task_id):
                reset_count += 1
                team_logger.info(f"Reset task {task.task_id} from member {member_id}")

        if reset_count > 0:
            team_logger.info(f"Reset {reset_count} tasks from member {member_id}")

        success = await self.message_manager.send_message(
            content="当前任务有变动，请停止执行当前任务，重新尝试认领合适任务", to_member=member_id)
        if not success:
            team_logger.error(f"Failed to send cancel request message to member {member_id}")
            return False

        # Publish cancel event (for cross-process notification to member)
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_id),
                message=EventMessage.from_event(MemberCanceledEvent(
                    team_id=self.team_id,
                    member_id=member_id
                )),
            )
            team_logger.debug(f"Member canceled event published: {member_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish member canceled event for {member_id}: {e}")

        team_logger.info(f"Cancel request sent to member {member_id}")
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
        members = await self.db.get_team_members(self.team_id)
        for member_data in members:
            if member_data.member_id == self.member_id:
                continue
            if member_data.status != MemberStatus.SHUTDOWN.value:
                member_id = member_data.member_id
                team_logger.info(f"Member {member_id} is not shutdown (status: {member_data.status})")
                all_shutdown = False
                break

        if not all_shutdown:
            team_logger.error(f"Cannot clean team {self.team_id}: not all members are shutdown")
            return False

        # Delete team from database
        await self.db.delete_team(self.team_id)

        # Publish team cleaned event
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), self.team_id),
                message=EventMessage.from_event(TeamCleanedEvent(team_id=self.team_id)),
            )
            team_logger.debug(f"Team cleaned event published: {self.team_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish team cleaned event for {self.team_id}: {e}")

        team_logger.info(f"Team {self.team_id} cleaned successfully")
        return True

    async def get_member(self, member_id: str) -> Optional[TeamMember]:
        """Get a member by ID

        Args:
            member_id: Member identifier

        Returns:
            TeamMember info or None
        """
        return await self.db.get_member(member_id)

    async def list_members(self) -> List[TeamMember]:
        """List all team members

        Returns:
            List of TeamMember info
        """
        members = await self.db.get_team_members(self.team_id)
        return [member for member in members if member.member_id != self.member_id]

    async def get_team_info(self) -> Optional[Team]:
        """Get team information

        Returns:
            Team information
        """
        return await self.db.get_team(self.team_id)

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
                to_member=task.assignee,
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
            team_logger.info(f"No tasks to cancel in team {self.team_id}")
            return 0

        # Send broadcast message to all team members
        broadcast_content = f"All tasks ({len(cancelled_tasks)}) have been cancelled by team leader."
        await self.message_manager.broadcast_message(content=broadcast_content)

        team_logger.info(f"Cancelled {len(cancelled_tasks)} tasks in team {self.team_id}")
        return len(cancelled_tasks)

    async def build_team(
        self, name: str, desc: str,
        leader_name: str, leader_desc: str,
    ):
        """Create a team and register the leader as a member.

        Creates team in database, writes the leader into the member table,
        then publishes TeamEvent.Created.

        Args:
            name: Team name.
            desc: Team goal, scope, and directives.
            leader_name: Display name of the leader member.
            leader_desc: Persona description of the leader member.
        """
        # Create team in database
        team_id = self.team_id
        leader_id = self.member_id
        success = await self.db.create_team(team_id=team_id,
                                            name=name,
                                            leader_member_id=leader_id,
                                            desc=desc)

        if not success:
            raise RuntimeError(f"Failed to create team {team_id}")

        # Register leader as a member — starts busy/running immediately
        leader_card = AgentCard(
            id=leader_id,
            name=leader_name,
            description=leader_desc,
        )
        await self.spawn_member(
            member_id=leader_id,
            name=leader_name,
            agent_card=leader_card,
            desc=leader_desc,
            status=MemberStatus.BUSY,
            execution_status=ExecutionStatus.RUNNING,
            mode=MemberMode.BUILD_MODE,
        )

        # Register predefined teammates (UNSTARTED, launched later via broadcast)
        for member_spec in self.predefined_members:
            member_card = AgentCard(
                id=member_spec.member_id,
                name=member_spec.name,
                description=member_spec.persona,
            )
            await self.spawn_member(
                member_id=member_spec.member_id,
                name=member_spec.name,
                agent_card=member_card,
                desc=member_spec.persona,
                prompt=member_spec.prompt_hint,
                status=MemberStatus.UNSTARTED,
                execution_status=ExecutionStatus.IDLE,
                mode=self.teammate_mode,
            )

        # Publish team created event
        session_id = get_session_id()
        try:
            await self.messager.publish(
                topic_id=TeamTopic.TEAM.build(session_id, team_id),
                message=EventMessage.from_event(TeamCreatedEvent(
                    team_id=team_id,
                    name=name,
                    leader_id=leader_id,
                    created=TeamDatabase.get_current_time()
                )),
            )
            team_logger.debug(f"Team created event published: {team_id}")
        except Exception as e:
            team_logger.error(f"Failed to publish team created event for {team_id}: {e}")

        team_logger.info(f"Team {team_id} created successfully")
