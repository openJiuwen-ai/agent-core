# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lightweight test for TeamSkillRail evolution flow.

No real LLM calls — uses a mock that returns canned JSON.
No real agent/service needed — constructs synthetic Trajectory directly.
Typical run time: < 3 seconds.
"""

from __future__ import annotations

import asyncio
import pytest
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryStore
from openjiuwen.agent_evolving.trajectory import TrajectoryBuilder
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    Trajectory,
    TrajectoryStep,
    ToolCallDetail,
)
from openjiuwen.harness.rails.skills.team_skill_rail import TeamSkillRail, TrajectoryIssue, UserIntent


# ============================================================
# Mock LLM: returns canned JSON, no network calls
# ============================================================

# Response for trajectory issue detection — JSON array of issues
# Note: Must NOT be wrapped in ``` fences, because _parse_json's balanced-brace
# scan will extract the first {...} object from the array, returning a dict
# instead of the full list.  Plain JSON array parses correctly via json.loads.
_TRAJECTORY_ISSUES_RESPONSE = '[{"issue_type": "coordination", "description": "各成员产出格式不统一，缺少统一的工作流约束", "affected_role": "researcher", "severity": "medium"}]'

# Response for patch generation (from TeamSkillOptimizer)
_PATCH_RESPONSE = """\
```json
{
  "need_patch": true,
  "section": "Workflow",
  "content": "### 经验: PPT风格统一\\n在分配任务时需明确要求所有Researcher使用统一的颜色主题和字体，避免合并后风格不一致。",
  "reason": "本次执行中各成员PPT风格差异大，合并后不协调"
}
```"""


class MockLLM:
    """Fake LLM that returns pre-defined responses based on prompt keywords."""

    def __init__(self, response: str = _PATCH_RESPONSE):
        self.response = response
        self.call_count = 0
        self._responses = {
            "user_request": '{"is_improvement": false, "intent": ""}',
            "trajectory_issue": _TRAJECTORY_ISSUES_RESPONSE,
            "patch": _PATCH_RESPONSE,
        }

    async def invoke(self, messages: list, model: str = "", **kwargs) -> Any:
        return await self.chat(messages, model, **kwargs)

    async def chat(self, messages: list, model: str = "", **kwargs) -> Any:
        self.call_count = (self.call_count or 0) + 1
        prompt = messages[0]["content"] if messages else ""
        # Route to different responses based on prompt content.
        # Order matters: check the most specific keywords first.
        if "need_patch" in prompt or "生成演进 patch" in prompt:
            resp = self._responses["patch"]
        elif "分析以下执行轨迹" in prompt:
            resp = self._responses["trajectory_issue"]
        elif "改进意见" in prompt:
            resp = self._responses["user_request"]
        else:
            resp = self._responses["patch"]
        return _MockResponse(resp)


@dataclass
class _MockResponse:
    content: str


# ============================================================
# Mock AgentCallbackContext (minimal stub)
# ============================================================

@dataclass
class _MockAgent:
    @dataclass
    class _Card:
        id: str = "test-leader"
    card: _Card = field(default_factory=_Card)


@dataclass
class _MockCtx:
    agent: Any = field(default_factory=_MockAgent)
    event: Any = None
    inputs: Any = None
    config: Any = None
    session: Any = None
    context: Any = None
    extra: dict = field(default_factory=dict)


class _MsgContext:
    def __init__(self, messages=None):
        self._messages = list(messages) if messages else []

    def get_messages(self):
        return self._messages


# ============================================================
# Trajectory builders
# ============================================================

def build_patch_trajectory(skill_name: str = "deep-research-to-ppt") -> Trajectory:
    """Build a trajectory that references an existing team skill (triggers PATCH)."""
    steps = []
    steps.append(TrajectoryStep(
        kind="tool",
        detail=ToolCallDetail(
            tool_name="read_file",
            call_args=f"team_skills/{skill_name}/SKILL.md",
            call_result="---\nname: deep-research-to-ppt\n---\n# ...",
        ),
    ))
    for i in range(3):
        steps.append(TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(
                tool_name="spawn_member",
                call_args={"name": f"researcher-{i}", "desc": f"调研{i}"},
                call_result={"status": "spawned"},
            ),
        ))
    return Trajectory(
        execution_id="test-patch-001",
        steps=steps,
        source="online",
        session_id="test-session-2",
    )


def _build_member_step(
    tool_name: str,
    args: Any = None,
    start_time_ms: int = 0,
    meta: dict | None = None,
) -> TrajectoryStep:
    return TrajectoryStep(
        kind="tool",
        detail=ToolCallDetail(tool_name=tool_name, call_args=args or {}),
        start_time_ms=start_time_ms,
        meta=meta or {},
    )


def _build_team_store_trajectory(
    member_id: str,
    session_id: str,
    steps: list,
) -> Trajectory:
    """Build a trajectory with member_id meta for team store."""
    return Trajectory(
        execution_id=f"exec-{member_id}",
        session_id=session_id,
        source="online",
        steps=steps,
        meta={"member_id": member_id},
    )


# ============================================================
# Test cases
# ============================================================

@pytest.mark.asyncio
async def test_patch_path():
    """Test: existing skill detected → PATCH proposal → approval events emitted."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        skill_dir = tmp / "deep-research-to-ppt"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deep-research-to-ppt\nkind: team-skill\n---\n# Deep Research\nWorkflow here.",
            encoding="utf-8",
        )

        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            auto_save=False,
            async_evolution=False,
        )

        trajectory = build_patch_trajectory("deep-research-to-ppt")
        ctx = _MockCtx()

        await rail.run_evolution(trajectory, ctx)

        events = await rail.drain_pending_approval_events()
        approval_events = [e for e in events if e.type == "chat.ask_user_question"]
        patches = rail._pending_patch_snapshots

        if patches:
            req_id = list(patches.keys())[0]
            await rail.on_approve_patch(req_id)

        assert mock_llm.call_count == 2
        assert len(approval_events) == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_run_evolution_passes_current_skill_content_to_trajectory_patch():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        skill_dir = tmp / "deep-research-to-ppt"
        skill_dir.mkdir(parents=True)
        skill_content = (
            "---\nname: deep-research-to-ppt\nkind: team-skill\n---\n"
            "# Deep Research\n## Workflow\nKeep the reviewer handoff explicit."
        )
        (skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")

        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            auto_save=False,
            async_evolution=False,
        )

        captured: dict[str, Any] = {}

        async def _detect_user_request(_messages, _content):
            return None

        async def _detect_trajectory_issues(_trajectory, _content):
            return [TrajectoryIssue(issue_type="coordination", description="handoff gap")]

        async def _generate_trajectory_patch(captured_trajectory, used_skill, current_content, issues):
            captured["trajectory"] = captured_trajectory
            captured["skill"] = used_skill
            captured["current_content"] = current_content
            captured["issues"] = issues
            return None

        rail._detect_user_request = _detect_user_request
        rail._detect_trajectory_issues = _detect_trajectory_issues
        rail._optimizer.generate_trajectory_patch = _generate_trajectory_patch

        await rail.run_evolution(build_patch_trajectory("deep-research-to-ppt"), _MockCtx())

        assert captured["skill"] == "deep-research-to-ppt"
        assert captured["current_content"] == skill_content
        assert captured["issues"] == [
            {
                "issue_type": "coordination",
                "description": "handoff gap",
                "affected_role": "",
                "severity": "medium",
            }
        ]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_async_snapshot_messages_are_preserved_for_team_evolution():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        skill_dir = tmp / "deep-research-to-ppt"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deep-research-to-ppt\nkind: team-skill\n---\n# Deep Research\nWorkflow here.",
            encoding="utf-8",
        )

        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            auto_save=False,
            async_evolution=True,
        )

        trajectory = build_patch_trajectory("deep-research-to-ppt")
        ctx = _MockCtx(context=_MsgContext(messages=[{"role": "user", "content": "请优化协作流程"}]))
        snapshot = await rail._snapshot_for_evolution(trajectory, ctx)

        captured: dict[str, Any] = {}

        async def _detect_trajectory_issues(captured_trajectory, _content):
            captured["trajectory"] = captured_trajectory
            return [TrajectoryIssue(issue_type="workflow", description="需要优化")]

        async def _generate_trajectory_patch(captured_trajectory, used_skill, _content, _issues):
            captured["patch_trajectory"] = captured_trajectory
            captured["skill"] = used_skill
            return None

        rail._detect_user_request = AsyncMock(side_effect=AssertionError("should not be called"))
        rail._detect_trajectory_issues = _detect_trajectory_issues
        rail._optimizer.generate_trajectory_patch = _generate_trajectory_patch

        await rail.run_evolution(trajectory, ctx=None, snapshot=snapshot)

        assert snapshot["parsed_messages"] == [{"role": "user", "content": "请优化协作流程"}]
        assert captured["skill"] == "deep-research-to-ppt"
        assert captured["trajectory"] is trajectory
        assert captured["patch_trajectory"] is trajectory
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_patch_auto_save():
    """Test: auto_save=True → patch persisted immediately without approval."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        skill_dir = tmp / "deep-research-to-ppt"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deep-research-to-ppt\nkind: team-skill\n---\n# Deep Research",
            encoding="utf-8",
        )

        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            auto_save=True,
            async_evolution=False,
        )

        trajectory = build_patch_trajectory("deep-research-to-ppt")
        ctx = _MockCtx()

        await rail.run_evolution(trajectory, ctx)

        events = await rail.drain_pending_approval_events()
        approval_events = [e for e in events if e.type == "chat.ask_user_question"]
        patches = rail._pending_patch_snapshots

        assert len(approval_events) == 0
        assert len(patches) == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_notify_team_completed_without_view_task():
    """Test: notify_team_completed triggers evolution without view_task interception."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            async_evolution=False,
        )

        rail._builder = TrajectoryBuilder(session_id="test-session", source="online")

        result = await rail.notify_team_completed(ctx=None)

        assert result is True
        assert rail._evolution_in_progress is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_notify_team_completed_idempotent():
    """Test: notify_team_completed is idempotent — second call is a no-op."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            async_evolution=False,
        )

        rail._builder = TrajectoryBuilder(session_id="test-session", source="online")

        result1 = await rail.notify_team_completed(ctx=None)
        result2 = await rail.notify_team_completed(ctx=None)

        assert result1 is True
        assert result2 is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_notify_team_completed_no_trajectory():
    """Test: notify_team_completed returns False when no trajectory is available."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            async_evolution=False,
        )

        result = await rail.notify_team_completed(ctx=None)

        assert result is False
        assert rail._evolution_in_progress is False
        assert mock_llm.call_count == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_notify_team_completed_blocks_only_while_async_evolution_runs():
    """Async evolution should be re-entrant after completion, but not while running."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        skill_dir = tmp / "deep-research-to-ppt"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deep-research-to-ppt\nkind: team-skill\n---\n# Deep Research",
            encoding="utf-8",
        )

        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            auto_save=False,
            async_evolution=True,
        )
        rail._builder = TrajectoryBuilder(session_id="test-session", source="online")
        for step in build_patch_trajectory("deep-research-to-ppt").steps:
            rail._builder.record_step(step)

        result1 = await rail.notify_team_completed(ctx=None)
        result2 = await rail.notify_team_completed(ctx=None)
        events1 = await rail.drain_pending_approval_events(wait=True, timeout=5.0)
        result3 = await rail.notify_team_completed(ctx=None)
        events2 = await rail.drain_pending_approval_events(wait=True, timeout=5.0)

        assert result1 is True
        assert result2 is False
        assert result3 is True
        assert rail._evolution_in_progress is False
        assert any(event.type == "chat.ask_user_question" for event in events1)
        assert any(event.type == "chat.ask_user_question" for event in events2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class FailingLLM:
    """LLM stub that always times out to force a visible evolution failure."""

    async def invoke(self, messages: list, model: str = "", **kwargs) -> Any:
        raise asyncio.TimeoutError("request timed out")


@pytest.mark.asyncio
async def test_async_evolution_failure_is_buffered_and_visible():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        skill_dir = tmp / "deep-research-to-ppt"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deep-research-to-ppt\nkind: team-skill\n---\n# Deep Research",
            encoding="utf-8",
        )

        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=FailingLLM(),
            model="mock-model",
            auto_save=False,
            async_evolution=True,
        )
        rail._builder = TrajectoryBuilder(session_id="test-session", source="online")
        for step in build_patch_trajectory("deep-research-to-ppt").steps:
            rail._builder.record_step(step)

        result = await rail.notify_team_completed(ctx=None)
        await rail.drain_pending_approval_events(wait=True, timeout=5.0)
        outcomes = rail.drain_evolution_outcomes()

        assert result is True
        assert rail._evolution_in_progress is False
        assert outcomes
        assert outcomes[-1]["status"] == "failed"
        assert "team skill evolution failed" in outcomes[-1]["message"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_run_evolution_uses_team_trajectory_store():
    """Test: when team_trajectory_store is configured, evolution aggregates from store."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        # Setup: create team store with two members
        team_store = InMemoryTrajectoryStore()

        # Member 1: leader with collaborative steps
        steps1 = [
            _build_member_step("spawn_member", {"name": "researcher-1"}, start_time_ms=100),
            _build_member_step("view_task", {}, start_time_ms=500),
        ]
        t1 = _build_team_store_trajectory("leader", "session-1", steps1)
        team_store.save(t1)

        # Member 2: researcher reads the team skill on disk
        steps2 = [
            _build_member_step("read_file", "team_skills/deep-research-to-ppt/SKILL.md", start_time_ms=200),
        ]
        t2 = _build_team_store_trajectory("researcher", "session-1", steps2)
        team_store.save(t2)

        # Setup: skill on disk
        skill_dir = tmp / "deep-research-to-ppt"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deep-research-to-ppt\nkind: team-skill\n---\n# Deep Research",
            encoding="utf-8",
        )

        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            auto_save=False,
            async_evolution=False,
            team_trajectory_store=team_store,
        )

        # Build a minimal trajectory (just needs session_id)
        trajectory = Trajectory(
            execution_id="test-001",
            session_id="session-1",
            source="online",
            steps=[],
        )
        ctx = _MockCtx()

        await rail.run_evolution(trajectory, ctx)

        events = await rail.drain_pending_approval_events()
        approval_events = [e for e in events if e.type == "chat.ask_user_question"]

        # The MockLLM returns canned responses, so approval events should be emitted
        assert len(approval_events) == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_run_evolution_filters_non_collaborative_steps():
    """Test: team store aggregation filters out internal steps."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        team_store = InMemoryTrajectoryStore()

        steps = [
            # Collaborative (has invoke_id meta)
            _build_member_step("spawn_member", {"name": "r1"}, start_time_ms=100, meta={"invoke_id": "inv-1"}),
            # Internal LLM (should be filtered — no cross-member markers)
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="gpt-4", messages=[]),
                meta={"operator_id": "leader/llm_main"},
                start_time_ms=200,
            ),
            # Collaborative (read_file of team skill)
            _build_member_step("read_file", "team_skills/deep-research-to-ppt/SKILL.md", start_time_ms=250),
            # Collaborative (view_task is a collaborative tool)
            _build_member_step("view_task", {}, start_time_ms=300),
        ]
        traj = _build_team_store_trajectory("leader", "session-1", steps)
        team_store.save(traj)

        skill_dir = tmp / "deep-research-to-ppt"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deep-research-to-ppt\nkind: team-skill\n---\n# Deep Research",
            encoding="utf-8",
        )

        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            auto_save=False,
            async_evolution=False,
            team_trajectory_store=team_store,
        )

        trajectory = Trajectory(
            execution_id="test-002",
            session_id="session-1",
            source="online",
            steps=[],
        )
        ctx = _MockCtx()

        await rail.run_evolution(trajectory, ctx)

        events = await rail.drain_pending_approval_events()
        approval_events = [e for e in events if e.type == "chat.ask_user_question"]

        # Collaborative steps remain after filtering, so evolution proceeds
        assert len(approval_events) == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_run_evolution_keeps_full_leader_trajectory():
    """Team analysis keeps leader internal steps while filtering teammate internals."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        team_store = InMemoryTrajectoryStore()

        leader_steps = [
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="gpt-4", messages=[]),
                meta={"operator_id": "leader/llm_main"},
                start_time_ms=100,
            ),
            _build_member_step("view_task", {}, start_time_ms=300),
        ]
        member_steps = [
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="gpt-4", messages=[]),
                meta={"operator_id": "researcher/llm_main"},
                start_time_ms=150,
            ),
            _build_member_step(
                "read_file",
                "team_skills/deep-research-to-ppt/SKILL.md",
                start_time_ms=250,
            ),
        ]
        team_store.save(_build_team_store_trajectory("leader", "session-1", leader_steps))
        team_store.save(_build_team_store_trajectory("researcher", "session-1", member_steps))

        skill_dir = tmp / "deep-research-to-ppt"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deep-research-to-ppt\nkind: team-skill\n---\n# Deep Research",
            encoding="utf-8",
        )

        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            auto_save=False,
            async_evolution=False,
            team_trajectory_store=team_store,
        )

        captured: dict[str, Trajectory] = {}

        async def _detect_trajectory_issues(captured_trajectory, _content):
            captured["trajectory"] = captured_trajectory
            return [TrajectoryIssue(issue_type="workflow", description="keep leader full")]

        rail._detect_trajectory_issues = _detect_trajectory_issues
        rail._optimizer.generate_trajectory_patch = AsyncMock(return_value=None)

        trajectory = Trajectory(
            execution_id="test-003",
            session_id="session-1",
            source="online",
            steps=[],
        )

        await rail.run_evolution(trajectory, _MockCtx())

        used_trajectory = captured["trajectory"]
        assert [step.kind for step in used_trajectory.steps] == ["llm", "tool", "tool"]
        assert used_trajectory.steps[0].meta["operator_id"] == "leader/llm_main"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detect_used_team_skill_prefers_skill_tool_and_filters_non_team_skill():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        regular_dir = tmp / "regular-skill"
        regular_dir.mkdir(parents=True)
        (regular_dir / "SKILL.md").write_text(
            "---\nname: regular-skill\nkind: skill\n---\n# Regular Skill",
            encoding="utf-8",
        )

        team_dir = tmp / "deep-research-to-ppt"
        team_dir.mkdir(parents=True)
        (team_dir / "SKILL.md").write_text(
            "---\nname: deep-research-to-ppt\nkind: team-skill\n---\n# Team Skill",
            encoding="utf-8",
        )

        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            async_evolution=False,
        )

        trajectory = Trajectory(
            execution_id="detect-001",
            session_id="session-1",
            source="online",
            steps=[
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="skill_tool",
                        call_args={"skill_name": "regular-skill", "relative_file_path": "reference.md"},
                    ),
                ),
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="read_file",
                        call_args="/workspace/deep-research-to-ppt/SKILL.md",
                    ),
                ),
            ],
        )

        result = rail._detect_used_team_skill(trajectory)

        assert result == "deep-research-to-ppt"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detect_used_team_skill_prefers_skills_path_over_legacy_skill_md():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        legacy_dir = tmp / "legacy-team"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "SKILL.md").write_text(
            "---\nname: legacy-team\nkind: team-skill\n---\n# Legacy Team",
            encoding="utf-8",
        )

        modern_dir = tmp / "modern-team"
        modern_dir.mkdir(parents=True)
        (modern_dir / "SKILL.md").write_text(
            "---\nname: modern-team\nkind: team-skill\n---\n# Modern Team",
            encoding="utf-8",
        )

        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            async_evolution=False,
        )

        trajectory = Trajectory(
            execution_id="detect-002",
            session_id="session-1",
            source="online",
            steps=[
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="read_file",
                        call_args="/workspace/legacy-team/SKILL.md",
                    ),
                ),
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="read_file",
                        call_args="/workspace/skills/modern-team/reference/guide.md",
                    ),
                ),
            ],
        )

        result = rail._detect_used_team_skill(trajectory)

        assert result == "modern-team"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_is_team_skill_checks_frontmatter_kind_only():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        fake_team_dir = tmp / "fake-team"
        fake_team_dir.mkdir(parents=True)
        (fake_team_dir / "SKILL.md").write_text(
            "---\nname: fake-team\nkind: skill\n---\n# Body\nkind: team-skill",
            encoding="utf-8",
        )

        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            async_evolution=False,
        )

        assert rail._is_team_skill("fake-team") is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def main():
    await test_patch_path()
    await test_patch_auto_save()
    await test_notify_team_completed_without_view_task()
    await test_notify_team_completed_idempotent()
    await test_notify_team_completed_no_trajectory()
    await test_run_evolution_uses_team_trajectory_store()
    await test_run_evolution_filters_non_collaborative_steps()
    await test_run_evolution_keeps_full_leader_trajectory()


if __name__ == "__main__":
    asyncio.run(main())
