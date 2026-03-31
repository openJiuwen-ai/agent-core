# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for AgentTeam module"""

from unittest.mock import (
    AsyncMock,
    patch,
)

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.tools.context import set_session_id, reset_session_id
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase, Team, TeamMember,
)
from openjiuwen.agent_teams.tools.status import (
    ExecutionStatus,
    MemberStatus, TaskStatus,
)
from openjiuwen.agent_teams.tools.team import (
    TeamBackend,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@pytest.fixture
def db_config():
    """Provide in-memory database config for testing"""
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    """Provide initialized database instance"""
    token = set_session_id("session_id")
    database = TeamDatabase(db_config)
    try:
        await database.initialize()
        yield database
    finally:
        reset_session_id(token)
        await database.close()


@pytest_asyncio.fixture
async def message_bus():
    """Provide Messager mock instance for testing"""
    bus = AsyncMock(spec=Messager)
    yield bus


@pytest_asyncio.fixture
async def agent_team(db, message_bus):
    """Provide initialized AgentTeam instance"""
    team_id = "test_team"
    await db.create_team(
        team_id=team_id,
        name="Test Team",
        leader_member_id="leader1"
    )
    return TeamBackend(
        team_id=team_id,
        member_id="leader1",
        db=db,
        messager=message_bus,
        is_leader=True
    )


@pytest.fixture
def sample_agent_card():
    """Provide sample AgentCard for testing"""
    return AgentCard(
        name="TestAgent",
        description="A test agent",
        version="1.0.0"
    )


class TestAgentTeamInit:
    """Test AgentTeam initialization"""

    @pytest.mark.asyncio
    async def test_agent_team_init(self, agent_team):
        """Test AgentTeam initialization"""
        assert agent_team.team_id == "test_team"
        assert agent_team.member_id == "leader1"
        assert agent_team.task_manager is not None

    @pytest.mark.asyncio
    async def test_agent_team_with_optional_fields(self, db, message_bus):
        """Test AgentTeam with optional description and prompt"""
        await db.create_team(
            team_id="team_with_optional",
            name="Optional Team",
            leader_member_id="leader1",
            desc="Team description",
            prompt="Team prompt"
        )
        team = TeamBackend(
            team_id="team_with_optional",
            member_id="leader1",
            db=db,
            messager=message_bus,
            is_leader=True
        )

        team_info = await team.get_team_info()
        assert team_info.desc == "Team description"
        assert team_info.prompt == "Team prompt"


class TestSpawnMember:
    """Test spawn_member functionality"""

    @pytest.mark.asyncio
    async def test_spawn_member_success(self, agent_team, sample_agent_card):
        """Test spawning a member successfully"""
        result = await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card,
            desc="Test member",
            prompt="Member prompt"
        )

        assert result is True
        assert await agent_team.get_member("member1")

    @pytest.mark.asyncio
    async def test_spawn_member_creates_in_database(self, agent_team, sample_agent_card, db):
        """Test that spawn_member creates member in database"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        member = await db.get_member("member1")
        assert member is not None
        assert member.member_id == "member1"
        assert member.name == "Member One"
        assert member.team_id == "test_team"
        assert member.status == MemberStatus.UNSTARTED.value
        assert member.execution_status == ExecutionStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_spawn_member_multiple(self, agent_team, sample_agent_card):
        """Test spawning multiple members"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )
        await agent_team.spawn_member(
            member_id="member2",
            name="Member Two",
            agent_card=sample_agent_card
        )

        members = await agent_team.list_members()
        assert len(members) == 2

    @pytest.mark.asyncio
    async def test_spawn_member_with_minimal_args(self, agent_team, sample_agent_card):
        """Test spawning member with minimal arguments"""
        result = await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        assert result is True
        members = await agent_team.list_members()
        assert len(members) == 1
        assert "member1" == members[0].member_id


class TestApprovePlan:
    """Test approve_plan functionality"""

    @pytest.mark.asyncio
    async def test_approve_plan_success(self, agent_team, sample_agent_card):
        """Test approving a plan successfully"""
        # Create a member first
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        # Approve plan
        result = await agent_team.approve_plan(
            member_id="member1",
            approved=True,
            feedback="Plan looks good"
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_approve_plan_sends_message(self, agent_team, sample_agent_card):
        """Test that approve_plan sends a message"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        with patch.object(agent_team.message_manager, 'send_message', new_callable=AsyncMock, return_value="msg123") as mock_send:
            await agent_team.approve_plan(
                member_id="member1",
                approved=True,
                feedback="Great plan!"
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            content = call_args.kwargs.get('content') or call_args[1].get('content')
            to_member = call_args.kwargs.get('to_member') or call_args[1].get('to_member')

            assert "APPROVED" in content
            assert "Great plan!" in content
            assert to_member == "member1"

    @pytest.mark.asyncio
    async def test_reject_plan_sends_message(self, agent_team, sample_agent_card):
        """Test that rejecting a plan sends appropriate message"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        with patch.object(agent_team.message_manager, 'send_message', new_callable=AsyncMock, return_value="msg123") as mock_send:
            await agent_team.approve_plan(
                member_id="member1",
                approved=False,
                feedback="Please revise"
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            content = call_args.kwargs.get('content') or call_args[1].get('content')

            assert "REJECTED" in content
            assert "Please revise" in content

    @pytest.mark.asyncio
    async def test_approve_plan_member_not_found(self, agent_team):
        """Test approving plan for non-existent member"""
        result = await agent_team.approve_plan(
            member_id="nonexistent_member",
            approved=True
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_approve_plan_without_feedback(self, agent_team, sample_agent_card):
        """Test approving plan without feedback"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        with patch.object(agent_team.message_manager, 'send_message', new_callable=AsyncMock, return_value="msg123") as mock_send:
            await agent_team.approve_plan(
                member_id="member1",
                approved=True
            )

            call_args = mock_send.call_args
            content = call_args.kwargs.get('content') or call_args[1].get('content')
            assert "Your plan has been APPROVED" in content


class TestShutdownMember:
    """Test shutdown_member functionality"""

    @pytest.mark.asyncio
    async def test_shutdown_member_success(self, agent_team, sample_agent_card, db):
        """Test shutting downser successfully"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card,
            status=MemberStatus.READY
        )

        result = await agent_team.shutdown_member(member_id="member1", force=False)

        assert result is True

    @pytest.mark.asyncio
    async def test_shutdown_member_updates_status(self, agent_team, sample_agent_card, db):
        """Test that shutdown_member updates member status"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card,
            status=MemberStatus.READY
        )

        await agent_team.shutdown_member(member_id="member1")

        member = await db.get_member("member1")
        assert member.status == MemberStatus.SHUTDOWN_REQUESTED.value

    @pytest.mark.asyncio
    async def test_shutdown_member_already_shutdown(self, agent_team, sample_agent_card, db):
        """Test shutting down an already shutdown member"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        # First shutdown
        await agent_team.shutdown_member(member_id="member1")
        await db.update_member_status("member1", MemberStatus.SHUTDOWN.value)

        # Try to shutdown again
        result = await agent_team.shutdown_member(member_id="member1")
        assert result is True

    @pytest.mark.asyncio
    async def test_shutdown_member_not_found(self, agent_team):
        """Test shutting down non-existent member"""
        result = await agent_team.shutdown_member(member_id="nonexistent_member")
        assert result is False


class TestCancelMember:
    """Test cancel_member functionality"""

    @pytest.mark.asyncio
    async def test_cancel_member_success(self, agent_team, sample_agent_card, db):
        """Test cancelling a member execution successfully"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        result = await agent_team.cancel_member(member_id="member1")

        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_member_when_busy(self, agent_team, sample_agent_card, db):
        """Test cancelling a busy member"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )
        # Set member to busy
        await db.update_member_status("member1", MemberStatus.BUSY.value)

        result = await agent_team.cancel_member(member_id="member1")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_member_when_not_busy(self, agent_team, sample_agent_card, db):
        """Test cancelling a non-busy member returns True (no-op)"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )
        # Set member to ready (not busy)
        await db.update_member_status("member1", MemberStatus.READY.value)

        result = await agent_team.cancel_member(member_id="member1")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_member_not_found(self, agent_team):
        """Test cancelling non-existent member"""
        result = await agent_team.cancel_member(member_id="nonexistent_member")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_member_resets_claimed_tasks(self, agent_team, sample_agent_card, db, message_bus):
        """Test that cancelling a member resets their claimed tasks"""
        from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager

        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card,
            status=MemberStatus.BUSY
        )

        # Create member1's task_manager
        member1_task_manager = TeamTaskManager(
            team_id="test_team",
            member_id="member1",
            db=db,
            messager=message_bus
        )

        # Create and claim tasks for the member using member1's task_manager
        task1 = await member1_task_manager.add(title="Task 1", content="Content 1")
        task2 = await member1_task_manager.add(title="Task 2", content="Content 2")
        task3 = await member1_task_manager.add(title="Task 3", content="Content 3")

        await member1_task_manager.claim(task1.task_id)
        await member1_task_manager.claim(task2.task_id)
        # task3 remains unclaimed

        # Verify tasks are claimed
        task1_claimed = await db.get_task(task1.task_id)
        task2_claimed = await db.get_task(task2.task_id)
        assert task1_claimed.status == TaskStatus.CLAIMED.value
        assert task2_claimed.status == TaskStatus.CLAIMED.value
        assert task1_claimed.assignee == "member1"
        assert task2_claimed.assignee == "member1"

        # Cancel member
        result = await agent_team.cancel_member(member_id="member1")
        assert result is True

        # Verify claimed tasks are reset to PENDING
        task1_reset = await db.get_task(task1.task_id)
        task2_reset = await db.get_task(task2.task_id)
        assert task1_reset.status == TaskStatus.PENDING.value
        assert task2_reset.status == TaskStatus.PENDING.value
        assert task1_reset.assignee is None
        assert task2_reset.assignee is None

        # Verify unclaimed task remains unchanged
        task3_unchanged = await db.get_task(task3.task_id)
        assert task3_unchanged.status == TaskStatus.PENDING.value
        assert task3_unchanged.assignee is None

    @pytest.mark.asyncio
    async def test_cancel_member_no_claimed_tasks(self, agent_team, sample_agent_card, db):
        """Test cancelling a member with no claimed tasks"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card,
            status=MemberStatus.BUSY
        )

        # Create tasks but don't claim them
        task1 = await agent_team.task_manager.add(title="Task 1", content="Content 1")
        task2 = await agent_team.task_manager.add(title="Task 2", content="Content 2")

        # Cancel member
        result = await agent_team.cancel_member(member_id="member1")
        assert result is True

        # Verify tasks remain pending with no assignee
        task1_after = await db.get_task(task1.task_id)
        task2_after = await db.get_task(task2.task_id)
        assert task1_after.status == TaskStatus.PENDING.value
        assert task2_after.status == TaskStatus.PENDING.value
        assert task1_after.assignee is None
        assert task2_after.assignee is None


class TestCleanTeam:
    """Test clean_team functionality"""

    @pytest.mark.asyncio
    async def test_clean_team_success(self, agent_team, sample_agent_card, db):
        """Test cleaning up a team successfully"""
        # Create members
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )
        await agent_team.spawn_member(
            member_id="member2",
            name="Member Two",
            agent_card=sample_agent_card
        )

        # Shutdown all members
        await db.update_member_status("member1", MemberStatus.SHUTDOWN_REQUESTED.value)
        await db.update_member_status("member1", MemberStatus.SHUTDOWN.value)
        await db.update_member_status("member2", MemberStatus.SHUTDOWN_REQUESTED.value)
        await db.update_member_status("member2", MemberStatus.SHUTDOWN.value)

        result = await agent_team.clean_team()
        assert result is True

    @pytest.mark.asyncio
    async def test_clean_team_fails_when_members_not_shutdown(self, agent_team, sample_agent_card, db):
        """Test that clean_team fails when members are not shutdown"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )
        # Member is not shut down (status is BUSY)

        result = await agent_team.clean_team()
        assert result is False

    @pytest.mark.asyncio
    async def test_clean_team_partial_shutdown(self, agent_team, sample_agent_card, db):
        """Test clean_team when only some members are shutdown"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )
        await agent_team.spawn_member(
            member_id="member2",
            name="Member Two",
            agent_card=sample_agent_card
        )

        # Only shutdown one member
        await db.update_member_status("member1", MemberStatus.SHUTDOWN.value)

        result = await agent_team.clean_team()
        assert result is False


class TestGetMember:
    """Test get_member functionality"""

    @pytest.mark.asyncio
    async def test_get_member_success(self, agent_team, sample_agent_card, db):
        """Test getting a member successfully"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
                       agent_card=sample_agent_card,
            desc="Test description"
        )

        member = await agent_team.get_member("member1")

        assert member is not None
        assert member.member_id == "member1"
        assert member.name == "Member One"
        assert member.team_id == "test_team"
        assert isinstance(member, TeamMember)

    @pytest.mark.asyncio
    async def test_get_member_not_found(self, agent_team):
        """Test getting non-existent member"""
        member = await agent_team.get_member("nonexistent_member")
        assert member is None


class TestListMembers:
    """Test list_members functionality"""

    @pytest.mark.asyncio
    async def test_list_members_empty(self, agent_team):
        """Test listing members when none exist"""
        members = await agent_team.list_members()
        assert members == []

    @pytest.mark.asyncio
    async def test_list_members_with_members(self, agent_team, sample_agent_card):
        """Test listing members when they exist"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )
        await agent_team.spawn_member(
            member_id="member2",
            name="Member Two",
            agent_card=sample_agent_card
        )

        members = await agent_team.list_members()

        assert len(members) == 2
        member_ids = [m.member_id for m in members]
        assert "member1" in member_ids
        assert "member2" in member_ids
        assert all(isinstance(m, TeamMember) for m in members)


class TestGetTeamInfo:
    """Test get_team_info functionality"""

    @pytest.mark.asyncio
    async def test_get_team_info_success(self, agent_team):
        """Test getting team info successfully"""
        team_info = await agent_team.get_team_info()

        assert team_info is not None
        assert team_info.team_id == "test_team"
        assert team_info.name == "Test Team"
        assert team_info.leader_member_id == "leader1"
        assert isinstance(team_info, Team)

    @pytest.mark.asyncio
    async def test_get_team_info_with_optional_fields(self, db, message_bus):
        """Test getting team info with optional fields"""
        await db.create_team(
            team_id="full_team",
            name="Full Team",
            leader_member_id="leader1",
            desc="Full description",
            prompt="Full prompt"
        )

        team = TeamBackend(
            team_id="full_team",
            member_id="leader1",
            db=db,
            messager=message_bus,
            is_leader=True
        )

        team_info = await team.get_team_info()

        assert team_info is not None
        assert team_info.team_id == "full_team"
        assert team_info.name == "Full Team"
        assert team_info.leader_member_id == "leader1"
        assert team_info.desc == "Full description"
        assert team_info.prompt == "Full prompt"
        assert team_info.created is not None

    @pytest.mark.asyncio
    async def test_get_team_info_not_found(self, db, message_bus):
        """Test getting info for non-existent team"""
        team = TeamBackend(
            team_id="nonexistent_team",
            member_id="leader1",
            db=db,
            messager=message_bus,
            is_leader=True
        )

        team_info = await team.get_team_info()
        # Note: get_team_info uses db.get_team which returns None if not found
        assert team_info is None


class TestCancelTask:
    """Test cancel_task functionality"""

    @pytest.mark.asyncio
    async def test_cancel_task_success(self, agent_team, db):
        """Test cancelling a task successfully"""
        # Create a task
        await db.create_task(
            task_id="task1",
            team_id="test_team",
            title="Test Task",
            content="Task content",
            status="pending"
        )

        result = await agent_team.cancel_task(task_id="task1")

        assert result is True
        # Verify task is cancelled
        task = await db.get_task("task1")
        assert task.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_task_not_found(self, agent_team):
        """Test cancelling a non-existent task"""
        result = await agent_team.cancel_task(task_id="nonexistent_task")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_task_already_cancelled(self, agent_team, db):
        """Test cancelling an already cancelled task"""
        # Create and cancel a task
        await db.create_task(
            task_id="task1",
            team_id="test_team",
            title="Test Task",
            content="Task content",
            status="pending"
        )
        await db.update_task_status("task1", "cancelled")

        # Try to cancel again
        result = await agent_team.cancel_task(task_id="task1")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_task_with_assignee_sends_notification(self, agent_team, db):
        """Test cancelling a claimed task sends notification to assignee"""
        # Create a task and claim it
        await db.create_task(
            task_id="task1",
            team_id="test_team",
            title="Test Task",
            content="Task content",
            status="pending"
        )
        await db.claim_task(task_id="task1", member_id="member1")

        result = await agent_team.cancel_task(task_id="task1")

        assert result is True

        # Verify notification message was sent via database
        messages = await db.get_messages(team_id="test_team", to_member="member1")
        assert len(messages) == 1
        message = messages[0]
        assert "cancelled" in message.content.lower()
        assert "Test Task" in message.content
        assert message.from_member == "leader1"
        assert message.to_member == "member1"
        assert message.broadcast is False

    @pytest.mark.asyncio
    async def test_cancel_task_without_assignee_no_notification(self, agent_team, db):
        """Test cancelling an unclaimed task doesn't send notification"""
        # Create an unclaimed task
        await db.create_task(
            task_id="task1",
            team_id="test_team",
            title="Test Task",
            content="Task content",
            status="pending"
        )

        result = await agent_team.cancel_task(task_id="task1")

        assert result is True

        # Verify no notification message was sent
        messages = await db.get_team_messages(team_id="test_team", broadcast=False)
        cancel_notifications = [m for m in messages if "cancelled" in m["content"].lower()]
        assert len(cancel_notifications) == 0


class TestCancelAllTasks:
    """Test cancel_all_tasks functionality"""

    @pytest.mark.asyncio
    async def test_cancel_all_tasks_success(self, agent_team, db):
        """Test cancelling all tasks successfully"""
        # Create multiple tasks
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "pending")
        await db.create_task("task3", "test_team", "Task 3", "Content 3", "pending")

        # Cancel all tasks
        count = await agent_team.cancel_all_tasks()

        assert count == 3

        # Verify all tasks are cancelled
        task1 = await db.get_task("task1")
        task2 = await db.get_task("task2")
        task3 = await db.get_task("task3")
        assert task1.status == "cancelled"
        assert task2.status == "cancelled"
        assert task3.status == "cancelled"

        # Verify broadcast message was sent
        messages = await db.get_team_messages(team_id="test_team", broadcast=True)
        assert len(messages) == 1
        assert "All tasks (3) have been cancelled" in messages[0].content

    @pytest.mark.asyncio
    async def test_cancel_all_tasks_mixed_status(self, agent_team, db):
        """Test cancelling tasks with mixed statuses"""
        # Create tasks with different statuses
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")
        await db.claim_task("task2", "member1")
        await db.create_task("task3", "test_team", "Task 3", "Content 3", "cancelled")
        await db.create_task("task4", "test_team", "Task 4", "Content 4", "completed")

        # Cancel all tasks
        count = await agent_team.cancel_all_tasks()

        # Only pending and claimed tasks should be cancelled (2 tasks)
        assert count == 2

        # Verify
        task1 = await db.get_task("task1")
        task2 = await db.get_task("task2")
        task3 = await db.get_task("task3")
        task4 = await db.get_task("task4")
        assert task1.status == "cancelled"
        assert task2.status == "cancelled"
        assert task3.status == "cancelled"  # Stays cancelled
        assert task4.status == "completed"  # Stays completed

    @pytest.mark.asyncio
    async def test_cancel_all_tasks_no_active_tasks(self, agent_team, db):
        """Test cancelling when no active tasks"""
        # Only have cancelled and completed tasks
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "cancelled")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "completed")

        # Cancel all tasks
        count = await agent_team.cancel_all_tasks()

        assert count == 0

        # Verify no broadcast message was sent
        messages = await db.get_team_messages(team_id="test_team", broadcast=True)
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_cancel_all_tasks_empty_team(self, agent_team):
        """Test cancelling when team has no tasks"""
        count = await agent_team.cancel_all_tasks()
        assert count == 0
