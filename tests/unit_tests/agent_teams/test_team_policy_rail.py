# coding: utf-8

"""Tests for TeamPolicyRail: static sections plus team-state delivery."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.prompts import (
    TeamSectionName,
    build_leader_policy_disclosure,
    build_team_extra_section,
    build_team_identity_section,
    build_team_lifecycle_section,
    build_team_member_system_prompt,
    build_team_role_section,
    build_team_static_sections,
    build_team_task_state_section,
    build_team_workflow_section,
    load_template,
)
from openjiuwen.agent_teams.inbound_render import render_event
from openjiuwen.agent_teams.rails import TeamPolicyRail
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import TEAM_CONTEXT_STATE_KEY
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.single_agent.rail.base import SteeringDrainInputs, UserMessageInputs
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
        self.inputs = None


async def _admit(
    rail: TeamPolicyRail,
    ctx: _StubContext,
    *parts: str,
    source: str = "query",
    prefix: str = "",
):
    """Admit one batch of inputs the way ``ReActAgent._admit_user_message`` does.

    Rails see the queued inputs as a mutable list before they are joined, so
    they may drop or prepend entries; only what survives becomes the message
    that joins the conversation.
    """
    batch = list(parts)
    ctx.inputs = UserMessageInputs(parts=batch, source=source)
    await rail.on_user_message(ctx)
    ctx.inputs = None
    body = "\n".join(batch)
    message = UserMessage(content=f"{prefix}{body}")
    ctx.context.messages.append(message)
    return message


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


class TestTeamTaskStateSection:
    """The task state machine has one template per dispatch mode (F_76).

    The two modes differ on more than wording: who drives
    ``pending -> in_progress``, and whether a verify gate exists at all.
    """

    @pytest.mark.level0
    def test_autonomous_state_machine(self):
        section = build_team_task_state_section(
            role=TeamRole.LEADER,
            dispatch_mode="autonomous",
            language="cn",
        )
        assert section is not None
        assert section.name == TeamSectionName.TASK_STATE
        assert section.priority == 16
        content = section.render("cn")
        assert "# 任务状态流转" in content
        assert "自主认领" in content

    @pytest.mark.level0
    def test_scheduled_state_machine(self):
        section = build_team_task_state_section(
            role=TeamRole.LEADER,
            dispatch_mode="scheduled",
            language="cn",
        )
        assert section is not None
        content = section.render("cn")
        assert "调度指派模式" in content
        assert "in_review" in content

    @pytest.mark.level1
    def test_autonomous_never_mentions_the_verify_gate(self):
        """The regression guard for the mode-neutral wording of 1208ed1d.

        An autonomous ``create_task`` has no ``reviewer`` parameter, and the
        mode has no scheduling runtime to summon reviewers — a task pushed into
        ``in_review`` there stalls forever. The template therefore does not
        *describe* the gate, not even to warn about it: naming a capability
        that does not exist is what makes a model reach for it. Same rule as
        the fork section vanishing when ``enable_fork`` is off.
        """
        for language in ("cn", "en"):
            autonomous = build_team_task_state_section(
                role=TeamRole.LEADER,
                dispatch_mode="autonomous",
                language=language,
            ).render(language)
            scheduled = build_team_task_state_section(
                role=TeamRole.LEADER,
                dispatch_mode="scheduled",
                language=language,
            ).render(language)

            for absent in ("reviewer", "in_review", "verify_task", "验证"):
                assert absent not in autonomous, f"{language}: autonomous leaks {absent!r}"
            # Scheduled keeps the whole gate.
            assert "create_task(reviewer" in scheduled
            assert "in_review" in scheduled

    @pytest.mark.level1
    def test_leader_policy_carries_no_state_machine(self):
        """The section moved out of leader_policy — it must not linger there."""
        for language in ("cn", "en"):
            policy = load_template("leader_policy", language).content
            assert "in_review" not in policy
            assert "reviewer" not in policy

    @pytest.mark.level0
    def test_teammate_returns_none(self):
        assert (
            build_team_task_state_section(
                role=TeamRole.TEAMMATE,
                dispatch_mode="scheduled",
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
        self_row: _StubMember | None = None,
    ) -> None:
        self._team = team
        self._members: list[_StubMember] = list(members or [])
        # The member's own row, which ``list_members`` excludes. Defaults to a
        # plain row so tests that do not care about identity still get one; a
        # leader before ``build_team`` passes ``self_row=None`` to model "my row
        # does not exist yet".
        if self_row is None and self_member_name is not None:
            self_row = _StubMember(self_member_name, self_member_name)
        self._self_row = self_row
        self._team_mtime = team_mtime
        self._members_mtime = members_mtime
        self._hitt_enabled = hitt_enabled
        self._fork_enabled = False
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

    async def get_member(self, member_name: str):
        """Return this member's own row; None until it has been registered."""
        if self._self_row is not None and self._self_row.member_name == member_name:
            return self._self_row
        return next((m for m in self._members if m.member_name == member_name), None)

    def hitt_enabled(self) -> bool:
        """The rail probes this at init to gate the static HITT contract."""
        return self._hitt_enabled

    def fork_enabled(self) -> bool:
        """Whether the team's fork capability is on (gates the identity block)."""
        return self._fork_enabled

    # -- Mutators used by tests ----------------------------------------------

    def set_fork_enabled(self, enabled: bool) -> None:
        self._fork_enabled = enabled

    def set_team(self, team: _StubTeam | None, mtime: int) -> None:
        self._team = team
        self._team_mtime = mtime

    def add_member(self, member: _StubMember, mtime: int) -> None:
        self._members.append(member)
        self._members_mtime = mtime

    def register_self(self, member: _StubMember, mtime: int) -> None:
        """Write the member's own row, as ``build_team`` / spawn does."""
        self._self_row = member
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
    """Static-only behaviour (team_backend is None): the rail registers the
    leader's bootstrap + extra, and the full static set for everyone else."""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_leader_rail_registers_only_bootstrap_and_extra(self):
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)

        rail = _leader_rail(base_prompt="Stay sharp")
        rail.init(agent)
        await rail.before_model_call(_StubContext())

        sections = builder.get_all_sections()
        # F_76: the leader's prefix is the routing bootstrap plus the caller's
        # own instructions. Every collaboration convention is disclosed by the
        # build_team result instead.
        assert set(sections) == {TeamSectionName.BOOTSTRAP, TeamSectionName.EXTRA}
        for name in (
            TeamSectionName.ROLE,
            TeamSectionName.WORKFLOW,
            TeamSectionName.LIFECYCLE,
            TeamSectionName.DISPATCH,
            TeamSectionName.INBOUND_TAGS,
            # The per-member content never enters the builder.
            TeamSectionName.IDENTITY,
        ):
            assert name not in sections

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_leader_rail_hides_swarmflow_unless_the_tool_is_wired(self):
        # The rail takes the same signal the tool factory gates the swarmflow
        # tool on, so a leader without the tool never reads about it.
        for swarmflow_enabled in (False, True):
            builder = SystemPromptBuilder(language="cn")
            agent = _StubAgent(builder)

            rail = _leader_rail(swarmflow_enabled=swarmflow_enabled)
            rail.init(agent)
            await rail.before_model_call(_StubContext())

            bootstrap = builder.get_all_sections()[TeamSectionName.BOOTSTRAP].render("cn")
            assert ("swarmflow" in bootstrap.lower()) is swarmflow_enabled

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


class TestTeamPolicyRailTeamContext:
    """Team state is delivered into the conversation, not the system prompt.

    Two lanes, and neither ever rewrites a message that is already history:
    state normally rides the input being admitted (``on_user_message``), and
    when it appears mid tool-loop with no input to ride it is appended at the
    tail (``before_model_call``).
    """

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_state_rides_the_input_not_the_prompt(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test team"),
            members=[_StubMember("dev1", "Dev", "Coder")],
            self_member_name="leader1",
        )
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend)
        rail.init(agent)

        ctx = _StubContext()
        message = await _admit(rail, ctx, "ship it")

        # Prepended into the input itself; no extra message appeared.
        assert len(ctx.context.messages) == 1
        assert message.content.endswith("ship it")
        assert "<team-context>" in message.content
        assert "你的 member_name: leader1" in message.content
        assert "# 团队信息" in message.content
        assert '<team-event kind="roster">' in message.content
        assert "member_name=dev1" in message.content

        # And none of it leaked into the cache-stable system prompt.
        await rail.before_model_call(ctx)
        prompt = builder.build()
        assert "# 团队信息" not in prompt
        assert "# 成员关系" not in prompt
        assert "你的 member_name" not in prompt
        assert "PM" not in prompt
        logger.info("Team state delivered on the admitted input")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_only_the_first_input_carries_it(self):
        """State is consumed by whichever input is admitted first."""
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        rail = _leader_rail(backend)
        rail.init(_StubAgent(SystemPromptBuilder(language="cn")))

        ctx = _StubContext()
        first = await _admit(rail, ctx, "<team-inbound>报数任务已发布</team-inbound>")
        second = await _admit(rail, ctx, "[STEERING] task board", source="steering")

        assert "<team-context>" in first.content
        assert first.content.endswith("<team-inbound>报数任务已发布</team-inbound>")
        assert "<team-context>" not in second.content
        assert second.content == "[STEERING] task board"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_state_appearing_mid_tool_loop_is_appended(self):
        """A leader builds its team mid-round; the next input may be far away."""
        backend = _FakeTeamBackend(team=None, members=[], team_mtime=0, members_mtime=0)
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(backend, member_prompt="")
        rail.init(agent)

        ctx = _StubContext()
        query = await _admit(rail, ctx, "build me a team")
        await rail.before_model_call(ctx)
        # Nothing exists yet, so the input was left alone.
        assert query.content == "build me a team"
        assert len(ctx.context.messages) == 1

        # The round runs build_team through a tool call.
        ctx.context.messages.append(AssistantMessage(content="calling build_team"))
        ctx.context.messages.append(ToolMessage(content="Team created", tool_call_id="c1"))
        backend.set_team(_StubTeam("Beta", "报数小队", "count off"), mtime=7)
        backend.register_self(_StubMember("leader1", "队长"), mtime=7)
        await rail.before_model_call(ctx)

        assert len(ctx.context.messages) == 4
        tail = ctx.context.messages[-1]
        assert tail.role == "user"
        assert tail.content.count("<team-context>") == 1
        assert "你的 member_name: leader1" in tail.content
        assert "你的 display_name: 队长" in tail.content
        assert "# 团队信息" in tail.content
        assert "报数小队" in tail.content
        # Everything that was already history is untouched.
        assert ctx.context.messages[0].content == "build me a team"
        assert ctx.context.messages[2].content == "Team created"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_workspace_paths_alone_do_not_announce_a_team(self):
        """The workspace paths must not fabricate a team-info block pre-build.

        They are constructor arguments and always available, so an ungated
        team-info block renders with nothing but the workspace in it — and the
        real one follows moments later once ``build_team`` runs.
        """
        backend = _FakeTeamBackend(team=None, members=[], team_mtime=0, members_mtime=0)
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = _leader_rail(
            backend,
            member_prompt="",
            team_workspace_mount=".team/beta/",
            team_workspace_path="/abs/team-workspace",
        )
        rail.init(agent)

        ctx = _StubContext()
        query = await _admit(rail, ctx, "build me a team")
        assert query.content == "build me a team"

        backend.set_team(_StubTeam("Beta", "报数小队", "count off"), mtime=7)
        backend.register_self(_StubMember("leader1", "队长"), mtime=7)
        second = await _admit(rail, ctx, "go")

        assert second.count("<team-context>") == 1 if isinstance(second, str) else True
        assert second.content.count("<team-context>") == 1
        assert "# 团队信息" in second.content
        assert "`.team/beta/`" in second.content
        assert _team_texts(ctx).count("# 团队信息") == 1

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_identity_and_team_info_share_one_block(self):
        """Both are standing facts; two adjacent <team-context> say one thing twice."""
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "报数小队", "count off"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        rail = _leader_rail(backend)
        rail.init(_StubAgent(SystemPromptBuilder(language="cn")))

        ctx = _StubContext()
        message = await _admit(rail, ctx, "go")

        assert message.content.count("<team-context>") == 1
        assert "# 成员身份" in message.content
        assert "# 团队信息" in message.content

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_identity_carries_both_names_from_the_db_row(self):
        """Peers list members by both names, and the row is what they see."""
        backend = _FakeTeamBackend(
            self_member_name="dev1",
            self_row=_StubMember("dev1", "成员一"),
        )
        rail = TeamPolicyRail(
            role=TeamRole.TEAMMATE,
            member_name="dev1",
            display_name="spec-time default",
            member_workspace_path="/ws/dev1",
            member_prompt="报数要快",
            language="cn",
            team_backend=backend,
        )
        rail.init(_StubAgent(SystemPromptBuilder(language="cn")))

        ctx = _StubContext()
        message = await _admit(rail, ctx, "go")

        assert "你的 member_name: dev1" in message.content
        assert "你的 display_name: 成员一" in message.content
        assert "spec-time default" not in message.content
        assert "你的私有工作区: `/ws/dev1`" in message.content
        assert "## 私有工作约定" in message.content
        assert "报数要快" in message.content

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_unchanged_probes_deliver_nothing(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        rail = _leader_rail(backend)
        rail.init(_StubAgent(SystemPromptBuilder(language="cn")))

        ctx = _StubContext()
        await _admit(rail, ctx, "go")
        second = await _admit(rail, ctx, "still going")
        await rail.before_model_call(ctx)

        assert backend.get_info_calls == 1
        assert backend.list_members_calls == 1
        assert second.content == "still going"
        assert len(ctx.context.messages) == 2

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_roster_change_is_a_delta_and_leaves_history_alone(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        rail = _leader_rail(backend)
        rail.init(_StubAgent(SystemPromptBuilder(language="cn")))

        ctx = _StubContext()
        snapshot = await _admit(rail, ctx, "go")
        snapshot_body = snapshot.content
        assert '<team-event kind="roster">' in snapshot_body

        backend.add_member(_StubMember("dev2", "Newbie", "fresh"), mtime=2)
        backend.remove_member("dev1", mtime=3)
        delta = await _admit(rail, ctx, "next")

        assert '<team-event kind="roster-change">' in delta.content
        assert "[加入] member_name=dev2" in delta.content
        assert "[退出] member_name=dev1" in delta.content
        assert '<team-event kind="roster">' not in delta.content
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
        rail = _leader_rail(backend)
        rail.init(_StubAgent(SystemPromptBuilder(language="cn")))

        ctx = _StubContext()
        snapshot = await _admit(rail, ctx, "go")
        assert '<team-note kind="announcement-only">' in snapshot.content
        assert "不要" in snapshot.content
        # The note belongs to the roster event, so it is nested inside it.
        assert snapshot.content.index("<team-note") < snapshot.content.index("</team-event>")

        backend.add_member(_StubMember("dev2", "Newbie"), mtime=2)
        delta = await _admit(rail, ctx, "next")
        assert '<team-note kind="announcement-only">' in delta.content
        assert "不要" in delta.content
        assert delta.content.index("<team-note") < delta.content.index("</team-event>")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_rebuilt_rail_does_not_resend(self):
        """The rail is rebuilt every round, so the baseline must be persisted."""
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        session = _StubSession()

        rail = _leader_rail(backend)
        rail.init(_StubAgent(SystemPromptBuilder(language="cn")))
        ctx = _StubContext(session=session)
        first = await _admit(rail, ctx, "go")
        assert "<team-context>" in first.content
        assert session.state[TEAM_CONTEXT_STATE_KEY]["identity_emitted"] is True
        assert session.commits >= 1

        rebuilt = _leader_rail(backend)
        rebuilt.init(_StubAgent(SystemPromptBuilder(language="cn")))
        next_ctx = _StubContext(session=session)
        second = await _admit(rebuilt, next_ctx, "second round")
        assert second.content == "second round"

        # Losing the baseline (a fresh session) starts the announcements over.
        fresh = _leader_rail(backend)
        fresh.init(_StubAgent(SystemPromptBuilder(language="cn")))
        fresh_ctx = _StubContext(session=_StubSession("s2"))
        third = await _admit(fresh, fresh_ctx, "third")
        assert "<team-context>" in third.content

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_team_workspace_paths_ride_the_team_info_block(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[_StubMember("dev1", "Dev")],
            self_member_name="leader1",
        )
        rail = _leader_rail(
            backend,
            team_workspace_mount=".team/beta/",
            team_workspace_path="/abs/team-workspace",
        )
        rail.init(_StubAgent(SystemPromptBuilder(language="cn")))

        ctx = _StubContext()
        first = await _admit(rail, ctx, "go")
        assert "`.team/beta/`" in first.content
        assert "/abs/team-workspace" in first.content

        # A renamed team is announced again rather than rewritten in place.
        backend.set_team(_StubTeam("Beta-renamed", "Test"), mtime=99)
        second = await _admit(rail, ctx, "next")
        assert "Beta-renamed" in second.content
        assert "`.team/beta/`" in second.content

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
        rail = _leader_rail(backend, base_prompt="Stay sharp")
        rail.init(agent)
        await rail.before_model_call(_StubContext())

        # The leader's prefix is bootstrap-then-extra; the priority ordering
        # that used to be visible across role/workflow/lifecycle now belongs to
        # the disclosure (asserted below).
        prompt = builder.build()
        assert prompt.index("TeamLeader") < prompt.index("Stay sharp")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_disclosure_keeps_the_section_ordering(self):
        # The build_team result reuses the same priority-ordered assembly the
        # system prompt used to have, so the leader reads them in one order.
        policy = build_leader_policy_disclosure(language="cn")
        idx_role = policy.index("# 团队角色")
        idx_workflow = policy.index("# 工作流程")
        idx_lifecycle = policy.index("# 团队生命周期")
        idx_dispatch = policy.index("# 任务下发与获取")
        assert idx_role < idx_workflow < idx_lifecycle < idx_dispatch

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
        rail = _leader_rail(backend, base_prompt="Stay sharp")
        rail.init(agent)
        await rail.before_model_call(_StubContext())
        assert builder.has_section(TeamSectionName.BOOTSTRAP)

        rail.uninit(agent)
        for name in (TeamSectionName.BOOTSTRAP, TeamSectionName.EXTRA):
            assert not builder.has_section(name)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_fork_off_keeps_identity_output_byte_identical(self):
        """enable_fork=False must not change the identity render path at all."""
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test team"),
            members=[_StubMember("dev1", "Dev", "Coder")],
            self_member_name="leader1",
        )
        # Pre-fork reference: same member, fork capability off by default.
        rail = _leader_rail(backend)
        rail.init(_StubAgent(SystemPromptBuilder(language="cn")))
        ctx = _StubContext()
        message = await _admit(rail, ctx, "work")
        assert "<team-context>" in message.content
        assert "<identity>" not in message.content
        assert "身份转换能力" not in message.content
        assert "你的 member_name: leader1" in message.content

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_fork_on_wraps_identity_and_skips_conversion_for_plain_spawn(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test team"),
            members=[_StubMember("dev1", "Dev", "Coder")],
            self_member_name="leader1",
        )
        backend.set_fork_enabled(True)
        rail = _leader_rail(backend, fork_source=None)
        rail.init(_StubAgent(SystemPromptBuilder(language="cn")))
        ctx = _StubContext()
        message = await _admit(rail, ctx, "work")
        assert "<team-context>" in message.content
        assert "<identity>" in message.content
        assert "身份转换能力" in message.content
        # Plain spawn (no fork_source): capability statement present, but no
        # conversion notice.
        assert "<identity-conversion>" not in message.content

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_fork_source_renders_conversion_notice_in_identity(self):
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test team"),
            members=[_StubMember("dev1", "Dev", "Coder")],
            self_member_name="leader1",
        )
        backend.set_fork_enabled(True)
        rail = _leader_rail(backend, fork_source="reader")
        rail.init(_StubAgent(SystemPromptBuilder(language="cn")))
        ctx = _StubContext()
        message = await _admit(rail, ctx, "work")
        assert "<identity-conversion>" in message.content
        assert "reader" in message.content
        assert "不再适用" in message.content

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_fork_on_team_info_only_update_renders_no_empty_identity(self):
        """A team-info change after identity was emitted must not create an
        empty <identity> block (or a duplicated conversion notice)."""
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test team"),
            members=[_StubMember("dev1", "Dev", "Coder")],
            self_member_name="leader1",
        )
        backend.set_fork_enabled(True)
        rail = _leader_rail(backend, fork_source="reader")
        rail.init(_StubAgent(SystemPromptBuilder(language="cn")))
        ctx = _StubContext()

        first = await _admit(rail, ctx, "work")
        assert "<identity>" in first.content
        assert "<identity-conversion>" in first.content

        # Identity is already emitted; only the team row changes.
        backend.set_team(_StubTeam("Beta-renamed", "Test"), mtime=99)
        second = await _admit(rail, ctx, "next")
        assert "Beta-renamed" in second.content
        assert "<identity>" not in second.content
        assert "<identity-conversion>" not in second.content



class TestTeamPolicyRailHitt:
    """HITT contract is a static builder section gated on ``hitt_enabled``; the
    human roster is folded into the roster message as a ``[human]`` tag.

    The tag is gated on the viewer: LEADER / HUMAN_AGENT always, TEAMMATE only
    when ``expose_human_agents_to_teammates`` is set (F_18 privacy default).
    """

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_contract_disclosed_to_leader_and_human_tagged_in_roster(self):
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
        ctx = _StubContext()
        message = await _admit(rail, ctx, "go")
        await rail.before_model_call(ctx)

        # F_76: the leader's HITT contract rides the build_team disclosure —
        # that call is also what settles the effective enable_hitt value, so
        # the contract and the capability now appear together.
        assert not builder.has_section(TeamSectionName.HITT)
        disclosure = build_leader_policy_disclosure(language="cn", hitt_enabled=True)
        assert "禁止" in disclosure
        assert disclosure != build_leader_policy_disclosure(language="cn", hitt_enabled=False)
        # The roster message is the state lane and is unaffected.
        body = message.content
        assert "member_name=alice" in body
        assert "[human]" in body
        logger.info("HITT contract disclosed via build_team; human tagged in the roster message")

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
        await rail.before_model_call(_StubContext())
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
        ctx = _StubContext()
        body = (await _admit(rail, ctx, "go")).content
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
        ctx = _StubContext()
        assert "[human]" in (await _admit(rail, ctx, "go")).content

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_uninit_strips_hitt_contract_from_builder(self):
        # A teammate still carries the contract in its prefix — its conventions
        # are fixed at spawn and it has no build_team call to disclose them.
        backend = _FakeTeamBackend(
            team=_StubTeam("Beta", "Test"),
            members=[],
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
        await rail.before_model_call(_StubContext())
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

    @pytest.mark.level1
    def test_external_cli_prompt_includes_task_dispatch_section(self):
        # EXTERNAL_CLI shares the teammate dispatch template (claim/complete);
        # _DISPATCH_ROLE_SLUGS must list it or the section vanishes and the
        # member loses its task-intake instructions.
        prompt = build_team_member_system_prompt(role=TeamRole.EXTERNAL_CLI, member_name="cli-1", language="cn")
        assert "# 任务下发与获取" in prompt

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_rail_static_sections_include_the_notice(self):
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = TeamPolicyRail(role=TeamRole.TEAMMATE, member_name="dev1", language="cn")
        rail.init(agent)
        await rail.before_model_call(_StubContext())
        assert TeamSectionName.INBOUND_TAGS in builder.get_all_sections()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_leader_reads_the_notice_from_the_disclosure(self):
        # The leader gets the same notice, just later: the tags only start
        # appearing in its inputs once the team exists.
        builder = SystemPromptBuilder(language="cn")
        agent = _StubAgent(builder)
        rail = TeamPolicyRail(role=TeamRole.LEADER, member_name="l", language="cn")
        rail.init(agent)
        await rail.before_model_call(_StubContext())
        assert TeamSectionName.INBOUND_TAGS not in builder.get_all_sections()
        assert "team-inbound" in build_leader_policy_disclosure(language="cn")


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


class TestTeamPolicyRailSnapshotCollapse:
    """Superseded task boards are dropped as whole inputs before the join.

    Both input queues hand a busy member everything that piled up as one batch,
    so several full board surveys can arrive together — all but the newest
    already describing a board that no longer exists.
    """

    @staticmethod
    def _rail(role: TeamRole, backend: _FakeTeamBackend | None = None) -> TeamPolicyRail:
        """Build a rail in the given team role."""
        return TeamPolicyRail(role=role, member_name="dev1", language="cn", team_backend=backend)

    @staticmethod
    def _board(body: str) -> str:
        """Render one queued task-board input the way TaskBoardHandler does."""
        return render_event(kind="task-board", body=body)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_teammate_keeps_only_the_newest_board(self):
        rail = self._rail(TeamRole.TEAMMATE)
        ctx = _StubContext()

        body = (
            await _admit(
                rail,
                ctx,
                self._board("one task"),
                self._board("two tasks"),
                self._board("three tasks"),
            )
        ).content

        assert body.count("<team-event") == 1
        assert "three tasks" in body
        assert "one task" not in body
        logger.info("teammate batch collapsed to: %s", body)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_leader_boards_are_left_untouched(self):
        """The leader reads the sequence of boards, not just the latest one.

        Its board is the team's whole incomplete workload, and which task
        appeared or moved between two surveys is exactly the signal it uses to
        decide whether to re-plan or conclude.
        """
        rail = self._rail(TeamRole.LEADER)
        ctx = _StubContext()

        body = (
            await _admit(
                rail,
                ctx,
                self._board("one task"),
                self._board("two tasks"),
                self._board("three tasks"),
            )
        ).content

        assert body.count("<team-event") == 3
        assert "one task" in body
        assert "two tasks" in body

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_a_lone_board_is_never_dropped(self):
        rail = self._rail(TeamRole.TEAMMATE)
        ctx = _StubContext()

        body = (await _admit(rail, ctx, self._board("only board"))).content

        assert "only board" in body

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_only_snapshot_inputs_are_dropped(self):
        rail = self._rail(TeamRole.TEAMMATE)
        ctx = _StubContext()

        body = (
            await _admit(
                rail,
                ctx,
                render_event(kind="roster-change", body="alice joined"),
                self._board("stale board"),
                render_event(kind="stale-claim", body="task idle", task_id="t-1"),
                self._board("fresh board"),
                source="steering",
                prefix="[STEERING] ",
            )
        ).content

        assert "stale board" not in body
        assert "fresh board" in body
        # A roster delta and a per-task nudge each carry something no other
        # entry repeats, so neither may be dropped.
        assert "alice joined" in body
        assert "task idle" in body
        assert body.startswith("[STEERING] ")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_dropping_runs_before_team_context_is_prepended(self):
        """Team state must end up in front of the *surviving* inputs."""
        backend = _FakeTeamBackend(
            team=_StubTeam("t1", "T1"),
            members=[_StubMember("alice", "Alice", desc="peer")],
            self_member_name="dev1",
        )
        rail = self._rail(TeamRole.TEAMMATE, backend)
        ctx = _StubContext()

        body = (
            await _admit(
                rail,
                ctx,
                self._board("stale board"),
                self._board("fresh board"),
                source="steering",
            )
        ).content

        assert "<team-context>" in body
        assert body.index("<team-context>") < body.index("fresh board")
        assert "stale board" not in body


# ---------------------------------------------------------------------------
# Steering batch quota (F_78)
# ---------------------------------------------------------------------------


class _StubDrainContext:
    """AgentCallbackContext stand-in carrying only the drain inputs."""

    def __init__(self, pending: int = 0) -> None:
        self.inputs = SteeringDrainInputs(pending=pending)


async def _quota(rail: TeamPolicyRail, pending: int = 5) -> int | None:
    """Ask the rail how much of a backlog of ``pending`` this drain may take."""
    ctx = _StubDrainContext(pending=pending)
    await rail.before_steering_drain(ctx)
    return ctx.inputs.limit


class TestTeamPolicyRailSteeringQuota:
    """Non-leader members take the backlog in bounded batches; the leader does not."""

    @staticmethod
    def _rail(role: TeamRole, steer_batch_size: int = 2) -> TeamPolicyRail:
        """Build a rail in the given team role."""
        return TeamPolicyRail(
            role=role,
            member_name="dev1",
            language="cn",
            steer_batch_size=steer_batch_size,
        )

    @pytest.mark.asyncio
    @pytest.mark.level0
    @pytest.mark.parametrize(
        "role",
        [TeamRole.TEAMMATE, TeamRole.HUMAN_AGENT, TeamRole.BRIDGE_AGENT, TeamRole.WORKER],
    )
    async def test_non_leader_roles_cap_the_batch(self, role: TeamRole):
        """Every role that reads a mailbox gets the same cap — one gate, not four."""
        assert await _quota(self._rail(role)) == 2

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_leader_takes_the_whole_backlog(self):
        """It reads the sequence of boards, so it must keep seeing all of it."""
        assert await _quota(self._rail(TeamRole.LEADER)) is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_quota_follows_the_configured_batch_size(self):
        assert await _quota(self._rail(TeamRole.TEAMMATE, steer_batch_size=4)) == 4

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_quota_is_the_same_whatever_is_queued(self):
        """A fixed cap, not a fraction of the backlog: batches stay a known size."""
        rail = self._rail(TeamRole.TEAMMATE)
        assert [await _quota(rail, pending=depth) for depth in (1, 3, 50)] == [2, 2, 2]

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_missing_inputs_are_tolerated(self):
        """Same defensive read as ``on_user_message``: no inputs, nothing to cap."""
        rail = self._rail(TeamRole.TEAMMATE)
        ctx = _StubDrainContext()
        ctx.inputs = None

        await rail.before_steering_drain(ctx)

        assert ctx.inputs is None
