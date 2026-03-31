# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team_tools module"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.tools.context import set_session_id, reset_session_id
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.status import MemberStatus
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_tools import (
    AddTaskAsTopPriorityTool,
    AddBatchTasksTool,
    AddTaskTool,
    AddTaskWithPriorityTool,
    ApprovePlanTool,
    BroadcastMessageTool,
    CancelTaskTool,
    ClaimTaskTool,
    CleanTeamTool,
    CompleteTaskTool,
    GetClaimableTasksTool,
    GetMemberTool,
    GetMessagesTool,
    GetTaskTool,
    GetTeamInfoTool,
    ListMembersTool,
    ListTasksTool,
    MarkMessageReadTool,
    SendMessageTool,
    ShutdownMemberTool,
    SpawnMemberTool,
    BuildTeamTool,
    TaskManagerTool,
    UpdateTaskTool, CancelAllTasksTool,
    ViewTaskTool,
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
        leader_id="leader1",
        db=db,
        messager=message_bus,
    )


@pytest_asyncio.fixture
async def agent_team_without_team(db, message_bus):
    """Provide AgentTeam instance without pre-created team (for BuildTeamTool tests)"""
    return TeamBackend(
        team_id="test_team",
        leader_id="leader1",
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


class TestSpawnTeamTool:
    """Test BuildTeamTool"""

    def test_initialization(self, agent_team_without_team):
        """Test tool initialization"""
        tool = BuildTeamTool(agent_team_without_team)
        assert tool.card.name == "build_team"
        assert tool.card.id == "BuildTeamTool"
        assert tool.team == agent_team_without_team
        assert tool.db == agent_team_without_team.db
        assert tool.messager == agent_team_without_team.messager

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team_without_team, db):
        """Test invoking build team tool successfully"""
        tool = BuildTeamTool(agent_team_without_team)
        result = await tool.invoke({
            "name": "My Team",
            "desc": "Test team description",
            "prompt": "Team prompt",
            "leader_name": "Lead",
            "leader_desc": "Project manager",
        })

        assert result.success is True
        assert result.data is not None
        assert result.data["team_id"] == "test_team"
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
    async def test_invoke_with_minimal_args(self, agent_team_without_team, db):
        """Test invoking build team tool with minimal arguments"""
        tool = BuildTeamTool(agent_team_without_team)
        result = await tool.invoke({
            "name": "Minimal Team",
            "desc": "A minimal team",
            "leader_name": "Lead",
            "leader_desc": "PM",
        })

        assert result.success is True
        # Verify team was created in database
        team_info = await db.get_team("test_team")
        assert team_info.name == "Minimal Team"
        assert team_info.desc == "A minimal team"


class TestCleanTeamTool:
    """Test CleanTeamTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = CleanTeamTool(agent_team)
        assert tool.card.name == "clean_team"
        assert tool.card.id == "CleanTeamTool"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, sample_agent_card, db):
        """Test invoking clean team tool successfully"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )
        # Shutdown member
        await db.update_member_status("member1", MemberStatus.SHUTDOWN_REQUESTED.value)
        await db.update_member_status("member1", MemberStatus.SHUTDOWN.value)

        tool = CleanTeamTool(agent_team)
        result = await tool.invoke({})

        assert result.success is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_invoke_fails_when_members_not_shutdown(self, agent_team, sample_agent_card):
        """Test invoking clean team tool fails when members not shutdown"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        tool = CleanTeamTool(agent_team)
        result = await tool.invoke({})

        assert result.success is False
        assert result.error is not None


class TestGetTeamInfoTool:
    """Test GetTeamInfoTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = GetTeamInfoTool(agent_team)
        assert tool.card.name == "get_team_info"
        assert tool.card.id == "GetTeamInfoTool"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team):
        """Test invoking get team info tool successfully"""
        tool = GetTeamInfoTool(agent_team)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data is not None
        assert result.data["team_id"] == "test_team"
        assert result.data["name"] == "Test Team"
        assert result.data["leader_member_id"] == "leader1"


class TestSpawnMemberTool:
    """Test SpawnMemberTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = SpawnMemberTool(agent_team)
        assert tool.card.name == "spawn_member"
        assert tool.card.id == "SpawnMemberTool"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, sample_agent_card):
        """Test invoking spawn member tool successfully"""
        tool = SpawnMemberTool(agent_team)
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

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = ShutdownMemberTool(agent_team)
        assert tool.card.name == "shutdown_member"
        assert tool.card.id == "ShutdownMemberTool"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, sample_agent_card):
        """Test invoking shutdown member tool successfully"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        tool = ShutdownMemberTool(agent_team)
        result = await tool.invoke({
            "member_id": "member1",
            "force": False
        })

        assert result.success is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_invoke_with_force(self, agent_team, sample_agent_card):
        """Test invoking shutdown member tool with force option"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        tool = ShutdownMemberTool(agent_team)
        result = await tool.invoke({
            "member_id": "member1",
            "force": True
        })

        assert result.success is True

    @pytest.mark.asyncio
    async def test_invoke_member_not_found(self, agent_team):
        """Test invoking shutdown member tool for non-existent member"""
        tool = ShutdownMemberTool(agent_team)
        result = await tool.invoke({"member_id": "nonexistent"})

        assert result.success is False
        assert result.error is not None


class TestApprovePlanTool:
    """Test ApprovePlanTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = ApprovePlanTool(agent_team)
        assert tool.card.name == "approve_plan"
        assert tool.card.id == "ApprovePlanTool"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_approve(self, agent_team, sample_agent_card):
        """Test invoking approve plan tool to approve"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        tool = ApprovePlanTool(agent_team)
        result = await tool.invoke({
            "member_id": "member1",
            "approved": True,
            "feedback": "Great plan!"
        })

        assert result.success is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_invoke_reject(self, agent_team, sample_agent_card):
        """Test invoking approve plan tool to reject"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        tool = ApprovePlanTool(agent_team)
        result = await tool.invoke({
            "member_id": "member1",
            "approved": False,
            "feedback": "Please revise"
        })

        assert result.success is True

    @pytest.mark.asyncio
    async def test_invoke_member_not_found(self, agent_team):
        """Test invoking approve plan tool for non-existent member"""
        tool = ApprovePlanTool(agent_team)
        result = await tool.invoke({
            "member_id": "nonexistent",
            "approved": True
        })

        assert result.success is False


class TestGetMemberTool:
    """Test GetMemberTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = GetMemberTool(agent_team)
        assert tool.card.name == "get_member"
        assert tool.card.id == "GetMemberTool"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, sample_agent_card):
        """Test invoking get member tool successfully"""
        await agent_team.spawn_member(
            member_id="member1",
            name="Member One",
            agent_card=sample_agent_card
        )

        tool = GetMemberTool(agent_team)
        result = await tool.invoke({"member_id": "member1"})

        assert result.success is True
        assert result.data is not None
        assert result.data["member_id"] == "member1"
        assert result.data["name"] == "Member One"

    @pytest.mark.asyncio
    async def test_invoke_not_found(self, agent_team):
        """Test invoking get member tool for non-existent member"""
        tool = GetMemberTool(agent_team)
        result = await tool.invoke({"member_id": "nonexistent"})

        assert result.success is False
        assert result.error == "Member not found"


class TestListMembersTool:
    """Test ListMembersTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = ListMembersTool(agent_team)
        assert tool.card.name == "list_members"
        assert tool.card.id == "ListMembersTool"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    async def test_invoke_empty(self, agent_team):
        """Test invoking list members tool when empty"""
        tool = ListMembersTool(agent_team)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["count"] == 0
        assert result.data["members"] == []

    @pytest.mark.asyncio
    async def test_invoke_with_members(self, agent_team, sample_agent_card):
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

        tool = ListMembersTool(agent_team)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["count"] == 2
        member_ids = [m["member_id"] for m in result.data["members"]]
        assert "member1" in member_ids
        assert "member2" in member_ids


class TestAddTaskTool:
    """Test AddTaskTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = AddTaskTool(agent_team.task_manager)
        assert tool.card.name == "add_task"
        assert tool.card.id == "AddTaskTool"
        assert tool.task_manager == agent_team.task_manager

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team):
        """Test invoking add task tool successfully"""
        tool = AddTaskTool(agent_team.task_manager)
        result = await tool.invoke({
            "title": "Test Task",
            "content": "Test content"
        })

        assert result.success is True
        assert result.data is not None
        assert result.data["title"] == "Test Task"
        assert result.data["content"] == "Test content"

    @pytest.mark.asyncio
    async def test_invoke_with_dependencies(self, agent_team):
        """Test invoking add task tool with dependencies"""
        # Create dependency tasks
        tm = agent_team.task_manager
        task1 = await tm.add(title="Task 1", content="Content 1")
        task2 = await tm.add(title="Task 2", content="Content 2")

        tool = AddTaskTool(tm)
        result = await tool.invoke({
            "title": "Task 3",
            "content": "Content 3",
            "dependencies": [task1.task_id, task2.task_id]
        })

        assert result.success is True
        assert result.data["status"] == "blocked"


class TestAddBatchTasksTool:
    """Test AddBatchTasksTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = AddBatchTasksTool(agent_team.task_manager)
        assert tool.card.name == "add_batch_tasks"
        assert tool.card.id == "AddBatchTasksTool"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team):
        """Test invoking add batch tasks tool successfully"""
        tool = AddBatchTasksTool(agent_team.task_manager)
        result = await tool.invoke({
            "tasks": [
                {"title": "Task 1", "content": "Content 1"},
                {"title": "Task 2", "content": "Content 2"},
                {"title": "Task 3", "content": "Content 3"}
            ]
        })

        assert result.success is True
        assert result.data["count"] == 3
        assert result.data["skipped"] == 0
        assert len(result.data["tasks"]) == 3

    @pytest.mark.asyncio
    async def test_invoke_with_invalid_tasks(self, agent_team):
        """Test invoking add batch tasks tool with some invalid tasks"""
        tool = AddBatchTasksTool(agent_team.task_manager)
        result = await tool.invoke({
            "tasks": [
                {"title": "Valid Task", "content": "Valid content"},
                {"title": "Missing content"},  # invalid
                {"content": "Missing title"},  # invalid
                {"title": "Another Valid Task", "content": "Valid content"}
            ]
        })

        assert result.success is True
        assert result.data["count"] == 2
        assert result.data["skipped"] == 2

    @pytest.mark.asyncio
    async def test_invoke_empty(self, agent_team):
        """Test invoking add batch tasks tool with empty list"""
        tool = AddBatchTasksTool(agent_team.task_manager)
        result = await tool.invoke({"tasks": []})

        assert result.success is True
        assert result.data["count"] == 0
        assert result.data["skipped"] == 0


class TestAddTaskWithPriorityTool:
    """Test AddTaskWithPriorityTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = AddTaskWithPriorityTool(agent_team.task_manager)
        assert tool.card.name == "add_task_with_priority"
        assert tool.card.id == "AddTaskWithPriorityTool"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team):
        """Test invoking add task with priority tool successfully"""
        tool = AddTaskWithPriorityTool(agent_team.task_manager)
        result = await tool.invoke({
            "title": "Priority Task",
            "content": "Priority content"
        })

        assert result.success is True
        assert result.data["title"] == "Priority Task"


class TestAddTaskAsTopPriorityTool:
    """Test AddTaskAsTopPriorityTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = AddTaskAsTopPriorityTool(agent_team.task_manager)
        assert tool.card.name == "add_task_as_top_priority"
        assert tool.card.id == "AddTaskAsTopPriorityTool"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team):
        """Test invoking add task as top priority tool successfully"""
        tool = AddTaskAsTopPriorityTool(agent_team.task_manager)
        result = await tool.invoke({
            "title": "Urgent Task",
            "content": "Urgent content"
        })

        assert result.success is True
        assert result.data["title"] == "Urgent Task"


class TestTaskManagerTool:
    """Test TaskManagerTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = TaskManagerTool(agent_team)
        assert tool.card.name == "task_manager"
        assert tool.card.id == "TaskManagerTool"

    @pytest.mark.asyncio
    async def test_invoke_add_default_action(self, agent_team):
        """Test unified tool add action"""
        tool = TaskManagerTool(agent_team)
        result = await tool.invoke({
            "title": "Unified Task",
            "content": "Unified content"
        })

        assert result.success is True
        assert result.data["title"] == "Unified Task"

    @pytest.mark.asyncio
    async def test_invoke_update_task(self, agent_team):
        """Test unified tool update action"""
        task = await agent_team.task_manager.add(title="Original Title", content="Original Content")

        tool = TaskManagerTool(agent_team)
        result = await tool.invoke({
            "action": "update_task",
            "task_id": task.task_id,
            "title": "Updated Title",
            "content": "Updated Content"
        })

        assert result.success is True
        assert result.data["title"] == "Updated Title"
        assert result.data["content"] == "Updated Content"

    @pytest.mark.asyncio
    async def test_invoke_cancel_task(self, agent_team):
        """Test unified tool cancel task action"""
        task = await agent_team.task_manager.add(title="Task to Cancel", content="Content")

        tool = TaskManagerTool(agent_team)
        result = await tool.invoke({
            "action": "cancel_task",
            "task_id": task.task_id
        })

        assert result.success is True
        assert result.data["task_id"] == task.task_id
        assert result.data["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_invoke_cancel_all_tasks(self, agent_team, db):
        """Test unified tool cancel all tasks action"""
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")

        tool = TaskManagerTool(agent_team)
        result = await tool.invoke({
            "action": "cancel_all_tasks"
        })

        assert result.success is True
        assert result.data["cancelled_count"] == 2


class TestViewTaskTool:
    """Test ViewTaskTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = ViewTaskTool(agent_team.task_manager)
        assert tool.card.name == "view_task"
        assert tool.card.id == "ViewTaskTool"

    @pytest.mark.asyncio
    async def test_invoke_get_single_task(self, agent_team):
        """Test viewing a single task by task_id"""
        tm = agent_team.task_manager
        task = await tm.add(title="Single Task", content="Content")

        tool = ViewTaskTool(tm)
        result = await tool.invoke({"task_id": task.task_id})

        assert result.success is True
        assert result.data["task_id"] == task.task_id
        assert result.data["title"] == "Single Task"

    @pytest.mark.asyncio
    async def test_invoke_get_single_task_not_found(self, agent_team):
        """Test viewing a non-existent task"""
        tool = ViewTaskTool(agent_team.task_manager)
        result = await tool.invoke({"task_id": "nonexistent"})

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_invoke_list_tasks_by_status(self, agent_team, db):
        """Test listing tasks filtered by status"""
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")
        await db.create_task("task3", "test_team", "Task 3", "Content 3", "completed")

        tool = ViewTaskTool(agent_team.task_manager)
        result = await tool.invoke({"status": "pending"})

        assert result.success is True
        assert result.data["count"] == 1
        assert result.data["tasks"][0]["title"] == "Task 1"

    @pytest.mark.asyncio
    async def test_invoke_get_claimable_tasks(self, agent_team, db):
        """Test getting all claimable tasks (default behavior)"""
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")
        await db.create_task("task3", "test_team", "Task 3", "Content 3", "completed")

        tool = ViewTaskTool(agent_team.task_manager)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["count"] == 1
        assert result.data["tasks"][0]["task_id"] == "task1"


class TestClaimTaskTool:
    """Test ClaimTaskTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = ClaimTaskTool(agent_team.task_manager)
        assert tool.card.name == "claim_task"
        assert tool.card.id == "ClaimTaskTool"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team):
        """Test invoking claim task tool successfully"""
        tm = agent_team.task_manager
        task = await tm.add(title="Test Task", content="Test content")
        await agent_team.spawn_member(
            member_id="member1",
            name="member1",
            agent_card=AgentCard()
        )

        tool = ClaimTaskTool(tm)
        result = await tool.invoke({
            "task_id": task.task_id,
            "member_id": "member1"
        })

        assert result.success is True


class TestCompleteTaskTool:
    """Test CompleteTaskTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = CompleteTaskTool(agent_team.task_manager)
        assert tool.card.name == "complete_task"
        assert tool.card.id == "CompleteTaskTool"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team):
        """Test invoking complete task tool successfully"""
        tm = agent_team.task_manager
        task = await tm.add(title="Test Task", content="Test content")
        await agent_team.spawn_member(
            member_id="member1",
            name="member1",
            agent_card=AgentCard()
        )

        await tm.claim(task.task_id, member_id="member1")

        tool = CompleteTaskTool(tm)
        result = await tool.invoke({"task_id": task.task_id})

        assert result.success is True


class TestCancelTaskTool:
    """Test CancelTaskTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = CancelTaskTool(agent_team)
        assert tool.card.name == "cancel_task"
        assert tool.card.id == "CancelTaskTool"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team):
        """Test invoking cancel task tool successfully"""
        tm = agent_team.task_manager
        task = await tm.add(title="Test Task", content="Test content")

        tool = CancelTaskTool(agent_team)
        result = await tool.invoke({"task_id": task.task_id})

        assert result.success is True


class TestGetTaskTool:
    """Test GetTaskTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = GetTaskTool(agent_team.task_manager)
        assert tool.card.name == "get_task"
        assert tool.card.id == "GetTaskTool"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team):
        """Test invoking get task tool successfully"""
        tm = agent_team.task_manager
        task = await tm.add(title="Test Task", content="Test content")

        tool = GetTaskTool(tm)
        result = await tool.invoke({"task_id": task.task_id})

        assert result.success is True
        assert result.data["task_id"] == task.task_id

    @pytest.mark.asyncio
    async def test_invoke_not_found(self, agent_team):
        """Test invoking get task tool for non-existent task"""
        tool = GetTaskTool(agent_team.task_manager)
        result = await tool.invoke({"task_id": "nonexistent"})

        assert result.success is False
        assert result.error == "Task not found"


class TestListTasksTool:
    """Test ListTasksTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = ListTasksTool(agent_team.task_manager)
        assert tool.card.name == "list_tasks"
        assert tool.card.id == "ListTasksTool"

    @pytest.mark.asyncio
    async def test_invoke_all(self, agent_team):
        """Test invoking list tasks tool for all tasks"""
        tm = agent_team.task_manager
        await tm.add(title="Task 1", content="Content 1")
        await tm.add(title="Task 2", content="Content 2")

        tool = ListTasksTool(tm)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["count"] == 2

    @pytest.mark.asyncio
    async def test_invoke_with_status_filter(self, agent_team):
        """Test invoking list tasks tool with status filter"""
        tm = agent_team.task_manager
        await tm.add(title="Task 1", content="Content 1")

        tool = ListTasksTool(tm)
        result = await tool.invoke({"status": "pending"})

        assert result.success is True
        assert result.data["count"] == 1


class TestGetClaimableTasksTool:
    """Test GetClaimableTasksTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = GetClaimableTasksTool(agent_team.task_manager)
        assert tool.card.name == "get_claimable_tasks"
        assert tool.card.id == "GetClaimableTasksTool"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team):
        """Test invoking get claimable tasks tool successfully"""
        tm = agent_team.task_manager
        await tm.add(title="Task 1", content="Content 1")
        await tm.add(title="Task 2", content="Content 2")

        tool = GetClaimableTasksTool(tm)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["count"] == 2


class TestUpdateTaskTool:
    """Test UpdateTaskTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = UpdateTaskTool(agent_team.task_manager)
        assert tool.card.name == "update_task"
        assert tool.card.id == "UpdateTaskTool"

    @pytest.mark.asyncio
    async def test_invoke_title_only(self, agent_team):
        """Test invoking update task tool with title only"""
        tm = agent_team.task_manager
        task = await tm.add(title="Original Title", content="Content")

        tool = UpdateTaskTool(tm)
        result = await tool.invoke({
            "task_id": task.task_id,
            "title": "Updated Title"
        })

        assert result.success is True

    @pytest.mark.asyncio
    async def test_invoke_both(self, agent_team):
        """Test invoking update task tool with both title and content"""
        tm = agent_team.task_manager
        task = await tm.add(title="Original Title", content="Original Content")

        tool = UpdateTaskTool(tm)
        result = await tool.invoke({
            "task_id": task.task_id,
            "title": "Updated Title",
            "content": "Updated Content"
        })

        assert result.success is True


class TestSendMessageTool:
    """Test SendMessageTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = SendMessageTool(agent_team.messaging)
        assert tool.card.name == "send_message"
        assert tool.card.id == "SendMessageTool"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team):
        """Test invoking send message tool successfully"""
        tool = SendMessageTool(agent_team.messaging)
        result = await tool.invoke({
            "content": "Hello",
            "from_member": "member1",
            "to_member": "member2"
        })

        assert result.success is True
        assert result.data is not None
        assert "message_id" in result.data


class TestBroadcastMessageTool:
    """Test BroadcastMessageTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = BroadcastMessageTool(agent_team.messaging)
        assert tool.card.name == "broadcast_message"
        assert tool.card.id == "BroadcastMessageTool"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team):
        """Test invoking broadcast message tool successfully"""
        tool = BroadcastMessageTool(agent_team.messaging)
        result = await tool.invoke({
            "content": "Hello everyone",
            "from_member": "member1"
        })

        assert result.success is True
        assert "message_id" in result.data


class TestGetMessagesTool:
    """Test GetMessagesTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = GetMessagesTool(agent_team.messaging)
        assert tool.card.name == "get_messages"
        assert tool.card.id == "GetMessagesTool"

    @pytest.mark.asyncio
    async def test_invoke_empty(self, agent_team):
        """Test invoking get messages tool when empty"""
        tool = GetMessagesTool(agent_team.messaging)
        result = await tool.invoke({"to_member": "member1"})

        assert result.success is True
        assert result.data["count"] == 0


class TestMarkMessageReadTool:
    """Test MarkMessageReadTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = MarkMessageReadTool(agent_team.messaging)
        assert tool.card.name == "mark_message_read"
        assert tool.card.id == "MarkMessageReadTool"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, sample_agent_card):
        """Test invoking mark message read tool successfully"""
        await agent_team.spawn_member(member_id="member1", name="member1", agent_card=sample_agent_card)
        await agent_team.spawn_member(member_id="member2", name="member2", agent_card=sample_agent_card)

        mm = agent_team.messaging
        msg_id = await mm.send_message(
            content="Hello",
            from_member="member1",
            to_member="member2"
        )

        tool = MarkMessageReadTool(mm)
        result = await tool.invoke({
            "message_id": msg_id,
            "member_id": "member2"
        })

        assert result.success is True


class TestCancelAllTasksTool:
    """Test CancelAllTasksTool"""

    def test_initialization(self, agent_team):
        """Test tool initialization"""
        tool = CancelAllTasksTool(agent_team)
        assert tool.card.name == "cancel_all_tasks"
        assert tool.card.id == "CancelAllTasksTool"

    @pytest.mark.asyncio
    async def test_invoke_success(self, agent_team, db):
        """Test invoking cancel all tasks tool successfully"""
        # Create multiple tasks
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "pending")
        await db.create_task("task3", "test_team", "Task 3", "Content 3", "pending")

        tool = CancelAllTasksTool(agent_team)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["cancelled_count"] == 3

    @pytest.mark.asyncio
    async def test_invoke_with_mixed_status(self, agent_team, db):
        """Test invoking cancel all tasks with mixed task statuses"""
        # Create tasks with different statuses
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")
        await db.create_task("task3", "test_team", "Task 3", "Content 3", "cancelled")
        await db.create_task("task4", "test_team", "Task 4", "Content 4", "completed")

        tool = CancelAllTasksTool(agent_team)
        result = await tool.invoke({})

        assert result.success is True
        # Only pending and claimed tasks should be cancelled (2 tasks)
        assert result.data["cancelled_count"] == 2
