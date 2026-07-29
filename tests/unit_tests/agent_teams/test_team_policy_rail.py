# coding: utf-8

"""Tests for TeamPolicyRail: static sections plus team-state delivery."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.prompts import (
    TeamSectionName,
    build_team_extra_section,
    build_team_identity_section,
    build_team_lifecycle_section,
    build_team_member_system_prompt,
    build_team_role_section,
    build_team_static_sections,
    build_team_workflow_section,
)
from openjiuwen.agent_teams.rails import TeamPolicyRail
from openjiuwen.agent_teams.rails.team_policy_rail import prepend_to_content
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import TEAM_CONTEXT_STATE_KEY
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from tests.test_logger import logger

# Session id the team-context tests bind their context to.
_SESSION_ID = "s1"


class _StubSession:
    """Session stand-in exposing the id + the per-member state bucket.

    The delivery baseline lives in this bucket in production, keyed by the
    member's ``agent_id``; here a plain dict is enough to prove the rail writes
    it and reads it back on a rebuild.
    """

    def __init__(self, session_id: str = _SESSION_ID) -> None:
        self._session_id = session_id
        self.state: dict = {}
        self.commits = 0

    def get_session_id(self) -> str:
        """Return the bound session id."""
        return self._session_id

    def get_state(self, key: str | None = None):
        """Read one key out of the session state."""
        if key is None:
            return dict(self.state)
        return self.state.get(key)

    def update_state(self, data: dict) -> None:
        """Shallow-merge into the session state."""
        self.state.update(data)

    async def commit(self) -> None:
        """Record that the state was flushed."""
        self.commits += 1


class _StubModelContext:
    """ModelContext stand-in holding a plain message list."""

    def __init__(self, messages: list | None = None) -> None:
        self.messages = list(messages or [])

    def get_messages(self, size: int | None = None, with_history: bool = True) -> list:
        """Return the live message list (same objects, as the real one does)."""
        return list(self.messages)

    async def add_messages(self, message) -> list:
        """Append one message or a list of them to the tail."""
        if isinstance(message, list):
            self.messages.extend(message)
        else:
            self.messages.append(message)
        return self.messages


class _StubContext:
    """Minimal AgentCallbackContext stand-in with a session + model context."""

    def __init__(
        self,
        session: _StubSession | None = None,
        messages: list | None = None,
    ) -> None:
        self.session = session if session is not None else _StubSession()
        self.context = _StubModelContext(messages)


def _team_texts(ctx: _StubContext) -> str:
    """Join every message body so tests can assert on delivered team state."""
    return "\n".join(str(message.content) for message in ctx.context.messages)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


class TestTeamRoleSection:
    @pytest.mark.level0
    def test_leader_role_section(self):
        section = build_team_role_section(
            role=TeamRole.LEADER,
            language="cn",
        )
        assert section is not None
        assert section.name == TeamSectionName.ROLE
        assert section.priority == 11

        content = section.render("cn")
        assert "# 团队角色" in content
        assert "create_task" in content  # from leader_policy.md

    @pytest.mark.level0
    def test_teammate_role_section(self):
        section = build_team_role_section(
            role=TeamRole.TEAMMATE,
            language="cn",
        )
        content = section.render("cn")
        assert "view_task" in content  # from teammate_policy.md

    @pytest.mark.level0
    def test_role_section_carries_no_member_name(self):
        # The member's own name is the only per-member value; it lives in the
        # identity content, so the role section stays byte-identical for every
        # member sharing a role (shared prompt-prefix cache).
        section = build_team_role_section(role=TeamRole.TEAMMATE, language="cn")
        content = section.render("cn")
        assert "你的 member_name" not in content


class TestTeamIdentitySection:
    @pytest.mark.level0
    def test_identity_section(self):
        section = build_team_identity_section(member_name="dev1", language="cn")
        assert section is not None
        assert section.name == TeamSectionName.IDENTITY
        assert section.priority == 10
        content = section.render("cn")
        assert "# 成员身份" in content
        assert "你的 member_name: dev1" in content

    @pytest.mark.level0
    def test_identity_section_without_any_member_content(self):
        assert build_team_identity_section(member_name=None, language="cn") is None


class TestTeamWorkflowSection:
    @pytest.mark.level0
    def test_leader_workflow(self):
        section = build_team_workflow_section(
            role=TeamRole.LEADER,
            team_mode="default",
            language="cn",
        )
        assert section is not None
        assert section.name == TeamSectionName.WORKFLOW
        assert section.priority == 13
        content = section.render("cn")
        assert "# 工作流程" in content
        assert "build_team" in content

    @pytest.mark.level0
    def test_leader_workflow_predefined(self):
        section = build_team_workflow_section(
            role=TeamRole.LEADER,
            team_mode="predefined",
            language="cn",
        )
        assert section is not None
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

    @pytest.mark.level0
    def test_teammate_returns_none(self):
        assert (
            build_team_workflow_section(
                role=TeamRole.TEAMMATE,
                team_mode="default",
                language="cn",
            )
            is None
        )


class TestTeamLifecycleSection:
    @pytest.mark.level0
    def test_leader_temporary(self):
        section = build_team_lifecycle_section(
            role=TeamRole.LEADER,
            lifecycle="temporary",
            language="cn",
        )
        assert section is not None
        assert section.name == TeamSectionName.LIFECYCLE
        assert section.priority == 14
        content = section.render("cn")
        assert "# 团队生命周期" in content
        assert "clean_team" in content

    @pytest.mark.level0
    def test_leader_persistent(self):
        section = build_team_lifecycle_section(
            role=TeamRole.LEADER,
            lifecycle="persistent",
            language="cn",
        )
        assert section is not None
        content = section.render("cn")
        assert "长期团队" in content

    @pytest.mark.level0
    def test_teammate_returns_none(self):
        assert (
            build_team_lifecycle_section(
                role=TeamRole.TEAMMATE,
                lifecycle="temporary",
                language="cn",
            )
            is None
        )


class TestTeamPrivatePromptInIdentity:
    """The private working agreement is a subsection of the identity content.

    It shares a lifecycle with ``member_name`` (fixed at spawn, constant after,
    different between members) and the same delivery lane, so it is one piece
    of content rather than two.
    """

    @pytest.mark.level0
    def test_private_prompt_nested_under_identity(self):
        section = build_team_identity_section(
            member_name="dev1",
            member_prompt="always write tests",
            language="cn",
        )
        assert section is not None
        content = section.render("cn")
        assert "# 成员身份" in content
        assert "你的 member_name: dev1" in content
        assert "## 私有工作约定" in content
        assert "always write tests" in content

    @pytest.mark.level0
    def test_empty_private_prompt_drops_only_that_subsection(self):
        section = build_team_identity_section(
            member_name="dev1",
            member_prompt="   ",
            language="cn",
        )
        assert section is not None
        content = section.render("cn")
        assert "你的 member_name: dev1" in content
        assert "## 私有工作约定" not in content


class TestTeamExtraSection:
    @pytest.mark.level0
    def test_with_base_prompt(self):
        section = build_team_extra_section(base_prompt="Be concise", language="cn")
        assert section is not None
        assert section.name == TeamSectionName.EXTRA
        assert section.priority == 17
        assert "Be concise" in section.render("cn")

    @pytest.mark.level0
    def test_empty_returns_none(self):
        assert build_team_extra_section(base_prompt=None, language="cn") is None
        assert build_team_extra_section(base_prompt="   ", language="cn") is None


# ---------------------------------------------------------------------------
# Rail
# ---------------------------------------------------------------------------


class _StubAgent:
    """Minimal stand-in exposing the shared system prompt builder."""

    def __init__(self, builder: SystemPromptBuilder) -> None:
        self.system_prompt_builder = builder


class _StubMember:
    """Lightweight stand-in for the SQLModel TeamMember row."""

    def __init__(self, member_name: str, display_name: str, desc: str = "", role: str = "teammate") -> None:
        self.member_name = member_name
        self.display_name = display_name
        self.desc = desc
        self.role = role


class _StubTeam:
    """Lightweight stand-in for the SQLModel Team row."""

    def __init__(self, team_name: str, display_name: str = "", desc: str = "") -> None:
        self.team_name = team_name
        self.display_name = display_name
        self.desc = desc


class _FakeTeamBackend:
    """In-memory TeamBackend that tracks call counts.

    Mirrors the four TeamBackend methods the team-context tracker consumes:
    ``get_team_updated_at``, ``get_members_max_updated_at``, ``get_team_info``,
    ``list_members``. Lets tests assert the probes short-circuit the expensive
    reads while nothing has changed. ``list_members`` excludes the caller, as
    the real backend does.
    """

    def __init__(
        self,
        team: _StubTeam | None = None,
        members: list[_StubMember] | None = None,
        team_mtime: int = 1,
        members_mtime: int = 1,
        hitt_enabled: bool = False,
        self_member_name: str | None = None,
    ) -> None:
        self._team = team
        self._members: list[_StubMember] = list(members or [])
        self._team_mtime = team_mtime
        self._members_mtime = members_mtime
        self._hitt_enabled = hitt_enabled
        self._self_member_name = self_member_name

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
        return [member for member in self._members if member.member_name != self._self_member_name]

    def hitt_enabled(self) -> bool:
        """The rail probes this at init to gate the static HITT contract."""
        return self._hitt_enabled

    # -- Mutators used by tests ----------------------------------------------

    def set_team(self, team: _StubTeam | None, mtime: int) -> None:
        self._team = team
        self._team_mtime = mtime

    def add_member(self, member: _StubMember, mtime: int) -> None:
        self._members.append(member)
        self._members_mtime = mtime

    def remove_member(self, member_name: str, mtime: int) -> None:
        self._members = [m for m in self._members if m.member_name != member_name]
        self._members_mtime = mtime


def _leader_rail(backend: _FakeTeamBackend | None = None, **overrides) -> TeamPolicyRail:
    """Build a leader rail with the defaults most tests want."""
    kwargs = {
        "role": TeamRole.LEADER,
        "member_prompt": "PM",
        "member_name": "leader1",
        "lifecycle": "temporary",
        "language": "cn",
        "team_backend": backend,
    }
    kwargs.update(overrides)
    return TeamPolicyRail(**kwargs)


class TestTeamPolicyRailStaticSections:
    """Static-only behaviour (team_backend is None): the rail still registers
    role / workflow / lifecycle / extra without touching the DB."""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_leader_rail_registers_static_sections_without_backend(self):
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)

        rail = _leader_rail(base_prompt="Stay sharp")
        rail.init(agent)
        await rail.before_model_call(_StubContext())

        sections = builder.get_all_sections()
        for name in (
            TeamSectionName.ROLE,
            TeamSectionName.WORKFLOW,
            TeamSectionName.LIFECYCLE,
            TeamSectionName.EXTRA,
        ):
            assert name in sections
        # The per-member content never enters the builder.
        assert TeamSectionName.IDENTITY not in sections

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
            base_prompt=None,
        )
        rail.init(agent)
        await rail.before_model_call(_StubContext())

        sections = builder.get_all_sections()
        assert TeamSectionName.WORKFLOW not in sections
        assert TeamSectionName.LIFECYCLE not in sections
        assert TeamSectionName.EXTRA not in sections
        assert TeamSectionName.ROLE in sections
        assert TeamSectionName.IDENTITY not in sections


class TestPrependToContent:
    """Message bodies come in two shapes and both have to accept a prefix."""

    @pytest.mark.level0
    def test_string_content(self):
        assert prepend_to_content("hello", "CTX") == "CTX\n\nhello"

    @pytest.mark.level0
    def test_empty_string_content(self):
        assert prepend_to_content("", "CTX") == "CTX"

    @pytest.mark.level0
    def test_list_content_with_leading_text_block(self):
        assert prepend_to_content(["hello", {"type": "image"}], "CTX") == ["CTX\n\nhello", {"type": "image"}]

    @pytest.mark.level0
    def test_list_content_with_leading_structured_block(self):
        blocks = [{"type": "image"}, "hello"]
        assert prepend_to_content(blocks, "CTX") == ["CTX", {"type": "image"}, "hello"]

    @pytest.mark.level0
    def test_original_list_is_not_mutated(self):
        blocks = ["hello"]
        prepend_to_content(blocks, "CTX")
        assert blocks == ["hello"]


class TestTeamPolicyRailTeamContext:
    """Team state is written into the conversation, not the system prompt.

    It goes in at the model call where it first appears, into the newest
    segment of the conversation only, and never gets rewritten afterwards.
    """

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_state_goes_into_the_user_message_not_the_prompt(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test team"),
            members=[_StubMember("dev1", "Dev", "Coder")],
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend)
        rail.init(agent)

        ctx = _StubContext(messages=[UserMessage(content="ship it")])
        await rail.before_model_call(ctx)

        # Prepended into the existing user message; no new message appeared.
        assert len(ctx.context.messages) == 1
        body = ctx.context.messages[0].content
        assert body.endswith("ship it")
        assert "<team-context>" in body
        assert "你的 member_name: leader1" in body
        assert "# 团队信息" in body
        assert '<team-event kind="roster">' in body
        assert "member_name=dev1" in body

        # And none of it leaked into the cache-stable system prompt.
        prompt = builder.build()
        assert "# 团队信息" not in prompt
        assert "# 成员关系" not in prompt
        assert "你的 member_name" not in prompt
        assert "PM" not in prompt
        logger.info("Team state delivered inside the round's user message")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_appends_a_user_message_when_segment_has_none(self):
        """Mid tool-loop the new messages are assistant / tool results only."""
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test team"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend)
        rail.init(agent)

        ctx = _StubContext(messages=[UserMessage(content="ship it")])
        # First call consumes the pending state and records the boundary.
        await rail.before_model_call(ctx)
        first_body = ctx.context.messages[0].content

        # A tool round happens, then the team changes mid-loop.
        ctx.context.messages.append(AssistantMessage(content="calling a tool"))
        ctx.context.messages.append(ToolMessage(content="tool output", tool_call_id="c1"))
        backend.add_member(_StubMember("dev2", "Newbie"), mtime=2)

        await rail.before_model_call(ctx)

        assert len(ctx.context.messages) == 4
        tail = ctx.context.messages[-1]
        assert tail.role == "user"
        assert '<team-event kind="roster-change">' in tail.content
        assert "Newbie" in tail.content
        # The earlier messages were left exactly as they were.
        assert ctx.context.messages[0].content == first_body
        assert ctx.context.messages[2].content == "tool output"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_leader_says_nothing_before_the_team_exists(self):
        """A leader has no team on its first call; there is nothing to announce."""
        backend = _FakeTeamBackend(team=None, members=[], team_mtime=0, members_mtime=0)
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend, member_prompt="", member_name=None)
        rail.init(agent)

        ctx = _StubContext(messages=[UserMessage(content="build me a team")])
        await rail.before_model_call(ctx)
        assert ctx.context.messages[0].content == "build me a team"

        # build_team runs: the team row and its first member appear.
        backend.set_team(_StubTeam("Beta", "Test team"), mtime=7)
        backend.add_member(_StubMember("dev1", "Dev"), mtime=7)
        await rail.before_model_call(ctx)

        body = _team_texts(ctx)
        assert "# 团队信息" in body
        assert '<team-event kind="roster">' in body
        assert "member_name=dev1" in body

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_unchanged_probes_deliver_nothing(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend)
        rail.init(agent)

        ctx = _StubContext(messages=[UserMessage(content="go")])
        await rail.before_model_call(ctx)
        after_first = list(ctx.context.messages)
        first_body = after_first[0].content

        await rail.before_model_call(ctx)
        await rail.before_model_call(ctx)

        # Three calls, three probes each, one expensive read each.
        assert backend.team_mtime_calls == 3
        assert backend.members_mtime_calls == 3
        assert backend.get_info_calls == 1
        assert backend.list_members_calls == 1
        # And nothing was added or rewritten after the first call.
        assert len(ctx.context.messages) == 1
        assert ctx.context.messages[0].content == first_body

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_roster_change_is_a_delta_and_leaves_history_alone(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend)
        rail.init(agent)

        ctx = _StubContext(messages=[UserMessage(content="go")])
        await rail.before_model_call(ctx)
        snapshot_body = ctx.context.messages[0].content
        assert '<team-event kind="roster">' in snapshot_body

        backend.add_member(_StubMember("dev2", "Newbie", "fresh"), mtime=2)
        backend.remove_member("dev1", mtime=3)
        ctx.context.messages.append(UserMessage(content="next"))
        await rail.before_model_call(ctx)

        delta_body = ctx.context.messages[1].content
        assert '<team-event kind="roster-change">' in delta_body
        assert "[加入] member_name=dev2" in delta_body
        assert "[退出] member_name=dev1" in delta_body
        # Only the delta — the full roster is not resent.
        assert '<team-event kind="roster">' not in delta_body
        # The first message keeps its original body verbatim.
        assert ctx.context.messages[0].content == snapshot_body

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_roster_messages_carry_the_announcement_note(self):
        """Without it members greet every new peer and burn a round each way."""
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend)
        rail.init(agent)

        ctx = _StubContext(messages=[UserMessage(content="go")])
        await rail.before_model_call(ctx)
        snapshot_body = ctx.context.messages[0].content
        assert '<team-note kind="announcement-only">' in snapshot_body
        assert "不要" in snapshot_body

        backend.add_member(_StubMember("dev2", "Newbie"), mtime=2)
        ctx.context.messages.append(UserMessage(content="next"))
        await rail.before_model_call(ctx)
        delta_body = ctx.context.messages[1].content
        assert '<team-note kind="announcement-only">' in delta_body
        assert "不要" in delta_body

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_rebuilt_rail_does_not_resend(self):
        """The rail is rebuilt every round, so the baseline must be persisted.

        Same session, brand-new rail: nothing may be announced again. Clearing
        the persisted baseline is what makes it start over.
        """
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        session = _StubSession()

        rail = _leader_rail(backend)
        rail.init(agent)
        ctx = _StubContext(session=session, messages=[UserMessage(content="go")])
        await rail.before_model_call(ctx)
        assert "<team-context>" in ctx.context.messages[0].content
        assert session.state[TEAM_CONTEXT_STATE_KEY]["identity_emitted"] is True
        assert session.commits >= 1

        rebuilt = _leader_rail(backend)
        rebuilt.init(_StubAgent(SystemPromptBuilder(language="cn")))
        next_ctx = _StubContext(session=session, messages=[UserMessage(content="second round")])
        await rebuilt.before_model_call(next_ctx)
        assert next_ctx.context.messages[0].content == "second round"

        # Losing the baseline (a fresh session) starts the announcements over.
        fresh = _leader_rail(backend)
        fresh.init(_StubAgent(SystemPromptBuilder(language="cn")))
        fresh_ctx = _StubContext(session=_StubSession("s2"), messages=[UserMessage(content="third")])
        await fresh.before_model_call(fresh_ctx)
        assert "<team-context>" in fresh_ctx.context.messages[0].content

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_restored_history_is_never_rewritten(self):
        """A resumed member must not have its old messages edited in place."""
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend)
        rail.init(agent)

        restored = [
            UserMessage(content="old query"),
            AssistantMessage(content="old answer"),
            UserMessage(content="current round"),
        ]
        ctx = _StubContext(messages=restored)
        await rail.before_model_call(ctx)

        assert ctx.context.messages[0].content == "old query"
        assert ctx.context.messages[1].content == "old answer"
        assert "<team-context>" in ctx.context.messages[2].content
        assert ctx.context.messages[2].content.endswith("current round")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_team_workspace_paths_ride_the_team_info_block(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(
            backend,
            team_workspace_mount=".team/beta/",
            team_workspace_path="/abs/team-workspace",
        )
        rail.init(agent)

        ctx = _StubContext(messages=[UserMessage(content="go")])
        await rail.before_model_call(ctx)
        body = ctx.context.messages[0].content
        assert "`.team/beta/`" in body
        assert "/abs/team-workspace" in body

        # A renamed team is announced again rather than rewritten in place.
        backend.set_team(_StubTeam("Beta-renamed", "Test"), mtime=99)
        ctx.context.messages.append(UserMessage(content="next"))
        await rail.before_model_call(ctx)
        second = ctx.context.messages[1].content
        assert "Beta-renamed" in second
        assert "`.team/beta/`" in second

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_static_sections_stay_in_the_builder(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("T1", "D"),
            members=[_StubMember("dev1", "D")],
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend, base_prompt=None)
        rail.init(agent)
        await rail.before_model_call(_StubContext(messages=[UserMessage(content="go")]))

        prompt = builder.build()
        idx_role = prompt.index("# 团队角色")
        idx_workflow = prompt.index("# 工作流程")
        idx_lifecycle = prompt.index("# 团队生命周期")
        assert idx_role < idx_workflow < idx_lifecycle

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_uninit_removes_static_sections(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("T", "D"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend)
        rail.init(agent)
        await rail.before_model_call(_StubContext(messages=[UserMessage(content="go")]))
        assert builder.has_section(TeamSectionName.ROLE)

        rail.uninit(agent)
        for name in (
            TeamSectionName.ROLE,
            TeamSectionName.WORKFLOW,
            TeamSectionName.LIFECYCLE,
            TeamSectionName.EXTRA,
        ):
            assert not builder.has_section(name)


class TestTeamPolicyRailHitt:
    """HITT contract is a static builder section gated on ``hitt_enabled``; the
    human roster is folded into the roster message as a ``[human]`` tag.

    The tag is gated on the viewer: LEADER / HUMAN_AGENT always, TEAMMATE only
    when ``expose_human_agents_to_teammates`` is set (F_18 privacy default).
    """

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_contract_in_builder_and_human_tagged_in_roster(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("alice", "Alice", role="human_agent")],
            hitt_enabled=True,
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend)
        rail.init(agent)
        ctx = _StubContext(messages=[UserMessage(content="go")])
        await rail.before_model_call(ctx)

        assert builder.has_section(TeamSectionName.HITT)
        assert "禁止" in builder.get_section(TeamSectionName.HITT).render("cn")
        body = ctx.context.messages[0].content
        assert "member_name=alice" in body
        assert "[human]" in body
        logger.info("HITT contract in builder; human tagged in the roster message")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_no_hitt_contract_when_disabled(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            hitt_enabled=False,
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend, member_prompt="")
        rail.init(agent)
        await rail.before_model_call(_StubContext(messages=[UserMessage(content="go")]))
        assert not builder.has_section(TeamSectionName.HITT)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_teammate_default_hides_human_tag(self):
        """Default teammate (expose=False) sees no ``[human]`` tag (F_18)."""
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("alice", "Alice", role="human_agent")],
            hitt_enabled=True,
            self_member_name="dev1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = TeamPolicyRail(
            role=TeamRole.TEAMMATE,
            member_name="dev1",
            language="cn",
            team_backend=backend,
        )
        rail.init(agent)
        ctx = _StubContext(messages=[UserMessage(content="go")])
        await rail.before_model_call(ctx)
        body = ctx.context.messages[0].content
        assert "member_name=alice" in body
        assert "[human]" not in body

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_teammate_expose_shows_human_tag(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("alice", "Alice", role="human_agent")],
            hitt_enabled=True,
            self_member_name="dev1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = TeamPolicyRail(
            role=TeamRole.TEAMMATE,
            member_name="dev1",
            language="cn",
            team_backend=backend,
            expose_human_agents_to_teammates=True,
        )
        rail.init(agent)
        ctx = _StubContext(messages=[UserMessage(content="go")])
        await rail.before_model_call(ctx)
        assert "[human]" in ctx.context.messages[0].content

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_uninit_strips_hitt_contract_from_builder(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[],
            hitt_enabled=True,
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend, member_prompt="")
        rail.init(agent)
        await rail.before_model_call(_StubContext(messages=[UserMessage(content="go")]))
        assert builder.has_section(TeamSectionName.HITT)
        rail.uninit(agent)
        assert not builder.has_section(TeamSectionName.HITT)


class TestTagNoticeInclusion:
    """Every member — in-process or external CLI — reads the same XML tags.

    Team state now travels as ``<team-context>`` / ``<team-event>`` inside the
    conversation for both, so the notice is unconditional and there is no
    separate attachment notice left to gate.
    """

    @pytest.mark.level1
    def test_static_sections_always_include_inbound_tags(self):
        secs = build_team_static_sections(role=TeamRole.LEADER, member_name="l", language="cn")
        names = {s.name for s in secs}
        assert TeamSectionName.INBOUND_TAGS in names

    @pytest.mark.level1
    def test_inbound_tags_document_every_team_state_tag(self):
        # Every tag the member can receive must be named in the notice,
        # otherwise the LLM meets an XML element nothing introduced.
        secs = build_team_static_sections(role=TeamRole.LEADER, member_name="l", language="cn")
        section = next(s for s in secs if s.name == TeamSectionName.INBOUND_TAGS)
        for language in ("cn", "en"):
            content = section.render(language)
            assert "<team-context>" in content
            assert "roster-change" in content

    @pytest.mark.level1
    def test_external_cli_prompt_has_inbound_tags(self):
        prompt = build_team_member_system_prompt(role=TeamRole.LEADER, member_name="l", language="cn")
        assert "team-inbound" in prompt
        assert "prompt-attachment" not in prompt

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_rail_static_sections_include_the_notice(self):
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = TeamPolicyRail(role=TeamRole.LEADER, member_name="l", language="cn")
        rail.init(agent)
        await rail.before_model_call(_StubContext())
        assert TeamSectionName.INBOUND_TAGS in builder.get_all_sections()


class TestMemberSpecificInclusion:
    """The per-member section is inlined only for external CLI members.

    ``team_identity`` (member_name + private working agreement) differs between
    members, so in-process members receive it as a conversation message and the
    whole team shares one cacheable system-prompt prefix. An external CLI prompt
    is a standalone per-member snapshot with no conversation at launch, so it
    inlines it.
    """

    @pytest.mark.level1
    def test_static_sections_omit_member_specific_by_default(self):
        secs = build_team_static_sections(
            role=TeamRole.TEAMMATE,
            member_name="dev1",
            member_prompt="ship small PRs",
            language="cn",
        )
        names = {s.name for s in secs}
        assert TeamSectionName.IDENTITY not in names

    @pytest.mark.level1
    def test_static_sections_include_member_specific_when_flagged(self):
        secs = build_team_static_sections(
            role=TeamRole.TEAMMATE,
            member_name="dev1",
            member_prompt="ship small PRs",
            language="cn",
            include_member_specific=True,
        )
        names = {s.name for s in secs}
        assert TeamSectionName.IDENTITY in names

    @pytest.mark.level1
    def test_external_cli_prompt_inlines_member_specific(self):
        prompt = build_team_member_system_prompt(
            role=TeamRole.TEAMMATE,
            member_name="dev1",
            member_prompt="ship small PRs",
            language="cn",
        )
        assert "你的 member_name: dev1" in prompt
        assert "ship small PRs" in prompt
