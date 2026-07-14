# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lightweight test for TeamSkillRail evolution flow.

No real LLM calls — uses a mock that returns canned JSON.
No real agent/service needed — constructs synthetic Trajectory directly.
Typical run time: < 3 seconds.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.experience.online_orchestrator import OnlineEvolutionOrchestrator
from openjiuwen.agent_evolving.experience.skill_experience_manager import ExperienceManager
from openjiuwen.agent_evolving.experience.tracker import ExperienceTracker
from openjiuwen.agent_evolving.experience.types import OnlineEvolutionResult, PendingChange
from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.signal import (
    EvolutionSignal,
    TeamSignalDetector,
    TeamSignalType,
    TrajectoryIssue,
    UserIntent,
)
from openjiuwen.agent_evolving.trajectory import (
    InMemoryTrajectoryRegistry,
    MemberTrajectorySnapshot,
    TrajectoryBuilder,
)
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
)
from openjiuwen.agent_evolving.types import ApplyResult, UpdateValue
from openjiuwen.core.single_agent.rail.base import InvokeInputs, ModelCallInputs, ToolCallInputs
from openjiuwen.harness.rails.evolution.approval_runtime import EvolutionApprovalRuntime
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionTriggerPoint
from openjiuwen.harness.rails.evolution.team_skill_evolution_rail import (
    infer_team_skill_from_trajectory,
    is_completed_team_task_view,
)
from openjiuwen.harness.rails.skills.team_skill_rail import (
    TeamSkillRail,
)

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


def _bind_team_runtime(rail: TeamSkillRail) -> None:
    rail._manager = ExperienceManager(
        store=rail._store,
        scorer=getattr(rail, "_scorer", None),
        kind="team-skill",
        language=getattr(rail._generator, "language", getattr(rail._generator, "_language", "cn")),
        skill_ops=rail._experience_skill_ops,
        pending_approval_snapshots=rail._pending_record_snapshots,
        pending_governance=rail._pending_governance,
    )
    rail._approval_runtime = EvolutionApprovalRuntime(
        manager=rail._manager,
        pending_approval_snapshots=rail._pending_approval_snapshots,
    )
    rail._online_orchestrator = OnlineEvolutionOrchestrator(
        store=rail._store,
        updater=rail._online_updater,
        manager=rail._manager,
        skill_ops=rail._experience_skill_ops,
        request_id_prefix="team_skill_evolve",
        stage_source="team_skill_experience_updater",
    )


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


class _DummyToolMsg:
    def __init__(self, content: Any):
        self.content = content


def _progress_events(events):
    return [event for event in events if event.payload.get("evolution_meta", {}).get("event_kind") == "progress"]


def _make_record(skill_name: str, *, content: str = "experience content") -> EvolutionRecord:
    return EvolutionRecord.make(
        source=f"signal:{skill_name}",
        context="ctx",
        change=EvolutionPatch(
            section="Workflow",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
        ),
    )


def _no_records_result(skill_name: str = "team-skill-a") -> OnlineEvolutionResult:
    return OnlineEvolutionResult(
        skill_name=skill_name,
        status="no_evolution_no_records",
        message=f"no applied updates for skill={skill_name}",
    )


def _handle_result(
    request: Any,
    *,
    online_result: OnlineEvolutionResult | None = None,
    skill_name: str = "research-team",
) -> OnlineEvolutionResult:
    if online_result is None:
        online_result = OnlineEvolutionResult(
            skill_name=skill_name,
            status="staged" if request is not None else "no_evolution_no_records",
            request=request,
        )
    return online_result


# ============================================================
# Trajectory builders
# ============================================================


def build_patch_trajectory(skill_name: str = "deep-research-to-ppt") -> Trajectory:
    """Build a trajectory that references an existing team skill (triggers PATCH)."""
    steps = []
    steps.append(
        TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(
                tool_name="read_file",
                call_args=f"team_skills/{skill_name}/SKILL.md",
                call_result="---\nname: deep-research-to-ppt\n---\n# ...",
            ),
        )
    )
    for i in range(3):
        steps.append(
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="spawn_member",
                    call_args={"name": f"researcher-{i}", "desc": f"调研{i}"},
                    call_result={"status": "spawned"},
                ),
            )
        )
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
    member_role: str | None = None,
) -> Trajectory:
    """Build a trajectory with member_id meta for team store."""
    meta = {"member_id": member_id}
    if member_role is not None:
        meta["member_role"] = member_role
    return Trajectory(
        execution_id=f"exec-{member_id}",
        session_id=session_id,
        source="online",
        steps=steps,
        meta=meta,
    )


def _install_team_skill(skills_dir: Path, skill_name: str = "deep-research-to-ppt") -> None:
    skill_dir = skills_dir / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_name}\nkind: team-skill\n---\n# Team Skill",
        encoding="utf-8",
    )


def _publish_member_snapshot(
    source: InMemoryTrajectoryRegistry,
    *,
    member_id: str,
    session_id: str,
    steps: list[TrajectoryStep],
    member_role: str | None,
    team_id: str = "team-a",
    recorded_at_ms: int = 1000,
) -> None:
    source.publish_member_trajectory(
        MemberTrajectorySnapshot.make(
            team_id=team_id,
            member_id=member_id,
            member_role=member_role,
            trajectory=_build_team_store_trajectory(
                member_id,
                session_id,
                steps,
                member_role=member_role,
            ),
            recorded_at_ms=recorded_at_ms,
        )
    )


def _tool_names(trajectory: Trajectory) -> list[str]:
    return [step.detail.tool_name for step in trajectory.steps if step.detail is not None]


def _capture_trajectory_signals(rail: TeamSkillRail, captured: dict[str, Any]) -> None:
    async def _detect_trajectory_signals(*, trajectory, skill_name, skill_content):
        captured["trajectory"] = trajectory
        captured["skill_name"] = skill_name
        captured["skill_content"] = skill_content
        return [_build_trajectory_issue_signal(skill_name, skill_content)]

    rail._team_signal_detector = MagicMock(
        detect_trajectory_signals=AsyncMock(side_effect=_detect_trajectory_signals),
        detect_user_intent=AsyncMock(return_value=None),
    )
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())


def _build_trajectory_issue_signal(
    skill_name: str = "team-skill-a",
    skill_content: str = "team skill content",
) -> EvolutionSignal:
    return EvolutionSignal(
        signal_type=TeamSignalType.TRAJECTORY_ISSUE.value,
        section="",
        excerpt="Detected team skill trajectory issues requiring evolution.",
        skill_name=skill_name,
        context={
            "source": "passive_trajectory",
            "skill_content": skill_content,
            "trajectory_issues": [
                {
                    "issue_type": "workflow",
                    "description": "needs constraints",
                    "affected_role": "",
                    "severity": "medium",
                }
            ],
        },
    )


def _make_request_user_evolution_rail(
    *,
    store: Any | None = None,
    builder: Any | None = None,
    trajectory_source: Any | None = None,
    team_id: str | None = None,
    handle: Any | None = None,
    detector_signals: list[EvolutionSignal] | None = None,
    detector_error: Exception | None = None,
) -> TeamSkillRail:
    rail = TeamSkillRail.__new__(TeamSkillRail)
    if store is None:
        store = MagicMock()
        store.skill_exists.return_value = True
        store.read_skill_content = AsyncMock(return_value="# research-team")

    if detector_error is None:
        detect_trajectory_signals = AsyncMock(return_value=detector_signals or [])
    else:
        detect_trajectory_signals = AsyncMock(side_effect=detector_error)

    rail._store = store
    rail._builder = builder
    rail._trajectory_source = trajectory_source
    rail._team_id = team_id
    rail._pending_record_snapshots = {}
    rail._pending_host_events = []
    rail._emit_progress = MagicMock()
    handle = handle or AsyncMock(return_value=None)

    async def _handle_with_result(**kwargs):
        result = await handle(**kwargs)
        if isinstance(result, tuple):
            request, online_result = result
            return online_result
        return _handle_result(result, skill_name=kwargs["skill_name"])

    rail._handle_evolution_from_signals_with_result = _handle_with_result
    rail._team_signal_detector = MagicMock(detect_trajectory_signals=detect_trajectory_signals)
    return rail


# ============================================================
# Test cases
# ============================================================


def test_team_skill_evolution_rail_defaults_fixed_member_role_to_leader(tmp_path):
    registry = InMemoryTrajectoryRegistry()

    rail = TeamSkillRail(
        skills_dir=str(tmp_path),
        llm=MockLLM(),
        model="mock-model",
        team_id="team-a",
        trajectory_sink=registry,
        auto_scan=False,
        async_evolution=False,
    )

    assert rail._member_role == "leader"
    rail.set_trajectory_sink(registry, team_id="team-a")
    assert rail._member_role == "leader"


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
        patches = rail._pending_record_snapshots

        if patches:
            req_id = list(patches.keys())[0]
            await rail.on_approve_record(req_id)

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

        async def _detect_trajectory_signals(*, trajectory, skill_name, skill_content):
            captured["detector_trajectory"] = trajectory
            captured["detector_skill_name"] = skill_name
            captured["detector_skill_content"] = skill_content
            return [
                EvolutionSignal(
                    signal_type=TeamSignalType.TRAJECTORY_ISSUE.value,
                    section="",
                    excerpt="Detected team skill trajectory issues requiring evolution.",
                    skill_name=skill_name,
                    context={
                        "source": "passive_trajectory",
                        "skill_content": skill_content,
                        "trajectory_issues": [
                            {
                                "issue_type": "coordination",
                                "description": "handoff gap",
                                "affected_role": "",
                                "severity": "medium",
                            }
                        ],
                    },
                )
            ]

        async def _consume_signal(
            *,
            skill_name,
            trajectory,
            signals,
            auto_approve,
            user_query="",
            messages=None,
        ):
            captured["trajectory"] = trajectory
            captured["skill"] = skill_name
            signal = signals[0]
            captured["signal"] = signal
            captured["current_content"] = signal.context["skill_content"]
            captured["issues"] = signal.context["trajectory_issues"]
            captured["source"] = signal.context["source"]
            captured["auto_approve"] = auto_approve
            captured["messages"] = messages
            return None

        rail._team_signal_detector = MagicMock(
            detect_trajectory_signals=AsyncMock(side_effect=_detect_trajectory_signals),
            detect_user_intent=AsyncMock(return_value=None),
        )

        async def _consume_signal_with_result(**kwargs):
            await _consume_signal(**kwargs)
            return _handle_result(None, skill_name=kwargs["skill_name"])

        rail._handle_evolution_from_signals_with_result = _consume_signal_with_result

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
        assert captured["signal"].signal_type == TeamSignalType.TRAJECTORY_ISSUE.value
        assert captured["source"] == "passive_trajectory"
        assert captured["detector_trajectory"].session_id == "test-session-2"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_stage_evolution_from_signals_does_not_hardcode_workflow_signal_section():
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._store = MagicMock()
        rail._store.skill_exists.return_value = True
        rail._store.read_skill_content = AsyncMock(return_value="current")
        rail._store.get_pending_records = AsyncMock(side_effect=[[], [], []])
        rail._experience_skill_ops = {}
        rail._pending_record_snapshots = {}
        rail._pending_host_events = []
        rail._pending_governance = {}
        rail._generator = MagicMock(language="cn")
        rail._scorer = MagicMock()
        rail._online_updater = MagicMock()
        rail._online_updater.bind = MagicMock()

        record = EvolutionRecord.make(
            source="team_skill_trajectory_patch",
            context="ctx",
            change=EvolutionPatch(
                section="Constraints",
                action="append",
                content="Add an explicit review timeout.",
                target=EvolutionTarget.BODY,
            ),
        )
        rail._online_updater.process = AsyncMock(
            return_value={("skill_experience_team-skill-a", "experiences"): [record]}
        )
        pending = PendingChange.make("team-skill-a", [record])
        manager = MagicMock()
        manager.stage_apply_results = MagicMock(
            return_value=MagicMock(
                request_id=pending.change_id,
                pending_change=pending,
                proposal=MagicMock(records=[record]),
            )
        )
        rail._manager = manager
        rail._online_orchestrator = OnlineEvolutionOrchestrator(
            store=rail._store,
            updater=rail._online_updater,
            manager=manager,
            skill_ops=rail._experience_skill_ops,
            request_id_prefix="team_skill_evolve",
            stage_source="team_skill_experience_updater",
        )

        result = await rail._stage_evolution_from_signals(
            "team-skill-a",
            trajectory=Trajectory(execution_id="e1", session_id="s1", source="online", steps=[]),
            signals=[
                EvolutionSignal(
                    signal_type="trajectory_issue",
                    section="",
                    excerpt="detected issue",
                    skill_name="team-skill-a",
                    context={"trajectory_issues": [{"issue_type": "timeout"}]},
                )
            ],
            auto_approve=False,
        )

        bind_kwargs = rail._online_updater.bind.call_args.kwargs
        assert bind_kwargs["online_contexts"]["team-skill-a"].trajectory.session_id == "s1"
        passed_signals = rail._online_updater.process.await_args.args[1]
        assert len(passed_signals) == 1
        assert passed_signals[0].section == ""
        assert manager.stage_apply_results.call_args.kwargs["signal_type"] == "trajectory_issue"
        assert manager.stage_apply_results.call_args.kwargs["signal_source"] is None
        assert manager.stage_apply_results.call_args.kwargs["user_query"] == ""
        request = result.request
        assert request is not None
        assert request.pending_change.payload[0].change.section == "Constraints"


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
        expected_messages = list(snapshot["messages"])

        captured: dict[str, Any] = {}

        async def _detect_trajectory_signals(*, trajectory, skill_name, skill_content):
            captured["trajectory"] = trajectory
            captured["skill_name"] = skill_name
            captured["skill_content"] = skill_content
            return [
                EvolutionSignal(
                    signal_type=TeamSignalType.TRAJECTORY_ISSUE.value,
                    section="",
                    excerpt="Detected team skill trajectory issues requiring evolution.",
                    skill_name=skill_name,
                    context={
                        "source": "passive_trajectory",
                        "skill_content": skill_content,
                        "trajectory_issues": [
                            {
                                "issue_type": "workflow",
                                "description": "需要优化",
                                "affected_role": "",
                                "severity": "medium",
                            }
                        ],
                    },
                )
            ]

        async def _consume_signal(
            *,
            skill_name,
            trajectory,
            signals,
            auto_approve,
            user_query="",
            messages=None,
        ):
            captured["patch_trajectory"] = trajectory
            captured["skill"] = skill_name
            captured["signal"] = signals[0]
            captured["messages"] = messages
            return None

        rail._team_signal_detector = MagicMock(
            detect_trajectory_signals=AsyncMock(side_effect=_detect_trajectory_signals),
            detect_user_intent=AsyncMock(return_value=None),
        )

        async def _consume_signal_with_result(**kwargs):
            await _consume_signal(**kwargs)
            return _handle_result(None, skill_name=kwargs["skill_name"])

        rail._handle_evolution_from_signals_with_result = _consume_signal_with_result

        await rail.run_evolution(trajectory, ctx=None, snapshot=snapshot)

        assert snapshot["messages"] == expected_messages
        assert captured["skill"] == "deep-research-to-ppt"
        assert captured["trajectory"] is trajectory
        assert captured["patch_trajectory"] is trajectory
        assert captured["messages"] == expected_messages
        assert captured["signal"].context["source"] == "passive_trajectory"
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
        patches = rail._pending_record_snapshots

        assert len(approval_events) == 0
        assert len(patches) == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_auto_scan_and_auto_save_properties():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            auto_scan=False,
            auto_save=True,
            async_evolution=False,
        )

        assert rail.auto_scan is False
        assert rail.auto_save is True

        rail.auto_scan = True
        rail.auto_save = False

        assert rail.auto_scan is True
        assert rail.auto_save is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_team_experience_tracker_is_initialized():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            eval_interval=3,
            async_evolution=False,
        )

        assert isinstance(rail._experience_tracker, ExperienceTracker)
        assert rail.evolution_config["eval_interval"] == 3
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_notify_team_completed_without_view_task():
    """Test: notify_team_completed only marks completion and defers evolution to after_invoke."""
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
        rail.run_evolution = AsyncMock()

        result = await rail.notify_team_completed(ctx=None)
        assert rail.run_evolution.await_count == 0

        await rail.after_invoke(_MockCtx(inputs=InvokeInputs(query="round 1", conversation_id="test-session")))

        assert result is True
        assert rail.run_evolution.await_count == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_notify_team_completed_repeated_mark_keeps_same_session():
    """Repeated host completion marks keep the same pending trajectory session."""
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
        assert rail._host_completion_pending_session_id == "test-session"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_notify_team_completed_mark_survives_next_before_invoke():
    """Host completion marks are rail-local and survive the next invoke start."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            async_evolution=False,
        )
        rail.run_evolution = AsyncMock()

        await rail.before_invoke(_MockCtx(inputs=InvokeInputs(query="round 1", conversation_id="test-session")))
        await rail.after_model_call(
            _MockCtx(
                inputs=ModelCallInputs(
                    messages=[{"role": "user", "content": "round 1"}],
                    response={"role": "assistant", "content": "ok"},
                )
            )
        )
        result = await rail.notify_team_completed(ctx=None)

        await rail.before_invoke(_MockCtx(inputs=InvokeInputs(query="round 2", conversation_id="test-session")))
        await rail.after_invoke(_MockCtx(inputs=InvokeInputs(query="round 2", conversation_id="test-session")))
        await rail.after_invoke(_MockCtx(inputs=InvokeInputs(query="round 3", conversation_id="test-session")))

        assert result is True
        assert rail.run_evolution.await_count == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_notify_team_completed_mark_does_not_leak_to_new_session():
    """Host completion marks only apply to the session that was marked."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            async_evolution=False,
        )
        rail.run_evolution = AsyncMock()

        await rail.before_invoke(_MockCtx(inputs=InvokeInputs(query="round 1", conversation_id="session-a")))
        await rail.after_model_call(
            _MockCtx(
                inputs=ModelCallInputs(
                    messages=[{"role": "user", "content": "session a"}],
                    response={"role": "assistant", "content": "ok"},
                )
            )
        )
        result = await rail.notify_team_completed(ctx=None)

        await rail.before_invoke(_MockCtx(inputs=InvokeInputs(query="round 2", conversation_id="session-b")))
        await rail.after_model_call(
            _MockCtx(
                inputs=ModelCallInputs(
                    messages=[{"role": "user", "content": "session b"}],
                    response={"role": "assistant", "content": "ok"},
                )
            )
        )
        await rail.after_invoke(_MockCtx(inputs=InvokeInputs(query="round 2", conversation_id="session-b")))

        assert result is True
        assert rail.run_evolution.await_count == 0

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
        assert mock_llm.call_count == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_auto_scan_false_disables_passive_view_task_trigger():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            auto_scan=False,
            async_evolution=False,
        )
        rail.run_evolution = AsyncMock()

        await rail.before_invoke(_MockCtx(inputs=InvokeInputs(query="round 1", conversation_id="test-session")))
        await rail.after_tool_call(
            _MockCtx(
                inputs=ToolCallInputs(
                    tool_name="view_task",
                    tool_args={},
                    tool_result="task-a completed\ntask-b completed",
                )
            )
        )
        await rail.after_invoke(_MockCtx(inputs=InvokeInputs(query="round 1", conversation_id="test-session")))

        assert rail._passive_evolution_pending is False
        assert rail.run_evolution.await_count == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_auto_scan_false_disables_notify_team_completed():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            auto_scan=False,
            async_evolution=False,
        )
        rail._builder = TrajectoryBuilder(session_id="test-session", source="online")

        result = await rail.notify_team_completed(ctx=None)

        assert result is False
        assert rail._passive_evolution_pending is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_run_evolution_returns_immediately_when_auto_scan_disabled():
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._auto_scan = False
        rail._trajectory_source = MagicMock()
        rail._detect_used_team_skill = Mock(return_value="research-team")
        rail._emit_progress = MagicMock()

        await rail.run_evolution(
            Trajectory(execution_id="exec-1", session_id="sess-1", source="online", steps=[]),
            _MockCtx(),
        )

        rail._trajectory_source.get_trajectory.assert_not_called()
        rail._detect_used_team_skill.assert_not_called()
        rail._emit_progress.assert_not_called()


@pytest.mark.asyncio
async def test_team_record_presented_experiences_delegates_to_tracker():
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._experience_tracker = Mock()
        rail._experience_tracker.record_presented = AsyncMock()
        rail._experience_tracker.record_presented_records = AsyncMock()
        session = SimpleNamespace()

        await rail.record_presented_experiences(
            "team-skill-a",
            "presentation snippet",
            session=session,
        )
        await rail.record_presented_experiences(
            "team-skill-a",
            "presentation snippet",
            session=session,
            record_ids=["ev_1"],
        )

        rail._experience_tracker.record_presented.assert_awaited_once_with(
            session=session,
            skill_name="team-skill-a",
            presentation_snippet="presentation snippet",
        )
        rail._experience_tracker.record_presented_records.assert_awaited_once_with(
            session=session,
            skill_name="team-skill-a",
            presentation_snippet="presentation snippet",
            record_ids=["ev_1"],
        )


@pytest.mark.asyncio
async def test_team_snapshot_consumes_experience_tracker_state():
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._auto_scan = True
        rail._experience_tracker = Mock()
        record = _make_record("team-skill-a")
        presented_entries = [("team-skill-a", record, "snippet")]
        rail._experience_tracker.consume_eval_state = Mock(return_value=presented_entries)

        ctx = _MockCtx(
            inputs=InvokeInputs(query="round 1", conversation_id="test-session"),
            session=SimpleNamespace(),
            context=_MsgContext(messages=[{"role": "user", "content": "hello"}]),
        )
        trajectory = Trajectory(execution_id="exec-1", session_id="test-session", source="online", steps=[])

        snapshot = await rail._snapshot_for_evolution(trajectory, ctx)

        assert snapshot is not None
        assert snapshot["skill_name"] == "team-skill"
        assert snapshot["presented_entries"] == presented_entries
        rail._experience_tracker.consume_eval_state.assert_called_once_with(ctx.session)


@pytest.mark.asyncio
async def test_view_task_completion_marks_round_and_triggers_once_after_invoke():
    """Passive view_task detection should defer evolution until after_invoke."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            auto_save=False,
            async_evolution=False,
        )
        rail.run_evolution = AsyncMock()

        await rail.before_invoke(_MockCtx(inputs=InvokeInputs(query="round 1", conversation_id="test-session")))
        await rail.after_tool_call(
            _MockCtx(
                inputs=ToolCallInputs(
                    tool_name="view_task",
                    tool_args={},
                    tool_result="task-a completed\ntask-b completed",
                )
            )
        )
        await rail.after_tool_call(
            _MockCtx(
                inputs=ToolCallInputs(
                    tool_name="view_task",
                    tool_args={},
                    tool_result="task-a completed\ntask-b completed",
                )
            )
        )

        assert rail.run_evolution.await_count == 0

        await rail.after_invoke(_MockCtx(inputs=InvokeInputs(query="round 1", conversation_id="test-session")))

        assert rail.run_evolution.await_count == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_team_after_tool_call_records_skill_tool_evolution_detail_read(tmp_path):
    skill_dir = tmp_path / "team-skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: team-skill-a\nkind: team-skill\n---\n# Team Skill",
        encoding="utf-8",
    )
    rail = TeamSkillRail(
        skills_dir=str(tmp_path),
        llm=MockLLM(),
        model="mock-model",
        async_evolution=False,
    )
    rail._experience_tracker.record_presented_records = AsyncMock()
    session = SimpleNamespace()

    await rail._on_after_tool_call(
        _MockCtx(
            inputs=ToolCallInputs(
                tool_name="skill_tool",
                tool_args={
                    "skill_name": "team-skill-a",
                    "relative_file_path": "evolution/workflow.md",
                },
                tool_result=SimpleNamespace(data={"skill_content": "### [ev_body] Coordinate reviewers"}),
            ),
            session=session,
        )
    )

    rail._experience_tracker.record_presented_records.assert_awaited_once_with(
        session=session,
        skill_name="team-skill-a",
        presentation_snippet="### [ev_body] Coordinate reviewers",
        record_ids=["ev_body"],
    )


@pytest.mark.asyncio
async def test_team_after_tool_call_records_read_file_evolution_detail_read(tmp_path):
    skill_dir = tmp_path / "team-skill-a"
    experience_file = skill_dir / "evolution" / "workflow.md"
    experience_file.parent.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: team-skill-a\nkind: team-skill\n---\n# Team Skill",
        encoding="utf-8",
    )
    rail = TeamSkillRail(
        skills_dir=str(tmp_path),
        llm=MockLLM(),
        model="mock-model",
        async_evolution=False,
    )
    rail._experience_tracker.record_presented_records = AsyncMock()
    session = SimpleNamespace()

    await rail._on_after_tool_call(
        _MockCtx(
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_args={"file_path": str(experience_file)},
                tool_msg=_DummyToolMsg("### [ev_body] Coordinate reviewers"),
            ),
            session=session,
        )
    )

    rail._experience_tracker.record_presented_records.assert_awaited_once_with(
        session=session,
        skill_name="team-skill-a",
        presentation_snippet="### [ev_body] Coordinate reviewers",
        record_ids=["ev_body"],
    )


@pytest.mark.asyncio
async def test_team_auto_scan_false_still_records_evolution_detail_read(tmp_path):
    skill_dir = tmp_path / "team-skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: team-skill-a\nkind: team-skill\n---\n# Team Skill",
        encoding="utf-8",
    )
    rail = TeamSkillRail(
        skills_dir=str(tmp_path),
        llm=MockLLM(),
        model="mock-model",
        auto_scan=False,
        async_evolution=False,
    )
    rail._experience_tracker.record_presented_records = AsyncMock()
    session = SimpleNamespace()

    await rail._on_after_tool_call(
        _MockCtx(
            inputs=ToolCallInputs(
                tool_name="skill_tool",
                tool_args={
                    "skill_name": "team-skill-a",
                    "relative_file_path": "evolution/workflow.md",
                },
                tool_result=SimpleNamespace(data={"skill_content": "### [ev_body] Coordinate reviewers"}),
            ),
            session=session,
        )
    )

    assert rail._passive_evolution_pending is False
    rail._experience_tracker.record_presented_records.assert_awaited_once_with(
        session=session,
        skill_name="team-skill-a",
        presentation_snippet="### [ev_body] Coordinate reviewers",
        record_ids=["ev_body"],
    )


@pytest.mark.asyncio
async def test_team_run_evolution_evaluates_presented_entries_when_no_skill_detected():
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._auto_scan = True
        rail._pending_host_events = []
        rail._trajectory_source = None
        rail._detect_used_team_skill = Mock(return_value=None)
        rail._experience_tracker = Mock()
        rail._experience_tracker.evaluate_presented = AsyncMock()
        record = _make_record("team-skill-a")
        presented_entries = [("team-skill-a", record, "snippet")]

        await rail.run_evolution(
            Trajectory(execution_id="exec-1", session_id="sess-1", source="online", steps=[]),
            snapshot={
                "trajectory": Trajectory(execution_id="exec-1", session_id="sess-1", source="online", steps=[]),
                "messages": [{"role": "user", "content": "hello"}],
                "presented_entries": presented_entries,
            },
        )

        rail._experience_tracker.evaluate_presented.assert_awaited_once_with(presented_entries)
        events = _progress_events(await rail.drain_pending_host_events())
        stages = [event.payload["evolution_meta"]["stage"] for event in events]
        contents = [event.payload["content"] for event in events]
        assert stages == ["started", "cancelled"]
        assert "team/swarm skill" in contents[-1]
        assert "no skill usage" in contents[-1]
        assert "cancelling" in contents[-1]


@pytest.mark.asyncio
async def test_team_handle_evolution_from_signals_emits_no_records_outcome():
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._pending_host_events = []
        rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())

        result = await rail._handle_evolution_from_signals_with_result(
            skill_name="team-skill-a",
            trajectory=Trajectory(execution_id="e1", session_id="s1", source="online", steps=[]),
            signals=[
                EvolutionSignal(
                    signal_type="trajectory_issue",
                    section="",
                    excerpt="issue",
                    skill_name="team-skill-a",
                )
            ],
            auto_approve=False,
            emit_host_events=True,
        )

        assert result.status == "no_evolution_no_records"
        events = await rail.drain_pending_host_events()
        outcomes = [
            event for event in events if event.payload.get("evolution_meta", {}).get("event_kind") == "outcome"
        ]
        assert outcomes
        assert outcomes[-1].payload["evolution_meta"]["status"] == "no_evolution_no_records"
        assert outcomes[-1].payload["evolution_meta"]["rail_kind"] == "team"
        assert outcomes[-1].payload["evolution_meta"]["skill_name"] == "team-skill-a"


@pytest.mark.asyncio
async def test_team_handle_evolution_emits_persistence_failed_without_auto_approved_finalize():
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._pending_host_events = []
        rail._pending_approval_snapshots = {}
        rail._manager = SimpleNamespace()
        failed_request = SimpleNamespace(request_id="req-failed")
        rail._stage_evolution_from_signals = AsyncMock(
            return_value=OnlineEvolutionResult(
                skill_name="team-skill-a",
                status="persistence_failed",
                request=failed_request,
                message="disk full",
            )
        )
        rail._approval_runtime = SimpleNamespace(
            _manager=rail._manager,
            _pending_approval_snapshots=rail._pending_approval_snapshots,
            finalize_staged_evolution_request=AsyncMock(return_value=failed_request),
        )

        result = await rail._handle_evolution_from_signals_with_result(
            skill_name="team-skill-a",
            trajectory=Trajectory(execution_id="e1", session_id="s1", source="online", steps=[]),
            signals=[
                EvolutionSignal(
                    signal_type="trajectory_issue",
                    section="",
                    excerpt="issue",
                    skill_name="team-skill-a",
                )
            ],
            auto_approve=True,
            emit_host_events=True,
        )

        assert result.request is failed_request
        rail._approval_runtime.finalize_staged_evolution_request.assert_not_awaited()
        events = await rail.drain_pending_host_events()
        outcomes = [
            event for event in events if event.payload.get("evolution_meta", {}).get("event_kind") == "outcome"
        ]
        assert outcomes
        assert outcomes[-1].payload["evolution_meta"]["status"] == "persistence_failed"
        assert outcomes[-1].payload["evolution_meta"]["stage"] == "failed"
        assert outcomes[-1].payload["evolution_meta"]["request_id"] == "req-failed"
        assert "disk full" in outcomes[-1].payload["content"]


@pytest.mark.asyncio
async def test_team_run_evolution_does_not_report_persistence_failed_as_ready():
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._auto_scan = True
        rail._auto_save = True
        rail._pending_host_events = []
        rail._trajectory_source = None
        rail._detect_used_team_skill = Mock(return_value="team-skill-a")
        rail._store = MagicMock()
        rail._store.read_skill_content = AsyncMock(return_value="team skill content")
        rail._team_signal_detector = MagicMock(
            detect_trajectory_signals=AsyncMock(
                return_value=[
                    EvolutionSignal(
                        signal_type=TeamSignalType.TRAJECTORY_ISSUE.value,
                        section="",
                        excerpt="issue",
                        skill_name="team-skill-a",
                    )
                ]
            ),
        )
        rail._detect_user_request = AsyncMock(return_value=None)
        rail._experience_tracker = MagicMock()
        rail._experience_tracker.evaluate_presented = AsyncMock()
        failed_request = SimpleNamespace(request_id="team-req-failed")
        rail._stage_evolution_from_signals = AsyncMock(
            return_value=OnlineEvolutionResult(
                skill_name="team-skill-a",
                status="persistence_failed",
                request=failed_request,
                message="disk full",
            )
        )

        await rail.run_evolution(
            Trajectory(execution_id="e1", session_id="s1", source="online", steps=[]),
            snapshot={"messages": [], "presented_entries": []},
        )

        events = await rail.drain_pending_host_events()
        outcomes = [
            event for event in events if event.payload.get("evolution_meta", {}).get("event_kind") == "outcome"
        ]
        progress_contents = [
            event.payload["content"]
            for event in events
            if event.payload.get("evolution_meta", {}).get("event_kind") == "progress"
        ]
        assert outcomes
        assert outcomes[-1].payload["evolution_meta"]["status"] == "persistence_failed"
        assert outcomes[-1].payload["evolution_meta"]["request_id"] == "team-req-failed"
        assert not any("evolution request ready" in content for content in progress_contents)


@pytest.mark.asyncio
async def test_notify_team_completed_allows_new_invoke_after_async_evolution():
    """Async evolution should still allow a new invoke round to schedule another run."""
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
        events0 = await rail.drain_pending_approval_events(wait=False)
        await rail.after_invoke(_MockCtx(inputs=InvokeInputs(query="round 1", conversation_id="test-session")))
        events1 = await rail.drain_pending_approval_events(wait=True, timeout=5.0)
        await rail.before_invoke(_MockCtx(inputs=InvokeInputs(query="round 2", conversation_id="test-session")))
        for step in build_patch_trajectory("deep-research-to-ppt").steps:
            rail._builder.record_step(step)
        result3 = await rail.notify_team_completed(ctx=None)
        await rail.after_invoke(_MockCtx(inputs=InvokeInputs(query="round 2", conversation_id="test-session")))
        events2 = await rail.drain_pending_approval_events(wait=True, timeout=5.0)

        assert result1 is True
        assert result2 is True
        assert result3 is True
        assert not any(event.type == "chat.ask_user_question" for event in events0)
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
        await rail.after_invoke(_MockCtx(inputs=InvokeInputs(query="round 1", conversation_id="test-session")))
        events = await rail.drain_pending_approval_events(wait=True, timeout=5.0)
        outcome_events = [
            event for event in events if event.payload.get("evolution_meta", {}).get("event_kind") == "outcome"
        ]

        assert result is True
        assert rail._host_completion_pending_session_id is None
        assert outcome_events
        assert outcome_events[-1].payload["evolution_meta"]["status"] == "failed"
        assert "team skill evolution failed" in outcome_events[-1].payload["content"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_run_evolution_uses_trajectory_source():
    """Test: when trajectory_source is configured, evolution aggregates from source."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        source = InMemoryTrajectoryRegistry()
        _publish_member_snapshot(
            source,
            member_id="leader",
            session_id="session-1",
            member_role="leader",
            steps=[
                _build_member_step("spawn_member", {"name": "researcher-1"}, start_time_ms=100),
                _build_member_step("view_task", {}, start_time_ms=500),
            ],
            recorded_at_ms=1000,
        )
        _publish_member_snapshot(
            source,
            member_id="researcher",
            session_id="session-1",
            member_role="teammate",
            steps=[
                _build_member_step("read_file", "team_skills/deep-research-to-ppt/SKILL.md", start_time_ms=200),
            ],
            recorded_at_ms=1001,
        )

        _install_team_skill(tmp)

        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            auto_save=False,
            async_evolution=False,
            team_id="team-a",
            trajectory_source=source,
        )
        captured: dict[str, Any] = {}
        _capture_trajectory_signals(rail, captured)

        trajectory = Trajectory(
            execution_id="test-001",
            session_id="session-1",
            source="online",
            steps=[],
        )
        ctx = _MockCtx()

        await rail.run_evolution(trajectory, ctx)

        used_trajectory = captured["trajectory"]
        assert used_trajectory.execution_id == "team-team-a"
        assert used_trajectory.meta["member_count"] == 2
        assert _tool_names(used_trajectory) == ["spawn_member", "read_file", "view_task"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_run_evolution_filters_non_collaborative_steps():
    """Test: team trajectory source aggregation filters out internal steps."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        source = InMemoryTrajectoryRegistry()
        _publish_member_snapshot(
            source,
            member_id="researcher",
            session_id="session-1",
            member_role="teammate",
            steps=[
                _build_member_step("spawn_member", {"name": "r1"}, start_time_ms=100, meta={"invoke_id": "inv-1"}),
                TrajectoryStep(
                    kind="llm",
                    detail=LLMCallDetail(model="gpt-4", messages=[]),
                    meta={"operator_id": "leader/llm_main"},
                    start_time_ms=200,
                ),
                _build_member_step("read_file", "team_skills/deep-research-to-ppt/SKILL.md", start_time_ms=250),
                _build_member_step("view_task", {}, start_time_ms=300),
            ],
        )

        _install_team_skill(tmp)

        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            auto_save=False,
            async_evolution=False,
            team_id="team-a",
            trajectory_source=source,
        )
        captured: dict[str, Any] = {}
        _capture_trajectory_signals(rail, captured)

        trajectory = Trajectory(
            execution_id="test-002",
            session_id="session-1",
            source="online",
            steps=[],
        )
        ctx = _MockCtx()

        await rail.run_evolution(trajectory, ctx)

        used_trajectory = captured["trajectory"]
        assert [step.kind for step in used_trajectory.steps] == ["tool", "tool", "tool"]
        assert _tool_names(used_trajectory) == ["spawn_member", "read_file", "view_task"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_run_evolution_keeps_full_leader_trajectory():
    """Team analysis keeps leader internal steps while filtering teammate internals."""
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        source = InMemoryTrajectoryRegistry()
        _publish_member_snapshot(
            source,
            member_id="leader",
            session_id="session-1",
            member_role="leader",
            steps=[
                TrajectoryStep(
                    kind="llm",
                    detail=LLMCallDetail(model="gpt-4", messages=[]),
                    meta={"operator_id": "leader/llm_main"},
                    start_time_ms=100,
                ),
                _build_member_step("view_task", {}, start_time_ms=300),
            ],
            recorded_at_ms=1000,
        )
        _publish_member_snapshot(
            source,
            member_id="researcher",
            session_id="session-1",
            member_role="teammate",
            steps=[
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
            ],
            recorded_at_ms=1001,
        )

        _install_team_skill(tmp)

        mock_llm = MockLLM()
        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=mock_llm,
            model="mock-model",
            auto_save=False,
            async_evolution=False,
            team_id="team-a",
            trajectory_source=source,
        )

        captured: dict[str, Any] = {}
        _capture_trajectory_signals(rail, captured)

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
        swarm_dir = tmp / "swarm-research"
        swarm_dir.mkdir(parents=True)
        (swarm_dir / "SKILL.md").write_text(
            "---\nname: swarm-research\nkind: swarm-skill\nroles:\n  - name: planner\n    kind: ai_agent\n---\n# Swarm Skill",
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

        swarm_trajectory = Trajectory(
            execution_id="detect-001-swarm",
            session_id="session-1",
            source="online",
            steps=[
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="read_file",
                        call_args="/workspace/swarm-research/SKILL.md",
                    ),
                ),
            ],
        )

        assert rail._detect_used_team_skill(swarm_trajectory) == "swarm-research"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detect_used_team_skill_excludes_disabled_skills():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        team_dir_a = tmp / "team-skill-a"
        team_dir_a.mkdir(parents=True)
        (team_dir_a / "SKILL.md").write_text(
            "---\nname: team-skill-a\nkind: team-skill\n---\n# Team Skill A",
            encoding="utf-8",
        )
        team_dir_b = tmp / "team-skill-b"
        team_dir_b.mkdir(parents=True)
        (team_dir_b / "SKILL.md").write_text(
            "---\nname: team-skill-b\nkind: team-skill\n---\n# Team Skill B",
            encoding="utf-8",
        )

        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            async_evolution=False,
            disabled_skills=["team-skill-a"],
        )

        trajectory = Trajectory(
            execution_id="detect-disabled",
            session_id="session-1",
            source="online",
            steps=[
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="read_file",
                        call_args="/workspace/team-skill-a/SKILL.md",
                    ),
                ),
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="read_file",
                        call_args="/workspace/team-skill-b/SKILL.md",
                    ),
                ),
            ],
        )

        result = rail._detect_used_team_skill(trajectory)
        assert result == "team-skill-b"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detect_used_team_skill_returns_none_when_all_disabled():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_test_"))
    try:
        team_dir = tmp / "team-skill-x"
        team_dir.mkdir(parents=True)
        (team_dir / "SKILL.md").write_text(
            "---\nname: team-skill-x\nkind: team-skill\n---\n# Team Skill X",
            encoding="utf-8",
        )

        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            async_evolution=False,
            disabled_skills=["team-skill-x"],
        )

        trajectory = Trajectory(
            execution_id="detect-all-disabled",
            session_id="session-1",
            source="online",
            steps=[
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="read_file",
                        call_args="/workspace/team-skill-x/SKILL.md",
                    ),
                ),
            ],
        )

        result = rail._detect_used_team_skill(trajectory)
        assert result is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_team_skill_evolution_rail_disabled_skills_defaults_to_empty(tmp_path):
    rail = TeamSkillRail(
        skills_dir=str(tmp_path / "skills"),
        llm=MockLLM(),
        model="mock-model",
    )
    assert rail.disabled_skills == set()


def test_team_skill_evolution_rail_disabled_skills_from_list(tmp_path):
    rail = TeamSkillRail(
        skills_dir=str(tmp_path / "skills"),
        llm=MockLLM(),
        model="mock-model",
        disabled_skills=["skill-a", "skill-b"],
    )
    assert rail.disabled_skills == {"skill-a", "skill-b"}


def test_team_skill_evolution_rail_disabled_skills_from_single_string(tmp_path):
    rail = TeamSkillRail(
        skills_dir=str(tmp_path / "skills"),
        llm=MockLLM(),
        model="mock-model",
        disabled_skills="skill-a",
    )
    assert rail.disabled_skills == {"skill-a"}


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ("task-a completed\ntask-b completed", True),
        ("task-a pending\ntask-b completed", False),
        ("task-a in_progress\ntask-b completed", False),
        ("task-a blocked\ntask-b completed", False),
        ("task-a claimed\ntask-b completed", False),
        ("task-a ready", False),
    ],
)
def test_team_task_completion_helper_covers_terminal_and_non_terminal_text(result, expected):
    assert is_completed_team_task_view(result) is expected


def test_infer_team_skill_from_trajectory_helper_handles_multi_skill_and_no_match():
    trajectory = Trajectory(
        execution_id="detect-helper",
        session_id="session-1",
        source="online",
        steps=[
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="skill_tool",
                    call_args={"skill_name": "team-a", "relative_file_path": "SKILL.md"},
                ),
            ),
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="read_file",
                    call_args="/workspace/skills/team-b/SKILL.md",
                ),
            ),
        ],
    )

    assert infer_team_skill_from_trajectory(trajectory, {"team-a", "team-b"}) == "team-a"
    assert infer_team_skill_from_trajectory(trajectory, {"team-c"}) is None


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
        swarm_dir = tmp / "real-swarm"
        swarm_dir.mkdir(parents=True)
        (swarm_dir / "SKILL.md").write_text(
            "---\nname: real-swarm\nkind: swarm-skill\nroles:\n  - name: planner\n    kind: ai_agent\n---\n# Body",
            encoding="utf-8",
        )

        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            async_evolution=False,
        )

        assert rail._is_team_skill("fake-team") is False
        assert rail._is_team_skill("real-swarm") is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# Helper and contract tests
# ============================================================


def test_team_signal_type_enum():
    """TeamSignalType enum must have expected values."""
    assert TeamSignalType.USER_INTENT.value == "user_intent"
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


def test_init_accepts_custom_llm_policies_timeout_and_concurrency(tmp_path):
    """TeamSkillRail should propagate custom policies, timeout, and concurrency."""
    evaluate_policy = LLMInvokePolicy(
        attempt_timeout_secs=19,
        total_budget_secs=57,
        max_attempts=2,
    )
    simplify_policy = LLMInvokePolicy(
        attempt_timeout_secs=23,
        total_budget_secs=69,
        max_attempts=2,
    )
    rail = TeamSkillRail(
        skills_dir=str(tmp_path),
        llm=MagicMock(),
        model="test-model",
        user_request_llm_policy=LLMInvokePolicy(
            attempt_timeout_secs=7,
            total_budget_secs=21,
            max_attempts=2,
        ),
        trajectory_issue_llm_policy=LLMInvokePolicy(
            attempt_timeout_secs=13,
            total_budget_secs=39,
            max_attempts=2,
        ),
        record_llm_policy=LLMInvokePolicy(
            attempt_timeout_secs=17,
            total_budget_secs=51,
            max_attempts=2,
        ),
        max_concurrent_evolution=3,
        evaluate_llm_policy=evaluate_policy,
        simplify_llm_policy=simplify_policy,
        evolution_total_timeout_secs=555.0,
    )

    assert rail.user_request_llm_policy.attempt_timeout_secs == 7
    assert rail.trajectory_issue_llm_policy.attempt_timeout_secs == 13
    assert rail.record_llm_policy.attempt_timeout_secs == 17
    assert rail.evaluate_llm_policy is evaluate_policy
    assert rail.simplify_llm_policy is simplify_policy
    assert rail.evolution_total_timeout_secs == 555.0
    assert rail.evolution_config["record_llm_policy"] is rail.record_llm_policy
    assert rail.evolution_config["evolution_total_timeout_secs"] == 555.0
    assert rail.evolution_config["max_concurrent_evolution"] == 3
    assert rail._evolution_trigger == EvolutionTriggerPoint.AFTER_INVOKE


@pytest.mark.asyncio
async def test_drain_pending_approval_events_defaults_to_total_timeout(tmp_path):
    rail = TeamSkillRail(
        skills_dir=str(tmp_path),
        llm=MagicMock(),
        model="test-model",
        evolution_total_timeout_secs=444.0,
    )

    assert await rail.drain_pending_approval_events(wait=True) == []


@pytest.mark.asyncio
async def test_detect_user_request_retries_on_invalid_response():
    """_detect_user_request should retry after an invalid first response."""
    import json

    llm = MagicMock()
    llm.invoke = AsyncMock(
        side_effect=[
            MagicMock(content="not json"),
            MagicMock(content=json.dumps({"is_improvement": True, "intent": "增加 reviewer"})),
        ]
    )

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._team_signal_detector = TeamSignalDetector(
            llm=llm,
            model="test-model",
            language="cn",
            llm_policy=LLMInvokePolicy(
                attempt_timeout_secs=5,
                total_budget_secs=15,
                max_attempts=2,
            ),
        )

        result = await rail._detect_user_request(
            [{"role": "user", "content": "请增加 reviewer 角色"}],
            "team skill",
        )

        assert result == UserIntent(is_improvement=True, intent="增加 reviewer")
        assert llm.invoke.await_count == 2


@pytest.mark.parametrize(
    (
        "trajectory_signals",
        "user_intent_return",
        "user_intent_error",
        "expected_signal_types",
        "expected_user_query",
    ),
    [
        pytest.param(
            [_build_trajectory_issue_signal()],
            UserIntent(is_improvement=True, intent="add reviewer"),
            None,
            [TeamSignalType.TRAJECTORY_ISSUE.value, "user_intent"],
            "add reviewer",
            id="trajectory+user_intent",
        ),
        pytest.param(
            [],
            UserIntent(is_improvement=True, intent="add reviewer"),
            None,
            ["user_intent"],
            "add reviewer",
            id="user_only",
        ),
        pytest.param(
            [_build_trajectory_issue_signal()],
            None,
            None,
            [TeamSignalType.TRAJECTORY_ISSUE.value],
            "",
            id="trajectory_only",
        ),
        pytest.param(
            [_build_trajectory_issue_signal()],
            None,
            RuntimeError("intent llm down"),
            [TeamSignalType.TRAJECTORY_ISSUE.value],
            "",
            id="trajectory_only_when_user_intent_fails",
        ),
    ],
)
@pytest.mark.asyncio
async def test_run_evolution_signal_combination(
    trajectory_signals: list[object],
    user_intent_return: UserIntent | None,
    user_intent_error: Exception | None,
    expected_signal_types: list[str],
    expected_user_query: str,
):
    """notify_team_completed/run_evolution should compose trajectory and user-intent signals."""
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._trajectory_source = None
        rail._team_id = None
        rail._passive_evolution_pending = True
        rail._store = MagicMock()
        rail._store.read_skill_content = AsyncMock(return_value="team skill content")
        rail._detect_used_team_skill = MagicMock(return_value="team-skill-a")
        rail._generator = MagicMock(llm=MagicMock(), model="test-model", language="cn")
        rail._team_signal_detector = MagicMock(
            detect_trajectory_signals=AsyncMock(return_value=trajectory_signals),
            detect_user_intent=(
                AsyncMock(side_effect=user_intent_error)
                if user_intent_error is not None
                else AsyncMock(return_value=user_intent_return)
            ),
        )
        rail._handle_evolution_from_signals_with_result = AsyncMock(
            return_value=_handle_result(MagicMock(request_id="team_skill_evolve_req"), skill_name="team-skill-a")
        )
        rail._emit_progress = MagicMock()
        rail._pending_host_events = []

        await rail.run_evolution(
            Trajectory(execution_id="e1", session_id="s1", source="online", steps=[]),
            snapshot={"messages": [{"role": "user", "content": "please improve"}]},
        )

        rail._team_signal_detector.detect_trajectory_signals.assert_awaited_once()
        rail._team_signal_detector.detect_user_intent.assert_awaited_once_with(
            messages=[{"role": "user", "content": "please improve"}],
            team_skill_content="team skill content",
        )
        rail._handle_evolution_from_signals_with_result.assert_awaited_once()
        signals = rail._handle_evolution_from_signals_with_result.await_args.kwargs["signals"]
        assert [signal.signal_type for signal in signals] == expected_signal_types
        assert rail._handle_evolution_from_signals_with_result.await_args.kwargs["user_query"] == expected_user_query


@pytest.mark.asyncio
async def test_on_approve_record_partial_failure_retains_request_for_retry():
    record_1 = EvolutionRecord.make(
        source="team-skill",
        context="ctx",
        change=EvolutionPatch(
            section="Workflow",
            action="append",
            content="first",
            target=EvolutionTarget.BODY,
        ),
    )
    record_2 = EvolutionRecord.make(
        source="team-skill",
        context="ctx",
        change=EvolutionPatch(
            section="Workflow",
            action="append",
            content="second",
            target=EvolutionTarget.BODY,
        ),
    )
    pending = PendingChange.make("team-skill-a", [record_1, record_2])

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._pending_record_snapshots = {pending.change_id: pending}
        rail._experience_skill_ops = {}
        rail._store = MagicMock()
        manager = MagicMock()
        manager.approve_request = AsyncMock(
            side_effect=[
                Mock(applied_count=1, pending_count=1),
                Mock(applied_count=1, pending_count=0),
            ]
        )
        rail._manager = manager
        rail._ensure_manager = MagicMock(return_value=manager)

        await rail.on_approve_record(pending.change_id)

        assert pending.change_id in rail._pending_record_snapshots
        manager.approve_request.assert_awaited_with(pending.change_id)

        await rail.on_approve_record(pending.change_id)

        assert pending.change_id not in rail._pending_record_snapshots


@pytest.mark.asyncio
async def test_on_approve_record_does_not_touch_other_requests():
    record_1 = EvolutionRecord.make(
        source="team-skill",
        context="ctx",
        change=EvolutionPatch(
            section="Workflow",
            action="append",
            content="batch-1",
            target=EvolutionTarget.BODY,
        ),
    )
    record_2 = EvolutionRecord.make(
        source="team-skill",
        context="ctx",
        change=EvolutionPatch(
            section="Workflow",
            action="append",
            content="batch-2",
            target=EvolutionTarget.BODY,
        ),
    )
    pending_1 = PendingChange.make("team-skill-a", [record_1])
    pending_2 = PendingChange.make("team-skill-a", [record_2])

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._pending_record_snapshots = {
            pending_1.change_id: pending_1,
            pending_2.change_id: pending_2,
        }
        rail._experience_skill_ops = {}
        manager = MagicMock()
        manager.approve_request = AsyncMock(return_value=Mock(applied_count=1, pending_count=0))
        rail._manager = manager
        rail._ensure_manager = MagicMock(return_value=manager)

        await rail.on_approve_record(pending_1.change_id)

        manager.approve_request.assert_awaited_once_with(pending_1.change_id)
        assert pending_1.change_id not in rail._pending_record_snapshots
        assert rail._pending_record_snapshots[pending_2.change_id].payload == [record_2]


@pytest.mark.asyncio
async def test_on_approve_record_uses_rebound_pending_snapshot_store():
    tmp = Path(tempfile.mkdtemp(prefix="team_skill_rebind_"))
    try:
        skill_dir = tmp / "team-skill-a"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: team-skill-a\nkind: team-skill\n---\n# Team Skill\nWorkflow here.",
            encoding="utf-8",
        )

        rail = TeamSkillRail(
            skills_dir=str(tmp),
            llm=MockLLM(),
            model="mock-model",
            auto_save=False,
            async_evolution=False,
        )
        rail._store.append_record = AsyncMock()

        record = EvolutionRecord.make(
            source="team-skill",
            context="ctx",
            change=EvolutionPatch(
                section="Workflow",
                action="append",
                content="rebound patch",
                target=EvolutionTarget.BODY,
            ),
        )
        request = rail._manager.stage_records(
            "team-skill-a",
            [record],
            requires_approval=True,
            source="team_skill_experience_updater",
            request_id_prefix="team_skill_evolve",
        )
        pending = rail._pending_record_snapshots.pop(request.request_id)

        rebound_snapshots = {request.request_id: pending}
        rail._pending_record_snapshots = rebound_snapshots
        _bind_team_runtime(rail)

        await rail.on_approve_record(request.request_id)

        rail._store.append_record.assert_awaited_once_with("team-skill-a", record)
        assert request.request_id not in rebound_snapshots
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_on_approve_record_uses_rebound_snapshot_store_after_snapshot_dict_swap(tmp_path):
    skill_dir = tmp_path / "team-skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: team-skill-a\nkind: team-skill\n---\n# Team Skill\n",
        encoding="utf-8",
    )

    rail = TeamSkillRail(
        skills_dir=str(tmp_path),
        llm=MockLLM(),
        model="mock-model",
        auto_save=False,
        async_evolution=False,
    )
    rail.store.append_record = AsyncMock()

    old_snapshots = rail._pending_record_snapshots
    rail._pending_record_snapshots = {}
    _bind_team_runtime(rail)
    record = EvolutionRecord.make(
        source="team-skill",
        context="ctx",
        change=EvolutionPatch(
            section="Workflow",
            action="append",
            content="rebinding-safe",
            target=EvolutionTarget.BODY,
        ),
    )
    rail._manager.bind_pending_approval_snapshots(rail._pending_record_snapshots)
    request = rail._manager.stage_records(
        "team-skill-a",
        [record],
        requires_approval=True,
        source="team_skill_experience_updater",
        request_id_prefix="team_skill_evolve",
    )

    assert request.request_id in rail._pending_record_snapshots
    assert request.request_id not in old_snapshots

    await rail.on_approve_record(request.request_id)

    rail.store.append_record.assert_awaited_once_with("team-skill-a", record)
    assert request.request_id not in rail._pending_record_snapshots


@pytest.mark.asyncio
async def test_request_simplify_stages_governance_and_returns_approval():
    """/evolve_simplify should stage governance and emit an approval event."""
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
        rail._auto_save = False
        rail._pending_record_snapshots = {}
        rail._pending_governance = {}
        rail._pending_host_events = []
        rail._experience_skill_ops = {}
        rail._manager = ExperienceManager(
            store=mock_store,
            scorer=rail._scorer,
            kind="team-skill",
            pending_approval_snapshots=rail._pending_record_snapshots,
            pending_governance=rail._pending_governance,
        )
        result = await rail.request_simplify("test-team-skill")

        rail._scorer.simplify.assert_called_once()
        assert result is not None
        assert result.request_id in rail._pending_governance
        assert rail._pending_governance[result.request_id]["kind"] == "simplify"
        assert result.approval_event.type == "chat.ask_user_question"
        assert rail._pending_host_events == []
        assert result.approval_event.payload["request_id"] == result.request_id
        assert result.approval_event.payload["evolution_meta"]["rail_kind"] == "team"
        assert result.approval_event.payload["evolution_meta"]["skill_name"] == "test-team-skill"


@pytest.mark.asyncio
async def test_request_simplify_returns_empty_result_when_no_records():
    """/evolve_simplify should return None when no evolution records exist."""
    mock_store = MagicMock()
    mock_store.skill_exists.return_value = True
    mock_store.load_full_evolution_log = AsyncMock(return_value=MagicMock(entries=[]))

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._store = mock_store
        rail._pending_record_snapshots = {}
        rail._pending_governance = {}
        rail._pending_host_events = []
        rail._experience_skill_ops = {}
        rail._scorer = MagicMock()
        rail._manager = ExperienceManager(
            store=mock_store,
            scorer=rail._scorer,
            kind="team-skill",
            pending_approval_snapshots=rail._pending_record_snapshots,
            pending_governance=rail._pending_governance,
        )

        result = await rail.request_simplify("test-team-skill")

        assert result.request_id is None


@pytest.mark.asyncio
async def test_request_simplify_returns_empty_result_when_no_actions():
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
        rail._pending_record_snapshots = {}
        rail._pending_governance = {}
        rail._pending_host_events = []
        rail._experience_skill_ops = {}
        rail._manager = ExperienceManager(
            store=mock_store,
            scorer=rail._scorer,
            kind="team-skill",
            pending_approval_snapshots=rail._pending_record_snapshots,
            pending_governance=rail._pending_governance,
        )

        result = await rail.request_simplify("test-team-skill")

        assert result.request_id is None


@pytest.mark.asyncio
async def test_on_approve_simplify_delegates_to_manager():
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._pending_host_events = []
        rail._pending_governance = {}
        rail._pending_record_snapshots = {}
        rail._experience_skill_ops = {}
        rail._store = MagicMock()
        rail._scorer = MagicMock()
        rail._manager = MagicMock()
        rail._manager.approve_simplify = AsyncMock(
            return_value={"deleted": 1, "merged": 0, "refined": 0, "kept": 0, "errors": 0}
        )

        result = await rail.on_approve_simplify("req-1")

        rail._manager.approve_simplify.assert_awaited_once_with("req-1")
        assert result["deleted"] == 1


@pytest.mark.asyncio
async def test_on_reject_simplify_delegates_to_manager():
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._pending_governance = {}
        rail._pending_record_snapshots = {}
        rail._experience_skill_ops = {}
        rail._store = MagicMock()
        rail._scorer = MagicMock()
        rail._manager = MagicMock()
        rail._manager.reject_simplify = AsyncMock()

        await rail.on_reject_simplify("req-2")

        rail._manager.reject_simplify.assert_awaited_once_with("req-2")


@pytest.mark.asyncio
async def test_request_rebuild_returns_none_when_no_skill():
    """/evolve_rebuild should return None when skill doesn't exist."""
    mock_store = MagicMock()
    mock_store.skill_exists.return_value = False

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._store = mock_store
        rail._manager = ExperienceManager(
            store=mock_store,
            scorer=MagicMock(),
            kind="team-skill",
            language="cn",
        )
        rail._pending_governance = {}
        rail._pending_record_snapshots = {}
        rail._experience_skill_ops = {}
        rail._generator = MagicMock(language="cn")
        rail._online_updater = MagicMock()
        _bind_team_runtime(rail)

        result = await rail.request_rebuild("nonexistent-skill")

        assert result is None


@pytest.mark.asyncio
async def test_request_rebuild_archives_before_building_prompt():
    """request_rebuild should archive old version BEFORE building the prompt."""
    mock_record = MagicMock()
    mock_record.change.section = "Collaboration"
    mock_record.change.content = "test collaboration experience"
    mock_record.change.skip_reason = None
    mock_record.source = "user_request"
    mock_record.timestamp = "2026-04-25T10:30:00"
    mock_record.score = 0.8

    mock_low_score_record = MagicMock()
    mock_low_score_record.change.section = "Workflow"
    mock_low_score_record.change.content = "low quality experience"
    mock_low_score_record.change.skip_reason = None
    mock_low_score_record.source = "trajectory_issue"
    mock_record.timestamp = "2026-04-25T10:31:00"
    mock_low_score_record.score = 0.3

    mock_store = MagicMock()
    mock_store.skill_exists.return_value = True
    mock_store.archive_skill_body = AsyncMock(return_value="SKILL.v20260426_171500.md")
    mock_store.archive_evolutions = AsyncMock(return_value="evolutions.v20260426_171500.json")
    mock_store.clear_evolutions = AsyncMock()
    mock_store.load_full_evolution_log = AsyncMock(return_value=MagicMock(entries=[mock_record, mock_low_score_record]))

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._store = mock_store
        rail._generator = MagicMock(language="cn")
        rail._pending_host_events = []
        rail._pending_governance = {}
        rail._pending_record_snapshots = {}
        rail._experience_skill_ops = {}
        rail._scorer = MagicMock()
        rail._online_updater = MagicMock()
        rail._manager = ExperienceManager(
            store=mock_store,
            scorer=rail._scorer,
            kind="team-skill",
            language="cn",
            skill_ops=rail._experience_skill_ops,
            pending_approval_snapshots=rail._pending_record_snapshots,
            pending_governance=rail._pending_governance,
        )
        _bind_team_runtime(rail)

        result = await rail.request_rebuild("test-team-skill", user_intent="优化协作流程")

        mock_store.archive_skill_body.assert_called_once_with("test-team-skill")
        mock_store.archive_evolutions.assert_called_once_with("test-team-skill")
        mock_store.clear_evolutions.assert_called_once_with("test-team-skill")
        assert result is not None
        assert "Collaboration" in result
        assert "test collaboration experience" in result
        assert "Workflow" not in result
        assert "0.50" in result or "0.5" in result
        assert "teamskill-creator" in result.lower()
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
    mock_store.clear_evolutions = AsyncMock()
    mock_store.load_full_evolution_log = AsyncMock(return_value=MagicMock(entries=[mock_record]))

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._store = mock_store
        rail._generator = MagicMock(language="cn")
        rail._pending_host_events = []
        rail._pending_governance = {}
        rail._pending_record_snapshots = {}
        rail._experience_skill_ops = {}
        rail._scorer = MagicMock()
        rail._online_updater = MagicMock()
        rail._manager = ExperienceManager(
            store=mock_store,
            scorer=rail._scorer,
            kind="team-skill",
            language="cn",
            skill_ops=rail._experience_skill_ops,
            pending_approval_snapshots=rail._pending_record_snapshots,
            pending_governance=rail._pending_governance,
        )
        _bind_team_runtime(rail)

        result = await rail.request_rebuild("test-team-skill")

        assert result is not None
        mock_store.load_full_evolution_log.assert_called_once_with("test-team-skill")
        mock_store.clear_evolutions.assert_not_called()


class TestRequestUserEvolution:
    """request_user_evolution 方法测试。"""

    @pytest.mark.asyncio
    async def test_returns_empty_result_when_skill_not_found(self):
        """对不存在的 skill 应返回 None。"""
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = False
        handle = AsyncMock(return_value=None)
        rail = _make_request_user_evolution_rail(store=mock_store, handle=handle)

        result = await rail.request_user_evolution(
            "nonexistent-skill",
            "增加 reviewer 角色",
        )

        assert result.request_id is None
        handle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_empty_result_when_subject_is_not_team_skill(self, tmp_path):
        """显式 subject 存在但不是 team/swarm skill 时应跳过。"""
        skill_dir = tmp_path / "regular-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: regular-skill\nkind: regular\n---\n# Regular Skill",
            encoding="utf-8",
        )
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True
        mock_store.resolve_skill_dir.return_value = skill_dir
        handle = AsyncMock()
        rail = _make_request_user_evolution_rail(store=mock_store, handle=handle)

        result = await rail.request_user_evolution("regular-skill", "优化")

        assert result.request_id is None
        handle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_request_id_when_patch_generated(self):
        """对有效输入且生成 patch 时应返回 request_id。"""
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True
        mock_builder = MagicMock()
        mock_builder.build.return_value = Trajectory(
            execution_id="test-exec",
            session_id="test-session",
            steps=[],
        )
        captured: dict[str, Any] = {}

        async def _consume_signal(**kwargs):
            captured["signal"] = kwargs["signals"][0]
            captured["user_query"] = kwargs["user_query"]
            return MagicMock(
                request_id="team_skill_evolve_req",
                pending_change=MagicMock(),
                proposal=SimpleNamespace(
                    user_query=kwargs["user_query"],
                    signal_type="user_intent",
                    signal_source="explicit_request",
                ),
            )

        handle = AsyncMock(side_effect=_consume_signal)
        rail = _make_request_user_evolution_rail(store=mock_store, builder=mock_builder, handle=handle)

        result = await rail.request_user_evolution(
            "research-team",
            "增加 reviewer 角色，限制 review 时间不超过 5 分钟",
        )

        assert result is not None
        assert result.request_id.startswith("team_skill_evolve_")
        assert rail._pending_host_events == []
        handle.assert_awaited_once()
        assert isinstance(captured["signal"], EvolutionSignal)
        assert captured["signal"].signal_type == "user_intent"
        assert captured["signal"].context == {
            "source": "explicit_request",
        }
        assert captured["user_query"] == "增加 reviewer 角色，限制 review 时间不超过 5 分钟"

    @pytest.mark.asyncio
    async def test_auto_approve_true_stores_directly(self):
        """auto_approve=True 应直接存储 patch 并返回稳定的 request_id。"""
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True
        mock_request = MagicMock(request_id="team_skill_evolve_req_auto", pending_change=None)

        mock_builder = MagicMock()
        mock_builder.build.return_value = Trajectory(
            execution_id="test-exec",
            session_id="test-session",
            steps=[],
        )
        handle = AsyncMock(return_value=mock_request)
        rail = _make_request_user_evolution_rail(store=mock_store, builder=mock_builder, handle=handle)

        result = await rail.request_user_evolution(
            "research-team",
            "优化协作流程",
            auto_approve=True,
        )

        assert result.request_id == "team_skill_evolve_req_auto"
        assert result.auto_approved is True
        assert rail._pending_host_events == []
        handle.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_no_records_status_when_generation_runs(self):
        """生成流程执行但没有产出经验时，应给 host 可区分的状态。"""
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True
        handle = AsyncMock(return_value=(None, _no_records_result("research-team")))
        rail = _make_request_user_evolution_rail(store=mock_store, handle=handle)

        result = await rail.request_user_evolution("research-team", "优化协作流程")

        assert result.request_id is None
        assert result.status == "no_evolution_no_records"
        assert "no applied updates" in result.message
        assert rail._pending_host_events == []

    @pytest.mark.asyncio
    async def test_auto_approve_true_returns_persistence_failed_request_status(self):
        """auto_approve 持久化失败时，应返回可重试 request_id 和失败状态。"""
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True
        failed_request = MagicMock(request_id="team_skill_evolve_failed", pending_change=None)
        failed_result = OnlineEvolutionResult(
            skill_name="research-team",
            status="persistence_failed",
            request=failed_request,
            message="disk full",
        )
        handle = AsyncMock(return_value=(failed_request, failed_result))
        rail = _make_request_user_evolution_rail(store=mock_store, handle=handle)

        result = await rail.request_user_evolution(
            "research-team",
            "优化协作流程",
            auto_approve=True,
        )

        assert result.request_id == "team_skill_evolve_failed"
        assert result.status == "persistence_failed"
        assert result.message == "disk full"
        assert result.auto_approved is True
        assert rail._pending_host_events == []

    @pytest.mark.asyncio
    async def test_stage_evolution_from_signals_auto_approve_preserves_staged_request_id(self):
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True

        with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
            rail = TeamSkillRail.__new__(TeamSkillRail)
            rail._store = mock_store
            rail._experience_skill_ops = {}
            rail._pending_record_snapshots = {}
            rail._pending_host_events = []
            rail._pending_governance = {}
            rail._generator = MagicMock(language="cn")
            rail._scorer = MagicMock()
            rail._online_updater = MagicMock()
            rail._online_updater.bind = MagicMock()
            record = EvolutionRecord.make(
                source="team-skill",
                context="ctx",
                change=EvolutionPatch(
                    section="Workflow",
                    action="append",
                    content="auto-approved patch",
                    target=EvolutionTarget.BODY,
                ),
            )
            rail._online_updater.process = AsyncMock(
                return_value={
                    ("research-team", "experiences"): UpdateValue(
                        payload=[record],
                        mode="append",
                        effect="pending_change",
                        change_type="skill_experience_entry",
                    )
                }
            )
            rail._emit_progress = MagicMock()
            mock_store.read_skill_content = AsyncMock(return_value="team skill")
            mock_store.get_pending_records = AsyncMock(side_effect=[[], [], []])

            operator = MagicMock()
            operator.operator_id = "research-team"
            operator.apply_update = MagicMock(
                return_value=ApplyResult(
                    operator_id="research-team",
                    target="experiences",
                    applied=True,
                    mode="append",
                    effect="pending_change",
                    records=[record],
                    change_type="skill_experience_entry",
                    lifecycle_stage="local_apply_completed",
                )
            )
            operator.get_state.return_value = {}
            operator.load_state = MagicMock()
            operator.refresh_state = AsyncMock()
            rail._experience_skill_ops["research-team"] = operator

            manager = MagicMock()
            manager.apply_updates.return_value = [SimpleNamespace(applied=True)]
            staged_request = SimpleNamespace(
                request_id="team_skill_evolve_req_auto",
                proposal=SimpleNamespace(
                    records=[SimpleNamespace(id="ev_123")],
                    signal_type="user_intent",
                    signal_source="explicit_request",
                ),
            )
            manager.stage_apply_results.return_value = staged_request
            manager.approve_request = AsyncMock()
            rail._manager = manager
            rail._online_orchestrator = MagicMock()
            rail._online_orchestrator.evolve = AsyncMock(
                return_value=OnlineEvolutionResult(
                    skill_name="research-team",
                    status="auto_approved",
                    request=staged_request,
                )
            )

            result = await rail._stage_evolution_from_signals(
                "research-team",
                trajectory=Trajectory(execution_id="e1", session_id="s1", source="online", steps=[]),
                signals=[
                    EvolutionSignal(
                        signal_type="user_intent",
                        section="Instructions",
                        excerpt="optimize workflow",
                        skill_name="research-team",
                        context={"source": "explicit_request"},
                    )
                ],
                auto_approve=True,
                user_query="优化协作流程",
            )

            request = result.request
            assert request is not None
            assert request.request_id == "team_skill_evolve_req_auto"
            rail._online_orchestrator.evolve.assert_awaited_once()
            assert rail._online_orchestrator.evolve.await_args.kwargs["user_query"] == "优化协作流程"

    @pytest.mark.asyncio
    async def test_auto_approve_false_stages_for_approval(self):
        """auto_approve=False 应暂存等待审批。"""
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True

        mock_builder = MagicMock()
        mock_builder.build.return_value = Trajectory(
            execution_id="test-exec",
            session_id="test-session",
            steps=[],
        )
        pending_change = MagicMock()
        handle = AsyncMock(
            return_value=MagicMock(
                request_id="team_skill_evolve_req",
                pending_change=pending_change,
                metadata={
                    "signal_type": "user_intent",
                    "source": "explicit_request",
                },
            )
        )
        rail = _make_request_user_evolution_rail(store=mock_store, builder=mock_builder, handle=handle)

        result = await rail.request_user_evolution(
            "research-team",
            "增加超时限制",
            auto_approve=False,
        )

        assert result is not None
        assert result.request_id.startswith("team_skill_evolve_")
        handle.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_empty_result_when_no_patch_generated(self):
        """optimizer 未生成 patch 时应返回 None。"""
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True

        mock_builder = MagicMock()
        mock_builder.build.return_value = Trajectory(
            execution_id="test-exec",
            session_id="test-session",
            steps=[],
        )
        rail = _make_request_user_evolution_rail(store=mock_store, builder=mock_builder)

        result = await rail.request_user_evolution(
            "research-team",
            "无效的改进建议",
        )

        assert result.request_id is None
        assert rail._pending_host_events == []
        rail._emit_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_placeholder_trajectory_when_no_builder(self):
        """无 builder 时应使用 placeholder trajectory。"""
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True
        captured: dict[str, Any] = {}

        async def _consume_signal(**kwargs):
            captured["trajectory"] = kwargs["trajectory"]
            captured["signal"] = kwargs["signals"][0]
            return MagicMock(request_id="team_skill_evolve_placeholder", pending_change=None)

        rail = _make_request_user_evolution_rail(store=mock_store, handle=_consume_signal)

        result = await rail.request_user_evolution(
            "research-team",
            "用户主动触发演进",
            auto_approve=True,
        )

        assert result.request_id == "team_skill_evolve_placeholder"
        trajectory_arg = captured["trajectory"]
        assert isinstance(trajectory_arg, Trajectory)
        assert trajectory_arg.source == "user_triggered"
        assert captured["signal"].context["source"] == "explicit_request"

    @pytest.mark.asyncio
    async def test_uses_aggregated_team_trajectory_when_source_available(self):
        """主动触发演进时应使用同 session 的聚合 team trajectory。"""
        source = InMemoryTrajectoryRegistry()
        _publish_member_snapshot(
            source,
            member_id="leader",
            session_id="session-1",
            member_role="leader",
            steps=[
                _build_member_step("view_task", {}, start_time_ms=100),
            ],
            recorded_at_ms=1000,
        )
        _publish_member_snapshot(
            source,
            member_id="researcher",
            session_id="session-1",
            member_role="teammate",
            steps=[
                _build_member_step(
                    "read_file",
                    "team_skills/research-team/SKILL.md",
                    start_time_ms=200,
                ),
            ],
            recorded_at_ms=1001,
        )

        mock_builder = MagicMock()
        mock_builder.build.return_value = Trajectory(
            execution_id="leader-only",
            session_id="session-1",
            source="online",
            steps=[],
        )
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True
        mock_store.read_skill_content = AsyncMock(return_value="# research-team")
        captured: dict[str, Any] = {}

        async def _consume_signal(**kwargs):
            captured["trajectory"] = kwargs["trajectory"]
            captured["messages"] = kwargs["messages"]
            captured["signals"] = kwargs["signals"]
            return MagicMock(request_id="team_skill_evolve_aggregated", pending_change=None)

        rail = _make_request_user_evolution_rail(
            store=mock_store,
            builder=mock_builder,
            trajectory_source=source,
            team_id="team-a",
            handle=AsyncMock(side_effect=_consume_signal),
            detector_signals=[_build_trajectory_issue_signal("research-team", "# research-team")],
        )

        result = await rail.request_user_evolution(
            "research-team",
            "根据团队执行结果优化协作流程",
            auto_approve=True,
        )

        assert result.request_id == "team_skill_evolve_aggregated"
        assert rail._pending_host_events == []
        trajectory_arg = captured["trajectory"]
        assert trajectory_arg.execution_id == "team-team-a"
        assert trajectory_arg.meta["member_count"] == 2
        assert _tool_names(trajectory_arg) == ["view_task", "read_file"]
        assert [message["name"] for message in captured["messages"]] == ["view_task", "read_file"]
        assert [signal.signal_type for signal in captured["signals"]] == [
            TeamSignalType.TRAJECTORY_ISSUE.value,
            "user_intent",
        ]

    @pytest.mark.asyncio
    async def test_active_request_uses_explicit_subject_without_detecting_used_skill(self):
        """主动请求信任显式 skill_name，不要求轨迹中自动检测到 used team skill。"""
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True
        mock_store.read_skill_content = AsyncMock(return_value="# research-team")
        mock_builder = MagicMock()
        trajectory = Trajectory(
            execution_id="exec-without-skill-read",
            session_id="session-1",
            source="online",
            steps=[
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="view_task",
                        call_result="completed with format issue",
                    ),
                )
            ],
        )
        mock_builder.build.return_value = trajectory
        captured: dict[str, Any] = {}

        async def _detect_trajectory_signals(*, trajectory, skill_name, skill_content):
            captured["detected_skill_name"] = skill_name
            captured["skill_content"] = skill_content
            return [_build_trajectory_issue_signal(skill_name, skill_content)]

        handle = AsyncMock(return_value=MagicMock(request_id="team_skill_evolve_explicit_subject", pending_change=None))
        rail = _make_request_user_evolution_rail(store=mock_store, builder=mock_builder, handle=handle)
        rail._detect_used_team_skill = Mock(side_effect=AssertionError("should not be called"))
        rail._team_signal_detector = MagicMock(
            detect_trajectory_signals=AsyncMock(side_effect=_detect_trajectory_signals),
        )

        result = await rail.request_user_evolution("research-team", "", auto_approve=True)

        assert result.request_id == "team_skill_evolve_explicit_subject"
        assert captured["detected_skill_name"] == "research-team"
        assert captured["skill_content"] == "# research-team"
        handle.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_active_request_continues_when_trajectory_detection_fails(self):
        """轨迹问题检测失败时，非空 explicit user_intent 仍应触发主动请求。"""
        mock_store = MagicMock()
        mock_store.skill_exists.return_value = True
        mock_store.read_skill_content = AsyncMock(return_value="# research-team")
        mock_builder = MagicMock()
        mock_builder.build.return_value = Trajectory(
            execution_id="exec-with-evidence",
            session_id="session-1",
            source="online",
            steps=[
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="view_task",
                        call_result="completed with issue",
                    ),
                )
            ],
        )
        captured: dict[str, Any] = {}

        async def _consume_signal(**kwargs):
            captured.update(kwargs)
            return MagicMock(request_id="team_skill_evolve_user_intent_only", pending_change=None)

        handle = AsyncMock(side_effect=_consume_signal)
        rail = _make_request_user_evolution_rail(
            store=mock_store,
            builder=mock_builder,
            handle=handle,
            detector_error=RuntimeError("detector timeout"),
        )

        result = await rail.request_user_evolution("research-team", "增加 reviewer", auto_approve=True)

        assert result.request_id == "team_skill_evolve_user_intent_only"
        assert [signal.signal_type for signal in captured["signals"]] == ["user_intent"]
        assert captured["signals"][0].context == {"source": "explicit_request"}
        handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_evolution_and_request_user_evolution_share_signal_consumer():
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._store = MagicMock()
        rail._store.read_skill_content = AsyncMock(return_value="# current")
        rail._builder = None
        rail._trajectory_source = None
        rail._team_id = None
        rail._pending_record_snapshots = {}
        rail._pending_host_events = []
        rail._emit_progress = MagicMock()
        rail._emit_record_approval_event = MagicMock()
        rail._auto_save = False
        captured: list[dict[str, Any]] = []

        async def _consume_signal(**kwargs):
            captured.append(kwargs)
            return None

        async def _consume_signal_with_result(**kwargs):
            captured.append(kwargs)
            return _handle_result(None, skill_name=kwargs["skill_name"])

        rail._detect_used_team_skill = Mock(return_value="research-team")
        rail._generator = MagicMock(llm=MagicMock(), model="test-model", language="cn")
        rail._team_signal_detector = MagicMock(
            detect_trajectory_signals=AsyncMock(
                return_value=[
                    EvolutionSignal(
                        signal_type=TeamSignalType.TRAJECTORY_ISSUE.value,
                        section="",
                        excerpt="Detected team skill trajectory issues requiring evolution.",
                        skill_name="research-team",
                        context={
                            "source": "passive_trajectory",
                            "trajectory_issues": [
                                {
                                    "issue_type": "coordination",
                                    "description": "handoff gap",
                                    "affected_role": "",
                                    "severity": "medium",
                                }
                            ],
                            "skill_content": "# current",
                        },
                    )
                ]
            ),
            detect_user_intent=AsyncMock(return_value=None),
        )
        rail._handle_evolution_from_signals_with_result = AsyncMock(side_effect=_consume_signal_with_result)

        await rail.request_user_evolution("research-team", "add reviewer")
        await rail.run_evolution(
            Trajectory(execution_id="exec-1", session_id="sess-1", source="online", steps=[]),
            _MockCtx(),
        )

        assert rail._handle_evolution_from_signals_with_result.await_count == 2
        explicit_call = captured[0]
        passive_call = captured[1]
        assert explicit_call["skill_name"] == "research-team"
        assert explicit_call["signals"][0].signal_type == "user_intent"
        assert explicit_call["signals"][0].context == {"source": "explicit_request"}
        assert passive_call["skill_name"] == "research-team"
        assert passive_call["signals"][0].signal_type == TeamSignalType.TRAJECTORY_ISSUE.value
        assert passive_call["signals"][0].context["source"] == "passive_trajectory"
        progress_stages = [call.args[0] for call in rail._emit_progress.call_args_list]
        assert "started" in progress_stages
        assert "detecting_signals" in progress_stages
        assert progress_stages.count("completed") >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("signal_type", "signal_source", "expect_source"),
    [
        ("user_intent", "explicit_request", True),
        ("trajectory_issue", None, False),
    ],
)
async def test_emit_record_approval_event_preserves_signal_metadata(
    signal_type: str,
    signal_source: str | None,
    expect_source: bool,
):
    pending = MagicMock(change_id="team_skill_evolve_req", payload=[])
    event = MagicMock(
        payload={"evolution_meta": {"skill_name": "research-team", "request_id": "team_skill_evolve_req"}}
    )

    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._generator = MagicMock(language="cn")
        rail._pending_host_events = []
        rail._emit_progress = MagicMock()

        with patch(
            "openjiuwen.harness.rails.evolution.team_skill_evolution_rail.build_team_skill_approval_event_from_records",
            return_value=event,
        ):
            rail._emit_record_approval_event(
                "research-team",
                pending,
                proposal=SimpleNamespace(
                    signal_type=signal_type,
                    signal_source=signal_source,
                ),
            )

        assert rail._pending_host_events[-1].payload["evolution_meta"] == {
            "event_kind": "approval",
            "rail_kind": "team",
            "skill_name": "research-team",
            "request_id": "team_skill_evolve_req",
            "signal_type": signal_type,
            **({"source": signal_source} if expect_source else {}),
        }


@pytest.mark.asyncio
async def test_team_approve_record_and_reject_record_aliases():
    with patch.object(TeamSkillRail, "__init__", lambda self, *args, **kwargs: None):
        rail = TeamSkillRail.__new__(TeamSkillRail)
        rail._pending_host_events = []
        rail._pending_record_snapshots = {}
        rail._manager = MagicMock()
        rail.approval_runtime.approve_pending_request = AsyncMock(
            return_value=(SimpleNamespace(skill_name="team-skill"), SimpleNamespace(pending_count=0, applied_count=1))
        )
        rail.approval_runtime.reject_pending_request = AsyncMock(
            return_value=(SimpleNamespace(skill_name="team-skill"), SimpleNamespace(rejected_count=1))
        )

        await rail.on_approve_record("req-approve")
        await rail.on_reject_record("req-reject")

        rail.approval_runtime.approve_pending_request.assert_awaited_once_with(
            "req-approve",
            rail_name="TeamSkillEvolutionRail",
            action_name="approve_record",
        )
        rail.approval_runtime.reject_pending_request.assert_awaited_once_with(
            "req-reject",
            rail_name="TeamSkillEvolutionRail",
            action_name="reject_record",
        )


@pytest.mark.asyncio
async def test_stage_evolution_from_signals_rejects_legacy_excerpt_arguments():
    rail = MagicMock(spec=TeamSkillRail)

    with pytest.raises(TypeError):
        await TeamSkillRail._stage_evolution_from_signals(  # type: ignore[misc]
            rail,
            "team-skill-a",
            trajectory=Trajectory(execution_id="e1", session_id="s1", source="online", steps=[]),
            excerpt="legacy",
            auto_approve=False,
        )


async def main():
    await test_patch_path()
    await test_patch_auto_save()
    await test_notify_team_completed_without_view_task()
    await test_notify_team_completed_repeated_mark_keeps_same_session()
    await test_notify_team_completed_mark_survives_next_before_invoke()
    await test_notify_team_completed_mark_does_not_leak_to_new_session()
    await test_notify_team_completed_no_trajectory()
    await test_run_evolution_uses_trajectory_source()
    await test_run_evolution_filters_non_collaborative_steps()
    await test_run_evolution_keeps_full_leader_trajectory()


if __name__ == "__main__":
    asyncio.run(main())
