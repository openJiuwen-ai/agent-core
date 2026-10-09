# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host-facing project-space features: prompts, build, membership, reports."""

from types import SimpleNamespace
from uuid import uuid4
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.prompts.sections import TeamSectionName, build_team_static_sections
from openjiuwen.agent_teams.schema.blueprint import StorageSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.conversation import ConversationMessage
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import MemberOpResult, TeamMemberSpec, TeamRole
from openjiuwen.agent_teams.tools.team import CapabilityOverrides, TeamBackend
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.runner.team_runner import _TeamRunnerMixin
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

pytestmark = pytest.mark.level0


def _spec(**kwargs) -> TeamAgentSpec:
    payload = {"agents": {"leader": DeepAgentSpec()}, "storage": StorageSpec(type="memory")}
    payload.update(kwargs)
    return TeamAgentSpec(**payload)


def _message(team_name: str, session_id: str) -> ConversationMessage:
    return ConversationMessage(
        message_id="m1",
        team_name=team_name,
        session_id=session_id,
        client_message_id="c1",
        sender="dev",
        sender_name="Dev",
        content="shipped the API",
        timestamp=1,
    )


class TestPromptOverrides:
    def test_unknown_key_fails_at_build(self):
        spec = _spec(prompt_overrides={"not_a_section": "hello"})
        with pytest.raises(BaseError, match="unknown section"):
            spec.build()

    def test_empty_string_drops_the_section(self):
        sections = build_team_static_sections(
            role=TeamRole.LEADER,
            member_name="leader",
            prompt_overrides={TeamSectionName.DISPATCH: ""},
        )
        assert TeamSectionName.DISPATCH not in [section.name for section in sections]
        assert TeamSectionName.ROLE in [section.name for section in sections]

    def test_replacement_keeps_priority_and_skips_unowned_sections(self):
        sections = build_team_static_sections(
            role=TeamRole.TEAMMATE,
            member_name="dev",
            prompt_overrides={TeamSectionName.WORKFLOW: "# custom workflow"},
        )
        names = [section.name for section in sections]
        assert TeamSectionName.WORKFLOW not in names

        replaced = build_team_static_sections(
            role=TeamRole.LEADER,
            member_name="leader",
            prompt_overrides={TeamSectionName.DISPATCH: "# custom dispatch"},
        )
        dispatch = next(section for section in replaced if section.name == TeamSectionName.DISPATCH)
        assert dispatch.content["cn"] == "# custom dispatch"
        assert dispatch.priority == next(
            section.priority
            for section in build_team_static_sections(role=TeamRole.LEADER, member_name="leader")
            if section.name == TeamSectionName.DISPATCH
        )


@pytest.mark.asyncio
async def test_existing_team_build_keeps_roster_and_flags():
    from openjiuwen.agent_teams.context import reset_session_id, set_session_id
    from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase

    token = set_session_id("build-session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await database.initialize()
    try:
        backend = TeamBackend(
            team_name="kept-team",
            member_name="team_leader",
            is_leader=True,
            db=database,
            messager=AsyncMock(),
            enable_hitt=True,
        )
        await backend.build_team(
            display_name="Original",
            desc="keep this",
            leader_display_name="Leader",
            leader_desc="leads",
            overrides=CapabilityOverrides(enable_hitt=False),
        )
        await backend.build_team(
            display_name="Renamed",
            desc="changed",
            leader_display_name="Other",
            leader_desc="nope",
            overrides=CapabilityOverrides(enable_hitt=True),
        )
        stored = await database.team.get_team("kept-team")
        assert stored.display_name == "Original"
        assert stored.desc == "keep this"
        assert backend.hitt_enabled() is False
    finally:
        await database.close()
        reset_session_id(token)


@pytest.mark.asyncio
async def test_ensure_team_built_rejects_missing_backend():
    agent = TeamAgent(AgentCard(id="card", name="leader", description="d"))
    with pytest.raises(RuntimeError, match="team spec and a team backend"):
        await agent.ensure_team_built()


def _runner(entry):
    pool = SimpleNamespace(get=AsyncMock(return_value=entry))

    class Host(_TeamRunnerMixin):
        def _get_team_runtime_manager(self):
            return SimpleNamespace(pool=pool)

        _resolve_spec_from_session_bucket = AsyncMock(return_value=None)

    return Host()


def _member_spec(role: TeamRole, **kwargs) -> TeamMemberSpec:
    return TeamMemberSpec(member_name="dev", display_name="Dev", desc="builds", role_type=role, **kwargs)


class TestMemberFacade:
    @pytest.mark.asyncio
    async def test_spawn_requires_an_active_matching_session(self):
        host = _runner(None)
        result = await _TeamRunnerMixin.spawn_team_member(host, _member_spec(TeamRole.TEAMMATE), "team")
        assert result == {"ok": False, "reason": "team_not_active"}

    @pytest.mark.asyncio
    async def test_spawn_passive_does_not_start_a_process(self):
        backend = SimpleNamespace(
            spawn_passive_human=AsyncMock(return_value=MemberOpResult.success()),
            team_name="team",
        )
        agent = SimpleNamespace(
            team_backend=backend,
            spec=None,
            auto_start_member=AsyncMock(return_value=True),
        )
        entry = SimpleNamespace(current_session_id="sess", agent=agent)
        result = await _TeamRunnerMixin.spawn_team_member(
            _runner(entry),
            _member_spec(TeamRole.PASSIVE_HUMAN),
            "team",
            "sess",
        )
        assert result == {"ok": True, "reason": ""}
        agent.auto_start_member.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_spawn_keeps_the_backend_reason(self):
        backend = SimpleNamespace(
            spawn_member=AsyncMock(return_value=MemberOpResult.fail("Member dev already exists")),
            teammate_mode="build_mode",
            team_name="team",
            _allocate_model_config=None,
        )
        agent = SimpleNamespace(team_backend=backend, spec=None, auto_start_member=AsyncMock())
        entry = SimpleNamespace(current_session_id="sess", agent=agent)
        result = await _TeamRunnerMixin.spawn_team_member(
            _runner(entry),
            _member_spec(TeamRole.TEAMMATE),
            "team",
            "sess",
        )
        assert result["ok"] is False
        assert "already exists" in result["reason"]
        agent.auto_start_member.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unsupported_role_and_failed_start(self):
        host = _runner(SimpleNamespace(current_session_id="sess", agent=SimpleNamespace(team_backend=object(), spec=None)))
        rejected = await _TeamRunnerMixin.spawn_team_member(
            host,
            _member_spec(TeamRole.LEADER),
            "team",
            "sess",
        )
        assert rejected == {"ok": False, "reason": "unsupported_role_type"}

        backend = SimpleNamespace(
            spawn_member=AsyncMock(return_value=MemberOpResult.success()),
            teammate_mode="build_mode",
            team_name="team",
            _allocate_model_config=None,
        )
        agent = SimpleNamespace(team_backend=backend, spec=None, auto_start_member=AsyncMock(return_value=False))
        entry = SimpleNamespace(current_session_id="sess", agent=agent)
        failed = await _TeamRunnerMixin.spawn_team_member(
            _runner(entry),
            _member_spec(TeamRole.TEAMMATE),
            "team",
            "sess",
        )
        assert failed == {"ok": False, "reason": "member dev registered but failed to start"}

    @pytest.mark.asyncio
    async def test_spawn_registers_agent_spec_before_start(self):
        team_spec = _spec(team_name="team")
        backend = SimpleNamespace(
            spawn_human_agent=AsyncMock(return_value=MemberOpResult.success()),
        )
        agent = SimpleNamespace(
            team_backend=backend,
            spec=team_spec,
            auto_start_member=AsyncMock(return_value=True),
            session_manager=SimpleNamespace(team_session=object()),
            persist_session_manifest=MagicMock(),
        )
        entry = SimpleNamespace(current_session_id="sess", agent=agent)
        custom = DeepAgentSpec(system_prompt="custom")
        result = await _TeamRunnerMixin.spawn_team_member(
            _runner(entry),
            _member_spec(TeamRole.HUMAN_AGENT, agent_spec=custom),
            "team",
            "sess",
        )
        assert result == {"ok": True, "reason": ""}
        assert team_spec.agents["dev"] is custom
        agent.persist_session_manifest.assert_called_once()
        agent.auto_start_member.assert_awaited_once_with("dev")

    @pytest.mark.asyncio
    async def test_remove_is_idempotent_and_forwards_shutdown(self):
        backend = SimpleNamespace(
            get_member=AsyncMock(return_value=None),
            shutdown_member=AsyncMock(),
        )
        entry = SimpleNamespace(
            current_session_id="sess",
            agent=SimpleNamespace(team_backend=backend),
        )
        missing = await _TeamRunnerMixin.remove_team_member(_runner(entry), "team", "dev", "sess")
        assert missing == {"ok": True, "reason": ""}
        backend.shutdown_member.assert_not_awaited()

        member = SimpleNamespace(status=MemberStatus.BUSY.value)
        backend.get_member = AsyncMock(return_value=member)
        backend.shutdown_member = AsyncMock(return_value=MemberOpResult.fail("member holds active tasks"))
        refused = await _TeamRunnerMixin.remove_team_member(_runner(entry), "team", "dev", "sess", False)
        assert refused == {"ok": False, "reason": "member holds active tasks"}
        backend.shutdown_member.assert_awaited_once_with("dev", force=False)

        member.status = MemberStatus.SHUTDOWN.value
        departed = await _TeamRunnerMixin.remove_team_member(_runner(entry), "team", "dev", "sess")
        assert departed == {"ok": True, "reason": ""}


class TestProgressReport:
    @pytest.mark.asyncio
    async def test_scope_and_missing_team_fail_before_a_model_call(self):
        host = _runner(None)
        with pytest.raises(ValueError, match="invalid scope"):
            await _TeamRunnerMixin.get_progress_report(
                host,
                team_name="missing",
                session_id="sess-1",
                scope="nope",
            )
        host._resolve_spec_from_session_bucket.assert_not_awaited()

        with pytest.raises(ValueError, match="team_not_found"):
            await _TeamRunnerMixin.get_progress_report(host, team_name="missing", session_id="sess-1")

    @pytest.mark.asyncio
    async def test_empty_team_does_not_call_the_model(self, tmp_path):
        from openjiuwen.agent_teams.progress_report.service import EMPTY_REPORT, ProgressReportService

        service = ProgressReportService(
            team_name="report-team",
            session_id=f"sess-{uuid4().hex}",
            spec=_spec(team_name="report-team"),
            workspace=tmp_path,
        )
        service._build_model = lambda: (_ for _ in ()).throw(AssertionError("model"))
        assert await service.generate() == EMPTY_REPORT

    @pytest.mark.asyncio
    async def test_material_without_a_model_is_a_distinct_error(self, tmp_path):
        from openjiuwen.agent_teams.group_chat.conversation import GroupConversationLog
        from openjiuwen.agent_teams.progress_report.service import ProgressReportService

        session_id = f"sess-{uuid4().hex}"
        GroupConversationLog("report-team", session_id, tmp_path).sync([_message("report-team", session_id)])
        service = ProgressReportService(
            team_name="report-team",
            session_id=session_id,
            spec=_spec(team_name="report-team"),
            workspace=tmp_path,
        )
        with pytest.raises(ValueError, match="report_model_unavailable"):
            await service.generate()

    @pytest.mark.asyncio
    async def test_one_model_call_uses_public_history(self, tmp_path):
        from openjiuwen.agent_teams.group_chat.conversation import GroupConversationLog
        from openjiuwen.agent_teams.progress_report.service import ProgressReportService

        session_id = f"sess-{uuid4().hex}"
        GroupConversationLog("report-team", session_id, tmp_path).sync([_message("report-team", session_id)])
        seen: dict[str, str] = {}

        class FakeModel:
            async def invoke(self, messages):
                seen["user"] = messages[1].content
                return SimpleNamespace(content="目标：交付")

        service = ProgressReportService(
            team_name="report-team",
            session_id=session_id,
            spec=_spec(team_name="report-team"),
            workspace=tmp_path,
        )
        service._build_model = lambda: FakeModel()
        assert await service.generate() == "目标：交付"
        assert "shipped the API" in seen["user"]
        assert "暂无进展" not in seen["user"]
