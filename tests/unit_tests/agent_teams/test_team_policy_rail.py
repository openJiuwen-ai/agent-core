# coding: utf-8

"""Tests for TeamPolicyRail and its section builders."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.prompts import (
    TeamSectionName,
    build_team_extra_section,
    build_team_info_section,
    build_team_lifecycle_section,
    build_team_members_section,
    build_team_private_prompt_section,
    build_team_role_section,
    build_team_workflow_section,
)
from openjiuwen.agent_teams.rails import TeamPolicyRail
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager
from tests.test_logger import logger

# Session id the dynamic-attachment tests bind their context to.
_SESSION_ID = "s1"


class _StubSession:
    """Minimal session exposing the id the attachment writer resolves."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    def get_session_id(self) -> str:
        """Return the bound session id."""
        return self._session_id


class _StubContext:
    """Minimal AgentCallbackContext stand-in carrying a resolvable session."""

    def __init__(self, session_id: str = _SESSION_ID) -> None:
        self.session = _StubSession(session_id)

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
            team_mode="default",
            language="cn",
        )
        assert section is not None
        assert section.priority == 13
        content = section.render("cn")
        assert "# 工作流程" in content
        assert "spawn_teammate" in content

    @pytest.mark.level0
    def test_leader_workflow_predefined(self):
        section = build_team_workflow_section(
            role=TeamRole.LEADER,
            team_mode="predefined",
            language="cn",
        )
        content = section.render("cn")
        assert "预定义团队模式" in content

    @pytest.mark.level0
    def test_leader_workflow_hybrid(self):
        section = build_team_workflow_section(
            role=TeamRole.LEADER,
            team_mode="hybrid",
            language="cn",
        )
        assert section is not None
        content = section.render("cn")
        assert "混合团队模式" in content
        assert "spawn_teammate" in content

    @pytest.mark.level0
    def test_teammate_returns_none(self):
        section = build_team_workflow_section(
            role=TeamRole.TEAMMATE,
            team_mode="default",
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


class TestTeamPrivatePromptSection:
    @pytest.mark.level0
    def test_with_private_prompt(self):
        section = build_team_private_prompt_section(member_prompt="Ship small PRs", language="cn")
        assert section is not None
        assert section.priority == 16
        content = section.render("cn")
        assert "# 私有工作约定" in content
        assert "Ship small PRs" in content

    @pytest.mark.level1
    def test_empty_private_prompt_returns_none(self):
        assert build_team_private_prompt_section(member_prompt="", language="cn") is None
        assert build_team_private_prompt_section(member_prompt=None, language="cn") is None


class TestTeamExtraSection:
    @pytest.mark.level1
    def test_with_base_prompt(self):
        section = build_team_extra_section(base_prompt="Be careful", language="cn")
        assert section is not None
        assert section.priority == 17
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
        assert (
            build_team_info_section(
                team_info={"unrelated": "value"},
                language="cn",
            )
            is None
        )

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
        assert (
            build_team_members_section(
                team_members=None,
                self_member_name="x",
                language="cn",
            )
            is None
        )


# ---------------------------------------------------------------------------
# TeamPolicyRail
# ---------------------------------------------------------------------------


class _StubAgent:
    """Minimal stand-in exposing the builder + prompt attachment manager.

    Dynamic team-state sections now land in the attachment manager rather
    than the system prompt builder, so the stub agent provides a real
    :class:`PromptAttachmentManager` for the dynamic-section tests to read.
    """

    def __init__(
        self,
        builder: SystemPromptBuilder,
        attachment_manager: PromptAttachmentManager | None = None,
    ) -> None:
        self.system_prompt_builder = builder
        self.prompt_attachment_manager = attachment_manager


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

    Mirrors the four TeamBackend methods that ``TeamPolicyRail`` consumes:
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
        human_agents: list[str] | None = None,
    ) -> None:
        self._team = team
        self._members: list[_StubMember] = list(members or [])
        self._team_mtime = team_mtime
        self._members_mtime = members_mtime
        self._human_agents: list[str] = list(human_agents or [])

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

    def hitt_enabled(self) -> bool:
        """TeamPolicyRail probes this; fake teams never enable HITT."""
        return False

    async def human_agent_names(self) -> frozenset[str]:
        """TeamPolicyRail queries DB for the roster (empty unless injected)."""
        return frozenset(self._human_agents)

    def bridge_agent_names(self) -> frozenset[str]:
        """TeamPolicyRail snapshots the bridge roster too; empty for fakes."""
        return frozenset()

    # -- Mutators used by tests ----------------------------------------------

    def set_team(self, team: _StubTeam | None, mtime: int) -> None:
        self._team = team
        self._team_mtime = mtime

    def add_member(self, member: _StubMember, mtime: int) -> None:
        self._members.append(member)
        self._members_mtime = mtime


class TestTeamPolicyRailStaticSections:
    """Static-only behaviour (team_backend is None) -- the rail still
    registers role/workflow/lifecycle/private-prompt/extra without touching DB."""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_leader_rail_registers_static_sections_without_backend(self):
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)

        rail = TeamPolicyRail(
            role=TeamRole.LEADER,
            member_prompt="PM Expert",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_mode="default",
            base_prompt="Stay sharp",
        )
        rail.init(agent)
        await rail.before_model_call(None)

        sections = builder.get_all_sections()
        for name in (
            TeamSectionName.ROLE,
            TeamSectionName.WORKFLOW,
            TeamSectionName.LIFECYCLE,
            TeamSectionName.PRIVATE_PROMPT,
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

        rail = TeamPolicyRail(
            role=TeamRole.TEAMMATE,
            member_prompt="Coder",
            member_name="dev1",
            lifecycle="temporary",
            language="cn",
            team_mode="default",
            base_prompt=None,
        )
        rail.init(agent)
        await rail.before_model_call(None)

        sections = builder.get_all_sections()
        assert TeamSectionName.WORKFLOW not in sections
        assert TeamSectionName.LIFECYCLE not in sections
        assert TeamSectionName.EXTRA not in sections
        assert TeamSectionName.ROLE in sections
        assert TeamSectionName.PRIVATE_PROMPT in sections


async def _attachment_content(
    manager: PromptAttachmentManager,
    section: str,
    *,
    session_id: str = _SESSION_ID,
) -> str | None:
    """Return the content of one dynamic section attachment, or None."""
    items = await manager.list_by_filter(session_id=session_id, section=section)
    if not items:
        return None
    return items[0].content


class TestTeamPolicyRailDynamicSections:
    """Dynamic behaviour driven by the injected ``_FakeTeamBackend``.

    The three dynamic sections (team_info / team_members / team_hitt) no
    longer live in the system prompt builder; the rail pushes them to the
    DeepAgent's :class:`PromptAttachmentManager` so the system-prompt
    prefix stays cache-stable. These tests assert the dynamic sections are
    absent from the builder and present in the attachment manager, while
    keeping the original cache hit / miss / mtime intents intact.
    """

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
        manager = PromptAttachmentManager()
        agent = _StubAgent(builder, manager)
        rail = TeamPolicyRail(
            role=TeamRole.LEADER,
            member_prompt="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(_StubContext())

        # Dynamic sections are no longer in the builder.
        assert not builder.has_section(TeamSectionName.INFO)
        assert not builder.has_section(TeamSectionName.MEMBERS)
        # They live in the attachment manager instead.
        info = await _attachment_content(manager, TeamSectionName.INFO)
        members = await _attachment_content(manager, TeamSectionName.MEMBERS)
        assert info is not None
        assert members is not None
        assert backend.get_info_calls == 1
        assert backend.list_members_calls == 1
        # The members section excluded the leader (self exclusion).
        assert "Dev" in members
        assert "Leader" not in members
        logger.info("First call pushed info + members to attachment manager")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_cache_hit_skips_full_query(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
        )
        builder = SystemPromptBuilder(language="cn")
        manager = PromptAttachmentManager()
        agent = _StubAgent(builder, manager)
        rail = TeamPolicyRail(
            role=TeamRole.LEADER,
            member_prompt="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(_StubContext())
        await rail.before_model_call(_StubContext())
        await rail.before_model_call(_StubContext())

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
        manager = PromptAttachmentManager()
        agent = _StubAgent(builder, manager)
        rail = TeamPolicyRail(
            role=TeamRole.LEADER,
            member_prompt="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(_StubContext())
        first_render = await _attachment_content(manager, TeamSectionName.MEMBERS)
        assert first_render is not None
        assert "Dev" in first_render
        assert "Newbie" not in first_render

        # Simulate spawn_member: add a row and bump mtime.
        backend.add_member(_StubMember("dev2", "Newbie", "fresh"), mtime=2)
        await rail.before_model_call(_StubContext())

        second_render = await _attachment_content(manager, TeamSectionName.MEMBERS)
        assert second_render is not None
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
        manager = PromptAttachmentManager()
        agent = _StubAgent(builder, manager)
        rail = TeamPolicyRail(
            role=TeamRole.LEADER,
            member_prompt="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(_StubContext())
        # mtime stays at 42 -- a real status update would not bump it.
        await rail.before_model_call(_StubContext())
        assert backend.list_members_calls == 1

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_team_workspace_mount_preserved_after_refresh(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
        )
        builder = SystemPromptBuilder(language="cn")
        manager = PromptAttachmentManager()
        agent = _StubAgent(builder, manager)
        rail = TeamPolicyRail(
            role=TeamRole.LEADER,
            member_prompt="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
            team_workspace_mount=".team/beta/",
            team_workspace_path="/abs/team-workspace",
        )
        rail.init(agent)
        await rail.before_model_call(_StubContext())
        first = await _attachment_content(manager, TeamSectionName.INFO)
        assert first is not None
        assert "`.team/beta/`" in first

        # Trigger a roster refresh (members mtime bump).  The info section
        # is independent but should keep the workspace mount on rebuild too.
        backend.set_team(_StubTeam("Beta-renamed", "Test"), mtime=99)
        await rail.before_model_call(_StubContext())
        second = await _attachment_content(manager, TeamSectionName.INFO)
        assert second is not None
        assert "`.team/beta/`" in second
        assert "Beta-renamed" in second

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_static_sections_in_builder_dynamic_in_attachments(self):
        """Static sections stay in the cache-stable builder prefix.

        Replaces the old prompt-ordering test: dynamic sections no longer
        appear in ``builder.build()``, so the relevant invariant is now
        "statics in the builder, dynamics only in attachments".
        """
        backend = _FakeTeamBackend(
            team=_StubTeam("T1", "D"),
            members=[_StubMember("dev1", "D")],
        )
        builder = SystemPromptBuilder(language="cn")
        manager = PromptAttachmentManager()
        agent = _StubAgent(builder, manager)
        rail = TeamPolicyRail(
            role=TeamRole.LEADER,
            member_prompt="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            base_prompt=None,
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(_StubContext())

        prompt = builder.build()
        # Static sections render into the system prompt, ordered by priority.
        idx_role = prompt.index("# 团队角色")
        idx_workflow = prompt.index("# 工作流程")
        idx_lifecycle = prompt.index("# 团队生命周期")
        idx_private_prompt = prompt.index("# 私有工作约定")
        assert idx_role < idx_workflow < idx_lifecycle < idx_private_prompt
        # Dynamic sections do not leak into the system prompt anymore.
        assert "# 团队信息" not in prompt
        assert "# 成员关系" not in prompt
        # But are present as attachments.
        assert await _attachment_content(manager, TeamSectionName.INFO) is not None
        assert await _attachment_content(manager, TeamSectionName.MEMBERS) is not None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_uninit_removes_static_sections_only(self):
        """uninit strips static builder sections; dynamics never were there."""
        backend = _FakeTeamBackend(
            team=_StubTeam("T", "D"),
            members=[_StubMember("dev1", "Dev")],
        )
        builder = SystemPromptBuilder(language="cn")
        manager = PromptAttachmentManager()
        agent = _StubAgent(builder, manager)
        rail = TeamPolicyRail(
            role=TeamRole.LEADER,
            member_prompt="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(_StubContext())
        # Static sections registered in the builder; dynamics not in builder.
        assert builder.has_section(TeamSectionName.ROLE)
        assert not builder.has_section(TeamSectionName.INFO)
        assert not builder.has_section(TeamSectionName.MEMBERS)
        # Dynamics live in the attachment manager.
        assert await _attachment_content(manager, TeamSectionName.INFO) is not None

        rail.uninit(agent)
        for name in (
            TeamSectionName.ROLE,
            TeamSectionName.WORKFLOW,
            TeamSectionName.LIFECYCLE,
            TeamSectionName.PRIVATE_PROMPT,
        ):
            assert not builder.has_section(name)


class TestTeamPolicyRailHittSplit:
    """HITT contract rides the builder prefix; the roster rides the attachment.

    F_50 splits the old single ``team_hitt`` section into a static
    collaboration contract (system-prompt builder) and a dynamic human-member
    roster (``team_hitt_roster`` attachment). ``_FakeTeamBackend`` injects a
    human roster so these paths actually fire.
    """

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_leader_contract_in_builder_roster_in_attachment(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("leader1", "Leader")],
            human_agents=["alice", "bob"],
        )
        builder = SystemPromptBuilder(language="cn")
        manager = PromptAttachmentManager()
        agent = _StubAgent(builder, manager)
        rail = TeamPolicyRail(
            role=TeamRole.LEADER,
            member_prompt="PM",
            member_name="leader1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(_StubContext())

        # Contract (rules) rides the cache-stable builder prefix, names absent.
        assert builder.has_section(TeamSectionName.HITT)
        contract = builder.get_section(TeamSectionName.HITT).render("cn")
        assert "禁止" in contract
        assert "alice" not in contract and "bob" not in contract

        # Roster (names) rides the attachment tail, never the builder.
        assert not builder.has_section(TeamSectionName.HITT_ROSTER)
        roster = await _attachment_content(manager, TeamSectionName.HITT_ROSTER)
        assert roster is not None
        assert "alice" in roster and "bob" in roster
        logger.info("HITT split: contract in builder, roster in attachment")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_anonymous_teammate_has_contract_but_no_roster(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            human_agents=["alice"],
        )
        builder = SystemPromptBuilder(language="cn")
        manager = PromptAttachmentManager()
        agent = _StubAgent(builder, manager)
        rail = TeamPolicyRail(
            role=TeamRole.TEAMMATE,
            member_prompt="Coder",
            member_name="dev1",
            lifecycle="temporary",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(_StubContext())

        # Anonymous teammate keeps the contract but never lists names anywhere.
        assert builder.has_section(TeamSectionName.HITT)
        assert "alice" not in builder.get_section(TeamSectionName.HITT).render("cn")
        assert await _attachment_content(manager, TeamSectionName.HITT_ROSTER) is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_no_human_agents_no_hitt_anywhere(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            human_agents=[],
        )
        builder = SystemPromptBuilder(language="cn")
        manager = PromptAttachmentManager()
        agent = _StubAgent(builder, manager)
        rail = TeamPolicyRail(
            role=TeamRole.LEADER,
            member_name="leader1",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(_StubContext())
        assert not builder.has_section(TeamSectionName.HITT)
        assert await _attachment_content(manager, TeamSectionName.HITT_ROSTER) is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_uninit_strips_hitt_contract_from_builder(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("leader1", "Leader")],
            human_agents=["alice"],
        )
        builder = SystemPromptBuilder(language="cn")
        manager = PromptAttachmentManager()
        agent = _StubAgent(builder, manager)
        rail = TeamPolicyRail(
            role=TeamRole.LEADER,
            member_name="leader1",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        await rail.before_model_call(_StubContext())
        assert builder.has_section(TeamSectionName.HITT)
        rail.uninit(agent)
        assert not builder.has_section(TeamSectionName.HITT)
