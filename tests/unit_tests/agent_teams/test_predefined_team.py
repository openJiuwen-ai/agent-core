# coding: utf-8

"""Unit tests for predefined team feature."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.status import (
    ExecutionStatus,
    MemberStatus,
)
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
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
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_tools import (
    create_team_tools,
)
from tests.test_logger import logger


@pytest.fixture
def db_config():
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
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
    bus = AsyncMock(spec=Messager)
    yield bus


@pytest.fixture
def predefined_members():
    return [
        TeamMemberSpec(
            member_name="backend-dev",
            display_name="Backend Developer",
            persona="Senior backend engineer",
            prompt_hint="Check tasks and start working",
        ),
        TeamMemberSpec(
            member_name="frontend-dev",
            display_name="Frontend Developer",
            persona="Senior frontend engineer",
        ),
    ]


class TestBuildTeamWithPredefinedMembers:
    """Test build_team writes all predefined members to DB."""

    @pytest_asyncio.fixture
    async def team_with_predefined(self, db, message_bus, predefined_members):
        return TeamBackend(
            team_name="predefined_team",
            member_name="leader1",
            is_leader=True,
            db=db,
            messager=message_bus,
            predefined_members=predefined_members,
        )

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_build_team_registers_predefined_members(self, team_with_predefined, db):
        await team_with_predefined.build_team(
            display_name="Test Team",
            desc="A predefined team",
            leader_display_name="Leader",
            leader_desc="PM",
        )

        members = await db.get_team_members("predefined_team")
        member_ids = {m.member_name for m in members}
        logger.info("Members after build_team: {}", member_ids)

        assert "leader1" in member_ids
        assert "backend-dev" in member_ids
        assert "frontend-dev" in member_ids
        assert len(members) == 3

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_predefined_members_status_is_unstarted(self, team_with_predefined, db):
        await team_with_predefined.build_team(
            display_name="Test Team",
            desc="desc",
            leader_display_name="Leader",
            leader_desc="PM",
        )

        backend_dev = await db.get_member("backend-dev", "predefined_team")
        frontend_dev = await db.get_member("frontend-dev", "predefined_team")

        assert backend_dev.status == MemberStatus.UNSTARTED.value
        assert frontend_dev.status == MemberStatus.UNSTARTED.value
        assert backend_dev.execution_status == ExecutionStatus.IDLE.value
        assert frontend_dev.execution_status == ExecutionStatus.IDLE.value

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_predefined_members_preserve_desc_and_prompt(self, team_with_predefined, db):
        await team_with_predefined.build_team(
            display_name="Test Team",
            desc="desc",
            leader_display_name="Leader",
            leader_desc="PM",
        )

        backend_dev = await db.get_member("backend-dev", "predefined_team")
        frontend_dev = await db.get_member("frontend-dev", "predefined_team")

        assert backend_dev.desc == "Senior backend engineer"
        assert backend_dev.prompt == "Check tasks and start working"
        assert frontend_dev.desc == "Senior frontend engineer"
        assert frontend_dev.prompt is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_leader_still_registered_as_busy(self, team_with_predefined, db):
        await team_with_predefined.build_team(
            display_name="Test Team",
            desc="desc",
            leader_display_name="Leader",
            leader_desc="PM",
        )

        leader = await db.get_member("leader1", "predefined_team")
        assert leader.status == MemberStatus.BUSY.value
        assert leader.execution_status == ExecutionStatus.RUNNING.value


class TestBuildTeamWithoutPredefinedMembers:
    """Ensure backward compatibility: empty predefined_members behaves like before."""

    @pytest_asyncio.fixture
    async def team_no_predefined(self, db, message_bus):
        return TeamBackend(
            team_name="auto_team",
            member_name="leader1",
            is_leader=True,
            db=db,
            messager=message_bus,
        )

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_build_team_only_registers_leader(self, team_no_predefined, db):
        await team_no_predefined.build_team(
            display_name="Auto Team",
            desc="desc",
            leader_display_name="Leader",
            leader_desc="PM",
        )

        members = await db.get_team_members("auto_team")
        assert len(members) == 1
        assert members[0].member_name == "leader1"


class TestToolExclusion:
    """Test spawn_member is excluded from leader tools in predefined mode."""

    @pytest.mark.level0
    def test_exclude_spawn_member_when_predefined(self, predefined_members):
        agent_team = AsyncMock()
        agent_team.is_leader = True

        tools = create_team_tools(
            role="leader",
            agent_team=agent_team,
            exclude_tools={"spawn_member"},
        )
        tool_names = {t.card.name for t in tools}
        logger.info("Leader tools with exclusion: {}", tool_names)

        assert "spawn_member" not in tool_names
        assert "build_team" in tool_names
        assert "shutdown_member" in tool_names
        assert "create_task" in tool_names

    @pytest.mark.level1
    def test_no_exclusion_without_predefined(self):
        agent_team = AsyncMock()
        agent_team.is_leader = True

        tools = create_team_tools(
            role="leader",
            agent_team=agent_team,
        )
        tool_names = {t.card.name for t in tools}
        logger.info("Leader tools without exclusion: {}", tool_names)

        assert "spawn_member" in tool_names

    @pytest.mark.level1
    def test_exclude_does_not_affect_teammate_tools(self):
        agent_team = AsyncMock()
        agent_team.is_leader = False

        tools = create_team_tools(
            role="teammate",
            agent_team=agent_team,
            exclude_tools={"spawn_member"},
        )
        tool_names = {t.card.name for t in tools}

        assert "claim_task" in tool_names

    @pytest.mark.level1
    def test_leader_has_approval_tools_in_plan_mode(self):
        """Leader must get approve_plan and approve_tool in plan_mode — required for plan review."""
        agent_team = AsyncMock()
        agent_team.is_leader = True

        tools = create_team_tools(
            role="leader",
            agent_team=agent_team,
            teammate_mode="plan_mode",
        )
        tool_names = {t.card.name for t in tools}
        logger.info("Leader tools (plan_mode): {}", tool_names)

        assert "approve_plan" in tool_names
        assert "approve_tool" in tool_names

    @pytest.mark.level1
    def test_leader_no_approval_tools_in_build_mode(self):
        """build_mode has no plan workflow — approval tools must be excluded from the leader."""
        agent_team = AsyncMock()
        agent_team.is_leader = True

        tools = create_team_tools(
            role="leader",
            agent_team=agent_team,
            teammate_mode="build_mode",
        )
        tool_names = {t.card.name for t in tools}
        logger.info("Leader tools (build_mode): {}", tool_names)

        assert "approve_plan" not in tool_names
        assert "approve_tool" not in tool_names

    @pytest.mark.level1
    def test_teammate_does_not_have_approval_tools(self):
        """approve_plan / approve_tool are leader-only, regardless of mode."""
        agent_team = AsyncMock()
        agent_team.is_leader = False

        for mode in ("build_mode", "plan_mode"):
            tools = create_team_tools(
                role="teammate",
                agent_team=agent_team,
                teammate_mode=mode,
            )
            tool_names = {t.card.name for t in tools}

            assert "approve_plan" not in tool_names, f"teammate got approve_plan in {mode}"
            assert "approve_tool" not in tool_names, f"teammate got approve_tool in {mode}"


class TestPredefinedTeamPrompt:
    """Test system prompt includes predefined team override."""

    @pytest.mark.level1
    def test_predefined_prompt_includes_override(self):
        from openjiuwen.agent_teams.agent.policy import build_system_prompt

        prompt = build_system_prompt(
            role=TeamRole.LEADER,
            persona="PM",
            team_mode="predefined",
        )
        logger.info("Predefined prompt length: {}", len(prompt))

        assert "预定义团队模式" in prompt
        assert "spawn_member" in prompt

    @pytest.mark.level1
    def test_auto_team_prompt_no_override(self):
        from openjiuwen.agent_teams.agent.policy import build_system_prompt

        prompt = build_system_prompt(
            role=TeamRole.LEADER,
            persona="PM",
            team_mode="default",
        )

        assert "预定义团队模式" not in prompt

    @pytest.mark.level1
    def test_hybrid_prompt_includes_hybrid_mode(self):
        from openjiuwen.agent_teams.agent.policy import build_system_prompt

        prompt = build_system_prompt(
            role=TeamRole.LEADER,
            persona="PM",
            team_mode="hybrid",
        )

        assert "混合团队模式" in prompt
        assert "spawn_member" in prompt

    @pytest.mark.level1
    def test_predefined_workflow_not_applied_to_teammate(self):
        from openjiuwen.agent_teams.agent.policy import build_system_prompt

        prompt = build_system_prompt(
            role=TeamRole.TEAMMATE,
            persona="Dev",
            team_mode="predefined",
        )

        assert "预定义团队模式" not in prompt


class TestResolveAgentSpecByMemberName:
    """Test _resolve_agent_spec resolves custom member_name key correctly."""

    @pytest.mark.level1
    def test_resolve_by_member_name_first(self):
        """When member_name exists in agents dict, use that spec."""
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent
        from openjiuwen.agent_teams.schema.blueprint import (
            DeepAgentSpec,
            LeaderSpec,
            TeamAgentSpec,
        )

        # Create agent specs with different max_iterations
        leader_spec = DeepAgentSpec(max_iterations=100)
        teammate_spec = DeepAgentSpec(max_iterations=50)
        custom_spec = DeepAgentSpec(max_iterations=30)

        spec = TeamAgentSpec(
            agents={
                "leader": leader_spec,
                "teammate": teammate_spec,
                "custom-member": custom_spec,  # Custom key matching member_name
            },
            leader=LeaderSpec(),
        )

        # Create a minimal TeamAgent to test _resolve_agent_spec
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        card = AgentCard(id="test", name="test")
        agent = TeamAgent(card)
        agent._configurator.spec = spec

        # Resolve by member_name should return custom_spec
        result = agent._resolve_agent_spec(spec, TeamRole.TEAMMATE, "custom-member")
        assert result.max_iterations == 30

    @pytest.mark.level1
    def test_fallback_to_role_value(self):
        """When member_name not in agents, fallback to role.value."""
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent
        from openjiuwen.agent_teams.schema.blueprint import (
            DeepAgentSpec,
            LeaderSpec,
            TeamAgentSpec,
        )
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        leader_spec = DeepAgentSpec(max_iterations=100)
        teammate_spec = DeepAgentSpec(max_iterations=50)

        spec = TeamAgentSpec(
            agents={
                "leader": leader_spec,
                "teammate": teammate_spec,
                # No "unknown-member" key
            },
            leader=LeaderSpec(),
        )

        card = AgentCard(id="test", name="test")
        agent = TeamAgent(card)
        agent._configurator.spec = spec

        # Fallback to teammate spec
        result = agent._resolve_agent_spec(spec, TeamRole.TEAMMATE, "unknown-member")
        assert result.max_iterations == 50

    @pytest.mark.level1
    def test_fallback_chain_to_leader(self):
        """When neither member_name nor role.value in agents, fallback to leader."""
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent
        from openjiuwen.agent_teams.schema.blueprint import (
            DeepAgentSpec,
            LeaderSpec,
            TeamAgentSpec,
        )
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        leader_spec = DeepAgentSpec(max_iterations=100)

        spec = TeamAgentSpec(
            agents={
                "leader": leader_spec,
                # No "teammate" key
            },
            leader=LeaderSpec(),
        )

        card = AgentCard(id="test", name="test")
        agent = TeamAgent(card)
        agent._configurator.spec = spec

        # Fallback chain: member_name -> role.value -> "teammate" -> "leader"
        result = agent._resolve_agent_spec(spec, TeamRole.TEAMMATE, "unknown-member")
        assert result.max_iterations == 100

    @pytest.mark.level1
    def test_leader_role_uses_leader_spec(self):
        """Leader role should use leader spec regardless of member_name."""
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent
        from openjiuwen.agent_teams.schema.blueprint import (
            DeepAgentSpec,
            LeaderSpec,
            TeamAgentSpec,
        )
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        leader_spec = DeepAgentSpec(max_iterations=100)
        teammate_spec = DeepAgentSpec(max_iterations=50)

        spec = TeamAgentSpec(
            agents={
                "leader": leader_spec,
                "teammate": teammate_spec,
            },
            leader=LeaderSpec(),
        )

        card = AgentCard(id="test", name="test")
        agent = TeamAgent(card)
        agent._configurator.spec = spec

        # Leader role with no member_name uses leader spec
        result = agent._resolve_agent_spec(spec, TeamRole.LEADER, None)
        assert result.max_iterations == 100
