# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team_tools module"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.status import (
    MemberStatus,
)
from openjiuwen.agent_teams.spawn.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.locales import Translator, make_translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_tools import (
    ApprovePlanTool,
    ApproveToolCallTool,
    BuildTeamTool,
    ClaimTaskTool,
    CleanTeamTool,
    ListMembersTool,
    MappedToolOutput,
    SendMessageTool,
    ShutdownMemberTool,
    SpawnMemberTool,
    TaskCreateTool,
    UpdateTaskTool,
    ViewTaskToolV2,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.base_tool import ToolOutput


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
        team_name=team_id,
        display_name="Test Team",
        leader_member_name="leader1"
    )
    return TeamBackend(
        team_name=team_id,
        member_name="leader1",
        is_leader=True,
        db=db,
        messager=message_bus,
    )


@pytest_asyncio.fixture
async def agent_team_without_team(db, message_bus):
    """Provide AgentTeam instance without pre-created team (for BuildTeamTool tests)"""
    return TeamBackend(
        team_name="test_team",
        member_name="leader1",
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

    @pytest.mark.level0
    def test_initialization(self, agent_team_without_team, t):
        """Test tool initialization"""
        tool = BuildTeamTool(agent_team_without_team, t)
        assert tool.card.name == "build_team"
        assert tool.card.id == "team.build_team"
        assert tool.team == agent_team_without_team
        assert tool.db == agent_team_without_team.db
        assert tool.messager == agent_team_without_team.messager

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_success(self, agent_team_without_team, t, db):
        """Test invoking build team tool successfully"""
        tool = BuildTeamTool(agent_team_without_team, t)
        result = await tool.invoke({
            "display_name": "My Team",
            "team_desc": "Test team description",
            "leader_display_name": "Lead",
            "leader_desc": "Project manager",
        })

        assert result.success is True
        assert result.error is None
        # Verify team was created in database
        team_info = await db.get_team("test_team")
        assert team_info.display_name == "My Team"
        assert team_info.desc == "Test team description"
        # Verify leader was registered as a member
        leader = await db.get_member("leader1", "test_team")
        assert leader is not None
        assert leader.display_name == "Lead"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_with_minimal_args(self, agent_team_without_team, t, db):
        """Test invoking build team tool with minimal arguments"""
        tool = BuildTeamTool(agent_team_without_team, t)
        result = await tool.invoke({
            "display_name": "Minimal Team",
            "team_desc": "A minimal team",
            "leader_display_name": "Lead",
            "leader_desc": "PM",
        })

        assert result.success is True
        team_info = await db.get_team("test_team")
        assert team_info.display_name == "Minimal Team"
        assert team_info.desc == "A minimal team"


class TestCleanTeamTool:
    """Test CleanTeamTool"""

    @pytest.mark.level0
    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = CleanTeamTool(agent_team, t)
        assert tool.card.name == "clean_team"
        assert tool.card.id == "team.clean_team"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_success(self, agent_team, t, sample_agent_card, db):
        """Test invoking clean team tool successfully"""
        await agent_team.spawn_member(
            member_name="member1",
            display_name="Member One",
            agent_card=sample_agent_card
        )
        # Shutdown member
        await db.update_member_status("member1", "test_team", MemberStatus.SHUTDOWN_REQUESTED.value)
        await db.update_member_status("member1", "test_team", MemberStatus.SHUTDOWN.value)

        tool = CleanTeamTool(agent_team, t)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["team_name"] == agent_team.team_name

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_fails_when_members_not_shutdown(self, agent_team, t, sample_agent_card):
        """Test invoking clean team tool fails when members not shutdown"""
        await agent_team.spawn_member(
            member_name="member1",
            display_name="Member One",
            agent_card=sample_agent_card
        )

        tool = CleanTeamTool(agent_team, t)
        result = await tool.invoke({})

        assert result.success is False
        assert "shutdown_member" in result.error


# ========== Member Management Tools ==========


class TestSpawnMemberTool:
    """Test SpawnMemberTool"""

    @pytest.mark.level0
    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = SpawnMemberTool(agent_team, t)
        assert tool.card.name == "spawn_member"
        assert tool.card.id == "team.spawn_member"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_success(self, agent_team, t):
        """Test invoking spawn member tool successfully"""
        tool = SpawnMemberTool(agent_team, t)
        result = await tool.invoke({
            "member_name": "member1",
            "display_name": "Member One",
            "desc": "Test member",
            "prompt": "Member prompt"
        })

        assert result.success is True
        assert result.error is None


class TestShutdownMemberTool:
    """Test ShutdownMemberTool"""

    @pytest.mark.level0
    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = ShutdownMemberTool(agent_team, t)
        assert tool.card.name == "shutdown_member"
        assert tool.card.id == "team.shutdown_member"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_success(self, agent_team, t, sample_agent_card, db):
        """Test invoking shutdown member tool successfully"""
        await agent_team.spawn_member(
            member_name="member1",
            display_name="Member One",
            agent_card=sample_agent_card,
            status=MemberStatus.READY,
        )

        tool = ShutdownMemberTool(agent_team, t)
        result = await tool.invoke({
            "member_name": "member1",
            "force": False
        })

        assert result.success is True
        assert result.error is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_with_force(self, agent_team, t, sample_agent_card, db):
        """Test invoking shutdown member tool with force option"""
        await agent_team.spawn_member(
            member_name="member1",
            display_name="Member One",
            agent_card=sample_agent_card,
            status=MemberStatus.READY,
        )

        tool = ShutdownMemberTool(agent_team, t)
        result = await tool.invoke({
            "member_name": "member1",
            "force": True
        })

        assert result.success is True

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_member_not_found(self, agent_team, t):
        """Test invoking shutdown member tool for non-existent member"""
        tool = ShutdownMemberTool(agent_team, t)
        result = await tool.invoke({"member_name": "nonexistent"})

        assert result.success is False
        assert result.error is not None


class TestApprovePlanTool:
    """Test ApprovePlanTool"""

    @pytest.mark.level0
    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = ApprovePlanTool(agent_team, t)
        assert tool.card.name == "approve_plan"
        assert tool.card.id == "team.approve_plan"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_approve(self, agent_team, t, sample_agent_card):
        """Test invoking approve plan tool to approve"""
        await agent_team.spawn_member(
            member_name="member1",
            display_name="Member One",
            agent_card=sample_agent_card
        )

        tool = ApprovePlanTool(agent_team, t)
        result = await tool.invoke({
            "member_name": "member1",
            "approved": True,
            "feedback": "Great plan!"
        })

        assert result.success is True
        assert result.error is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_reject(self, agent_team, t, sample_agent_card):
        """Test invoking approve plan tool to reject"""
        await agent_team.spawn_member(
            member_name="member1",
            display_name="Member One",
            agent_card=sample_agent_card
        )

        tool = ApprovePlanTool(agent_team, t)
        result = await tool.invoke({
            "member_name": "member1",
            "approved": False,
            "feedback": "Please revise"
        })

        assert result.success is True

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_member_not_found(self, agent_team, t):
        """Test invoking approve plan tool for non-existent member"""
        tool = ApprovePlanTool(agent_team, t)
        result = await tool.invoke({
            "member_name": "nonexistent",
            "approved": True
        })

        assert result.success is False


class TestApproveToolCallTool:
    """Test ApproveToolCallTool."""

    @pytest.mark.level0
    def test_initialization(self, agent_team, t):
        tool = ApproveToolCallTool(agent_team, t)
        assert tool.card.name == "approve_tool"
        assert tool.card.id == "team.approve_tool"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_approve(self, agent_team, t, sample_agent_card):
        await agent_team.spawn_member(
            member_name="member1",
            display_name="Member One",
            agent_card=sample_agent_card,
        )

        tool = ApproveToolCallTool(agent_team, t)
        result = await tool.invoke({
            "member_name": "member1",
            "tool_call_id": "call-1",
            "approved": True,
            "feedback": "approved",
            "auto_confirm": True,
        })

        assert result.success is True
        assert result.error is None


class TestListMembersTool:
    """Test ListMembersTool"""

    @pytest.mark.level0
    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = ListMembersTool(agent_team, t)
        assert tool.card.name == "list_members"
        assert tool.card.id == "team.list_members"
        assert tool.team == agent_team

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_empty(self, agent_team, t):
        """Test invoking list members tool when empty"""
        tool = ListMembersTool(agent_team, t)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["count"] == 0
        assert result.data["members"] == []

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_with_members(self, agent_team, t, sample_agent_card):
        """Test invoking list members tool with members"""
        await agent_team.spawn_member(
            member_name="member1",
            display_name="Member One",
            agent_card=sample_agent_card
        )
        await agent_team.spawn_member(
            member_name="member2",
            display_name="Member Two",
            agent_card=sample_agent_card
        )

        tool = ListMembersTool(agent_team, t)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["count"] == 2
        member_ids = [m["member_name"] for m in result.data["members"]]
        assert "member1" in member_ids
        assert "member2" in member_ids


# ========== Task Management Tools (V2) ==========


class TestTaskCreateTool:
    """Test TaskCreateTool (create tasks)"""

    @pytest.mark.level0
    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = TaskCreateTool(agent_team, t)
        assert tool.card.name == "create_task"
        assert tool.card.id == "team.create_task"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_create_single_task(self, agent_team, t):
        """Test creating a single task"""
        tool = TaskCreateTool(agent_team, t)
        result = await tool.invoke({
            "tasks": [{"title": "Task 1", "content": "Content 1"}]
        })

        assert result.success is True
        assert result.data["title"] == "Task 1"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_create_batch_tasks(self, agent_team, t):
        """Test batch task creation"""
        tool = TaskCreateTool(agent_team, t)
        result = await tool.invoke({
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
    @pytest.mark.level1
    async def test_create_empty_tasks(self, agent_team, t):
        """Test with empty tasks list"""
        tool = TaskCreateTool(agent_team, t)
        result = await tool.invoke({"tasks": []})

        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_create_task_with_depended_by(self, agent_team, t):
        """Test creating a task with reverse dependencies (depended_by)"""
        # Create a base task first
        base = await agent_team.task_manager.add(title="Base Task", content="Base content")

        tool = TaskCreateTool(agent_team, t)
        result = await tool.invoke({
            "tasks": [{
                "title": "Priority Task",
                "content": "Priority content",
                "depended_by": [base.task_id],
            }]
        })

        assert result.success is True
        assert result.data["title"] == "Priority Task"


class TestUpdateTaskTool:
    """Test UpdateTaskTool (leader: content update + cancel)"""

    @pytest.mark.level1
    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = UpdateTaskTool(agent_team, t)
        assert tool.card.name == "update_task"
        assert tool.card.id == "team.update_task"
        props = tool.card.input_params["properties"]
        assert "task_id" in props
        assert "status" in props
        assert "title" in props
        assert "content" in props

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_update_content(self, agent_team, t):
        """Test updating task content"""
        task = await agent_team.task_manager.add(title="Original", content="Original Content")

        tool = UpdateTaskTool(agent_team, t)
        result = await tool.invoke({
            "task_id": task.task_id,
            "title": "Updated Title",
            "content": "Updated Content",
        })

        assert result.success is True
        assert result.data["status"] == "updated"
        assert "title" in result.data["updated_fields"]
        assert "content" in result.data["updated_fields"]

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cancel_task(self, agent_team, t):
        """Test cancelling a task"""
        task = await agent_team.task_manager.add(title="Task to Cancel", content="Content")

        tool = UpdateTaskTool(agent_team, t)
        result = await tool.invoke({
            "task_id": task.task_id,
            "status": "cancelled",
        })

        assert result.success is True
        assert result.data["task_id"] == task.task_id
        assert result.data["status"] == "cancelled"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cancel_all_tasks(self, agent_team, t, db):
        """Test cancel all tasks via task_id='*'"""
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")

        tool = UpdateTaskTool(agent_team, t)
        result = await tool.invoke({"task_id": "*", "status": "cancelled"})

        assert result.success is True
        assert result.data["cancelled_count"] == 2

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_assign_task(self, agent_team, t, sample_agent_card, db):
        """Test assigning a task to a member"""
        await db.create_member(
            member_name="dev-1",
            team_name="test_team",
            display_name="Dev",
            agent_card=sample_agent_card.model_dump_json(),
            status=MemberStatus.READY,
        )
        task = await agent_team.task_manager.add(title="Task", content="Content")

        tool = UpdateTaskTool(agent_team, t)
        result = await tool.invoke({
            "task_id": task.task_id,
            "assignee": "dev-1",
        })

        assert result.success is True
        assert "assignee" in result.data["updated_fields"]

        # Verify assignee is set in DB
        updated = await agent_team.task_manager.get(task.task_id)
        assert updated.assignee == "dev-1"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_assign_reassigns_to_new_member(self, agent_team, t, sample_agent_card, db):
        """Reassigning a claimed task cancels the old owner and binds the new one."""
        for member_name in ("dev-1", "dev-2"):
            await db.create_member(
                member_name=member_name,
                team_name="test_team",
                display_name=member_name,
                agent_card=sample_agent_card.model_dump_json(),
                status=MemberStatus.READY,
            )
        task = await agent_team.task_manager.add(title="Task", content="Content")
        await db.assign_task(task.task_id, "dev-1")

        tool = UpdateTaskTool(agent_team, t)
        result = await tool.invoke({
            "task_id": task.task_id,
            "assignee": "dev-2",
        })

        assert result.success is True
        assert "assignee" in result.data["updated_fields"]
        updated = await agent_team.task_manager.get(task.task_id)
        assert updated.assignee == "dev-2"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_add_dependencies(self, agent_team, t):
        """Test adding dependencies to a task"""
        upstream = await agent_team.task_manager.add(title="Upstream", content="First")
        downstream = await agent_team.task_manager.add(title="Downstream", content="Second")

        tool = UpdateTaskTool(agent_team, t)
        result = await tool.invoke({
            "task_id": downstream.task_id,
            "add_blocked_by": [upstream.task_id],
        })

        assert result.success is True
        assert "blocked_by" in result.data["updated_fields"]

        # Verify task is now blocked
        updated = await agent_team.task_manager.get(downstream.task_id)
        assert updated.status == "blocked"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_no_update_specified(self, agent_team, t):
        """Test with no update fields"""
        task = await agent_team.task_manager.add(title="Task", content="Content")

        tool = UpdateTaskTool(agent_team, t)
        result = await tool.invoke({"task_id": task.task_id})

        assert result.success is False
        assert "No update specified" in result.error

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_add_blocked_by_rejects_cycle(self, agent_team, t):
        """add_blocked_by must reject edges that would close a cycle.

        Regression guard: this path used to skip cycle detection entirely
        because it routed through a primitive that was missing the check.
        """
        a = await agent_team.task_manager.add(title="A", content="c")
        b = await agent_team.task_manager.add(title="B", content="c", dependencies=[a.task_id])

        tool = UpdateTaskTool(agent_team, t)
        # b -> a already; trying to make a -> b would close A -> B -> A.
        result = await tool.invoke({
            "task_id": a.task_id,
            "add_blocked_by": [b.task_id],
        })

        assert result.success is False
        assert "Circular dependency" in result.error

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_cancel_unblocks_downstream(self, agent_team, t):
        """Cancelling a task via update_task unblocks anything that
        depended on it. Mirrors the bug-fix coverage in test_database
        but exercises the full tool boundary."""
        upstream = await agent_team.task_manager.add(title="Up", content="c")
        downstream = await agent_team.task_manager.add(
            title="Down", content="c", dependencies=[upstream.task_id]
        )
        assert downstream.status == "blocked"

        tool = UpdateTaskTool(agent_team, t)
        result = await tool.invoke({"task_id": upstream.task_id, "status": "cancelled"})
        assert result.success is True

        refreshed = await agent_team.task_manager.get(downstream.task_id)
        assert refreshed.status == "pending"


class TestViewTaskToolV2:
    """Test ViewTaskToolV2 (unified task viewing)"""

    @pytest.mark.level1
    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = ViewTaskToolV2(agent_team.task_manager, t)
        assert tool.card.name == "view_task"
        assert tool.card.id == "team.view_task"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_get_single_task(self, agent_team, t):
        """Test get action returns detail with blocked_by and blocks"""
        tm = agent_team.task_manager
        task = await tm.add(title="Single Task", content="Content")

        tool = ViewTaskToolV2(tm, t)
        result = await tool.invoke({"action": "get", "task_id": task.task_id})

        assert result.success is True
        assert result.data["task_id"] == task.task_id
        assert result.data["title"] == "Single Task"
        assert result.data["content"] == "Content"
        assert result.data["blocked_by"] == []
        assert result.data["blocks"] == []
        assert "team_id" not in result.data

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_get_with_dependencies(self, agent_team, t):
        """Test get action returns correct blocked_by and blocks"""
        tm = agent_team.task_manager
        upstream = await tm.add(title="Upstream", content="Do first")
        downstream = await tm.add(
            title="Downstream", content="Do second",
            dependencies=[upstream.task_id],
        )

        tool = ViewTaskToolV2(tm, t)

        # downstream is blocked by upstream
        result = await tool.invoke({"action": "get", "task_id": downstream.task_id})
        assert result.success is True
        assert upstream.task_id in result.data["blocked_by"]

        # upstream blocks downstream
        result = await tool.invoke({"action": "get", "task_id": upstream.task_id})
        assert result.success is True
        assert downstream.task_id in result.data["blocks"]

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_get_task_not_found(self, agent_team, t):
        """Test get action for a non-existent task"""
        tool = ViewTaskToolV2(agent_team.task_manager, t)
        result = await tool.invoke({"action": "get", "task_id": "nonexistent"})

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_get_without_task_id(self, agent_team, t):
        """Test get action without task_id"""
        tool = ViewTaskToolV2(agent_team.task_manager, t)
        result = await tool.invoke({"action": "get"})

        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_list_tasks_by_status(self, agent_team, t, db):
        """Test list action returns summary with blocked_by, no content"""
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")
        await db.create_task("task3", "test_team", "Task 3", "Content 3", "completed")

        tool = ViewTaskToolV2(agent_team.task_manager, t)
        result = await tool.invoke({"action": "list", "status": "pending"})

        assert result.success is True
        assert result.data["count"] == 1
        task_summary = result.data["tasks"][0]
        assert task_summary["title"] == "Task 1"
        assert "blocked_by" in task_summary
        assert "content" not in task_summary
        assert "team_id" not in task_summary

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_default_action_is_list(self, agent_team, t, db):
        """Test default action is list (returns all tasks, not just pending)"""
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")
        await db.create_task("task3", "test_team", "Task 3", "Content 3", "completed")

        tool = ViewTaskToolV2(agent_team.task_manager, t)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["count"] == 3

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_claimable(self, agent_team, t, db):
        """Test claimable action returns only pending tasks"""
        await db.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")
        await db.create_task("task3", "test_team", "Task 3", "Content 3", "completed")

        tool = ViewTaskToolV2(agent_team.task_manager, t)
        result = await tool.invoke({"action": "claimable"})

        assert result.success is True
        assert result.data["count"] == 1
        assert result.data["tasks"][0]["task_id"] == "task1"


# ========== Task Execution Tools ==========


class TestClaimTaskTool:
    """Test ClaimTaskTool (member: claim + complete)"""

    @pytest.mark.level1
    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = ClaimTaskTool(agent_team.task_manager, t)
        assert tool.card.name == "claim_task"
        assert tool.card.id == "team.claim_task"
        props = tool.card.input_params["properties"]
        assert "task_id" in props
        assert "status" in props
        assert tool.card.input_params["required"] == ["task_id", "status"]

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_claim_via_status(self, agent_team, t, sample_agent_card, db):
        """Test claiming a task by setting status=claimed"""
        await db.create_member(
            member_name="leader1",
            team_name="test_team",
            display_name="Leader",
            agent_card=sample_agent_card.model_dump_json(),
            status=MemberStatus.READY,
        )
        tm = agent_team.task_manager
        task = await tm.add(title="Test Task", content="Test content")

        tool = ClaimTaskTool(tm, t)
        result = await tool.invoke({"task_id": task.task_id, "status": "claimed"})

        assert result.success is True
        assert "status" in result.data["updated_fields"]
        assert result.data["status_change"]["to"] == "claimed"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_complete_via_status(self, agent_team, t, sample_agent_card, db):
        """Test completing a task by setting status=completed"""
        from openjiuwen.agent_teams.schema.status import MemberMode
        await db.create_member(
            member_name="leader1",
            team_name="test_team",
            display_name="Leader",
            agent_card=sample_agent_card.model_dump_json(),
            status=MemberStatus.READY,
            mode=MemberMode.BUILD_MODE.value,
        )
        tm = agent_team.task_manager
        task = await tm.add(title="Test Task", content="Test content")
        await tm.claim(task.task_id)

        tool = ClaimTaskTool(tm, t)
        result = await tool.invoke({"task_id": task.task_id, "status": "completed"})

        assert result.success is True
        assert "status" in result.data["updated_fields"]
        assert result.data["status_change"]["to"] == "completed"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_task_not_found(self, agent_team, t):
        """Test updating a non-existent task"""
        tool = ClaimTaskTool(agent_team.task_manager, t)
        result = await tool.invoke({"task_id": "nonexistent", "status": "claimed"})

        assert result.success is False
        assert result.error == "Task not found"



# ========== Result Mapping ==========


class TestMappedToolOutput:
    """Test MappedToolOutput and map_result integration"""

    @pytest.mark.level1
    def test_str_returns_mapped_content(self):
        """MappedToolOutput.__str__ returns mapped content, not Pydantic repr"""
        output = MappedToolOutput.from_output(
            ToolOutput(success=True, data={"key": "value"}),
            mapped_content="Custom text for LLM",
        )
        assert str(output) == "Custom text for LLM"
        # underlying data still accessible
        assert output.success is True
        assert output.data == {"key": "value"}

    @pytest.mark.level1
    def test_claim_task_map_result_completed_guidance(self, agent_team, t):
        """ClaimTaskTool.map_result injects behavior guidance on completion"""
        tool = ClaimTaskTool(agent_team.task_manager, t)
        output = ToolOutput(
            success=True,
            data={
                "task_id": "t1",
                "updated_fields": ["status"],
                "status_change": {"from": "claimed", "to": "completed"},
            },
        )
        result = tool.map_result(output)
        assert "Task #t1 claimed → completed" in result
        assert "view_task" in result

    @pytest.mark.level1
    def test_claim_task_map_result_claimed_no_guidance(self, agent_team, t):
        """ClaimTaskTool.map_result does NOT inject guidance on claim"""
        tool = ClaimTaskTool(agent_team.task_manager, t)
        output = ToolOutput(
            success=True,
            data={
                "task_id": "t1",
                "updated_fields": ["status"],
                "status_change": {"from": "pending", "to": "claimed"},
            },
        )
        result = tool.map_result(output)
        assert "Task #t1 pending → claimed" in result
        assert "view_task" not in result

    @pytest.mark.level1
    def test_view_task_map_result_list(self, agent_team, t):
        """ViewTaskToolV2.map_result formats list view as compact lines"""
        tool = ViewTaskToolV2(agent_team.task_manager, t)
        output = ToolOutput(
            success=True,
            data={
                "tasks": [
                    {"task_id": "t1", "title": "Fix bug", "status": "pending", "assignee": None, "blocked_by": []},
                    {"task_id": "t2", "title": "Add test", "status": "claimed", "assignee": "dev-1", "blocked_by": ["t1"]},
                ],
                "count": 2,
            },
        )
        result = tool.map_result(output)
        assert "#t1 [pending] Fix bug" in result
        assert "(dev-1)" in result
        assert "[blocked by #t1]" in result

    @pytest.mark.level1
    def test_view_task_map_result_get(self, agent_team, t):
        """ViewTaskToolV2.map_result formats detail view with dependencies"""
        tool = ViewTaskToolV2(agent_team.task_manager, t)
        output = ToolOutput(
            success=True,
            data={
                "task_id": "t1",
                "title": "Fix bug",
                "content": "Fix the login bug",
                "status": "claimed",
                "assignee": "dev-1",
                "blocked_by": [],
                "blocks": ["t2", "t3"],
            },
        )
        result = tool.map_result(output)
        assert "Task #t1: Fix bug" in result
        assert "Content: Fix the login bug" in result
        assert "Blocks: #t2, #t3" in result

    @pytest.mark.level1
    def test_send_message_map_result(self, agent_team, t):
        """SendMessageTool.map_result formats routing summary"""
        tool = SendMessageTool(agent_team.message_manager, t)
        output = ToolOutput(
            success=True,
            data={"type": "message", "from": "leader", "to": "dev-1", "summary": None},
        )
        assert tool.map_result(output) == "Message sent from leader to dev-1"

    @pytest.mark.level1
    def test_send_message_map_result_broadcast(self, agent_team, t):
        """SendMessageTool.map_result formats broadcast summary"""
        tool = SendMessageTool(agent_team.message_manager, t)
        output = ToolOutput(
            success=True,
            data={"type": "broadcast", "from": "leader", "summary": None},
        )
        assert tool.map_result(output) == "Broadcast sent from leader"

    @pytest.mark.level1
    def test_default_map_result_json(self, agent_team, t):
        """TeamTool default map_result returns JSON for data"""
        tool = ListMembersTool(agent_team, t)
        output = ToolOutput(
            success=True,
            data={"members": [{"member_name": "m1", "display_name": "Dev", "status": "ready"}], "count": 1},
        )
        # ListMembersTool overrides map_result, so test directly
        result = tool.map_result(output)
        assert "member_name=m1 display_name=Dev status=ready" in result


# ========== Messaging Tools ==========


class TestSendMessageTool:
    """Test SendMessageTool (merged send + broadcast)"""

    @pytest.mark.level1
    def test_initialization(self, agent_team, t):
        """Test tool initialization"""
        tool = SendMessageTool(agent_team.message_manager, t, team=agent_team)
        assert tool.card.name == "send_message"
        assert tool.card.id == "team.send_message"
        props = tool.card.input_params["properties"]
        assert "to" in props
        assert "content" in props
        assert "summary" in props

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_point_to_point(self, agent_team, t):
        """Test point-to-point message"""
        tool = SendMessageTool(agent_team.message_manager, t)
        result = await tool.invoke({"to": "member2", "content": "Hello"})

        assert result.success is True
        assert result.data["type"] == "message"
        assert result.data["to"] == "member2"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_broadcast(self, agent_team, t):
        """Test broadcast message with to='*'"""
        tool = SendMessageTool(agent_team.message_manager, t)
        result = await tool.invoke({"to": "*", "content": "Hello everyone"})

        assert result.success is True
        assert result.data["type"] == "broadcast"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_with_summary(self, agent_team, t):
        """Test message with summary field"""
        tool = SendMessageTool(agent_team.message_manager, t)
        result = await tool.invoke({
            "to": "member2",
            "content": "Please claim task-1",
            "summary": "assign task-1",
        })

        assert result.success is True
        assert result.data["summary"] == "assign task-1"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_empty_to(self, agent_team, t):
        """Test validation: empty 'to' field"""
        tool = SendMessageTool(agent_team.message_manager, t)
        result = await tool.invoke({"to": "", "content": "Hello"})

        assert result.success is False
        assert "'to'" in result.error

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_empty_content(self, agent_team, t):
        """Test validation: empty 'content' field"""
        tool = SendMessageTool(agent_team.message_manager, t)
        result = await tool.invoke({"to": "member2", "content": ""})

        assert result.success is False
        assert "'content'" in result.error

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_member_not_found(self, agent_team, t):
        """Test validation: target member does not exist"""
        tool = SendMessageTool(agent_team.message_manager, t, team=agent_team)
        result = await tool.invoke({"to": "nonexistent", "content": "Hello"})

        assert result.success is False
        assert "not found" in result.error


# ========== Skipped Tests (tools temporarily removed) ==========


@pytest.mark.skip(reason="tool removed, functionality in TaskCreateTool")
class TestAddTaskTool:
    """Test AddTaskTool (removed)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool removed, functionality in TaskCreateTool")
class TestAddBatchTasksTool:
    """Test AddBatchTasksTool (removed)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool removed, functionality in TaskCreateTool")
class TestAddTaskWithPriorityTool:
    """Test AddTaskWithPriorityTool (removed)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool removed, functionality in TaskCreateTool")
class TestAddTaskAsTopPriorityTool:
    """Test AddTaskAsTopPriorityTool (removed)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool removed, functionality in UpdateTaskTool")
class TestCancelTaskTool:
    """Test CancelTaskTool (removed)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool removed, functionality in UpdateTaskTool")
class TestCancelAllTasksTool:
    """Test CancelAllTasksTool (removed)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into ViewTaskToolV2.get")
class TestGetTaskTool:
    """Test GetTaskTool (removed - merged into ViewTaskToolV2)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into ViewTaskToolV2.list")
class TestListTasksTool:
    """Test ListTasksTool (removed - merged into ViewTaskToolV2)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed, functionality merged into ViewTaskToolV2.claimable")
class TestGetClaimableTasksTool:
    """Test GetClaimableTasksTool (removed - merged into ViewTaskToolV2)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool removed, functionality in UpdateTaskTool")
class TestUpdateTaskToolLegacy:
    """Test UpdateTaskTool legacy (removed)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed")
class TestGetTeamInfoTool:
    """Test GetTeamInfoTool (removed)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed")
class TestGetMemberTool:
    """Test GetMemberTool (removed)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed")
class TestGetMessagesTool:
    """Test GetMessagesTool (removed)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


@pytest.mark.skip(reason="tool temporarily removed")
class TestMarkMessageReadTool:
    """Test MarkMessageReadTool (removed)"""

    @pytest.mark.level1
    def test_placeholder(self):
        pass


class TestTranslator:
    """Test the i18n translator closure returned by make_translator()."""

    @pytest.mark.level1
    def test_desc_from_markdown_is_returned(self):
        """When a <lang>/<tool>.md exists, it is used for _desc."""
        translate = make_translator("cn")

        desc = translate("build_team")
        assert "build_team" in desc or "组建" in desc

    @pytest.mark.level1
    def test_param_keys_return_strings_dict_entries(self):
        """Non-_desc keys are resolved from the in-module STRINGS dict."""
        translate = make_translator("cn")

        value = translate("send_message", "summary")
        assert value == "5-10 词摘要，用于消息预览和日志"

    @pytest.mark.level1
    def test_missing_desc_raises_file_not_found(self):
        """Unknown tool: no markdown and no STRINGS entry → FileNotFoundError.

        Protects against silent KeyError if a descs/<lang>/<tool>.md
        is deleted or mis-named.
        """
        translate = make_translator("cn")

        with pytest.raises(FileNotFoundError) as excinfo:
            translate("nonexistent_tool_for_translator_test")

        msg = str(excinfo.value)
        assert "nonexistent_tool_for_translator_test" in msg
        assert "cn" in msg
