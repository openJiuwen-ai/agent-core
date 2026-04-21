# coding: utf-8

"""Tests for TeamRail and its section builders."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.agent.team_rail import (
    TeamRail,
    TeamSectionName,
    build_team_extra_section,
    build_team_info_section,
    build_team_lifecycle_section,
    build_team_members_section,
    build_team_persona_section,
    build_team_role_section,
    build_team_workflow_section,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from tests.test_logger import logger


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


class TestTeamRoleSection:
    @pytest.mark.level0
    def test_leader_role_section(self):
        section = build_team_role_section(
            role=TeamRole.LEADER,
            member_name="leader1",
            language="cn",
        )
        assert section is not None
        assert section.name == TeamSectionName.ROLE
        assert section.priority == 11

        content = section.render("cn")
        assert "# 团队角色" in content
        assert "你的 member_name: leader1" in content
        assert "create_task" in content  # from leader_policy.md

    @pytest.mark.level0
    def test_teammate_role_section(self):
        section = build_team_role_section(
            role=TeamRole.TEAMMATE,
            member_name="dev1",
            language="cn",
        )
        content = section.render("cn")
        assert "view_task" in content  # from teammate_policy.md

    @pytest.mark.level0
    def test_role_without_member_id(self):
        section = build_team_role_section(
            role=TeamRole.LEADER,
            member_name=None,
            language="cn",
        )
        content = section.render("cn")
        assert "你的 member_name" not in content


class TestTeamWorkflowSection:
    @pytest.mark.level0
    def test_leader_workflow(self):
        section = build_team_workflow_section(
            role=TeamRole.LEADER,
            predefined_team=False,
            language="cn",
        )
        assert section is not None
        assert section.priority == 13
        content = section.render("cn")
        assert "# 工作流程" in content
        assert "spawn_member" in content

    @pytest.mark.level0
    def test_leader_workflow_predefined(self):
        section = build_team_workflow_section(
            role=TeamRole.LEADER,
            predefined_team=True,
            language="cn",
        )
        content = section.render("cn")
        assert "预定义团队模式" in content

    @pytest.mark.level0
    def test_teammate_returns_none(self):
        section = build_team_workflow_section(
            role=TeamRole.TEAMMATE,
            predefined_team=False,
            language="cn",
        )
        assert section is None


class TestTeamLifecycleSection:
    @pytest.mark.level0
    def test_leader_temporary(self):
        section = build_team_lifecycle_section(
            role=TeamRole.LEADER,
            lifecycle="temporary",
            language="cn",
        )
        assert section is not None
        assert section.priority == 14
        content = section.render("cn")
        assert "# 团队生命周期" in content
        assert "shutdown_member" in content

    @pytest.mark.level0
    def test_leader_persistent(self):
        section = build_team_lifecycle_section(
            role=TeamRole.LEADER,
            lifecycle="persistent",
            language="cn",
        )
        content = section.render("cn")
        # persistent template has different content
        assert "# 团队生命周期" in content

    @pytest.mark.level0
    def test_teammate_returns_none(self):
        section = build_team_lifecycle_section(
            role=TeamRole.TEAMMATE,
            lifecycle="temporary",
            language="cn",
        )
        assert section is None


class TestTeamPersonaSection:
    @pytest.mark.level0
    def test_with_persona(self):
        section = build_team_persona_section(persona="PM Expert", language="cn")
        assert section is not None
        assert section.priority == 15
        content = section.render("cn")
        assert "# 当前人设" in content
        assert "PM Expert" in content

    @pytest.mark.level1
    def test_empty_persona_returns_none(self):
        assert build_team_persona_section(persona="", language="cn") is None
        assert build_team_persona_section(persona=None, language="cn") is None


class TestTeamExtraSection:
    @pytest.mark.level1
    def test_with_base_prompt(self):
        section = build_team_extra_section(base_prompt="Be careful", language="cn")
        assert section is not None
        assert section.priority == 16
        content = section.render("cn")
        assert "Be careful" in content

    @pytest.mark.level1
    def test_empty_returns_none(self):
        assert build_team_extra_section(base_prompt=None, language="cn") is None
        assert build_team_extra_section(base_prompt="   ", language="cn") is None


class TestTeamInfoSection:
    @pytest.mark.level1
    def test_full_info(self):
        section = build_team_info_section(
            team_info={"team_name": "AlphaTeam", "desc": "Build a thing"},
            language="cn",
        )
        assert section is not None
        assert section.priority == 65
        content = section.render("cn")
        assert "# 团队信息" in content
        assert "AlphaTeam" in content
        assert "Build a thing" in content

    @pytest.mark.level1
    def test_empty_returns_none(self):
        assert build_team_info_section(team_info=None, language="cn") is None
        assert build_team_info_section(team_info={}, language="cn") is None
        assert build_team_info_section(
            team_info={"unrelated": "value"},
            language="cn",
        ) is None

    @pytest.mark.level1
    def test_team_workspace_mount_appended(self):
        section = build_team_info_section(
            team_info={"team_name": "AlphaTeam", "desc": "Build a thing"},
            team_workspace_mount=".team/alpha/",
            team_workspace_path="/abs/team-workspace",
            language="cn",
        )
        assert section is not None
        content = section.render("cn")
        assert "团队共享工作空间" in content
        assert "`.team/alpha/`" in content
        assert "`/abs/team-workspace`" in content

    @pytest.mark.level1
    def test_team_workspace_only(self):
        # Workspace info alone (no name/desc) is still enough to emit the
        # section, so the LLM sees the shared workspace hint.
        section = build_team_info_section(
            team_info=None,
            team_workspace_mount=".team/solo/",
            language="en",
        )
        assert section is not None
        content = section.render("en")
        assert "Team Shared Workspace" in content
        assert "`.team/solo/`" in content


class TestTeamMembersSection:
    @pytest.mark.level1
    def test_excludes_self(self):
        section = build_team_members_section(
            team_members=[
                {"member_name": "leader1", "display_name": "Leader", "desc": "PM"},
                {"member_name": "dev1", "display_name": "Dev", "desc": "Coder"},
            ],
            self_member_name="leader1",
            language="cn",
        )
        assert section is not None
        assert section.priority == 66
        content = section.render("cn")
        assert "# 成员关系" in content
        assert "Dev" in content
        assert "Leader" not in content

    @pytest.mark.level1
    def test_no_peers_returns_none(self):
        section = build_team_members_section(
            team_members=[{"member_name": "self", "display_name": "Me"}],
            self_member_name="self",
            language="cn",
        )
        assert section is None

    @pytest.mark.level1
    def test_empty_returns_none(self):
        assert build_team_members_section(
            team_members=None,
            self_member_name="x",
            language="cn",
        ) is None


# ---------------------------------------------------------------------------
# TeamRail
# ---------------------------------------------------------------------------


class _StubAgent:
    """Minimal stand-in exposing only the system_prompt_builder attribute."""

    def __init__(self, builder: SystemPromptBuilder) -> None:
        self.system_prompt_builder = builder


class _StubMember:
    """Lightweight stand-in for the SQLModel TeamMember row."""

    def __init__(self, member_name: str, display_name: str, desc: str = "") -> None:
        self.member_name = member_name
        self.display_name = display_name
        self.desc = desc


class _StubTeam:
    """Lightweight stand-in for the SQLModel Team row."""

    def __init__(self, team_name: str, display_name: str = "", desc: str = "") -> None:
        self.team_name = team_name
        self.display_name = display_name
        self.desc = desc


class _FakeTeamBackend:
    """In-memory TeamBackend that tracks call counts.

    Mirrors the four TeamBackend methods that ``TeamRail`` consumes:
    ``get_team_updated_at``, ``get_members_max_updated_at``,
    ``get_team_info``, ``list_members``.  Lets tests assert that the
    cache short-circuits expensive calls when the mtime probe is stable.
    """

    def __init__(
        self,
        team: _StubTeam | None = None,
        members: list[_StubMember] | None = None,
        team_mtime: int = 1,
        members_mtime: int = 1,
    ) -> None:
        self._team = team
        self._members: list[_StubMember] = list(members or [])
        self._team_mtime = team_mtime
        self._members_mtime = members_mtime

        self.team_mtime_calls = 0
        self.members_mtime_calls = 0
        self.get_info_calls = 0
        self.list_members_calls = 0

    async def get_team_updated_at(self) -> int:
        self.team_mtime_calls += 1
        return self._team_mtime

    async def get_members_max_updated_at(self) -> int:
        self.members_mtime_calls += 1
        return self._members_mtime

    async def get_team_info(self):
        self.get_info_calls += 1
        return self._team

    async def list_members(self):
        self.list_members_calls += 1
        return list(self._members)

    # -- Mutators used by tests ----------------------------------------------

    def set_team(self, team: _StubTeam | None, mtime: int) -> None:
        self._team = team
        self._team_mtime = mtime

    def add_member(self, member: _StubMember, mtime: int) -> None:
        self._members.append(member)
        self._members_mtime = mtime


class TestTeamRailStaticSections:
    """Static-only behaviour (team_backend is None) -- the rail still
    registers role/workflow/lifecycle/persona/extra without touching DB."""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_leader_rail_registers_static_sections_without_backend(self):
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)

        rail = TeamRail(
            role=TeamRole.LEADER,
            persona="PM Expert",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            predefined_team=False,
            base_prompt="Stay sharp",
        )
        rail.init(agent)
        await rail.before_model_call(None)

        sections = builder.get_all_sections()
        for name in (
            TeamSectionName.ROLE,
            TeamSectionName.WORKFLOW,
            TeamSectionName.LIFECYCLE,
            TeamSectionName.PERSONA,
            TeamSectionName.EXTRA,
        ):
            assert name in sections
        # Without a backend the dynamic sections are skipped entirely.
        assert TeamSectionName.INFO not in sections
        assert TeamSectionName.MEMBERS not in sections

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_teammate_rail_omits_leader_only_sections(self):
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)

        rail = TeamRail(
            role=TeamRole.TEAMMATE,
            persona="Coder",
            member_name="dev1",
            lifecycle="temporary",
            language="cn",
            predefined_team=False,
            base_prompt=None,
        )
        rail.init(agent)
        await rail.before_model_call(None)

        sections = builder.get_all_sections()
        assert TeamSectionName.WORKFLOW not in sections
        assert TeamSectionName.LIFECYCLE not in sections
        assert TeamSectionName.EXTRA not in sections
        assert TeamSectionName.ROLE in sections
        assert TeamSectionName.PERSONA in sections


class TestTeamRailDynamicSections:
    """Dynamic behaviour driven by the injected ``_FakeTeamBackend``."""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_first_call_loads_from_db(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test team"),
            members=[
                _StubMember("leader1", "Leader", "PM"),
                _StubMember("dev1", "Dev", "Coder"),
            ],
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = TeamRail(
            role=TeamRole.LEADER,
            persona="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(None)

        assert builder.has_section(TeamSectionName.INFO)
        assert builder.has_section(TeamSectionName.MEMBERS)
        assert backend.get_info_calls == 1
        assert backend.list_members_calls == 1
        # The members section excluded the leader (self exclusion).
        members_render = builder.get_section(TeamSectionName.MEMBERS).render("cn")
        assert "Dev" in members_render
        assert "Leader" not in members_render
        logger.info("First call loaded info + members from backend")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cache_hit_skips_full_query(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = TeamRail(
            role=TeamRole.LEADER,
            persona="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(None)
        await rail.before_model_call(None)
        await rail.before_model_call(None)

        # Three model calls, three probes each, but only one full fetch.
        assert backend.team_mtime_calls == 3
        assert backend.members_mtime_calls == 3
        assert backend.get_info_calls == 1
        assert backend.list_members_calls == 1
        logger.info("Cache hit skipped 2 expensive fetches")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cache_miss_when_member_added(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            members_mtime=1,
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = TeamRail(
            role=TeamRole.LEADER,
            persona="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(None)
        first_render = builder.get_section(TeamSectionName.MEMBERS).render("cn")
        assert "Dev" in first_render
        assert "Newbie" not in first_render

        # Simulate spawn_member: add a row and bump mtime.
        backend.add_member(_StubMember("dev2", "Newbie", "fresh"), mtime=2)
        await rail.before_model_call(None)

        second_render = builder.get_section(TeamSectionName.MEMBERS).render("cn")
        assert "Newbie" in second_render
        assert backend.list_members_calls == 2
        logger.info("Member roster bump triggered refetch")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_status_update_does_not_refetch(self):
        """Status changes don't bump mtime (per design), so the cache holds."""
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            members_mtime=42,
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = TeamRail(
            role=TeamRole.LEADER,
            persona="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(None)
        # mtime stays at 42 -- a real status update would not bump it.
        await rail.before_model_call(None)
        assert backend.list_members_calls == 1

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_team_workspace_mount_preserved_after_refresh(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = TeamRail(
            role=TeamRole.LEADER,
            persona="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
            team_workspace_mount=".team/beta/",
            team_workspace_path="/abs/team-workspace",
        )
        rail.init(agent)
        await rail.before_model_call(None)
        first = builder.get_section(TeamSectionName.INFO).render("cn")
        assert "`.team/beta/`" in first

        # Trigger a roster refresh (members mtime bump).  The info section
        # is independent but should keep the workspace mount on rebuild too.
        backend.set_team(_StubTeam("Beta-renamed", "Test"), mtime=99)
        await rail.before_model_call(None)
        second = builder.get_section(TeamSectionName.INFO).render("cn")
        assert "`.team/beta/`" in second
        assert "Beta-renamed" in second

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_priority_order_in_built_prompt(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("T1", "D"),
            members=[_StubMember("dev1", "D")],
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = TeamRail(
            role=TeamRole.LEADER,
            persona="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            base_prompt=None,
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(None)

        prompt = builder.build()
        idx_role = prompt.index("# 团队角色")
        idx_workflow = prompt.index("# 工作流程")
        idx_lifecycle = prompt.index("# 团队生命周期")
        idx_persona = prompt.index("# 当前人设")
        idx_info = prompt.index("# 团队信息")
        idx_members = prompt.index("# 成员关系")
        assert idx_role < idx_workflow < idx_lifecycle < idx_persona < idx_info < idx_members

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_uninit_removes_static_and_dynamic_sections(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("T", "D"),
            members=[_StubMember("dev1", "Dev")],
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = TeamRail(
            role=TeamRole.LEADER,
            persona="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(None)
        assert builder.has_section(TeamSectionName.ROLE)
        assert builder.has_section(TeamSectionName.INFO)
        assert builder.has_section(TeamSectionName.MEMBERS)

        rail.uninit(agent)
        for name in (
            TeamSectionName.ROLE,
            TeamSectionName.WORKFLOW,
            TeamSectionName.LIFECYCLE,
            TeamSectionName.PERSONA,
            TeamSectionName.INFO,
            TeamSectionName.MEMBERS,
        ):
            assert not builder.has_section(name)
