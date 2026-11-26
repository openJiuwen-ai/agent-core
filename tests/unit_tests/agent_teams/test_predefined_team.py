# coding: utf-8

"""Unit tests for predefined team feature."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.events import TeamEvent
from openjiuwen.agent_teams.schema.status import (
    ExecutionStatus,
    MemberStatus,
)
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
)
from openjiuwen.agent_teams.context import (
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
            desc="Senior backend engineer",
            prompt="Check tasks and start working",
        ),
        TeamMemberSpec(
            member_name="frontend-dev",
            display_name="Frontend Developer",
            desc="Senior frontend engineer",
        ),
    ]


def test_team_agent_spec_defaults_teammate_mode_to_build_mode():
    spec = TeamAgentSpec(agents={})

    assert spec.enable_team_plan is False
    assert spec.teammate_mode == "build_mode"


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

        members = await db.member.get_team_members("predefined_team")
        member_ids = {m.member_name for m in members}
        logger.info("Members after build_team: {}", member_ids)

        assert "leader1" in member_ids
        assert "backend-dev" in member_ids
        assert "frontend-dev" in member_ids
        assert len(members) == 3

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_build_team_skips_redundant_team_exists_probe(
            self, team_with_predefined, db, monkeypatch):
        """build_team 内所有成员注册走 skip_team_exists——团队行刚由同一
        调用栈创建，逐成员复查是纯冗余读池 checkout（装配突发里每次
        checkout 都在共享读池排队）。探针打桩成必抛：一旦被调用即失败。"""

        async def _must_not_be_called(*args, **kwargs):  # pragma: no cover
            raise AssertionError("team_exists probe must be skipped during build_team")

        monkeypatch.setattr(db.team, "team_exists", _must_not_be_called)

        await team_with_predefined.build_team(
            display_name="Test Team",
            desc="A predefined team",
            leader_display_name="Leader",
            leader_desc="PM",
        )

        members = await db.member.get_team_members("predefined_team")
        assert {m.member_name for m in members} == {"leader1", "backend-dev", "frontend-dev"}

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_build_team_raises_when_member_registration_fails(
            self, team_with_predefined, monkeypatch):
        """成员注册返回失败必须上抛。"""
        from openjiuwen.agent_teams.schema.team import MemberOpResult

        async def _fail(**kwargs):
            return MemberOpResult.fail("member boom")

        monkeypatch.setattr(team_with_predefined, "spawn_member", _fail)

        with pytest.raises(RuntimeError, match="member boom"):
            await team_with_predefined.build_team(
                display_name="Test Team",
                desc="desc",
                leader_display_name="Leader",
                leader_desc="PM",
            )

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_build_team_keeps_registering_after_mid_loop_failure(
            self, db, message_bus, monkeypatch):
        """单个预定义成员注册失败不得中断剩余成员注册，也不得跳过收尾
        （on_team_built / TeamCreatedEvent）：尾段照跑后统一上抛，缺失
        成员名称随异常文案透出（五轮检视：立即 raise 会把团队行留在
        "已可用但收尾未执行"的矛盾态）。"""
        from openjiuwen.agent_teams.schema.team import MemberOpResult

        specs = [
            TeamMemberSpec(member_name="backend-dev", display_name="Backend Developer"),
            TeamMemberSpec(member_name="frontend-dev", display_name="Frontend Developer"),
            TeamMemberSpec(member_name="qa-dev", display_name="QA Engineer"),
        ]
        built: list[str] = []

        async def _on_built() -> None:
            built.append("built")

        backend = TeamBackend(
            team_name="predefined_team",
            member_name="leader1",
            is_leader=True,
            db=db,
            messager=message_bus,
            predefined_members=specs,
            on_team_built=_on_built,
        )

        original_spawn = backend.spawn_member
        attempted: list[str] = []

        async def _flaky_spawn(**kwargs):
            attempted.append(kwargs["member_name"])
            if kwargs["member_name"] == "frontend-dev":
                return MemberOpResult.fail("insert rejected")
            return await original_spawn(**kwargs)

        monkeypatch.setattr(backend, "spawn_member", _flaky_spawn)

        with pytest.raises(RuntimeError, match="frontend-dev"):
            await backend.build_team(
                display_name="Test Team",
                desc="desc",
                leader_display_name="Leader",
                leader_desc="PM",
            )

        # 失败成员之后的成员照常注册（保留旧 best-effort 语义）
        assert attempted == ["leader1", "backend-dev", "frontend-dev", "qa-dev"]
        members = await db.member.get_team_members("predefined_team")
        assert {m.member_name for m in members} == {
            "leader1", "backend-dev", "qa-dev",
        }

        # 尾段必须执行：on_team_built（调度器装载钩子）与 TeamCreatedEvent
        assert built == ["built"]
        created_events = [
            call.kwargs["message"]
            for call in message_bus.publish.await_args_list
            if getattr(call.kwargs.get("message"), "event_type", None)
               == TeamEvent.CREATED
        ]
        assert len(created_events) == 1
        assert created_events[0].payload["team_name"] == "predefined_team"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_build_team_keeps_registering_after_mid_loop_exception(
            self, db, message_bus, monkeypatch):
        """spawn_member 内部裸读/写抛出的异常同样不得逃出循环：转成失败
        汇总，其余成员照常注册、尾段照跑。"""
        specs = [
            TeamMemberSpec(member_name="backend-dev", display_name="Backend Developer"),
            TeamMemberSpec(member_name="frontend-dev", display_name="Frontend Developer"),
            TeamMemberSpec(member_name="qa-dev", display_name="QA Engineer"),
        ]
        built: list[str] = []

        async def _on_built() -> None:
            built.append("built")

        backend = TeamBackend(
            team_name="predefined_team",
            member_name="leader1",
            is_leader=True,
            db=db,
            messager=message_bus,
            predefined_members=specs,
            on_team_built=_on_built,
        )

        original_spawn = backend.spawn_member
        attempted: list[str] = []

        async def _flaky_spawn(**kwargs):
            attempted.append(kwargs["member_name"])
            if kwargs["member_name"] == "frontend-dev":
                raise TimeoutError("read pool timeout")
            return await original_spawn(**kwargs)

        monkeypatch.setattr(backend, "spawn_member", _flaky_spawn)

        with pytest.raises(RuntimeError, match="read pool timeout"):
            await backend.build_team(
                display_name="Test Team",
                desc="desc",
                leader_display_name="Leader",
                leader_desc="PM",
            )

        # 异常成员之后的成员照常注册
        assert attempted == ["leader1", "backend-dev", "frontend-dev", "qa-dev"]
        members = await db.member.get_team_members("predefined_team")
        assert {m.member_name for m in members} == {
            "leader1", "backend-dev", "qa-dev",
        }

        # 尾段必须执行（异常路径不得跳过调度器装载/created 事件）
        assert built == ["built"]
        created_events = [
            call.kwargs["message"]
            for call in message_bus.publish.await_args_list
            if getattr(call.kwargs.get("message"), "event_type", None)
               == TeamEvent.CREATED
        ]
        assert len(created_events) == 1

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_predefined_members_status_is_unstarted(self, team_with_predefined, db):
        await team_with_predefined.build_team(
            display_name="Test Team",
            desc="desc",
            leader_display_name="Leader",
            leader_desc="PM",
        )

        backend_dev = await db.member.get_member("backend-dev", "predefined_team")
        frontend_dev = await db.member.get_member("frontend-dev", "predefined_team")

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

        backend_dev = await db.member.get_member("backend-dev", "predefined_team")
        frontend_dev = await db.member.get_member("frontend-dev", "predefined_team")

        assert backend_dev.desc == "Senior backend engineer"
        assert backend_dev.prompt == "Check tasks and start working"
        assert frontend_dev.desc == "Senior frontend engineer"
        # A predefined member with no ``prompt`` stores the schema default (empty
        # string), not NULL — ``MemberSpecBase.prompt`` defaults to "".
        assert frontend_dev.prompt == ""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_leader_still_registered_as_busy(self, team_with_predefined, db):
        await team_with_predefined.build_team(
            display_name="Test Team",
            desc="desc",
            leader_display_name="Leader",
            leader_desc="PM",
        )

        leader = await db.member.get_member("leader1", "predefined_team")
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

        members = await db.member.get_team_members("auto_team")
        assert len(members) == 1
        assert members[0].member_name == "leader1"


class TestToolExclusion:
    """Test spawn_teammate is excluded from leader tools in predefined mode."""

    @pytest.mark.level0
    def test_exclude_spawn_teammate_when_predefined(self, predefined_members):
        agent_team = AsyncMock()
        agent_team.hitt_enabled = MagicMock(return_value=False)
        agent_team.bridge_enabled = MagicMock(return_value=False)
        agent_team.external_cli_kinds = MagicMock(return_value=frozenset())
        agent_team.is_leader = True

        tools = create_team_tools(
            role="leader",
            agent_team=agent_team,
            exclude_tools={"spawn_teammate"},
        )
        tool_names = {t.card.name for t in tools}
        logger.info("Leader tools with exclusion: {}", tool_names)

        assert "spawn_teammate" not in tool_names
        assert "build_team" in tool_names
        assert "shutdown_member" in tool_names
        assert "create_task" in tool_names

    @pytest.mark.level1
    def test_no_exclusion_without_predefined(self):
        agent_team = AsyncMock()
        agent_team.hitt_enabled = MagicMock(return_value=False)
        agent_team.bridge_enabled = MagicMock(return_value=False)
        agent_team.external_cli_kinds = MagicMock(return_value=frozenset())
        agent_team.is_leader = True

        tools = create_team_tools(
            role="leader",
            agent_team=agent_team,
        )
        tool_names = {t.card.name for t in tools}
        logger.info("Leader tools without exclusion: {}", tool_names)

        assert "spawn_teammate" in tool_names

    @pytest.mark.level1
    def test_exclude_does_not_affect_teammate_tools(self):
        agent_team = AsyncMock()
        agent_team.hitt_enabled = MagicMock(return_value=False)
        agent_team.bridge_enabled = MagicMock(return_value=False)
        agent_team.external_cli_kinds = MagicMock(return_value=frozenset())
        agent_team.is_leader = False

        tools = create_team_tools(
            role="teammate",
            agent_team=agent_team,
            exclude_tools={"spawn_teammate"},
        )
        tool_names = {t.card.name for t in tools}

        assert "claim_task" in tool_names

    @pytest.mark.level1
    def test_leader_has_approval_tools_in_plan_mode(self):
        """Leader must get approve_plan and approve_tool in plan_mode — required for plan review."""
        agent_team = AsyncMock()
        agent_team.hitt_enabled = MagicMock(return_value=False)
        agent_team.bridge_enabled = MagicMock(return_value=False)
        agent_team.external_cli_kinds = MagicMock(return_value=frozenset())
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
        agent_team.hitt_enabled = MagicMock(return_value=False)
        agent_team.bridge_enabled = MagicMock(return_value=False)
        agent_team.external_cli_kinds = MagicMock(return_value=frozenset())
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
        agent_team.hitt_enabled = MagicMock(return_value=False)
        agent_team.bridge_enabled = MagicMock(return_value=False)
        agent_team.external_cli_kinds = MagicMock(return_value=frozenset())
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
            if mode == "plan_mode":
                assert "submit_plan" in tool_names
            else:
                assert "submit_plan" not in tool_names

    @pytest.mark.level1
    def test_team_plan_uses_teammate_mode(self):
        """team.plan uses the same member mode switch as normal team mode."""
        spec = TeamAgentSpec.model_construct(
            agents={},
            enable_team_plan=True,
            teammate_mode="plan_mode",
        )

        assert spec.teammate_mode == "plan_mode"


class TestPredefinedTeamPrompt:
    """Test system prompt includes predefined team override."""

    @pytest.mark.level1
    def test_predefined_prompt_includes_override(self):
        from openjiuwen.agent_teams.prompts import build_team_member_system_prompt

        prompt = build_team_member_system_prompt(
            role=TeamRole.LEADER,
            member_name="leader",
            team_mode="predefined",
        )
        logger.info("Predefined prompt length: {}", len(prompt))

        assert "预定义团队模式" in prompt
        assert "spawn_teammate" in prompt

    @pytest.mark.level1
    def test_auto_team_prompt_no_override(self):
        from openjiuwen.agent_teams.prompts import build_team_member_system_prompt

        prompt = build_team_member_system_prompt(
            role=TeamRole.LEADER,
            member_name="leader",
            team_mode="default",
        )

        assert "预定义团队模式" not in prompt

    @pytest.mark.level1
    def test_hybrid_prompt_includes_hybrid_mode(self):
        from openjiuwen.agent_teams.prompts import build_team_member_system_prompt

        prompt = build_team_member_system_prompt(
            role=TeamRole.LEADER,
            member_name="leader",
            team_mode="hybrid",
        )

        assert "混合团队模式" in prompt
        assert "spawn_teammate" in prompt

    @pytest.mark.level1
    def test_predefined_workflow_not_applied_to_teammate(self):
        from openjiuwen.agent_teams.prompts import build_team_member_system_prompt

        prompt = build_team_member_system_prompt(
            role=TeamRole.TEAMMATE,
            member_name="worker",
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

        # Leader role with no member_name uses leader spec
        result = agent._resolve_agent_spec(spec, TeamRole.LEADER, None)
        assert result.max_iterations == 100
