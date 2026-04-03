# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team_tools module"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.tools.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.status import (
    MemberStatus,
)
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_tools import (
    ApproveToolCallTool,
    ApprovePlanTool,
    BroadcastMessageTool,
    BuildTeamTool,
    ClaimTaskTool,
    CleanTeamTool,
    CompleteTaskTool,
    ListMembersTool,
    SendMessageTool,
    ShutdownMemberTool,
    SpawnMemberTool,
    TaskManagerToolV2,
    ViewTaskToolV2,
)
from openjiuwen.agent_teams.tools.locales import Translator, make_translator
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@pytest.fixture
def t() -> Translator:
    """Provide a default (cn) translator for tool construction."""
    return make_translator("cn")


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
    """Provide initialized AgentTeam instance with pre-created team"""
    team_id = "test_team"
    await db.create_team(
        team_id=team_id,
        name="Test Team",
        leader_member_id="leader1"
    )
    return TeamBackend(
        team_id=team_id,
        member_id="leader1",
        is_leader=True,
        db=db,
        messager=message_bus,
    )


@pytest_asyncio.fixture
async def agent_team_without_team(db, message_bus):
    """Provide AgentTeam instance without pre-created team (for BuildTeamTool tests)"""
    return TeamBackend(
        team_id="test_team",
        member_id="leader1",
        is_leader=True,
        db=db,
        messager=message_bus,
    )


@pytest.fixture
def sample_agent_card():
    """Provide sample AgentCard for testing"""
    return AgentCard(
        name="TestAgent",
        description="A test agent",
        version="1.0.0"
    )


# ========== Team Management Tools ==========


class TestBuildTeamTool:
    """Test BuildTeamTool"""

    def test_initialization(self, agent_team_without_team, t):
        """Test tool initialization"""
        tool = BuildTeamTool(agent_team_without_team, t)
        assert tool.card.name == "build_team"
        assert tool.card.id == "team.build_team"
        assert tool.team == agent_team_without_team
        assert tool.db == agent_team_without_team.db
        assert tool.messager == agent_team_without_team.messager

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team_without_team, t, db):
        """Test invoking build team tool successfully"""
        tool = BuildTeamTool(agent_team_without_team, t)
        result = await tool.invoke({
            "name": "My Team",
            "desc": "Test team description",
            "prompt": "Team prompt",
            "leader_name": "Lead",
            "leader_desc": "Project manager",
        })

        assert result.success is True
        assert result.error is None
        # Verify team was created in database
        team_info = await db.get_team("test_team")
        assert team_info.name == "My Team"
        assert team_info.desc == "Test team description"
        # Verify leader was registered as a member
        leader = await db.get_member("leader1")
        assert leader is not None
        assert leader.name == "Lead"

    @pytest.mark.asyncio
    async def test_invoke_with_minimal_args(self, agent_team_without_team, t, db):
        """Test invoking build team tool with minimal arguments"""
        tool = BuildTeamTool(agent_team_without_team, t)
        result = await tool.invoke({
            "name": "Minimal Team",
            "desc": "A minimal team",
            "leader_name": "Lead",
            "leader_desc": "PM",
        })

        assert result.success is True
        team_info = await db.get_team("test_team")
        assert team_info.name == "Minimal Team"
        assert team_info.desc == "A minimal team"


class TestCleanTeamTool:
    """Test CleanTeamTool"""

    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = CleanTeamTool(agent_team, t)
        assert tool.card.name == "clean_team"
        assert tool.card.id == "team.clean_team"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, t, sample_agent_card, db):
        """Test invoking clean team tool successfully"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )
        # Shutdown member
        await db.update_member_status("member1", MemberStatus.SHUTDOWN_REQUESTED.value)
        await db.update_member_status("member1", MemberStatus.SHUTDOWN.value)

        tool = CleanTeamTool(agent_team, t)
        result = await tool.invoke({})

        assert result.success is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_invoke_fails_when_members_not_shutdown(self, agent_team, t, sample_agent_card):
        """Test invoking clean team tool fails when members not shutdown"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        tool = CleanTeamTool(agent_team, t)
        result = await tool.invoke({})

        assert result.success is False
        assert result.error is not None


# ========== Member Management Tools ==========


class TestSpawnMemberTool:
    """Test SpawnMemberTool"""

    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = SpawnMemberTool(agent_team, t)
        assert tool.card.name == "spawn_member"
        assert tool.card.id == "team.spawn_member"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, t):
        """Test invoking spawn member tool successfully"""
        tool = SpawnMemberTool(agent_team, t)
        result = await tool.invoke({
            "member_id": "member1",
            "name": "Member One",
            "desc": "Test member",
            "prompt": "Member prompt"
        })

        assert result.success is True
        assert result.error is None


class TestShutdownMemberTool:
    """Test ShutdownMemberTool"""

    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = ShutdownMemberTool(agent_team, t)
        assert tool.card.name == "shutdown_member"
        assert tool.card.id == "team.shutdown_member"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, t, sample_agent_card, db):
        """Test invoking shutdown member tool successfully"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card,
            status=MemberStatus.READY,
        )

        tool = ShutdownMemberTool(agent_team, t)
        result = await tool.invoke({
            "member_id": "member1",
            "force": False
        })

        assert result.success is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_invoke_with_force(self, agent_team, t, sample_agent_card, db):
        """Test invoking shutdown member tool with force option"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card,
            status=MemberStatus.READY,
        )

        tool = ShutdownMemberTool(agent_team, t)
        result = await tool.invoke({
            "member_id": "member1",
            "force": True
        })

        assert result.success is True

    @pytest.mark.asyncio
    async def test_invoke_member_not_found(self, agent_team, t):
        """Test invoking shutdown member tool for non-existent member"""
        tool = ShutdownMemberTool(agent_team, t)
        result = await tool.invoke({"member_id": "nonexistent"})

        assert result.success is False
        assert result.error is not None


class TestApprovePlanTool:
    """Test ApprovePlanTool"""

    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = ApprovePlanTool(agent_team, t)
        assert tool.card.name == "approve_plan"
        assert tool.card.id == "team.approve_plan"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_approve(self, agent_team, t, sample_agent_card):
        """Test invoking approve plan tool to approve"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        tool = ApprovePlanTool(agent_team, t)
        result = await tool.invoke({
            "member_id": "member1",
            "approved": True,
            "feedback": "Great plan!"
        })

        assert result.success is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_invoke_reject(self, agent_team, t, sample_agent_card):
        """Test invoking approve plan tool to reject"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        tool = ApprovePlanTool(agent_team, t)
        result = await tool.invoke({
            "member_id": "member1",
            "approved": False,
            "feedback": "Please revise"
        })

        assert result.success is True

    @pytest.mark.asyncio
    async def test_invoke_member_not_found(self, agent_team, t):
        """Test invoking approve plan tool for non-existent member"""
        tool = ApprovePlanTool(agent_team, t)
        result = await tool.invoke({
            "member_id": "nonexistent",
            "approved": True
        })

        assert result.success is False


class TestApproveToolCallTool:
    """Test ApproveToolCallTool."""

    def test_initialization(self, agent_team, t):
        tool = ApproveToolCallTool(agent_team, t)
        assert tool.card.name == "approve_tool"
        assert tool.card.id == "team.approve_tool"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_approve(self, agent_team, t, sample_agent_card):
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card,
        )

        tool = ApproveToolCallTool(agent_team, t)
        result = await tool.invoke({
            "member_id": "member1",
            "tool_call_id": "call-1",
            "approved": True,
            "feedback": "approved",
            "auto_confirm": True,
        })

        assert result.success is True
        assert result.error is None


class TestListMembersTool:
    """Test ListMembersTool"""

    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = ListMembersTool(agent_team, t)
        assert tool.card.name == "list_members"
        assert tool.card.id == "team.list_members"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_empty(self, agent_team, t):
        """Test invoking list members tool when empty"""
        tool = ListMembersTool(agent_team, t)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["count"] == 0
        assert result.data["members"] == []

    @pytest.mark.asyncio
    async def test_invoke_with_members(self, agent_team, t, sample_agent_card):
        """Test invoking list members tool with members"""
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

        tool = ListMembersTool(agent_team, t)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["count"] == 2
        member_ids = [m["member_id"] for m in result.data["members"]]
        assert "member1" in member_ids
        assert "member2" in member_ids


# ========== Task Management Tools (V2) ==========


class TestTaskManagerToolV2:
    """Test TaskManagerToolV2 (unified task management)"""

    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = TaskManagerToolV2(agent_team, t)
        assert tool.card.name == "task_manager"
        assert tool.card.id == "team.task_manager"

    @pytest.mark.asyncio
    async def test_invoke_add_single_task(self, agent_team, t):
        """Test add action with a single task"""
        tool = TaskManagerToolV2(agent_team, t)
        result = await tool.invoke({
            "action": "add",
            "tasks": [{"title": "Task 1", "content": "Content 1"}]
        })

        assert result.success is True
        assert result.data["title"] == "Task 1"

    @pytest.mark.asyncio
    async def test_invoke_add_default_action(self, agent_team, t):
        """Test that add is the default action"""
        tool = TaskManagerToolV2(agent_team, t)
        result = await tool.invoke({
            "tasks": [{"title": "Default Task", "content": "Content"}]
        })

        assert result.success is True
        assert result.data["title"] == "Default Task"

    @pytest.mark.asyncio
    async def test_invoke_add_batch_tasks(self, agent_team, t):
        """Test add action with batch tasks"""
        tool = TaskManagerToolV2(agent_team, t)
        result = await tool.invoke({
            "action": "add",
            "tasks": [
                {"title": "Task 1", "content": "Content 1"},
                {"title": "Task 2", "content": "Content 2"},
                {"title": "Task 3", "content": "Content 3"},
            ]
        })

        assert result.success is True
        assert result.data["count"] == 3
        assert result.data["skipped"] == 0
        assert len(result.data["tasks"]) == 3

    @pytest.mark.asyncio
    async def test_invoke_add_no_tasks(self, agent_team, t):
        """Test add action with empty tasks list"""
        tool = TaskManagerToolV2(agent_team, t)
        result = await tool.invoke({"action": "add", "tasks": []})

        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_invoke_insert_task(self, agent_team, t):
        """Test insert action to add task into existing DAG"""
        tool = TaskManagerToolV2(agent_team, t)
        result = await tool.invoke({
            "action": "insert",
            "title": "Priority Task",
            "content": "Priority content"
        })

        assert result.success is True
        assert result.data["title"] == "Priority Task"

    @pytest.mark.asyncio
    async def test_invoke_update_task(self, agent_team, t):
        """Test update action"""
        task = await agent_team.task_manager.add(title="Original Title", content="Original Content")

        tool = TaskManagerToolV2(agent_team, t)
        result = await tool.invoke({
            "action": "update",
            "task_id": task.task_id,
            "title": "Updated Title",
            "content": "Updated Content"
        })

        assert result.success is True
        assert result.data["status"] == "updated"

    @pytest.mark.asyncio
    async def test_invoke_cancel_task(self, agent_team, t):
        """Test cancel action"""
        task = await agent_team.task_manager.add(title="Task to Cancel", content="Content")

        tool = TaskManagerToolV2(agent_team, t)
        result = await tool.invoke({
            "action": "cancel",
            "task_id": task.task_id
        })

        assert result.success is True
        assert result.data["task_id"] == task.task_id
        assert result.data["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_invoke_cancel_all_tasks(self, agent_team, t, db):
        """Test cancel_all action"""
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")

        tool = TaskManagerToolV2(agent_team, t)
        result = await tool.invoke({"action": "cancel_all"})

        assert result.success is True
        assert result.data["cancelled_count"] == 2


class TestViewTaskToolV2:
    """Test ViewTaskToolV2 (unified task viewing)"""

    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = ViewTaskToolV2(agent_team.task_manager, t)
        assert tool.card.name == "view_task"
        assert tool.card.id == "team.view_task"

    @pytest.mark.asyncio
    async def test_invoke_get_single_task(self, agent_team, t):
        """Test get action for a single task"""
        tm = agent_team.task_manager
        task = await tm.add(title="Single Task", content="Content")

        tool = ViewTaskToolV2(tm, t)
        result = await tool.invoke({"action": "get", "task_id": task.task_id})

        assert result.success is True
        assert result.data["task_id"] == task.task_id
        assert result.data["title"] == "Single Task"

    @pytest.mark.asyncio
    async def test_invoke_get_task_not_found(self, agent_team, t):
        """Test get action for a non-existent task"""
        tool = ViewTaskToolV2(agent_team.task_manager, t)
        result = await tool.invoke({"action": "get", "task_id": "nonexistent"})

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_invoke_get_without_task_id(self, agent_team, t):
        """Test get action without task_id"""
        tool = ViewTaskToolV2(agent_team.task_manager, t)
        result = await tool.invoke({"action": "get"})

        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_invoke_list_tasks_by_status(self, agent_team, t, db):
        """Test list action filtered by status"""
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")
        await db.create_task("task3", "test_team", "Task 3", "Content 3", "completed")

        tool = ViewTaskToolV2(agent_team.task_manager, t)
        result = await tool.invoke({"action": "list", "status": "pending"})

        assert result.success is True
        assert result.data["count"] == 1
        assert result.data["tasks"][0]["title"] == "Task 1"

    @pytest.mark.asyncio
    async def test_invoke_claimable_default(self, agent_team, t, db):
        """Test default action returns claimable tasks"""
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")
        await db.create_task("task3", "test_team", "Task 3", "Content 3", "completed")

        tool = ViewTaskToolV2(agent_team.task_manager, t)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["count"] == 1
        assert result.data["tasks"][0]["task_id"] == "task1"


# ========== Task Execution Tools ==========


class TestClaimTaskTool:
    """Test ClaimTaskTool"""

    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = ClaimTaskTool(agent_team.task_manager, t)
        assert tool.card.name == "claim_task"
        assert tool.card.id == "team.claim_task"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, t, sample_agent_card, db):
        """Test invoking claim task tool successfully"""
        # Create a member for claiming (task_manager uses member_id from TeamBackend)
        await db.create_member(
            member_id="leader1",
            team_id="test_team",
            name="Leader",
            agent_card=sample_agent_card.model_dump_json(),
            status=MemberStatus.READY,
        )
        tm = agent_team.task_manager
        task = await tm.add(title="Test Task", content="Test content")

        tool = ClaimTaskTool(tm, t)
        result = await tool.invoke({"task_id": task.task_id})

        assert result.success is True


class TestCompleteTaskTool:
    """Test CompleteTaskTool"""

    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = CompleteTaskTool(agent_team.task_manager, t)
        assert tool.card.name == "complete_task"
        assert tool.card.id == "team.complete_task"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, t, sample_agent_card, db):
        """Test invoking complete task tool successfully"""
        from openjiuwen.agent_teams.tools.status import MemberMode
        await db.create_member(
            member_id="leader1",
            team_id="test_team",
            name="Leader",
            agent_card=sample_agent_card.model_dump_json(),
            status=MemberStatus.READY,
            mode=MemberMode.BUILD_MODE.value,
        )
        tm = agent_team.task_manager
        task = await tm.add(title="Test Task", content="Test content")
        await tm.claim(task.task_id)

        tool = CompleteTaskTool(tm, t)
        result = await tool.invoke({"task_id": task.task_id})

        assert result.success is True


# ========== Messaging Tools ==========


class TestSendMessageTool:
    """Test SendMessageTool"""

    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = SendMessageTool(agent_team.message_manager, t)
        assert tool.card.name == "send_message"
        assert tool.card.id == "team.send_message"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, t):
        """Test invoking send message tool successfully"""
        tool = SendMessageTool(agent_team.message_manager, t)
        result = await tool.invoke({
            "content": "Hello",
            "to_member": "member2"
        })

        assert result.success is True


class TestBroadcastMessageTool:
    """Test BroadcastMessageTool"""

    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = BroadcastMessageTool(agent_team.message_manager, t)
        assert tool.card.name == "broadcast_message"
        assert tool.card.id == "team.broadcast_message"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, t):
        """Test invoking broadcast message tool successfully"""
        tool = BroadcastMessageTool(agent_team.message_manager, t)
        result = await tool.invoke({
            "content": "Hello everyone"
        })

        assert result.success is True


# ========== Skipped Tests (tools temporarily removed) ==========


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into TaskManagerToolV2.add")
class TestAddTaskTool:
    """Test AddTaskTool (removed - merged into TaskManagerToolV2)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into TaskManagerToolV2.add (batch)")
class TestAddBatchTasksTool:
    """Test AddBatchTasksTool (removed - merged into TaskManagerToolV2)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into TaskManagerToolV2.insert")
class TestAddTaskWithPriorityTool:
    """Test AddTaskWithPriorityTool (removed - merged into TaskManagerToolV2)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into TaskManagerToolV2.insert")
class TestAddTaskAsTopPriorityTool:
    """Test AddTaskAsTopPriorityTool (removed - merged into TaskManagerToolV2)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into TaskManagerToolV2.cancel")
class TestCancelTaskTool:
    """Test CancelTaskTool (removed - merged into TaskManagerToolV2)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into TaskManagerToolV2.cancel_all")
class TestCancelAllTasksTool:
    """Test CancelAllTasksTool (removed - merged into TaskManagerToolV2)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into ViewTaskToolV2.get")
class TestGetTaskTool:
    """Test GetTaskTool (removed - merged into ViewTaskToolV2)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into ViewTaskToolV2.list")
class TestListTasksTool:
    """Test ListTasksTool (removed - merged into ViewTaskToolV2)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into ViewTaskToolV2.claimable")
class TestGetClaimableTasksTool:
    """Test GetClaimableTasksTool (removed - merged into ViewTaskToolV2)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into TaskManagerToolV2.update")
class TestUpdateTaskTool:
    """Test UpdateTaskTool (removed - merged into TaskManagerToolV2)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed")
class TestGetTeamInfoTool:
    """Test GetTeamInfoTool (removed)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed")
class TestGetMemberTool:
    """Test GetMemberTool (removed)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed")
class TestGetMessagesTool:
    """Test GetMessagesTool (removed)"""

    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed")
class TestMarkMessageReadTool:
    """Test MarkMessageReadTool (removed)"""

    def test_placeholder(self):
        pass
