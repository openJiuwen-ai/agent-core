# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team_tools module"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.status import (
    MemberMode,
    MemberStatus,
    TaskStatus,
)
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.locales import Translator, make_translator
from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec
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
    await db.team.create_team(team_name=team_id, display_name="Test Team", leader_member_name="leader1")
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
    return AgentCard(name="TestAgent", description="A test agent", version="1.0.0")


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
        result = await tool.invoke(
            {
                "display_name": "My Team",
                "team_desc": "Test team description",
                "leader_display_name": "Lead",
                "leader_desc": "Project manager",
            }
        )

        assert result.success is True
        assert result.error is None
        # Verify team was created in database
        team_info = await db.team.get_team("test_team")
        assert team_info.display_name == "My Team"
        assert team_info.desc == "Test team description"
        # Verify leader was registered as a member
        leader = await db.member.get_member("leader1", "test_team")
        assert leader is not None
        assert leader.display_name == "Lead"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_with_minimal_args(self, agent_team_without_team, t, db):
        """Test invoking build team tool with minimal arguments"""
        tool = BuildTeamTool(agent_team_without_team, t)
        result = await tool.invoke(
            {
                "display_name": "Minimal Team",
                "team_desc": "A minimal team",
                "leader_display_name": "Lead",
                "leader_desc": "PM",
            }
        )

        assert result.success is True
        team_info = await db.team.get_team("test_team")
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
        await agent_team.spawn_member(member_name="member1", display_name="Member One", agent_card=sample_agent_card)
        # Shutdown member
        await db.member.update_member_status("member1", "test_team", MemberStatus.SHUTDOWN_REQUESTED.value)
        await db.member.update_member_status("member1", "test_team", MemberStatus.SHUTDOWN.value)

        tool = CleanTeamTool(agent_team, t)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["team_name"] == agent_team.team_name

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_fails_when_members_not_shutdown(self, agent_team, t, sample_agent_card):
        """Test invoking clean team tool fails when members not shutdown"""
        await agent_team.spawn_member(member_name="member1", display_name="Member One", agent_card=sample_agent_card)

        tool = CleanTeamTool(agent_team, t)
        result = await tool.invoke({})

        assert result.success is False
        assert "shutdown_member" in result.error


class TestCleanTeamLifecycleGate:
    """clean_team is wired only when lifecycle == 'temporary'."""

    @pytest.mark.level0
    def test_temporary_leader_has_clean_team(self, agent_team):
        """Temporary leader keeps the tear-down tool — the leader is the
        only one who can wind a temporary team down."""
        from openjiuwen.agent_teams.tools.team_tools import create_team_tools

        tools = create_team_tools(role="leader", agent_team=agent_team, lifecycle="temporary")
        names = {tool.card.name for tool in tools}
        assert "clean_team" in names

    @pytest.mark.level0
    def test_persistent_leader_drops_clean_team(self, agent_team):
        """Persistent leader must not see clean_team — operator SDK facades
        own tear-down, exposing it mid-round races pool invariants."""
        from openjiuwen.agent_teams.tools.team_tools import create_team_tools

        tools = create_team_tools(role="leader", agent_team=agent_team, lifecycle="persistent")
        names = {tool.card.name for tool in tools}
        assert "clean_team" not in names
        assert "build_team" in names


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
        # role_type is exposed to the LLM with a default of teammate.
        props = tool.card.input_params["properties"]
        assert "role_type" in props
        assert props["role_type"]["enum"] == ["teammate", "human_agent", "bridge_agent", "external_cli"]
        assert props["role_type"]["default"] == "teammate"
        # external_cli members reference a static spec config by name.
        assert "cli_agent" in props

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_success(self, agent_team, t):
        """Test invoking spawn member tool successfully"""
        tool = SpawnMemberTool(agent_team, t)
        result = await tool.invoke(
            {"member_name": "member1", "display_name": "Member One", "desc": "Test member", "prompt": "Member prompt"}
        )

        assert result.success is True
        assert result.error is None
        assert result.data["role_type"] == "teammate"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_role_type_teammate_explicit(self, agent_team, t):
        """Explicit role_type='teammate' is equivalent to omitting it."""
        tool = SpawnMemberTool(agent_team, t)
        result = await tool.invoke(
            {
                "member_name": "member-explicit-tm",
                "display_name": "Member",
                "desc": "Test member",
                "role_type": "teammate",
            }
        )
        assert result.success is True
        assert result.data["role_type"] == "teammate"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_role_type_human_agent_blocked_when_hitt_disabled(self, agent_team, t):
        """HITT-off team rejects role_type='human_agent' at the tool layer."""
        tool = SpawnMemberTool(agent_team, t)
        result = await tool.invoke(
            {
                "member_name": "alice",
                "display_name": "Alice",
                "desc": "Domain expert",
                "role_type": "human_agent",
            }
        )
        assert result.success is False
        assert "HITT capability is disabled" in (result.error or "")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_role_type_external_cli_requires_cli_agent(self, agent_team, t):
        """role_type='external_cli' without cli_agent fails at the tool layer."""
        tool = SpawnMemberTool(agent_team, t)
        result = await tool.invoke(
            {
                "member_name": "cli-1",
                "display_name": "CLI One",
                "desc": "external cli worker",
                "role_type": "external_cli",
            }
        )
        assert result.success is False
        assert "cli_agent" in (result.error or "")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_role_type_external_cli_undeclared_fails(self, agent_team, t):
        """A cli_agent absent from external_cli_agents is rejected (capability ceiling)."""
        tool = SpawnMemberTool(agent_team, t)
        result = await tool.invoke(
            {
                "member_name": "cli-2",
                "display_name": "CLI Two",
                "desc": "external cli worker",
                "role_type": "external_cli",
                "cli_agent": "claude",
            }
        )
        assert result.success is False
        assert "not declared" in (result.error or "")

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_role_type_external_cli_success(self, db, message_bus, t):
        """role_type='external_cli' with a declared cli_agent registers the member."""
        await db.team.create_team(
            team_name="ext_cli_tools_team",
            display_name="Ext",
            leader_member_name="leader1",
        )
        team = TeamBackend(
            team_name="ext_cli_tools_team",
            member_name="leader1",
            is_leader=True,
            db=db,
            messager=message_bus,
            external_cli_agents=[ExternalCliAgentSpec(cli_agent="claude")],
        )
        tool = SpawnMemberTool(team, t)
        result = await tool.invoke(
            {
                "member_name": "claude-1",
                "display_name": "Claude One",
                "desc": "external cli reviewer",
                "role_type": "external_cli",
                "cli_agent": "claude",
            }
        )
        assert result.success is True, result.error
        assert result.data["role_type"] == "external_cli"
        assert result.data["cli_agent"] == "claude"
        assert team.is_external_cli_agent("claude-1")

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_role_type_human_agent_rejects_model_name(self, db, message_bus, t):
        """role_type='human_agent' must not accept model_name."""
        # Build a HITT-capable backend so the gate falls through to the
        # field validation.
        team = TeamBackend(
            team_name="hitt_tools_team",
            member_name="leader1",
            is_leader=True,
            db=db,
            messager=message_bus,
            enable_hitt=True,
        )
        team._enable_hitt = True
        await db.team.create_team(
            team_name="hitt_tools_team",
            display_name="t",
            leader_member_name="leader1",
        )
        tool = SpawnMemberTool(team, t)
        result = await tool.invoke(
            {
                "member_name": "alice",
                "display_name": "Alice",
                "desc": "x",
                "role_type": "human_agent",
                "model_name": "gpt-4",
            }
        )
        assert result.success is False
        assert "does not accept" in (result.error or "")

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_role_type_human_agent_rejects_prompt(self, db, message_bus, t):
        """role_type='human_agent' must not accept prompt."""
        team = TeamBackend(
            team_name="hitt_tools_team_p",
            member_name="leader1",
            is_leader=True,
            db=db,
            messager=message_bus,
            enable_hitt=True,
        )
        team._enable_hitt = True
        await db.team.create_team(
            team_name="hitt_tools_team_p",
            display_name="t",
            leader_member_name="leader1",
        )
        tool = SpawnMemberTool(team, t)
        result = await tool.invoke(
            {
                "member_name": "alice",
                "display_name": "Alice",
                "desc": "x",
                "role_type": "human_agent",
                "prompt": "be nice",
            }
        )
        assert result.success is False
        assert "does not accept" in (result.error or "")

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_invalid_role_type_rejected(self, agent_team, t):
        tool = SpawnMemberTool(agent_team, t)
        result = await tool.invoke(
            {
                "member_name": "x",
                "display_name": "x",
                "desc": "x",
                "role_type": "admin",
            }
        )
        assert result.success is False
        assert "Invalid role_type" in (result.error or "")

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_role_type_human_agent_success(self, db, message_bus, t):
        """Happy path: HITT engaged, no forbidden fields → human_agent gets spawned."""
        team = TeamBackend(
            team_name="hitt_tools_team_ok",
            member_name="leader1",
            is_leader=True,
            db=db,
            messager=message_bus,
            enable_hitt=True,
        )
        team._enable_hitt = True
        await db.team.create_team(
            team_name="hitt_tools_team_ok",
            display_name="t",
            leader_member_name="leader1",
        )
        tool = SpawnMemberTool(team, t)
        result = await tool.invoke(
            {
                "member_name": "alice",
                "display_name": "Alice",
                "desc": "Domain expert",
                "role_type": "human_agent",
            }
        )
        assert result.success is True, result.error
        assert result.data["role_type"] == "human_agent"
        assert await team.is_human_agent("alice") is True

    @pytest.mark.asyncio
    @pytest.mark.level0
    @pytest.mark.parametrize(
        "bad_name",
        [
            "后端开发1",
            "Member1",
            "backend_dev_1",
            "backend dev",
            "backend.dev",
            "1backend",
            "-backend",
            "",
        ],
    )
    async def test_invoke_rejects_non_portable_member_name(self, agent_team, t, bad_name):
        """member_name must follow DNS-label kebab-case (a-z + 0-9 + hyphen, leading letter)."""
        tool = SpawnMemberTool(agent_team, t)
        result = await tool.invoke(
            {
                "member_name": bad_name,
                "display_name": "x",
                "desc": "x",
            }
        )
        assert result.success is False
        assert "Invalid member_name" in (result.error or "")

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_accepts_kebab_case_member_name(self, agent_team, t):
        """Lowercase kebab-case names with a leading letter pass validation."""
        tool = SpawnMemberTool(agent_team, t)
        result = await tool.invoke(
            {
                "member_name": "backend-dev-1",
                "display_name": "Backend Dev",
                "desc": "ok",
            }
        )
        assert result.success is True, result.error
        assert result.data["member_name"] == "backend-dev-1"


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
        result = await tool.invoke({"member_name": "member1", "force": False})

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
        result = await tool.invoke({"member_name": "member1", "force": True})

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

    @staticmethod
    async def _submit_member_plan(agent_team, member_name: str = "member1"):
        task = await agent_team.task_manager.add(title="Plan task", content="Do work")
        assert task.ok
        assign_result = await agent_team.task_manager.assign(task.task_id, member_name)
        assert assign_result.ok

        member_task_manager = TeamTaskManager(
            team_name=agent_team.team_name,
            member_name=member_name,
            db=agent_team.db,
            messager=agent_team.messager,
            plans_dir=agent_team.task_manager.plans_dir,
            team_plan_id=agent_team.task_manager.team_plan_id,
        )
        plan_path = agent_team.task_manager.plans_dir / "draft-plan.md"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("1. inspect\n2. implement\n", encoding="utf-8")
        submit_result = await member_task_manager.submit_plan(
            task.task_id,
            plan_path=str(plan_path),
        )
        assert submit_result["success"] is True
        return task, submit_result["plan_id"]

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
            agent_card=sample_agent_card,
            mode=MemberMode.PLAN_MODE,
        )
        task, plan_id = await self._submit_member_plan(agent_team)

        tool = ApprovePlanTool(agent_team, t)
        result = await tool.invoke({"plan_id": plan_id, "approved": True, "feedback": "Great plan!"})

        assert result.success is True
        assert result.error is None
        approved_task = await agent_team.task_manager.get(task.task_id)
        assert approved_task.status == TaskStatus.PLAN_APPROVED.value

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_requires_plan_id(self, agent_team, t, sample_agent_card):
        """Test invoking approve plan tool requires plan_id."""
        await agent_team.spawn_member(
            member_name="member1",
            display_name="Member One",
            agent_card=sample_agent_card,
            mode=MemberMode.PLAN_MODE,
        )
        task, _ = await self._submit_member_plan(agent_team)

        tool = ApprovePlanTool(agent_team, t)
        result = await tool.invoke({"task_id": task.task_id, "approved": True, "feedback": "Great plan!"})

        assert result.success is False
        approved_task = await agent_team.task_manager.get(task.task_id)
        assert approved_task.status == TaskStatus.CLAIMED.value

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_reject(self, agent_team, t, sample_agent_card):
        """Test invoking approve plan tool to reject"""
        await agent_team.spawn_member(
            member_name="member1",
            display_name="Member One",
            agent_card=sample_agent_card,
            mode=MemberMode.PLAN_MODE,
        )
        task, plan_id = await self._submit_member_plan(agent_team)

        tool = ApprovePlanTool(agent_team, t)
        result = await tool.invoke({"plan_id": plan_id, "approved": False, "feedback": "Please revise"})

        assert result.success is True
        rejected_task = await agent_team.task_manager.get(task.task_id)
        assert rejected_task.status == TaskStatus.CLAIMED.value

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_member_not_found(self, agent_team, t):
        """Test invoking approve plan tool for a non-existent plan."""
        tool = ApprovePlanTool(agent_team, t)
        result = await tool.invoke({"plan_id": "missing-plan", "approved": True})

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
        result = await tool.invoke(
            {
                "member_name": "member1",
                "tool_call_id": "call-1",
                "approved": True,
                "feedback": "approved",
                "auto_confirm": True,
            }
        )

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
        await agent_team.spawn_member(member_name="member1", display_name="Member One", agent_card=sample_agent_card)
        await agent_team.spawn_member(member_name="member2", display_name="Member Two", agent_card=sample_agent_card)

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
        result = await tool.invoke({"tasks": [{"title": "Task 1", "content": "Content 1"}]})

        assert result.success is True
        assert result.data["title"] == "Task 1"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_create_batch_tasks(self, agent_team, t):
        """Test batch task creation"""
        tool = TaskCreateTool(agent_team, t)
        result = await tool.invoke(
            {
                "tasks": [
                    {"title": "Task 1", "content": "Content 1"},
                    {"title": "Task 2", "content": "Content 2"},
                    {"title": "Task 3", "content": "Content 3"},
                ]
            }
        )

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
        result = await tool.invoke(
            {
                "tasks": [
                    {
                        "title": "Priority Task",
                        "content": "Priority content",
                        "depended_by": [base.task_id],
                    }
                ]
            }
        )

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
        result = await tool.invoke(
            {
                "task_id": task.task_id,
                "title": "Updated Title",
                "content": "Updated Content",
            }
        )

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
        result = await tool.invoke(
            {
                "task_id": task.task_id,
                "status": "cancelled",
            }
        )

        assert result.success is True
        assert result.data["task_id"] == task.task_id
        assert result.data["status"] == "cancelled"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cancel_all_tasks(self, agent_team, t, db):
        """Test cancel all tasks via task_id='*'"""
        await db.task.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.task.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")

        tool = UpdateTaskTool(agent_team, t)
        result = await tool.invoke({"task_id": "*", "status": "cancelled"})

        assert result.success is True
        assert result.data["cancelled_count"] == 2

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_assign_task(self, agent_team, t, sample_agent_card, db):
        """Test assigning a task to a member"""
        await db.member.create_member(
            member_name="dev-1",
            team_name="test_team",
            display_name="Dev",
            agent_card=sample_agent_card.model_dump_json(),
            status=MemberStatus.READY,
        )
        task = await agent_team.task_manager.add(title="Task", content="Content")

        tool = UpdateTaskTool(agent_team, t)
        result = await tool.invoke(
            {
                "task_id": task.task_id,
                "assignee": "dev-1",
            }
        )

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
            await db.member.create_member(
                member_name=member_name,
                team_name="test_team",
                display_name=member_name,
                agent_card=sample_agent_card.model_dump_json(),
                status=MemberStatus.READY,
            )
        task = await agent_team.task_manager.add(title="Task", content="Content")
        await db.task.claim_task(task.task_id, "dev-1")

        tool = UpdateTaskTool(agent_team, t)
        result = await tool.invoke(
            {
                "task_id": task.task_id,
                "assignee": "dev-2",
            }
        )

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
        result = await tool.invoke(
            {
                "task_id": downstream.task_id,
                "add_blocked_by": [upstream.task_id],
            }
        )

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
        result = await tool.invoke(
            {
                "task_id": a.task_id,
                "add_blocked_by": [b.task_id],
            }
        )

        assert result.success is False
        assert "Circular dependency" in result.error

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_cancel_unblocks_downstream(self, agent_team, t):
        """Cancelling a task via update_task unblocks anything that
        depended on it. Mirrors the bug-fix coverage in test_database
        but exercises the full tool boundary."""
        upstream = await agent_team.task_manager.add(title="Up", content="c")
        downstream = await agent_team.task_manager.add(title="Down", content="c", dependencies=[upstream.task_id])
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
            title="Downstream",
            content="Do second",
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
        await db.task.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.task.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")
        await db.task.create_task("task3", "test_team", "Task 3", "Content 3", "completed")

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
        await db.task.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.task.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")
        await db.task.create_task("task3", "test_team", "Task 3", "Content 3", "completed")

        tool = ViewTaskToolV2(agent_team.task_manager, t)
        result = await tool.invoke({})

        assert result.success is True
        assert result.data["count"] == 3

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_claimable(self, agent_team, t, db):
        """Test claimable action returns only pending tasks"""
        await db.task.create_task("task1", "test_team", "Task 1", "Content 1", "pending")
        await db.task.create_task("task2", "test_team", "Task 2", "Content 2", "claimed")
        await db.task.create_task("task3", "test_team", "Task 3", "Content 3", "completed")

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
        await db.member.create_member(
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

        await db.member.create_member(
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
                    {
                        "task_id": "t2",
                        "title": "Add test",
                        "status": "claimed",
                        "assignee": "dev-1",
                        "blocked_by": ["t1"],
                        "updated_at": 1_700_000_000_000,
                    },
                ],
                "count": 2,
            },
        )
        result = tool.map_result(output)
        assert "#t1 [pending] Fix bug" in result
        assert "(dev-1)" in result
        assert "[blocked by #t1]" in result
        # t2 carries updated_at → its line shows the absolute local time;
        # t1 has no updated_at → the None guard skips time rendering.
        assert "2023-11-" in result

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
                "updated_at": 1_700_000_000_000,
            },
        )
        result = tool.map_result(output)
        assert "Task #t1: Fix bug" in result
        assert "Content: Fix the login bug" in result
        assert "Blocks: #t2, #t3" in result
        assert "Updated:" in result

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
        result = await tool.invoke(
            {
                "to": "member2",
                "content": "Please claim task-1",
                "summary": "assign task-1",
            }
        )

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

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_multicast_all_success(self, agent_team, t, sample_agent_card):
        """Multicast to a strict subset of members returns success with all delivered"""
        # Spawn a third member so ["m1", "m2"] stays a strict subset of the
        # roster — a multicast covering every other member is rejected.
        for name in ("m1", "m2", "m3"):
            await agent_team.spawn_member(
                member_name=name,
                display_name=name.upper(),
                agent_card=sample_agent_card,
            )

        tool = SendMessageTool(agent_team.message_manager, t, team=agent_team)
        result = await tool.invoke({"to": ["m1", "m2"], "content": "Hello"})

        assert result.success is True
        assert result.data["type"] == "multicast"
        assert result.data["delivered"] == ["m1", "m2"]
        assert result.data["failed"] == []

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_multicast_partial_failure(
        self,
        agent_team,
        t,
        sample_agent_card,
    ):
        """Strict success: any failure flips overall success to False, delivered still listed"""
        await agent_team.spawn_member(
            member_name="m1",
            display_name="M1",
            agent_card=sample_agent_card,
        )

        tool = SendMessageTool(agent_team.message_manager, t, team=agent_team)
        result = await tool.invoke({"to": ["m1", "ghost"], "content": "Hello"})

        assert result.success is False
        assert "Multicast partially failed" in result.error
        assert result.data["delivered"] == ["m1"]
        assert len(result.data["failed"]) == 1
        assert result.data["failed"][0]["to"] == "ghost"
        assert "not found" in result.data["failed"][0]["reason"]

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_multicast_all_fail(self, agent_team, t):
        """All targets unknown -> success=False, delivered empty"""
        tool = SendMessageTool(agent_team.message_manager, t, team=agent_team)
        result = await tool.invoke({"to": ["ghost1", "ghost2"], "content": "Hi"})

        assert result.success is False
        assert result.data["delivered"] == []
        assert len(result.data["failed"]) == 2

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_multicast_dedup_preserves_order(
        self,
        agent_team,
        t,
        sample_agent_card,
    ):
        """Duplicate names are removed while preserving first-seen order"""
        # m3 keeps the deduped ["m1", "m2"] a strict subset of the roster.
        for name in ("m1", "m2", "m3"):
            await agent_team.spawn_member(
                member_name=name,
                display_name=name.upper(),
                agent_card=sample_agent_card,
            )

        tool = SendMessageTool(agent_team.message_manager, t, team=agent_team)
        result = await tool.invoke({"to": ["m1", "m1", "m2"], "content": "Hi"})

        assert result.success is True
        assert result.data["delivered"] == ["m1", "m2"]

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_multicast_rejects_wildcard(self, agent_team, t):
        """Mixing '*' inside a multicast list is rejected"""
        tool = SendMessageTool(agent_team.message_manager, t)
        result = await tool.invoke({"to": ["m1", "*"], "content": "Hi"})

        assert result.success is False
        assert "broadcast" in result.error

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_multicast_rejects_user(self, agent_team, t):
        """Mixing 'user' inside a multicast list is rejected"""
        tool = SendMessageTool(agent_team.message_manager, t)
        result = await tool.invoke({"to": ["m1", "user"], "content": "Hi"})

        assert result.success is False
        assert "user" in result.error

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_multicast_empty_list(self, agent_team, t):
        """Empty list rejected"""
        tool = SendMessageTool(agent_team.message_manager, t)
        result = await tool.invoke({"to": [], "content": "Hi"})

        assert result.success is False
        assert "at least one" in result.error

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_multicast_single_element_does_not_degrade(
        self,
        agent_team,
        t,
        sample_agent_card,
    ):
        """Single-element list still returns type=multicast (no auto-degrade)"""
        # m2 keeps ["m1"] a strict subset — a single-element multicast that
        # covers the whole roster would be rejected, not degraded.
        for name in ("m1", "m2"):
            await agent_team.spawn_member(
                member_name=name,
                display_name=name.upper(),
                agent_card=sample_agent_card,
            )

        tool = SendMessageTool(agent_team.message_manager, t, team=agent_team)
        result = await tool.invoke({"to": ["m1"], "content": "Hi"})

        assert result.success is True
        assert result.data["type"] == "multicast"
        assert result.data["delivered"] == ["m1"]

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_multicast_skips_blank_entries(
        self,
        agent_team,
        t,
        sample_agent_card,
    ):
        """Blank/whitespace entries are dropped before validation"""
        # m2 keeps the cleaned ["m1"] a strict subset of the roster.
        for name in ("m1", "m2"):
            await agent_team.spawn_member(
                member_name=name,
                display_name=name.upper(),
                agent_card=sample_agent_card,
            )

        tool = SendMessageTool(agent_team.message_manager, t, team=agent_team)
        result = await tool.invoke({"to": ["m1", "  ", ""], "content": "Hi"})

        assert result.success is True
        assert result.data["delivered"] == ["m1"]

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_multicast_rejects_full_roster(
        self,
        agent_team,
        t,
        sample_agent_card,
    ):
        """Multicast covering every other member is rejected — must broadcast"""
        for name in ("m1", "m2"):
            await agent_team.spawn_member(
                member_name=name,
                display_name=name.upper(),
                agent_card=sample_agent_card,
            )

        tool = SendMessageTool(agent_team.message_manager, t, team=agent_team)
        result = await tool.invoke({"to": ["m1", "m2"], "content": "Hi"})

        assert result.success is False
        assert "broadcast" in result.error
        assert result.data is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_invalid_to_type(self, agent_team, t):
        """Non-string non-list 'to' is rejected"""
        tool = SendMessageTool(agent_team.message_manager, t)
        result = await tool.invoke({"to": 123, "content": "Hi"})

        assert result.success is False
        assert "string" in result.error

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_string_path_unchanged(self, agent_team, t):
        """Sanity: passing 'to' as string still goes through point-to-point path"""
        tool = SendMessageTool(agent_team.message_manager, t)
        result = await tool.invoke({"to": "m1", "content": "Hi"})

        assert result.success is True
        assert result.data["type"] == "message"
        assert result.data["to"] == "m1"

    @pytest.mark.level1
    def test_send_message_map_result_multicast_success(self, agent_team, t):
        """map_result renders multicast success with sender, delivered list and count"""
        tool = SendMessageTool(agent_team.message_manager, t)
        output = ToolOutput(
            success=True,
            data={
                "type": "multicast",
                "from": "leader",
                "delivered": ["m1", "m2"],
                "failed": [],
                "summary": None,
            },
        )
        text = tool.map_result(output)
        assert "Multicast sent from leader" in text
        assert "m1, m2" in text
        assert "(2 delivered)" in text

    @pytest.mark.level1
    def test_send_message_map_result_multicast_partial(self, agent_team, t):
        """map_result on failure carries delivered + failed details"""
        tool = SendMessageTool(agent_team.message_manager, t)
        output = ToolOutput(
            success=False,
            error="Multicast partially failed: 1/2 target(s) failed",
            data={
                "type": "multicast",
                "from": "leader",
                "delivered": ["m1"],
                "failed": [{"to": "m2", "reason": "Member 'm2' not found"}],
                "summary": None,
            },
        )
        text = tool.map_result(output)
        assert "partially failed" in text
        assert "delivered: m1" in text
        assert "m2 — Member 'm2' not found" in text


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
