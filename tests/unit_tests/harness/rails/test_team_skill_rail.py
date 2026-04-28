# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for TeamSkillRail signal detection types and helpers."""

from __future__ import annotations


import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.harness.rails.skills.team_skill_rail import (
    TeamSignalType,
    TeamSkillRail,
    TrajectoryIssue,
    UserIntent,
)
from openjiuwen.agent_evolving.trajectory.types import Trajectory


def test_team_signal_type_enum():
    """TeamSignalType enum must have expected values."""
    assert TeamSignalType.USER_REQUEST.value == "user_request"
    assert TeamSignalType.TRAJECTORY_ISSUE.value == "trajectory_issue"


def test_user_intent_dataclass():
    """UserIntent dataclass should have expected fields."""
    intent = UserIntent(is_improvement=True, intent="add a coder role")
    assert intent.is_improvement is True
    assert intent.intent == "add a coder role"

    no_intent = UserIntent(is_improvement=False, intent="")
    assert no_intent.is_improvement is False
    assert no_intent.intent == ""


def test_trajectory_issue_dataclass():
    """TrajectoryIssue dataclass should have expected fields with defaults."""
    issue = TrajectoryIssue(
        issue_type="coordination",
        description="roles not passing data",
        affected_role="researcher",
        severity="high",
    )
    assert issue.issue_type == "coordination"
    assert issue.description == "roles not passing data"
    assert issue.affected_role == "researcher"
    assert issue.severity == "high"

    default_issue = TrajectoryIssue(issue_type="test", description="test desc")
    assert default_issue.severity == "medium"
    assert default_issue.affected_role == ""


@pytest.mark.asyncio
async def test_detect_user_request_retries_on_invalid_response():
    """_detect_user_request 首次坏结果时应重试。"""
    import json

    mock_optimizer = MagicMock()
    mock_optimizer.language = "cn"
    mock_optimizer.model = "test-model"
    mock_optimizer.llm = MagicMock()
    mock_optimizer.llm.invoke = AsyncMock(
        side_effect=[
            MagicMock(content="not json"),
            MagicMock(content=json.dumps({"is_improvement": True, "intent": "增加 reviewer"})),
        ]
    )

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._optimizer = mock_optimizer

        result = await rail._detect_user_request(
            [{"role": "user", "content": "请增加 reviewer 角色"}],
            "team skill",
        )

        assert result == UserIntent(is_improvement=True, intent="增加 reviewer")
        assert mock_optimizer.llm.invoke.await_count == 2


@pytest.mark.asyncio
async def test_request_simplify_calls_scorer_and_executes():
    """/evolve_simplify should load records, call scorer.simplify, then execute actions."""
    mock_store = MagicMock()
    mock_store.skill_exists.return_value = True
    mock_store.load_full_evolution_log = AsyncMock(return_value=MagicMock(entries=[MagicMock()]))
    mock_store.read_skill_content = AsyncMock(return_value="test content")
    mock_store.extract_description_from_skill_md.return_value = "test description"

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._store = mock_store
        rail._scorer = AsyncMock()
        rail._scorer.simplify = AsyncMock(return_value=[{"action": "DELETE", "record_id": "ev_123", "reason": "test"}])
        rail._scorer.execute_simplify_actions = AsyncMock(return_value={"deleted": 1})
        rail._auto_save = False
        rail._pending_patch_snapshots = {}
        rail._pending_approval_events = []

        result = await rail.request_simplify("test-team-skill")

        rail._scorer.simplify.assert_called_once()
        rail._scorer.execute_simplify_actions.assert_called_once()
        assert result == {"deleted": 1}


@pytest.mark.asyncio
async def test_request_simplify_returns_none_when_no_records():
    """/evolve_simplify should return None when no evolution records exist."""
    mock_store = MagicMock()
    mock_store.skill_exists.return_value = True
    mock_store.load_full_evolution_log = AsyncMock(return_value=MagicMock(entries=[]))

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._store = mock_store

        result = await rail.request_simplify("test-team-skill")

        assert result is None


@pytest.mark.asyncio
async def test_request_simplify_returns_none_when_no_actions():
    """/evolve_simplify should return None when scorer returns no actions."""
    mock_store = MagicMock()
    mock_store.skill_exists.return_value = True
    mock_store.load_full_evolution_log = AsyncMock(return_value=MagicMock(entries=[MagicMock()]))
    mock_store.read_skill_content = AsyncMock(return_value="test content")
    mock_store.extract_description_from_skill_md.return_value = "test description"

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._store = mock_store
        rail._scorer = AsyncMock()
        rail._scorer.simplify = AsyncMock(return_value=[])

        result = await rail.request_simplify("test-team-skill")

        assert result is None


def test_format_evolution_records():
    """_format_evolution_records should format records as readable text (Chinese by default)."""
    from openjiuwen.agent_evolving.checkpointing.types import (
        EvolutionPatch,
        EvolutionRecord,
        EvolutionTarget,
    )

    patches = [
        EvolutionPatch(section="Collaboration", action="append", content="协作经验", target=EvolutionTarget.BODY),
        EvolutionPatch(section="Constraints", action="append", content="约束条件", target=EvolutionTarget.BODY),
    ]
    records = [
        EvolutionRecord.make(source="user_request", context="test", change=patches[0]),
        EvolutionRecord.make(source="trajectory_issue", context="test", change=patches[1]),
    ]

    formatted = TeamSkillRail._format_evolution_records(records)

    assert "Collaboration" in formatted
    assert "Constraints" in formatted
    assert "协作经验" in formatted
    assert "约束条件" in formatted
    assert "经验 #1" in formatted
    assert "经验 #2" in formatted


def test_format_evolution_records_english():
    """_format_evolution_records should use English labels when language=en."""
    from openjiuwen.agent_evolving.checkpointing.types import (
        EvolutionPatch,
        EvolutionRecord,
        EvolutionTarget,
    )

    patch = EvolutionPatch(section="Workflow", action="append", content="step one then step two", target=EvolutionTarget.BODY)
    record = EvolutionRecord.make(source="user_request", context="test", change=patch)

    formatted = TeamSkillRail._format_evolution_records([record], language="en")

    assert "Experience #1" in formatted
    assert "Content: step one then step two" in formatted
    assert "经验" not in formatted
    assert "内容" not in formatted


def test_format_evolution_records_empty():
    """_format_evolution_records should return localized empty message for no records."""
    assert TeamSkillRail._format_evolution_records([]) == "（无演进经验）"
    assert TeamSkillRail._format_evolution_records([], language="en") == "(no evolution records)"


@pytest.mark.asyncio
async def test_request_rebuild_returns_none_when_no_skill():
    """/evolve_rebuild should return None when skill doesn't exist."""
    mock_store = MagicMock()
    mock_store.skill_exists.return_value = False

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._store = mock_store

        result = await rail.request_rebuild("nonexistent-skill")

        assert result is None


@pytest.mark.asyncio
async def test_request_rebuild_archives_before_building_prompt():
    """request_rebuild should archive old version BEFORE building the prompt."""

    mock_record = MagicMock()
    mock_record.change.section = "Collaboration"
    mock_record.change.content = "test collaboration experience"
    mock_record.change.skip_reason = None  # Not skipped
    mock_record.source = "user_request"
    mock_record.timestamp = "2026-04-25T10:30:00"
    mock_record.score = 0.8  # High score, should be included

    mock_low_score_record = MagicMock()
    mock_low_score_record.change.section = "Workflow"
    mock_low_score_record.change.content = "low quality experience"
    mock_low_score_record.change.skip_reason = None
    mock_low_score_record.source = "trajectory_issue"
    mock_record.timestamp = "2026-04-25T10:31:00"
    mock_low_score_record.score = 0.3  # Low score, should be filtered out

    mock_store = MagicMock()
    mock_store.skill_exists.return_value = True
    mock_store.archive_skill_body = AsyncMock(return_value="SKILL.v20260426_171500.md")
    mock_store.archive_evolutions = AsyncMock(return_value="evolutions.v20260426_171500.json")
    mock_store.load_full_evolution_log = AsyncMock(
        return_value=MagicMock(entries=[mock_record, mock_low_score_record])
    )

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._store = mock_store
        rail._optimizer = MagicMock()
        rail._optimizer._language = "cn"
        rail._pending_approval_events = []

        result = await rail.request_rebuild("test-team-skill", user_intent="优化协作流程")

        # Archive operations should be called BEFORE prompt is built
        mock_store.archive_skill_body.assert_called_once_with("test-team-skill")
        mock_store.archive_evolutions.assert_called_once_with("test-team-skill")

        # Returns the followup text
        assert result is not None
        # Prompt should NOT contain skill_content (it's already archived)
        # Prompt should contain filtered evolution records
        assert "Collaboration" in result
        assert "test collaboration experience" in result
        # Low score record should be filtered out (min_score=0.5 by default)
        assert "Workflow" not in result
        # Contains min_score in prompt
        assert "0.50" in result or "0.5" in result
        # Contains teamskill-creator instruction
        assert "teamskill-creator" in result.lower()
        # Contains indication that old version is archived
        assert "已归档" in result or "archived" in result.lower()


@pytest.mark.asyncio
async def test_request_rebuild_continues_on_archive_failure():
    """request_rebuild should continue even if archive fails."""

    mock_record = MagicMock()
    mock_record.change.section = "Test"
    mock_record.change.content = "test content"
    mock_record.change.skip_reason = None
    mock_record.source = "test"
    mock_record.timestamp = "2026-04-25T10:00:00"
    mock_record.score = 0.7

    mock_store = MagicMock()
    mock_store.skill_exists.return_value = True
    mock_store.archive_skill_body = AsyncMock(side_effect=RuntimeError("disk full"))
    mock_store.archive_evolutions = AsyncMock(return_value=None)
    mock_store.load_full_evolution_log = AsyncMock(
        return_value=MagicMock(entries=[mock_record])
    )

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._store = mock_store
        rail._optimizer = MagicMock()
        rail._optimizer._language = "cn"
        rail._pending_approval_events = []

        result = await rail.request_rebuild("test-team-skill")

        # Should still return prompt even if archive failed
        assert result is not None
        mock_store.load_full_evolution_log.assert_called_once_with("test-team-skill")


# ---------------------------------------------------------------------------
# _detect_trajectory_issues edge cases
# ---------------------------------------------------------------------------

class TestDetectTrajectoryIssues:
    """_detect_trajectory_issues 边界情况测试。"""

    @pytest.mark.asyncio
    async def test_returns_empty_on_llm_failure(self):
        """LLM 调用失败应返回空列表。"""
        mock_optimizer = MagicMock()
        mock_optimizer.language = "cn"
        mock_optimizer.llm = MagicMock()
        mock_optimizer.llm.invoke = AsyncMock(side_effect=RuntimeError("connection lost"))
        mock_optimizer.model = "test-model"

        with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
            rail = TeamSkillRail.__new__(TeamSkillRail)
            rail._optimizer = mock_optimizer

            issues = await rail._detect_trajectory_issues(MagicMock(), "skill content")

            assert issues == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_list_json(self):
        """LLM 返回非列表 JSON 应返回空列表。"""
        mock_optimizer = MagicMock()
        mock_optimizer.language = "cn"
        mock_optimizer.llm = MagicMock()
        mock_optimizer.llm.invoke = AsyncMock(
            return_value=MagicMock(content='{"not_a_list": true}')
        )
        mock_optimizer.model = "test-model"

        with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
            rail = TeamSkillRail.__new__(TeamSkillRail)
            rail._optimizer = mock_optimizer

            issues = await rail._detect_trajectory_issues(MagicMock(), "skill content")

            assert issues == []

    @pytest.mark.asyncio
    async def test_retries_when_first_response_is_invalid_json(self):
        """首次返回坏 JSON 时应重试并解析问题列表。"""
        import json

        mock_optimizer = MagicMock()
        mock_optimizer.language = "cn"
        mock_optimizer.llm = MagicMock()
        mock_optimizer.llm.invoke = AsyncMock(
            side_effect=[
                MagicMock(content="not json"),
                MagicMock(content=json.dumps([
                    {
                        "issue_type": "coordination",
                        "description": "data not passed",
                        "affected_role": "reviewer",
                        "severity": "high",
                    }
                ])),
            ]
        )
        mock_optimizer.model = "test-model"

        with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
            rail = TeamSkillRail.__new__(TeamSkillRail)
            rail._optimizer = mock_optimizer

            issues = await rail._detect_trajectory_issues(MagicMock(), "skill content")

            assert len(issues) == 1
            assert issues[0].issue_type == "coordination"
            assert mock_optimizer.llm.invoke.await_count == 2

    @pytest.mark.asyncio
    async def test_filters_out_low_severity(self):
        """严重度为 low 的问题应被过滤掉。"""
        import json

        mock_optimizer = MagicMock()
        mock_optimizer.language = "cn"
        mock_optimizer.llm = MagicMock()
        mock_optimizer.llm.invoke = AsyncMock(
            return_value=MagicMock(content=json.dumps([
                {"issue_type": "minor", "description": "cosmetic issue", "affected_role": "a", "severity": "low"},
                {"issue_type": "coordination", "description": "data not passed", "affected_role": "b", "severity": "high"},
            ]))
        )
        mock_optimizer.model = "test-model"

        with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
            rail = TeamSkillRail.__new__(TeamSkillRail)
            rail._optimizer = mock_optimizer

            issues = await rail._detect_trajectory_issues(MagicMock(), "skill content")

            assert len(issues) == 1
            assert issues[0].issue_type == "coordination"

    @pytest.mark.asyncio
    async def test_defaults_invalid_severity_to_medium(self):
        """非法 severity 值应默认为 medium。"""
        import json

        mock_optimizer = MagicMock()
        mock_optimizer.language = "cn"
        mock_optimizer.llm = MagicMock()
        mock_optimizer.llm.invoke = AsyncMock(
            return_value=MagicMock(content=json.dumps([
                {"issue_type": "test", "description": "bad severity value", "severity": "invalid"},
            ]))
        )
        mock_optimizer.model = "test-model"

        with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
            rail = TeamSkillRail.__new__(TeamSkillRail)
            rail._optimizer = mock_optimizer

            issues = await rail._detect_trajectory_issues(MagicMock(), "skill content")

            assert len(issues) == 1
            assert issues[0].severity == "medium"


# ---------------------------------------------------------------------------
# request_user_evolution tests (Gap 2)
# ---------------------------------------------------------------------------

class TestRequestUserEvolution:
    """request_user_evolution 方法测试。"""

    @pytest.mark.asyncio
    async def test_returns_none_when_skill_not_found(self):
        """对不存在的 skill 应返回 None。"""
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = False

        with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
            rail = TeamSkillRail.__new__(TeamSkillRail)
            rail._store = mock_store
            rail._optimizer = AsyncMock()
            rail._builder = None
            rail._pending_patch_snapshots = {}
            rail._pending_approval_events = []

            result = await rail.request_user_evolution(
                "nonexistent-skill",
                "增加 reviewer 角色",
            )

            assert result is None
            rail._optimizer.generate_user_patch.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_request_id_when_patch_generated(self):
        """对有效输入且生成 patch 时应返回 request_id。"""
        from openjiuwen.agent_evolving.checkpointing.types import (
            EvolutionPatch,
            EvolutionRecord,
            EvolutionTarget,
        )

        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True
        mock_store.append_record = AsyncMock()

        mock_record = EvolutionRecord.make(
            source="user_request",
            context="test context",
            change=EvolutionPatch(
                section="Collaboration",
                action="append",
                content="增加 reviewer 角色，限制 review 时间不超过 5 分钟",
                target=EvolutionTarget.BODY,
            ),
        )

        mock_optimizer = AsyncMock()
        mock_optimizer.generate_user_patch = AsyncMock(return_value=mock_record)

        mock_builder = MagicMock()
        mock_builder.build.return_value = Trajectory(
            execution_id="test-exec",
            session_id="test-session",
            steps=[],
        )

        with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
            rail = TeamSkillRail.__new__(TeamSkillRail)
            rail._store = mock_store
            rail._optimizer = mock_optimizer
            rail._builder = mock_builder
            rail._pending_patch_snapshots = {}
            rail._pending_approval_events = []
            rail._emit_progress = MagicMock()
            rail._emit_patch_approval_event = MagicMock()

            result = await rail.request_user_evolution(
                "research-team",
                "增加 reviewer 角色，限制 review 时间不超过 5 分钟",
            )

            assert result is not None
            assert result.startswith("team_skill_evolve_")
            mock_optimizer.generate_user_patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_approve_true_stores_directly(self):
        """auto_approve=True 应直接存储 patch 并返回 record.id。"""
        from openjiuwen.agent_evolving.checkpointing.types import (
            EvolutionPatch,
            EvolutionRecord,
            EvolutionTarget,
        )

        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True
        mock_store.append_record = AsyncMock()

        mock_record = EvolutionRecord.make(
            source="user_request",
            context="test",
            change=EvolutionPatch(
                section="Workflow",
                action="append",
                content="优化协作流程",
                target=EvolutionTarget.BODY,
            ),
        )

        mock_optimizer = AsyncMock()
        mock_optimizer.generate_user_patch = AsyncMock(return_value=mock_record)

        mock_builder = MagicMock()
        mock_builder.build.return_value = Trajectory(
            execution_id="test-exec",
            session_id="test-session",
            steps=[],
        )

        with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
            rail = TeamSkillRail.__new__(TeamSkillRail)
            rail._store = mock_store
            rail._optimizer = mock_optimizer
            rail._builder = mock_builder
            rail._pending_patch_snapshots = {}
            rail._pending_approval_events = []
            rail._emit_progress = MagicMock()

            result = await rail.request_user_evolution(
                "research-team",
                "优化协作流程",
                auto_approve=True,
            )

            # Should return record.id (not change_id) and call append_record
            assert result == mock_record.id
            mock_store.append_record.assert_called_once_with("research-team", mock_record)

    @pytest.mark.asyncio
    async def test_auto_approve_false_stages_for_approval(self):
        """auto_approve=False 应暂存等待审批。"""
        from openjiuwen.agent_evolving.checkpointing.types import (
            EvolutionPatch,
            EvolutionRecord,
            EvolutionTarget,
        )

        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True

        mock_record = EvolutionRecord.make(
            source="user_request",
            context="test",
            change=EvolutionPatch(
                section="Constraints",
                action="append",
                content="增加超时限制",
                target=EvolutionTarget.BODY,
            ),
        )

        mock_optimizer = AsyncMock()
        mock_optimizer.generate_user_patch = AsyncMock(return_value=mock_record)

        mock_builder = MagicMock()
        mock_builder.build.return_value = Trajectory(
            execution_id="test-exec",
            session_id="test-session",
            steps=[],
        )

        with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
            rail = TeamSkillRail.__new__(TeamSkillRail)
            rail._store = mock_store
            rail._optimizer = mock_optimizer
            rail._builder = mock_builder
            rail._pending_patch_snapshots = {}
            rail._pending_approval_events = []
            rail._emit_progress = MagicMock()
            rail._emit_patch_approval_event = MagicMock()

            result = await rail.request_user_evolution(
                "research-team",
                "增加超时限制",
                auto_approve=False,
            )

            # Should return change_id and NOT call append_record
            assert result is not None
            assert result.startswith("team_skill_evolve_")
            mock_store.append_record.assert_not_called()
            # Should be staged in pending snapshots
            assert result in rail._pending_patch_snapshots
            rail._emit_patch_approval_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_patch_generated(self):
        """optimizer 未生成 patch 时应返回 None。"""
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True

        mock_optimizer = AsyncMock()
        mock_optimizer.generate_user_patch = AsyncMock(return_value=None)

        mock_builder = MagicMock()
        mock_builder.build.return_value = Trajectory(
            execution_id="test-exec",
            session_id="test-session",
            steps=[],
        )

        with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
            rail = TeamSkillRail.__new__(TeamSkillRail)
            rail._store = mock_store
            rail._optimizer = mock_optimizer
            rail._builder = mock_builder
            rail._pending_patch_snapshots = {}
            rail._pending_approval_events = []
            rail._emit_progress = MagicMock()

            result = await rail.request_user_evolution(
                "research-team",
                "无效的改进建议",
            )

            assert result is None
            rail._emit_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_placeholder_trajectory_when_no_builder(self):
        """无 builder 时应使用 placeholder trajectory。"""
        from openjiuwen.agent_evolving.checkpointing.types import (
            EvolutionPatch,
            EvolutionRecord,
            EvolutionTarget,
        )

        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True
        mock_store.append_record = AsyncMock()

        mock_record = EvolutionRecord.make(
            source="user_request",
            context="test",
            change=EvolutionPatch(
                section="Workflow",
                action="append",
                content="用户主动触发演进",
                target=EvolutionTarget.BODY,
            ),
        )

        mock_optimizer = AsyncMock()
        mock_optimizer.generate_user_patch = AsyncMock(return_value=mock_record)

        with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
            rail = TeamSkillRail.__new__(TeamSkillRail)
            rail._store = mock_store
            rail._optimizer = mock_optimizer
            rail._builder = None  # No builder
            rail._pending_patch_snapshots = {}
            rail._pending_approval_events = []
            rail._emit_progress = MagicMock()

            result = await rail.request_user_evolution(
                "research-team",
                "用户主动触发演进",
                auto_approve=True,
            )

            # Should still work with placeholder trajectory
            assert result == mock_record.id
            # Verify optimizer was called with some trajectory
            call_args = mock_optimizer.generate_user_patch.call_args
            assert call_args is not None
            trajectory_arg = call_args[0][0]
            assert isinstance(trajectory_arg, Trajectory)
            assert trajectory_arg.source == "user_triggered"
