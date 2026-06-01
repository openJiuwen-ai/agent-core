# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.experience import (
    ExperienceTracker,
    OnlineEvolutionOrchestrator,
    OnlineEvolutionResult,
)
from openjiuwen.agent_evolving.experience.skill_experience_manager import ExperienceManager
from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.signal import (
    EvolutionSignal,
    SignalDetector,
    make_evolution_signal,
)
from openjiuwen.agent_evolving.trajectory.types import LLMCallDetail, Trajectory, TrajectoryStep
from openjiuwen.agent_evolving.types import ApplyResult
from openjiuwen.core.foundation.llm import SystemMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ToolCallInputs
from openjiuwen.harness.rails.evolution.approval_runtime import EvolutionApprovalRuntime
from openjiuwen.harness.rails.evolution.skill_evolution_rail import (
    _MAX_PROCESSED_SIGNAL_KEYS,
    SkillEvolutionRail,
)


def _make_rail(tmp_path, *, auto_scan: bool = True, auto_save: bool = True, disabled_skills=None) -> SkillEvolutionRail:
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        auto_scan=auto_scan,
        auto_save=auto_save,
        disabled_skills=disabled_skills,
    )
    rail._evolution_store = Mock()
    rail._evolution_store.read_skill_content = AsyncMock(return_value="# skill")
    rail._evolution_store.get_pending_records = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()
    rail._evolver = Mock()
    rail._online_updater = Mock()
    rail._manager = ExperienceManager(
        store=rail._evolution_store,
        scorer=rail._scorer,
        kind="skill",
        language=rail._language,
        skill_ops=rail._skill_ops,
        pending_approval_snapshots=rail._pending_approval_snapshots,
        pending_governance=rail._pending_governance,
    )
    rail._approval_runtime = EvolutionApprovalRuntime(
        manager=rail._manager,
        pending_approval_snapshots=rail._pending_approval_snapshots,
    )
    rail._online_orchestrator = OnlineEvolutionOrchestrator(
        store=rail._evolution_store,
        updater=rail._online_updater,
        manager=rail._manager,
        skill_ops=rail._skill_ops,
        stage_source="experience_updater",
    )
    rail._experience_tracker = ExperienceTracker(
        store=rail._evolution_store,
        scorer=rail._scorer,
        eval_interval=rail._eval_interval,
    )
    return rail


def _make_record(skill_name: str, *, content: str = "experience content") -> EvolutionRecord:
    return EvolutionRecord.make(
        source=f"signal:{skill_name}",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
        ),
    )


def _stage_approval_request(rail: SkillEvolutionRail, skill_name: str, records: list[EvolutionRecord]):
    return rail._manager.stage_records(skill_name, records, requires_approval=True)


def _make_signal(skill_name: str | None, *, excerpt: str = "signal excerpt") -> EvolutionSignal:
    return make_evolution_signal(
        signal_type="tool_failure",
        section="Troubleshooting",
        excerpt=excerpt,
        tool_name="bash",
        skill_name=skill_name,
        source="passive_conversation",
    )


def _no_records_result(skill_name: str = "skill-a") -> OnlineEvolutionResult:
    return OnlineEvolutionResult(
        skill_name=skill_name,
        status="no_evolution_no_records",
        message=f"no applied updates for skill={skill_name}",
    )


def _failed_result(status: str, skill_name: str = "skill-a") -> OnlineEvolutionResult:
    return OnlineEvolutionResult(
        skill_name=skill_name,
        status=status,
        message=f"{status} for skill={skill_name}",
    )


def _handle_result(request: Any, *, online_result: OnlineEvolutionResult | None = None) -> OnlineEvolutionResult:
    if online_result is None:
        online_result = OnlineEvolutionResult(
            skill_name="skill-a",
            status="staged" if request is not None else "no_evolution_no_records",
            request=request,
        )
    return online_result


def _staged_result(request: Any, skill_name: str = "skill-a") -> OnlineEvolutionResult:
    return OnlineEvolutionResult(
        skill_name=skill_name,
        status="staged",
        request=request,
    )


def _trajectory_with_messages(messages: list[dict]) -> Trajectory:
    return Trajectory(
        execution_id="exec-1",
        steps=[TrajectoryStep(kind="llm", detail=LLMCallDetail(model="test-model", messages=messages))],
        source="online",
    )


def _bind_active_request_evidence(
    rail: SkillEvolutionRail,
    messages: list[dict],
    *,
    skill_names: list[str] | None = None,
) -> Trajectory:
    trajectory = _trajectory_with_messages(messages)
    rail._builder = Mock()
    rail._builder.build.return_value = trajectory
    rail._evolution_store.list_skill_names = Mock(return_value=skill_names or ["skill-a"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._evolution_store.resolve_skill_dir = Mock(return_value=None)
    return trajectory


def _active_request_detector(
    *,
    trajectory_signals: list[EvolutionSignal] | None = None,
    user_signals: list[EvolutionSignal] | None = None,
    trajectory_error: Exception | None = None,
) -> Mock:
    detector = Mock()
    detector.bind_llm.return_value = detector
    if trajectory_error is None:
        detector.detect_trajectory_signals.return_value = trajectory_signals or []
    else:
        detector.detect_trajectory_signals.side_effect = trajectory_error
    detector.detect_user_intent = AsyncMock(return_value=user_signals or [])
    return detector


def _approval_events(events):
    return [event for event in events if event.type == "chat.ask_user_question"]


def _progress_events(events):
    return [event for event in events if event.payload.get("evolution_meta", {}).get("event_kind") == "progress"]


class _MsgContext:
    def __init__(self, messages=None, *, raise_error: bool = False):
        self._messages = list(messages) if messages else []
        self._raise_error = raise_error

    def get_messages(self):
        if self._raise_error:
            raise RuntimeError("get_messages failed")
        return self._messages


class _DummyToolMsg:
    def __init__(self, content: Any):
        self.content = content


# =============================================================================
# Basic Utility Tests
# =============================================================================


def test_extract_file_path():
    rail = SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="dummy")
    cases = [
        ({"file_path": "/a/b/SKILL.md"}, "/a/b/SKILL.md"),
        ({}, ""),
        ('{"file_path":"C:/skills/x/SKILL.md"}', "C:/skills/x/SKILL.md"),
        ("not-json", ""),
        (None, ""),
        (123, ""),
    ]
    for args, expected in cases:
        assert rail._extract_file_path(args) == expected


def test_parse_messages():
    rail = SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="dummy")
    tool_call = SimpleNamespace(id="tc1", name="read_file", arguments='{"file_path":"x"}')
    msg_obj = SimpleNamespace(
        role="assistant",
        content="done",
        tool_calls=[tool_call],
        name="assistant_name",
    )
    raw = [{"role": "user", "content": "hi"}, msg_obj]
    parsed = rail._parse_messages(raw)

    assert parsed[0] == {"role": "user", "content": "hi"}
    assert parsed[1]["role"] == "assistant"
    assert parsed[1]["content"] == "done"
    assert parsed[1]["name"] == "assistant_name"
    assert parsed[1]["tool_calls"][0]["id"] == "tc1"
    assert parsed[1]["tool_calls"][0]["name"] == "read_file"


def test_trajectory_sink_defaults_member_role_to_teammate(tmp_path):
    rail = _make_rail(tmp_path)

    rail.set_trajectory_sink(Mock(), team_id="team-a")

    assert rail._member_role == "teammate"


def test_properties_and_clear_processed_signals(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail.processed_signal_keys.add(("a", "b"))
    rail.auto_scan = False
    rail.auto_save = False
    rail.clear_processed_signals()

    assert rail.auto_scan is False
    assert rail.auto_save is False
    assert rail.processed_signal_keys == set()


@pytest.mark.asyncio
async def test_on_approve_simplify_delegates_to_manager(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    with patch.object(
        rail._manager,
        "approve_simplify",
        AsyncMock(return_value={"kept": 1, "deleted": 0, "merged": 0, "refined": 0, "errors": 0}),
    ) as approve_simplify:
        result = await rail.on_approve_simplify("req-1")

    approve_simplify.assert_awaited_once_with("req-1")
    assert result["kept"] == 1


@pytest.mark.asyncio
async def test_request_simplify_returns_approval_event(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._pending_governance["req-1"] = {
        "skill_name": "skill-a",
        "actions": [{"action": "KEEP", "record_id": "ev_1", "reason": "ok"}],
    }
    rail._manager.request_simplify = AsyncMock(return_value="req-1")

    result = await rail.request_simplify("skill-a", user_intent="trim duplicates")

    assert result.request_id == "req-1"
    rail._manager.request_simplify.assert_awaited_once_with("skill-a", user_intent="trim duplicates")
    assert result.approval_event.payload["request_id"] == "req-1"
    assert result.approval_event.payload["evolution_meta"]["rail_kind"] == "regular"
    assert result.approval_event.payload["evolution_meta"]["skill_name"] == "skill-a"
    assert rail._pending_host_events == []


@pytest.mark.asyncio
async def test_on_reject_simplify_delegates_to_manager(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._pending_governance["req-2"] = {"skill_name": "skill-a"}

    with patch.object(rail._manager, "reject_simplify", AsyncMock()) as reject_simplify:
        await rail.on_reject_simplify("req-2")

    reject_simplify.assert_awaited_once_with("req-2")


# =============================================================================
# after_tool_call Tests
# =============================================================================


@pytest.mark.asyncio
async def test_after_tool_call_does_not_inject_body_experience(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="\nnew experience")

    inputs = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": r"C:\skills\invoice-parser\SKILL.md"},
        tool_msg=_DummyToolMsg("original"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=inputs, session=None)

    await rail._on_after_tool_call(ctx)

    assert inputs.tool_msg.content == "original"
    rail._evolution_store.format_body_experience_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_tool_call_does_not_read_experience_text(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="\nnew experience")
    cases = [
        ToolCallInputs(tool_name="list_skill", tool_args={"file_path": "/a/s/SKILL.md"}, tool_msg=_DummyToolMsg("x")),
        ToolCallInputs(tool_name="read_file", tool_args={"file_path": "/a/s/README.md"}, tool_msg=_DummyToolMsg("x")),
        ToolCallInputs(tool_name="read_file", tool_args={"file_path": "/a/s/SKILL.md"}, tool_msg=None),
    ]
    for item in cases:
        ctx = AgentCallbackContext(agent=None, inputs=item, session=None)
        await rail._on_after_tool_call(ctx)

    rail._evolution_store.format_body_experience_text.assert_not_awaited()
    assert cases[0].tool_msg.content == "x"
    assert cases[1].tool_msg.content == "x"

    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="")
    empty_case = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": "/a/demo/SKILL.md"},
        tool_msg=_DummyToolMsg("z"),
    )
    await rail._on_after_tool_call(AgentCallbackContext(agent=None, inputs=empty_case, session=None))
    assert empty_case.tool_msg.content == "z"
    rail._evolution_store.format_body_experience_text.assert_not_awaited()


# =============================================================================
# after_invoke Tests
# =============================================================================


@pytest.mark.asyncio
async def test_run_evolution_returns_immediately_when_auto_scan_disabled(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=False)
    rail._collect_messages = AsyncMock(return_value=[{"role": "user", "content": "x"}])
    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))
    rail._collect_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_invoke_does_not_trigger_evolution_when_auto_scan_disabled(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=False)
    rail.run_evolution = AsyncMock()

    ctx = AgentCallbackContext(
        agent=None,
        inputs=InvokeInputs(query="round 1", conversation_id="conv-1"),
        session=None,
    )

    await rail.before_invoke(ctx)
    await rail.after_invoke(ctx)

    rail.run_evolution.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_auto_save_commits_via_manager_lifecycle(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    approval_request = SimpleNamespace(request_id="skill_evolve_req")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
    ]

    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_staged_result(approval_request))
    rail._emit_generated_records = AsyncMock()
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail.run_evolution(_trajectory_with_messages(messages), ctx)

    signals = rail._stage_evolution_from_signals.await_args.kwargs["signals"]
    rail._stage_evolution_from_signals.assert_awaited_once_with(
        skill_name="skill-a",
        signals=signals,
        messages=messages,
        user_query="",
        requires_approval=False,
    )
    rail._emit_generated_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_auto_save_false_emits_real_approval_event(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=False)
    record = _make_record("skill-a", content="fresh approval record")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
    ]
    trajectory = _trajectory_with_messages(messages)

    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    approval_request = SimpleNamespace(
        skill_name="skill-a",
        proposal=SimpleNamespace(record_count=1, signal_type=None, signal_source=None),
        pending_change=SimpleNamespace(payload=[record]),
        request_id="skill_evolve_req",
    )
    rail._stage_evolution_from_signals = AsyncMock(return_value=_staged_result(approval_request))
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail.run_evolution(trajectory, ctx)

    signals = rail._stage_evolution_from_signals.await_args.kwargs["signals"]
    rail._stage_evolution_from_signals.assert_awaited_once_with(
        skill_name="skill-a",
        signals=signals,
        messages=messages,
        user_query="",
        requires_approval=True,
    )
    events = _approval_events(await rail.drain_pending_approval_events())
    assert len(events) == 1
    assert events[0].payload["evolution_meta"]["skill_name"] == "skill-a"


@pytest.mark.asyncio
async def test_run_evolution_emits_completed_when_signals_generate_no_records(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=False)
    messages = [{"role": "user", "content": "please review whether this skill learned anything"}]
    trajectory = _trajectory_with_messages(messages)
    signal = _make_signal("skill-a", excerpt="review this conversation")
    detector = Mock()
    detector.bind_llm.return_value = detector
    detector.detect_trajectory_signals.return_value = [signal]
    detector.detect_user_intent = AsyncMock(return_value=[])

    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._online_updater.bind = Mock()
    rail._online_updater.process = AsyncMock(return_value={})

    with patch(
        "openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector",
        return_value=detector,
    ):
        await rail.run_evolution(trajectory, AgentCallbackContext(agent=None, inputs=None, session=None))

    rail._online_updater.process.assert_awaited_once()
    drained_events = await rail.drain_pending_host_events()
    events = _progress_events(drained_events)
    stages = [event.payload["evolution_meta"]["stage"] for event in events]
    assert "generating_updates" in stages
    assert stages[-1] == "completed"
    assert events[-1].payload["evolution_meta"]["skill_name"] == "skill-a"
    assert "no evolution records generated" in events[-1].payload["content"]
    outcomes = [
        event for event in drained_events if event.payload.get("evolution_meta", {}).get("event_kind") == "outcome"
    ]
    assert outcomes
    assert outcomes[-1].payload["evolution_meta"]["status"] == "no_evolution_no_records"
    assert outcomes[-1].payload["evolution_meta"]["skill_name"] == "skill-a"


@pytest.mark.asyncio
async def test_handle_evolution_emits_outcome_for_generation_failed(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=False)
    rail._stage_evolution_from_signals = AsyncMock(return_value=_failed_result("generation_failed"))

    result = await rail._handle_evolution_from_signals(
        skill_name="skill-a",
        signals=[_make_signal("skill-a")],
        messages=[],
        ctx=None,
        requires_approval=True,
    )

    assert result is None
    drained_events = await rail.drain_pending_host_events()
    outcomes = [
        event for event in drained_events if event.payload.get("evolution_meta", {}).get("event_kind") == "outcome"
    ]
    assert outcomes
    assert outcomes[-1].payload["evolution_meta"]["status"] == "generation_failed"
    assert outcomes[-1].payload["evolution_meta"]["skill_name"] == "skill-a"


@pytest.mark.asyncio
async def test_handle_evolution_emits_persistence_failed_without_auto_approved_finalize(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    failed_request = SimpleNamespace(request_id="req-failed")
    rail._stage_evolution_from_signals = AsyncMock(
        return_value=OnlineEvolutionResult(
            skill_name="skill-a",
            status="persistence_failed",
            request=failed_request,
            message="disk full",
        )
    )
    rail.approval_runtime.finalize_staged_evolution_request = AsyncMock(return_value=failed_request)

    result = await rail._handle_evolution_from_signals(
        skill_name="skill-a",
        signals=[_make_signal("skill-a")],
        messages=[],
        ctx=None,
        requires_approval=False,
    )

    assert result is failed_request
    rail.approval_runtime.finalize_staged_evolution_request.assert_not_awaited()
    drained_events = await rail.drain_pending_host_events()
    outcomes = [
        event for event in drained_events if event.payload.get("evolution_meta", {}).get("event_kind") == "outcome"
    ]
    assert outcomes
    assert outcomes[-1].payload["evolution_meta"]["status"] == "persistence_failed"
    assert outcomes[-1].payload["evolution_meta"]["stage"] == "failed"
    assert outcomes[-1].payload["evolution_meta"]["request_id"] == "req-failed"
    assert "disk full" in outcomes[-1].payload["content"]


@pytest.mark.asyncio
async def test_run_evolution_filters_empty_skill_name_and_swallow_exceptions(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
    ]
    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )
    rail._stage_evolution_from_signals.assert_awaited_once()
    assert rail._stage_evolution_from_signals.await_args.kwargs["skill_name"] == "skill-a"

    rail2 = _make_rail(tmp_path, auto_scan=True)
    rail2._collect_messages = AsyncMock(side_effect=RuntimeError("boom"))
    await rail2.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))


@pytest.mark.asyncio
async def test_run_evolution_clears_processed_signal_keys_when_exceed_limit(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
    ]
    rail._processed_signal_keys = {
        ("old", "", "skill-a", str(index)) for index in range(_MAX_PROCESSED_SIGNAL_KEYS + 1)
    }
    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())

    await rail.run_evolution(
        _trajectory_with_messages(messages),
        AgentCallbackContext(agent=None, inputs=None, session=None),
    )

    assert rail._processed_signal_keys == set()


# =============================================================================
# _detect_signals Tests
# =============================================================================


def test_detect_signals_deduplicates_with_processed_keys(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path)
    rail._evolution_store.skill_exists = Mock(return_value=True)
    signal = _make_signal("skill-a", excerpt="same-excerpt")
    monkeypatch.setattr(SignalDetector, "detect", lambda self, messages: [signal])

    first = rail._detect_signals([{"role": "user", "content": "x"}], ["skill-a"])
    second = rail._detect_signals([{"role": "user", "content": "x"}], ["skill-a"])

    assert len(first) == 1
    assert len(second) == 0
    assert ("tool_failure", "bash", "skill-a", "same-excerpt") in rail.processed_signal_keys


def test_detect_signals_clears_processed_keys_when_exceed_limit(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path)
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._processed_signal_keys = {(f"type-{i}", f"excerpt-{i}") for i in range(_MAX_PROCESSED_SIGNAL_KEYS)}
    monkeypatch.setattr(SignalDetector, "detect", lambda self, messages: [_make_signal("skill-a", excerpt="new-one")])

    detected = rail._detect_signals([{"role": "user", "content": "x"}], ["skill-a"])

    assert len(detected) == 1
    assert rail.processed_signal_keys == set()


# =============================================================================
# _stage_evolution_from_signals / event buffer Tests
# =============================================================================


@pytest.mark.asyncio
async def test_stage_evolution_from_signals_builds_context(tmp_path):
    rail = _make_rail(tmp_path)
    signal = _make_signal("skill-a")
    old_desc = _make_record("skill-a", content="old desc")
    old_body = _make_record("skill-a", content="old body")
    new_record = _make_record("skill-a", content="new")

    rail._evolution_store.read_skill_content = AsyncMock(return_value="# skill")
    rail._evolution_store.get_pending_records = AsyncMock(side_effect=[[old_desc], [old_body], []])
    rail._evolver.generate_records = AsyncMock(return_value=[new_record])

    rail._online_updater.bind = Mock()
    rail._online_updater.process = AsyncMock(return_value={("skill_experience_skill-a", "experiences"): [new_record]})
    rail._manager.stage_apply_results = Mock(return_value="approval-request")

    result = await rail._stage_evolution_from_signals(
        "skill-a",
        [signal],
        [{"role": "user", "content": "x"}],
        requires_approval=True,
    )

    assert result.status == "staged"
    assert result.request == "approval-request"
    bind_kwargs = rail._online_updater.bind.call_args.kwargs
    assert list(bind_kwargs["operators"]) == ["skill_experience_skill-a"]
    assert bind_kwargs["targets"] == ["experiences"]
    online_ctx = bind_kwargs["online_contexts"]["skill-a"]
    assert online_ctx.skill_content == "# skill"
    assert online_ctx.messages == [{"role": "user", "content": "x"}]
    assert online_ctx.existing_desc_records == [old_desc]
    assert online_ctx.existing_body_records == [old_body]
    assert online_ctx.existing_script_records == []
    rail._manager.stage_apply_results.assert_called_once()
    stage_args = rail._manager.stage_apply_results.call_args
    assert stage_args.args[0] == "skill-a"
    assert stage_args.kwargs["requires_approval"] is True


@pytest.mark.asyncio
async def test_stage_evolution_from_signals_returns_no_records_result_when_apply_results_empty(tmp_path):
    rail = _make_rail(tmp_path)
    rail._online_updater.bind = Mock()
    rail._online_updater.process = AsyncMock(return_value={})

    result = await rail._stage_evolution_from_signals("skill-a", [_make_signal("skill-a")], [], requires_approval=True)
    assert result.status == "no_evolution_no_records"
    assert result.request is None


@pytest.mark.asyncio
async def test_emit_generated_records_and_drain_pending_events(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a", content="x" * 1200)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    request = _stage_approval_request(rail, "skill-a", [record])

    await rail._emit_generated_records(ctx, "skill-a", request)
    events = _approval_events(await rail.drain_pending_approval_events())

    assert len(events) == 1
    event = events[0]
    assert event.type == "chat.ask_user_question"
    request_id = event.payload["request_id"]
    # request_id uses the "skill_evolve_" prefix (PendingChange.change_id)
    assert request_id.startswith("skill_evolve_")
    assert event.payload["questions"][0]["header"] == "技能演进审批"
    assert event.payload["evolution_meta"]["rail_kind"] == "regular"
    assert event.payload["evolution_meta"]["skill_name"] == "skill-a"
    assert event.payload["evolution_meta"]["request_id"] == request_id
    assert request_id in rail._pending_approval_snapshots
    assert await rail.drain_pending_approval_events() == []


@pytest.mark.asyncio
async def test_emit_generated_records_ignores_missing_approval_request(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail._emit_generated_records(ctx, "skill-a")

    assert await rail.drain_pending_approval_events() == []


@pytest.mark.asyncio
async def test_on_approve_flushes_snapshot_records(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    request_id = "skill_evolve_req"
    rail._manager.approve_request = AsyncMock(return_value=Mock(applied_count=1, pending_count=0))
    rail._pending_approval_snapshots[request_id] = Mock(
        skill_name="skill-a",
        payload=[],
        messages=None,
        is_shared_records=False,
    )

    await rail.on_approve(request_id)

    rail._manager.approve_request.assert_awaited_once_with(request_id)


@pytest.mark.asyncio
async def test_on_reject_discards_snapshot_records(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    request_id = "skill_evolve_req"
    rail._manager.reject_request = AsyncMock(return_value=Mock(rejected_count=1))
    rail._pending_approval_snapshots[request_id] = Mock(
        skill_name="skill-a",
        payload=[],
        messages=None,
        is_shared_records=False,
    )

    await rail.on_reject(request_id)

    rail._manager.reject_request.assert_awaited_once_with(request_id)


@pytest.mark.asyncio
async def test_on_approve_partial_failure_retains_pending_change(tmp_path):
    """If one record fails, the unwritten tail stays pending for retry."""
    rail = _make_rail(tmp_path, auto_save=False)
    record_1 = _make_record("skill-a", content="first")
    record_2 = _make_record("skill-a", content="second")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail._evolution_store.append_record = AsyncMock(side_effect=[None, OSError("disk full")])
    request = _stage_approval_request(rail, "skill-a", [record_1, record_2])

    await rail._emit_generated_records(ctx, "skill-a", request)
    events = _approval_events(await rail.drain_pending_approval_events())
    request_id = events[0].payload["request_id"]

    await rail.on_approve(request_id)

    assert rail._evolution_store.append_record.await_count == 2
    assert request_id in rail._pending_approval_snapshots
    pending = rail._pending_approval_snapshots[request_id]
    assert pending.payload == [record_2]

    # Host retries: now the remaining record succeeds
    rail._evolution_store.append_record = AsyncMock()
    await rail.on_approve(request_id)
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record_2)
    assert request_id not in rail._pending_approval_snapshots


@pytest.mark.asyncio
async def test_on_approve_full_failure_then_retry_succeeds(tmp_path):
    """If the first record fails, PendingChange is kept; retry succeeds."""
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a", content="important")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail._evolution_store.append_record = AsyncMock(side_effect=OSError("disk full"))
    request = _stage_approval_request(rail, "skill-a", [record])

    await rail._emit_generated_records(ctx, "skill-a", request)
    events = _approval_events(await rail.drain_pending_approval_events())
    request_id = events[0].payload["request_id"]

    await rail.on_approve(request_id)

    # All records still pending after full failure
    assert request_id in rail._pending_approval_snapshots
    pending = rail._pending_approval_snapshots[request_id]
    assert len(pending.payload) == 1

    # Host retries: now append succeeds
    rail._evolution_store.append_record = AsyncMock()
    await rail.on_approve(request_id)
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record)
    assert request_id not in rail._pending_approval_snapshots


@pytest.mark.asyncio
async def test_concurrent_approval_batches_are_independent(tmp_path):
    """Two approval prompts for the same skill must operate on disjoint record batches."""
    rail = _make_rail(tmp_path, auto_save=False)
    record_a = _make_record("skill-a", content="batch-1")
    record_b = _make_record("skill-a", content="batch-2")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail._evolution_store.append_record = AsyncMock()

    request_a = _stage_approval_request(rail, "skill-a", [record_a])
    await rail._emit_generated_records(ctx, "skill-a", request_a)

    request_b = _stage_approval_request(rail, "skill-a", [record_b])
    await rail._emit_generated_records(ctx, "skill-a", request_b)

    events = _approval_events(await rail.drain_pending_approval_events())
    assert len(events) == 2
    req1 = events[0].payload["request_id"]
    req2 = events[1].payload["request_id"]

    # Approving the first prompt should write only record_a
    await rail.on_approve(req1)
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record_a)
    rail._evolution_store.append_record.reset_mock()

    # Approving the second prompt should write only record_b
    await rail.on_approve(req2)
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record_b)


@pytest.mark.asyncio
async def test_on_approve_only_flushes_snapshot_records(tmp_path):
    """Approving one batch must not flush unrelated pending records for the same skill."""
    rail = _make_rail(tmp_path, auto_save=False)
    approved = _make_record("skill-a", content="approved")
    pending_later = _make_record("skill-a", content="pending-later")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail._evolution_store.append_record = AsyncMock()
    request = _stage_approval_request(rail, "skill-a", [approved])

    await rail._emit_generated_records(ctx, "skill-a", request)
    request_id = _approval_events(await rail.drain_pending_approval_events())[0].payload["request_id"]

    later_request = _stage_approval_request(rail, "skill-a", [pending_later])
    await rail.on_approve(request_id)

    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", approved)
    assert later_request.request_id in rail._pending_approval_snapshots
    assert request_id not in rail._pending_approval_snapshots


# =============================================================================
# _infer_primary_skill Tests
# =============================================================================


def test_infer_primary_skill_picks_most_frequent(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"arguments": "/skills/skill-a/SKILL.md"},
                {"arguments": "/skills/skill-b/SKILL.md"},
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"arguments": "/skills/skill-a/SKILL.md"},
            ],
        },
    ]
    result = rail._infer_primary_skill(messages, ["skill-a", "skill-b"])
    assert result == "skill-a"


def test_infer_primary_skill_returns_none_when_no_match(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {"role": "user", "content": "just chatting"},
    ]
    result = rail._infer_primary_skill(messages, ["skill-a"])
    assert result is None


def test_infer_primary_skill_from_tool_result_content(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {"role": "tool", "content": "Content of /skills/skill-x/SKILL.md: ..."},
    ]
    result = rail._infer_primary_skill(messages, ["skill-x"])
    assert result == "skill-x"


def test_infer_primary_skill_ignores_unknown_skills(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {"role": "tool", "content": "/skills/unknown-skill/SKILL.md"},
    ]
    result = rail._infer_primary_skill(messages, ["skill-a"])
    assert result is None


def test_infer_primary_skill_prefers_skill_tool_over_paths(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "read_file", "arguments": "/workspace/skills/skill-b/reference.md"},
                {"name": "skill_tool", "arguments": '{"skill_name":"skill-a","relative_file_path":"reference.md"}'},
            ],
        },
        {"role": "tool", "content": "/workspace/skill-b/SKILL.md"},
    ]

    result = rail._infer_primary_skill(messages, ["skill-a", "skill-b"])

    assert result == "skill-a"


def test_infer_primary_skill_prefers_skills_path_over_legacy_skill_md(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {"role": "tool", "content": "/workspace/legacy-skill/SKILL.md"},
        {"role": "tool", "content": "/workspace/skills/new-skill/reference/notes.md"},
    ]

    result = rail._infer_primary_skill(messages, ["legacy-skill", "new-skill"])

    assert result == "new-skill"


def test_is_regular_skill_filters_team_and_swarm_skill(tmp_path):
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
    )
    rail._evolver = Mock()

    regular_dir = tmp_path / "skills" / "regular-skill"
    regular_dir.mkdir(parents=True)
    (regular_dir / "SKILL.md").write_text(
        "---\nname: regular-skill\nkind: skill\n---\n# Regular",
        encoding="utf-8",
    )

    team_dir = tmp_path / "skills" / "team-skill"
    team_dir.mkdir(parents=True)
    (team_dir / "SKILL.md").write_text(
        "---\nname: team-skill\nkind: team-skill\n---\n# Team",
        encoding="utf-8",
    )
    swarm_dir = tmp_path / "skills" / "swarm-skill"
    swarm_dir.mkdir(parents=True)
    (swarm_dir / "SKILL.md").write_text(
        "---\nname: swarm-skill\nkind: swarm-skill\nroles:\n  - name: planner\n    kind: ai_agent\n---\n# Swarm",
        encoding="utf-8",
    )

    assert rail._is_regular_skill("regular-skill") is True
    assert rail._is_regular_skill("team-skill") is False
    assert rail._is_regular_skill("swarm-skill") is False


# =============================================================================
# Zero-signal fallback & unattributed signal tests
# =============================================================================


@pytest.mark.asyncio
async def test_run_evolution_zero_signals_creates_conversation_review(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"arguments": "/skills/skill-a/SKILL.md"},
            ],
        },
        {"role": "user", "content": "hello"},
    ]
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._infer_primary_skill = Mock(return_value="skill-a")
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._stage_evolution_from_signals.assert_awaited_once()
    call_args = rail._stage_evolution_from_signals.await_args
    assert call_args.kwargs["skill_name"] == "skill-a"
    signals_passed = call_args.kwargs["signals"]
    assert len(signals_passed) == 1
    assert signals_passed[0].signal_type == "conversation_review"
    assert signals_passed[0].context == {
        "source": "passive_conversation",
    }


@pytest.mark.asyncio
async def test_run_evolution_uses_normalized_messages_for_signal_detection(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._evolution_store.list_skill_names = Mock(return_value=[])
    detector = Mock()
    detector.bind_llm.return_value = detector
    detector.detect_trajectory_signals.return_value = []
    detector.detect_user_intent = AsyncMock(return_value=[])
    trajectory = Trajectory(
        execution_id="exec-message-object",
        steps=[
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="test-model",
                    messages=[SystemMessage(content="system prompt")],
                ),
            )
        ],
        source="online",
    )
    snapshot = {
        "trajectory": trajectory,
        "messages": [{"role": "system", "content": "system prompt"}],
        "presented_entries": [],
    }

    with patch(
        "openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector",
        return_value=detector,
    ):
        await rail.run_evolution(trajectory, ctx=None, snapshot=snapshot)

    detector.detect_trajectory_signals.assert_called_once_with(
        trajectory,
        messages=[{"role": "system", "content": "system prompt"}],
    )
    detector.detect_user_intent.assert_awaited_once_with([{"role": "system", "content": "system prompt"}])


@pytest.mark.asyncio
async def test_run_evolution_uses_llm_for_passive_user_messages(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}],
        },
        {"role": "user", "content": "不对，你应该先检查文件是否存在"},
    ]
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._infer_primary_skill = Mock(return_value="skill-a")
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())

    rail._evolver._llm.invoke = AsyncMock(
        return_value={"content": '{"is_feedback": true, "excerpt": "不对，你应该先检查文件是否存在"}'}
    )
    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._stage_evolution_from_signals.assert_awaited_once()
    signals_passed = rail._stage_evolution_from_signals.await_args.kwargs["signals"]
    assert len(signals_passed) == 1
    assert signals_passed[0].signal_type == "user_intent"
    assert signals_passed[0].excerpt == "不对，你应该先检查文件是否存在"
    assert signals_passed[0].context == {"source": "passive_conversation"}


@pytest.mark.asyncio
async def test_run_evolution_user_messages_without_llm_feedback_fall_back_to_conversation_review(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}],
        },
        {"role": "user", "content": "你好"},
    ]
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._infer_primary_skill = Mock(return_value="skill-a")
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())

    rail._evolver._llm.invoke = AsyncMock(return_value={"content": '{"is_feedback": false, "excerpt": ""}'})
    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._stage_evolution_from_signals.assert_awaited_once()
    signals_passed = rail._stage_evolution_from_signals.await_args.kwargs["signals"]
    assert len(signals_passed) == 1
    assert signals_passed[0].signal_type == "conversation_review"


@pytest.mark.asyncio
async def test_run_evolution_user_messages_llm_failure_falls_back_to_rule_signal(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}],
        },
        {"role": "user", "content": "不对，你应该先检查文件是否存在"},
    ]
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._infer_primary_skill = Mock(return_value="skill-a")
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())

    rail._evolver._llm.invoke = AsyncMock(side_effect=RuntimeError("llm down"))
    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._stage_evolution_from_signals.assert_awaited_once()
    signals_passed = rail._stage_evolution_from_signals.await_args.kwargs["signals"]
    assert len(signals_passed) == 1
    assert signals_passed[0].signal_type == "user_intent"
    assert signals_passed[0].excerpt == "不对，你应该先检查文件是否存在"


@pytest.mark.asyncio
async def test_run_evolution_deduplicates_user_intent_against_existing_signal(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}],
        },
        {"role": "user", "content": "不对，你应该先检查文件是否存在"},
    ]
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())

    rail._evolver._llm.invoke = AsyncMock(
        return_value={"content": '{"is_feedback": true, "excerpt": "不对，你应该先检查文件是否存在"}'}
    )
    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    signals_passed = rail._stage_evolution_from_signals.await_args.kwargs["signals"]
    assert len(signals_passed) == 1
    assert signals_passed[0].signal_type == "user_intent"


@pytest.mark.asyncio
async def test_run_evolution_zero_signals_no_primary_skill_returns(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [{"role": "user", "content": "hi"}]
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._infer_primary_skill = Mock(return_value=None)
    rail._stage_evolution_from_signals = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._stage_evolution_from_signals.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_emits_started_and_cancelled_when_no_skill_used(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [{"role": "user", "content": "hi"}]
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._infer_primary_skill = Mock(return_value=None)
    rail._stage_evolution_from_signals = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages),
        AgentCallbackContext(agent=None, inputs=None, session=None),
    )

    events = _progress_events(await rail.drain_pending_host_events())
    stages = [event.payload["evolution_meta"]["stage"] for event in events]
    contents = [event.payload["content"] for event in events]

    assert stages[0] == "started"
    assert "detecting_signals" in stages
    assert stages[-1] == "cancelled"
    assert "regular skill" in contents[-1]
    assert "no skill usage" in contents[-1]
    assert "cancelling" in contents[-1]
    rail._stage_evolution_from_signals.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_filters_team_and_swarm_skills_from_detection(tmp_path):
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        auto_scan=True,
        auto_save=True,
    )
    rail._evolver = Mock()

    regular_dir = tmp_path / "skills" / "skill-a"
    regular_dir.mkdir(parents=True)
    (regular_dir / "SKILL.md").write_text(
        "---\nname: skill-a\nkind: skill\n---\n# Skill A",
        encoding="utf-8",
    )

    team_dir = tmp_path / "skills" / "team-skill-a"
    team_dir.mkdir(parents=True)
    (team_dir / "SKILL.md").write_text(
        "---\nname: team-skill-a\nkind: team-skill\n---\n# Team Skill A",
        encoding="utf-8",
    )
    swarm_dir = tmp_path / "skills" / "swarm-skill-a"
    swarm_dir.mkdir(parents=True)
    (swarm_dir / "SKILL.md").write_text(
        "---\nname: swarm-skill-a\nkind: swarm-skill\nroles:\n  - name: planner\n    kind: ai_agent\n---\n# Swarm Skill A",
        encoding="utf-8",
    )

    messages = [{"role": "user", "content": "hi"}]
    rail._evolution_store = EvolutionStore(str(tmp_path / "skills"))
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a", "team-skill-a", "swarm-skill-a"])
    rail._infer_primary_skill = Mock(return_value=None)
    rail._stage_evolution_from_signals = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._infer_primary_skill.assert_called_once_with(messages, ["skill-a"])


@pytest.mark.asyncio
async def test_run_evolution_unattributed_signals_get_fallback_skill(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
        {"role": "tool", "content": "Traceback: boom"},
    ]

    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    rail._stage_evolution_from_signals.assert_awaited_once()
    call_args = rail._stage_evolution_from_signals.await_args
    assert all(signal.skill_name == "skill-a" for signal in call_args.kwargs["signals"])


@pytest.mark.asyncio
async def test_run_evolution_multiple_attributed_skills_no_fallback(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: a", "name": "bash"},
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-b/SKILL.md"}]},
        {"role": "tool", "content": "Error: b", "name": "bash"},
    ]

    rail._collect_messages = AsyncMock(return_value=messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a", "skill-b"])
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(
        _trajectory_with_messages(messages), AgentCallbackContext(agent=None, inputs=None, session=None)
    )

    assert rail._stage_evolution_from_signals.await_count == 2


# =============================================================================
# Constructor Validation Tests (Issue #4)
# =============================================================================


def test_init_invalid_eval_interval_raises():
    with pytest.raises(ValueError, match="eval_interval"):
        SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="m", eval_interval=0)


def test_init_valid_params_no_error():
    rail = SkillEvolutionRail(
        skills_dir="skills",
        llm=Mock(),
        model="m",
        eval_interval=3,
    )
    assert rail._eval_interval == 3


def test_init_accepts_custom_policies_and_timeout():
    generate_policy = LLMInvokePolicy(
        attempt_timeout_secs=9,
        total_budget_secs=27,
        max_attempts=2,
    )
    evaluate_policy = LLMInvokePolicy(
        attempt_timeout_secs=11,
        total_budget_secs=33,
        max_attempts=2,
    )
    simplify_policy = LLMInvokePolicy(
        attempt_timeout_secs=13,
        total_budget_secs=39,
        max_attempts=2,
    )
    rail = SkillEvolutionRail(
        skills_dir="skills",
        llm=Mock(),
        model="m",
        evolution_total_timeout_secs=555.0,
        generate_records_llm_policy=generate_policy,
        evaluate_llm_policy=evaluate_policy,
        simplify_llm_policy=simplify_policy,
    )

    assert rail.evolution_total_timeout_secs == 555.0
    assert rail.generate_records_llm_policy is generate_policy
    assert rail.evaluate_llm_policy is evaluate_policy
    assert rail.simplify_llm_policy is simplify_policy
    assert rail.evolution_config["generate_records_llm_policy"] is generate_policy
    assert rail.evolution_config["evolution_total_timeout_secs"] == 555.0


def test_update_llm_refreshes_fresh_optimizer_references(tmp_path):
    rail = _make_rail(tmp_path)
    rail._scorer = Mock()
    new_llm = Mock()

    rail.update_llm(new_llm, "new-model")

    rail._evolver.update_llm.assert_called_once_with(new_llm, "new-model")
    rail._scorer.update_llm.assert_called_once_with(new_llm, "new-model")


@pytest.mark.asyncio
async def test_drain_pending_approval_events_defaults_to_total_timeout(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_total_timeout_secs = 321.0

    assert await rail.drain_pending_approval_events(wait=True) == []


# =============================================================================
# Session-level State Isolation Tests
# =============================================================================


def test_tracker_session_presented_records_isolated_per_session(tmp_path):
    from types import SimpleNamespace

    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker
    session_a = SimpleNamespace()
    session_b = SimpleNamespace()

    # Set different values for different sessions
    telemetry.set_session_presented_records(session_a, [("skill-a", _make_record("skill-a"), "snippet-a")])
    telemetry.set_session_presented_records(session_b, [("skill-b", _make_record("skill-b"), "snippet-b")])

    records_a = telemetry.get_session_presented_records(session_a)
    records_b = telemetry.get_session_presented_records(session_b)

    assert len(records_a) == 1
    assert records_a[0][0] == "skill-a"
    assert records_a[0][2] == "snippet-a"
    assert len(records_b) == 1
    assert records_b[0][0] == "skill-b"
    assert records_b[0][2] == "snippet-b"


def test_tracker_session_eval_counter_isolated_per_session(tmp_path):
    from types import SimpleNamespace

    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker
    session_a = SimpleNamespace()
    session_b = SimpleNamespace()

    telemetry.set_session_eval_counter(session_a, 3)
    telemetry.set_session_eval_counter(session_b, 7)

    assert telemetry.get_session_eval_counter(session_a) == 3
    assert telemetry.get_session_eval_counter(session_b) == 7


def test_tracker_session_helpers_with_none_session(tmp_path):
    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker
    assert telemetry.get_session_presented_records(None) == []
    assert telemetry.get_session_eval_counter(None) == 0
    # Setting with None session must not raise
    telemetry.set_session_presented_records(None, [])
    telemetry.set_session_eval_counter(None, 5)


# =============================================================================
# Fix #2: Presented records carry per-presentation snippet
# =============================================================================


def test_tracker_session_presented_records_store_snippet(tmp_path):
    """Each entry stored in session must include the presentation-time snippet."""
    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker
    session = SimpleNamespace()

    record = _make_record("sk")
    telemetry.set_session_presented_records(session, [("sk", record, "my_snippet")])

    entries = telemetry.get_session_presented_records(session)
    assert len(entries) == 1
    skill_name, stored_record, snippet = entries[0]
    assert skill_name == "sk"
    assert stored_record is record
    assert snippet == "my_snippet"


@pytest.mark.asyncio
async def test_tracker_evaluation_uses_per_record_snippet(tmp_path):
    """Scorer must be called with the snippet bound to each record, not current messages."""
    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker

    record_a = _make_record("skill_a")
    record_b = _make_record("skill_b")

    # Two records from different conversations with different snippets
    presented_entries = [
        ("skill_a", record_a, "snippet_from_turn_1"),
        ("skill_b", record_b, "snippet_from_turn_2"),
    ]

    captured_snippets: list[str] = []

    async def fake_evaluate(snippet, records):
        captured_snippets.append(snippet)
        return []

    rail._scorer = Mock()
    rail._scorer.evaluate = fake_evaluate
    telemetry = ExperienceTracker(
        store=rail._evolution_store,
        scorer=rail._scorer,
        eval_interval=rail._eval_interval,
    )

    await telemetry.evaluate_presented(presented_entries)

    # Must have evaluated with the stored snippets, not the current messages
    assert "snippet_from_turn_1" in captured_snippets
    assert "snippet_from_turn_2" in captured_snippets


@pytest.mark.asyncio
async def test_snapshot_consumes_experience_tracker_state(tmp_path):
    rail = _make_rail(tmp_path)
    record = _make_record("skill-a")
    presented_entries = [("skill-a", record, "snippet")]
    rail._experience_tracker.consume_eval_state = Mock(return_value=presented_entries)
    session = SimpleNamespace()
    ctx = AgentCallbackContext(
        agent=None,
        inputs=SimpleNamespace(conversation_id="session-1"),
        session=session,
        context=_MsgContext(messages=[{"role": "user", "content": "hello"}]),
    )

    snapshot = await rail._snapshot_for_evolution(
        _trajectory_with_messages([{"role": "user", "content": "hello"}]),
        ctx,
    )

    assert snapshot is not None
    assert snapshot["presented_entries"] == presented_entries
    rail._experience_tracker.consume_eval_state.assert_called_once_with(session)


@pytest.mark.asyncio
async def test_run_evolution_evaluates_presented_entries_from_snapshot(tmp_path):
    rail = _make_rail(tmp_path)
    record = _make_record("skill-a")
    presented_entries = [("skill-a", record, "snippet")]
    rail._experience_tracker.evaluate_presented = AsyncMock()
    rail._evolution_store.list_skill_names = Mock(return_value=[])
    rail._infer_primary_skill = Mock(return_value=None)

    detector = Mock()
    detector.bind_llm.return_value = detector
    detector.detect_trajectory_signals = Mock(return_value=[])
    detector.detect_user_intent = AsyncMock(return_value=[])
    with patch("openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector", return_value=detector):
        await rail.run_evolution(
            None,
            snapshot={
                "trajectory": None,
                "messages": [{"role": "user", "content": "hello"}],
                "presented_entries": presented_entries,
            },
        )

    rail._experience_tracker.evaluate_presented.assert_awaited_once_with(presented_entries)


# =============================================================================
# Fix #3: Only BODY records are tracked as presented
# =============================================================================


@pytest.mark.asyncio
async def test_tracker_record_presented_only_body_records(tmp_path):
    """record_presented must only update BODY records, not DESCRIPTION records."""
    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker

    body_record = _make_record("sk")
    body_record.target = EvolutionTarget.BODY
    body_record.score = 0.8

    desc_record = _make_record("sk", content="desc exp")
    desc_record.target = EvolutionTarget.DESCRIPTION
    desc_record.score = 0.9  # Higher score but wrong type

    rail._evolution_store.get_records_by_score = AsyncMock(return_value=[desc_record, body_record])
    rail._evolution_store.update_record_scores = AsyncMock()

    session = SimpleNamespace()
    await telemetry.record_presented(session=session, skill_name="sk", presentation_snippet="some_snippet")

    # update_record_scores must only contain the body record id
    call_args = rail._evolution_store.update_record_scores.call_args
    updates: dict = call_args[0][1]
    assert body_record.id in updates
    assert desc_record.id not in updates

    # Session must only contain the body record
    entries = telemetry.get_session_presented_records(session)
    assert len(entries) == 1
    assert entries[0][1].id == body_record.id


@pytest.mark.asyncio
async def test_tracker_record_presented_skips_when_no_body_records(tmp_path):
    """record_presented must be a no-op when there are no BODY records."""
    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker

    desc_record = _make_record("sk")
    desc_record.target = EvolutionTarget.DESCRIPTION
    desc_record.score = 0.9

    rail._evolution_store.get_records_by_score = AsyncMock(return_value=[desc_record])
    rail._evolution_store.update_record_scores = AsyncMock()

    session = SimpleNamespace()
    await telemetry.record_presented(session=session, skill_name="sk", presentation_snippet="snip")

    rail._evolution_store.update_record_scores.assert_not_called()
    assert telemetry.get_session_presented_records(session) == []


@pytest.mark.asyncio
async def test_tracker_record_presented_records_only_matching_body_ids(tmp_path):
    rail = _make_rail(tmp_path)
    telemetry = rail._experience_tracker

    body_record = _make_record("sk")
    body_record.id = "ev_body"
    body_record.score = 0.8
    desc_record = _make_record("sk", content="desc exp")
    desc_record.id = "ev_desc"
    desc_record.change.target = EvolutionTarget.DESCRIPTION
    desc_record.score = 0.9

    rail._evolution_store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[desc_record, body_record]))
    rail._evolution_store.update_record_scores = AsyncMock()

    session = SimpleNamespace()
    await telemetry.record_presented_records(
        session=session,
        skill_name="sk",
        presentation_snippet="### [ev_desc]\n### [ev_body]",
        record_ids=["ev_desc", "ev_body"],
    )

    updates = rail._evolution_store.update_record_scores.call_args.args[1]
    assert list(updates) == ["ev_body"]
    entries = telemetry.get_session_presented_records(session)
    assert len(entries) == 1
    assert entries[0][1].id == "ev_body"


@pytest.mark.asyncio
async def test_after_tool_call_does_not_record_experience_tracker(tmp_path):
    """after_tool_call must not record presentation telemetry for SKILL.md reads."""
    rail = _make_rail(tmp_path, auto_scan=True)

    tool_inputs = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": "/workspace/skills/my_skill/SKILL.md"},
    )
    ctx = Mock()
    ctx.inputs = tool_inputs
    ctx.session = None

    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="")
    rail._experience_tracker.record_presented = AsyncMock()

    await rail._on_after_tool_call(ctx)

    rail._experience_tracker.record_presented.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_tool_call_does_not_record_skill_md_index_ids(tmp_path):
    """SKILL.md index visibility must not count as BODY experience presentation."""
    rail = _make_rail(tmp_path, auto_scan=True)
    rail._experience_tracker.record_presented_records = AsyncMock()

    tool_inputs = ToolCallInputs(
        tool_name="skill_tool",
        tool_args={"skill_name": "my_skill"},
        tool_msg=_DummyToolMsg("## Evolution Experiences\n- [ev_body] (score=0.90) useful"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=tool_inputs, session=SimpleNamespace())

    await rail._on_after_tool_call(ctx)

    rail._experience_tracker.record_presented_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_tool_call_records_skill_tool_evolution_detail_read(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True)
    session = SimpleNamespace()
    rail._experience_tracker.record_presented_records = AsyncMock()

    tool_inputs = ToolCallInputs(
        tool_name="skill_tool",
        tool_args={
            "skill_name": "my_skill",
            "relative_file_path": "evolution/troubleshooting.md",
        },
        tool_result=SimpleNamespace(
            data={
                "skill_content": "# Troubleshooting\n\n### [ev_body] Fix the parser\nDetails",
            }
        ),
        tool_msg=_DummyToolMsg("ToolOutput wrapper"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=tool_inputs, session=session)

    await rail._on_after_tool_call(ctx)

    rail._experience_tracker.record_presented_records.assert_awaited_once_with(
        session=session,
        skill_name="my_skill",
        presentation_snippet="# Troubleshooting\n\n### [ev_body] Fix the parser\nDetails",
        record_ids=["ev_body"],
    )


@pytest.mark.asyncio
async def test_after_tool_call_records_read_file_evolution_detail_read(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True)
    session = SimpleNamespace()
    skill_dir = tmp_path / "skills" / "my_skill"
    experience_file = skill_dir / "evolution" / "troubleshooting.md"
    rail._evolution_store.list_skill_names = Mock(return_value=["my_skill"])
    rail._evolution_store.resolve_skill_dir = Mock(return_value=skill_dir)
    rail._experience_tracker.record_presented_records = AsyncMock()

    tool_inputs = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": str(experience_file)},
        tool_msg=_DummyToolMsg("     7\t### [ev_body] Fix the parser\n     8\tDetails"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=tool_inputs, session=session)

    await rail._on_after_tool_call(ctx)

    rail._experience_tracker.record_presented_records.assert_awaited_once_with(
        session=session,
        skill_name="my_skill",
        presentation_snippet="     7\t### [ev_body] Fix the parser\n     8\tDetails",
        record_ids=["ev_body"],
    )


@pytest.mark.asyncio
async def test_after_tool_call_skips_evolution_detail_when_no_record_ids(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True)
    rail._experience_tracker.record_presented_records = AsyncMock()

    tool_inputs = ToolCallInputs(
        tool_name="skill_tool",
        tool_args={
            "skill_name": "my_skill",
            "relative_file_path": "evolution/troubleshooting.md",
        },
        tool_msg=_DummyToolMsg("Details from a partial read without a heading id"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=tool_inputs, session=SimpleNamespace())

    await rail._on_after_tool_call(ctx)

    rail._experience_tracker.record_presented_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_presented_experiences_delegates_to_tracker(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True)
    session = SimpleNamespace()
    rail._experience_tracker.record_presented = AsyncMock()

    await rail.record_presented_experiences(
        "skill-a",
        "presentation snippet",
        session=session,
    )

    rail._experience_tracker.record_presented.assert_awaited_once_with(
        session=session,
        skill_name="skill-a",
        presentation_snippet="presentation snippet",
    )


@pytest.mark.asyncio
async def test_record_presented_experiences_with_record_ids_delegates_to_tracker(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True)
    session = SimpleNamespace()
    rail._experience_tracker.record_presented_records = AsyncMock()

    await rail.record_presented_experiences(
        "skill-a",
        "presentation snippet",
        session=session,
        record_ids=["ev_1", "ev_2"],
    )

    rail._experience_tracker.record_presented_records.assert_awaited_once_with(
        session=session,
        skill_name="skill-a",
        presentation_snippet="presentation snippet",
        record_ids=["ev_1", "ev_2"],
    )


# =============================================================================
# Fix #4: create_skill refuses to overwrite existing skill
# =============================================================================


@pytest.mark.asyncio
async def test_create_skill_refuses_to_overwrite_existing(tmp_path):
    """create_skill must return None and not write files when skill dir already exists."""
    from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore

    store = EvolutionStore(str(tmp_path))

    # Pre-create the skill directory to simulate an existing skill
    existing_dir = tmp_path / "my_skill"
    existing_dir.mkdir()
    existing_skill_md = existing_dir / "SKILL.md"
    existing_skill_md.write_text("original content")

    result = await store.create_skill(
        name="my_skill",
        description="duplicate",
        body="should not overwrite",
    )

    assert result is None, "create_skill must return None for existing skill"
    # Original file must be untouched
    assert existing_skill_md.read_text() == "original content"


@pytest.mark.asyncio
async def test_create_skill_succeeds_for_new_skill(tmp_path):
    """create_skill must succeed and create files when skill does not exist."""
    from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore

    store = EvolutionStore(str(tmp_path))

    result = await store.create_skill(
        name="brand_new_skill",
        description="A fresh skill",
        body="## Instructions\nDo things.",
    )

    assert result is not None
    assert (result / "SKILL.md").exists()
    content = (result / "SKILL.md").read_text()
    assert "brand_new_skill" in content
    assert "A fresh skill" in content


# =============================================================================
# User-triggered evolution public API tests
# =============================================================================


@pytest.mark.asyncio
async def test_request_user_evolution_returns_request_id_when_records_staged(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    request = SimpleNamespace(request_id="skill_evolve_user", pending_change=SimpleNamespace(payload=[]))
    captured: dict[str, Any] = {}

    async def fake_handle(**kwargs):
        captured.update(kwargs)
        return _handle_result(request)

    rail._handle_evolution_from_signals_with_result = fake_handle

    result = await rail.request_user_evolution("skill-a", "add troubleshooting guidance")

    assert result.request_id == "skill_evolve_user"
    assert rail._pending_host_events == []
    assert captured["skill_name"] == "skill-a"
    assert captured["signals"][0].signal_type == "user_intent"
    assert captured["signals"][0].context == {"source": "explicit_request"}
    assert captured["messages"] == [{"role": "user", "content": "add troubleshooting guidance"}]
    assert captured["user_query"] == "add troubleshooting guidance"
    assert captured["requires_approval"] is True


@pytest.mark.asyncio
async def test_request_user_evolution_uses_current_trajectory_evidence(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/skill-a/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
    ]
    trajectory = _bind_active_request_evidence(rail, messages)
    trajectory_signal = _make_signal("skill-a", excerpt="bash failed")
    passive_user_signal = make_evolution_signal(
        signal_type="user_intent",
        section="Instructions",
        excerpt="不对，先检查文件是否存在",
        skill_name="skill-a",
        source="passive_conversation",
    )
    detector = _active_request_detector(
        trajectory_signals=[trajectory_signal],
        user_signals=[passive_user_signal],
    )
    captured: dict[str, Any] = {}

    async def fake_handle(**kwargs):
        captured.update(kwargs)
        request = SimpleNamespace(request_id="skill_evolve_with_evidence", pending_change=SimpleNamespace(payload=[]))
        return _handle_result(request)

    rail._handle_evolution_from_signals_with_result = fake_handle

    with patch(
        "openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector",
        return_value=detector,
    ):
        result = await rail.request_user_evolution("skill-a", "修复刚才的问题")

    assert result.request_id == "skill_evolve_with_evidence"
    detector.detect_trajectory_signals.assert_called_once_with(trajectory, messages=messages)
    detector.detect_user_intent.assert_awaited_once_with(messages)
    assert captured["messages"] == messages
    assert captured["user_query"] == "修复刚才的问题"
    assert [signal.excerpt for signal in captured["signals"]] == [
        "bash failed",
        "不对，先检查文件是否存在",
        "修复刚才的问题",
    ]
    assert [signal.context["source"] for signal in captured["signals"]] == [
        "passive_conversation",
        "passive_conversation",
        "explicit_request",
    ]


@pytest.mark.asyncio
async def test_request_user_evolution_empty_intent_uses_trajectory_signals(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    messages = [{"role": "tool", "content": "Error: command failed", "name": "bash"}]
    _bind_active_request_evidence(rail, messages)
    detector = _active_request_detector(trajectory_signals=[_make_signal("skill-a")])
    captured: dict[str, Any] = {}

    async def fake_handle(**kwargs):
        captured.update(kwargs)
        return _handle_result(SimpleNamespace(request_id="skill_evolve_empty_intent", pending_change=None))

    rail._handle_evolution_from_signals_with_result = fake_handle

    with patch(
        "openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector",
        return_value=detector,
    ):
        result = await rail.request_user_evolution("skill-a")

    assert result.request_id == "skill_evolve_empty_intent"
    assert captured["signals"][0].signal_type == "tool_failure"
    assert captured["user_query"] == ""


@pytest.mark.asyncio
async def test_request_user_evolution_filters_other_skill_signals(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    messages = [{"role": "tool", "content": "Error: command failed", "name": "bash"}]
    _bind_active_request_evidence(rail, messages, skill_names=["skill-a", "skill-b"])
    detector = _active_request_detector(
        trajectory_signals=[
            _make_signal("skill-b", excerpt="other skill failed"),
            _make_signal(None, excerpt="unattributed failure"),
        ],
    )
    captured: dict[str, Any] = {}

    async def fake_handle(**kwargs):
        captured.update(kwargs)
        return _handle_result(SimpleNamespace(request_id="skill_evolve_filtered", pending_change=None))

    rail._handle_evolution_from_signals_with_result = fake_handle

    with patch(
        "openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector",
        return_value=detector,
    ):
        result = await rail.request_user_evolution("skill-a", "修一下")

    assert result.request_id == "skill_evolve_filtered"
    assert [signal.excerpt for signal in captured["signals"]] == ["unattributed failure", "修一下"]


@pytest.mark.asyncio
async def test_request_user_evolution_empty_intent_without_evidence_returns_empty(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    rail._builder = None
    rail._handle_evolution_from_signals_with_result = AsyncMock()

    result = await rail.request_user_evolution("skill-a")

    assert result.request_id is None
    rail._handle_evolution_from_signals_with_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_user_evolution_continues_when_evidence_detection_fails(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    messages = [{"role": "tool", "content": "Error: command failed", "name": "bash"}]
    _bind_active_request_evidence(rail, messages)
    detector = _active_request_detector(trajectory_error=RuntimeError("detector timeout"))
    captured: dict[str, Any] = {}

    async def fake_handle(**kwargs):
        captured.update(kwargs)
        return _handle_result(SimpleNamespace(request_id="skill_evolve_fallback", pending_change=None))

    rail._handle_evolution_from_signals_with_result = fake_handle

    with patch(
        "openjiuwen.harness.rails.evolution.skill_evolution_rail.SignalDetector",
        return_value=detector,
    ):
        result = await rail.request_user_evolution("skill-a", "按刚才的问题修")

    assert result.request_id == "skill_evolve_fallback"
    assert [signal.signal_type for signal in captured["signals"]] == ["user_intent"]
    assert captured["signals"][0].context == {"source": "explicit_request"}


@pytest.mark.asyncio
async def test_request_user_evolution_auto_approve_disables_approval_requirement(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    captured: dict[str, Any] = {}

    async def fake_handle(**kwargs):
        captured.update(kwargs)
        return _handle_result(SimpleNamespace(request_id="skill_evolve_auto"))

    rail._handle_evolution_from_signals_with_result = fake_handle

    result = await rail.request_user_evolution("skill-a", "save directly", auto_approve=True)

    assert result.request_id == "skill_evolve_auto"
    assert result.auto_approved is True
    assert rail._pending_host_events == []
    assert captured["requires_approval"] is False


@pytest.mark.asyncio
async def test_request_user_evolution_returns_empty_result_when_no_records(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    rail._handle_evolution_from_signals_with_result = AsyncMock(return_value=_handle_result(None))

    result = await rail.request_user_evolution("skill-a", "nothing useful")

    assert result.request_id is None
    assert rail._pending_host_events == []


@pytest.mark.asyncio
async def test_request_user_evolution_returns_no_records_status_when_generation_runs(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    rail._stage_evolution_from_signals = AsyncMock(return_value=_no_records_result())

    result = await rail.request_user_evolution("skill-a", "nothing useful")

    assert result.request_id is None
    assert result.status == "no_evolution_no_records"
    assert "no applied updates" in result.message
    assert rail._pending_host_events == []


@pytest.mark.asyncio
async def test_request_user_evolution_auto_approve_returns_persistence_failed_request_status(tmp_path):
    rail = _make_rail(tmp_path, auto_save=True)
    failed_request = SimpleNamespace(request_id="skill_evolve_failed")
    rail._stage_evolution_from_signals = AsyncMock(
        return_value=OnlineEvolutionResult(
            skill_name="skill-a",
            status="persistence_failed",
            request=failed_request,
            message="disk full",
        )
    )
    rail.approval_runtime.finalize_staged_evolution_request = AsyncMock(return_value=failed_request)

    result = await rail.request_user_evolution("skill-a", "save directly", auto_approve=True)

    assert result.request_id == "skill_evolve_failed"
    assert result.status == "persistence_failed"
    assert result.message == "disk full"
    rail.approval_runtime.finalize_staged_evolution_request.assert_not_awaited()
    assert rail._pending_host_events == []


@pytest.mark.asyncio
async def test_approve_record_and_reject_record_aliases(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    rail.approval_runtime.approve_pending_request = AsyncMock(
        return_value=(SimpleNamespace(skill_name="skill-a"), SimpleNamespace(pending_count=0, applied_count=1))
    )
    rail.approval_runtime.reject_pending_request = AsyncMock(
        return_value=(SimpleNamespace(skill_name="skill-a"), SimpleNamespace(rejected_count=1))
    )

    await rail.on_approve("req-approve")
    await rail.on_reject("req-reject")

    rail.approval_runtime.approve_pending_request.assert_awaited_once_with(
        "req-approve",
        rail_name="SkillEvolutionRail",
        action_name="approve_record",
    )
    rail.approval_runtime.reject_pending_request.assert_awaited_once_with(
        "req-reject",
        rail_name="SkillEvolutionRail",
        action_name="reject_record",
    )


@pytest.mark.asyncio
async def test_generate_and_emit_experience_delegates_to_request_user_evolution_with_user_query(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    signals = [_make_signal("skill-a")]
    messages = [{"role": "user", "content": "test"}]
    rail.request_user_evolution = AsyncMock(return_value=SimpleNamespace(request_id="skill_evolve_manual"))

    result = await rail.generate_and_emit_experience(
        "skill-a",
        signals,
        messages,
        user_query="improve error handling",
    )

    assert result is True
    rail.request_user_evolution.assert_awaited_once_with(
        "skill-a",
        "improve error handling",
        auto_approve=False,
    )


@pytest.mark.asyncio
async def test_generate_and_emit_experience_delegates_with_signal_excerpt_when_user_query_empty(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    signal = _make_signal("skill-a", excerpt="capture troubleshooting lesson")
    rail.request_user_evolution = AsyncMock(return_value=SimpleNamespace(request_id="skill_evolve_signal"))

    result = await rail.generate_and_emit_experience("skill-a", [signal], [])

    assert result is True
    rail.request_user_evolution.assert_awaited_once_with(
        "skill-a",
        "capture troubleshooting lesson",
        auto_approve=False,
    )


@pytest.mark.asyncio
async def test_generate_and_emit_experience_delegates_with_last_message_when_no_signal_excerpt(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    signal = _make_signal("skill-a", excerpt="")
    messages = [
        {"role": "assistant", "content": "intermediate"},
        {"role": "user", "content": "preserve this behavior"},
    ]
    rail.request_user_evolution = AsyncMock(return_value=SimpleNamespace(request_id="skill_evolve_message"))

    result = await rail.generate_and_emit_experience("skill-a", [signal], messages)

    assert result is True
    rail.request_user_evolution.assert_awaited_once_with(
        "skill-a",
        "preserve this behavior",
        auto_approve=False,
    )


@pytest.mark.asyncio
async def test_on_approve_uses_rebound_pending_snapshot_store(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a", content="rebound")
    rail._evolution_store.append_record = AsyncMock()

    request = rail._manager.stage_records(
        "skill-a",
        [record],
        requires_approval=True,
    )
    pending = rail._pending_approval_snapshots.pop(request.request_id)

    rebound_snapshots = {request.request_id: pending}
    rail._pending_approval_snapshots = rebound_snapshots
    rail._manager.bind_pending_approval_snapshots(rebound_snapshots)

    await rail.on_approve(request.request_id)

    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record)
    assert request.request_id not in rebound_snapshots


@pytest.mark.asyncio
async def test_generate_and_emit_experience_returns_false_when_no_records(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    rail.request_user_evolution = AsyncMock(return_value=SimpleNamespace(request_id=None))

    result = await rail.generate_and_emit_experience("skill-a", [], [])

    assert result is False
    rail.request_user_evolution.assert_awaited_once_with(
        "skill-a",
        "",
        auto_approve=False,
    )


@pytest.mark.asyncio
async def test_stage_evolution_from_signals_stages_explicit_request_metadata_from_preferred_user_intent(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    operator = Mock()
    operator.operator_id = "skill_experience_skill-a"
    operator.get_state = Mock(return_value={})
    operator.load_state = Mock()
    operator.refresh_state = AsyncMock()
    operator.apply_update = Mock(
        return_value=ApplyResult(
            operator_id="skill_experience_skill-a",
            target="experiences",
            applied=True,
            mode="append",
            effect="pending_change",
            value=["new-record"],
            records=["new-record"],
            change_type="skill_experience_entry",
            lifecycle_stage="local_apply_completed",
        )
    )
    rail._skill_ops["skill-a"] = operator
    rail._online_updater.bind = Mock()
    rail._online_updater.process = AsyncMock(return_value={("skill_experience_skill-a", "experiences"): ["new-record"]})
    rail._manager.stage_apply_results = Mock(return_value=SimpleNamespace(request_id="req-1"))

    passive_signal = make_evolution_signal(
        signal_type="user_correction",
        section="Troubleshooting",
        excerpt="passive",
        skill_name="skill-a",
        source="conversation",
    )
    explicit_signal = make_evolution_signal(
        signal_type="user_intent",
        section="Instructions",
        excerpt="explicit",
        skill_name="skill-a",
        source="explicit_request",
    )

    await rail._stage_evolution_from_signals(
        "skill-a",
        [passive_signal, explicit_signal],
        [{"role": "user", "content": "explicit"}],
        user_query="explicit",
        requires_approval=True,
    )

    assert rail._manager.stage_apply_results.call_args.kwargs["user_query"] == "explicit"
    assert rail._manager.stage_apply_results.call_args.kwargs["signal_type"] == "user_intent"
    assert rail._manager.stage_apply_results.call_args.kwargs["signal_source"] == "explicit_request"


@pytest.mark.asyncio
async def test_emit_generated_records_preserves_signal_metadata_in_event(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    pending_change = SimpleNamespace(payload=[], change_id="req-1")
    approval_request = SimpleNamespace(
        pending_change=pending_change,
        request_id="req-1",
        proposal=SimpleNamespace(
            record_count=0,
            signal_type="user_intent",
            signal_source="explicit_request",
        ),
    )

    await rail._emit_generated_records(None, "skill-a", approval_request)

    event = _approval_events(rail._pending_host_events)[-1]
    assert event.payload["evolution_meta"] == {
        "event_kind": "approval",
        "rail_kind": "regular",
        "skill_name": "skill-a",
        "request_id": "req-1",
        "signal_type": "user_intent",
        "source": "explicit_request",
    }


# =============================================================================
# Breaking Change Tests
# =============================================================================


def test_rewrite_skill_api_removed(tmp_path):
    rail = _make_rail(tmp_path)
    assert not hasattr(rail, "rewrite_skill")


def test_skill_rewriter_exports_removed():
    from openjiuwen.agent_evolving.optimizer import skill_call

    assert not hasattr(skill_call, "SkillRewriter")
    assert not hasattr(skill_call, "SkillRewriteResult")


# ---------------------------------------------------------------------------
# request_rebuild tests (archive-first approach)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_rebuild_archives_before_building_prompt(tmp_path):
    """request_rebuild should archive old version BEFORE building the prompt."""

    rail = _make_rail(tmp_path)

    mock_record = _make_record("test-skill", content="test experience")
    mock_record.score = 0.8

    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._evolution_store.archive_skill_body = AsyncMock(return_value="SKILL.v20260426_172600.md")
    rail._evolution_store.archive_evolutions = AsyncMock(return_value="evolutions.v20260426_172600.json")
    rail._evolution_store.clear_evolutions = AsyncMock()
    rail._evolution_store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[mock_record]))

    result = await rail.request_rebuild("test-skill", user_intent="优化技能")

    # Archive operations should be called BEFORE prompt is built
    rail._evolution_store.archive_skill_body.assert_called_once_with("test-skill")
    rail._evolution_store.archive_evolutions.assert_called_once_with("test-skill")
    rail._evolution_store.clear_evolutions.assert_called_once_with("test-skill")

    # Should return prompt (not None)
    assert result is not None
    # Prompt should contain archived indication
    assert "已归档" in result or "archived" in result.lower()
    # Prompt should contain filtered experience
    assert "test experience" in result
    # Prompt should contain skill-creator instruction
    assert "skill-creator" in result.lower()


@pytest.mark.asyncio
async def test_request_rebuild_returns_none_when_skill_not_found(tmp_path):
    """request_rebuild should return None when skill doesn't exist."""
    rail = _make_rail(tmp_path)

    rail._evolution_store.skill_exists = Mock(return_value=False)

    result = await rail.request_rebuild("nonexistent-skill")

    assert result is None
    rail._evolution_store.archive_skill_body.assert_not_called()
    rail._evolution_store.archive_evolutions.assert_not_called()


@pytest.mark.asyncio
async def test_request_rebuild_filters_low_score_records(tmp_path):
    """request_rebuild should filter out records with score below min_score."""

    rail = _make_rail(tmp_path)

    high_record = _make_record("test-skill", content="good experience")
    high_record.score = 0.8

    low_record = _make_record("test-skill", content="bad experience")
    low_record.score = 0.3

    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._evolution_store.archive_skill_body = AsyncMock(return_value="SKILL.v1.md")
    rail._evolution_store.archive_evolutions = AsyncMock(return_value="evolutions.v1.json")
    rail._evolution_store.clear_evolutions = AsyncMock()
    rail._evolution_store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[high_record, low_record]))

    result = await rail.request_rebuild("test-skill", min_score=0.5)

    assert result is not None
    # High score record should be included
    assert "good experience" in result
    # Low score record should be filtered out
    assert "bad experience" not in result
    rail._evolution_store.clear_evolutions.assert_called_once_with("test-skill")


@pytest.mark.asyncio
async def test_request_rebuild_continues_on_archive_failure(tmp_path):
    """request_rebuild should continue even if archive fails."""

    rail = _make_rail(tmp_path)

    high_record = _make_record("test-skill", content="test content")
    high_record.score = 0.7

    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._evolution_store.archive_skill_body = AsyncMock(side_effect=RuntimeError("disk full"))
    rail._evolution_store.archive_evolutions = AsyncMock(return_value=None)
    rail._evolution_store.clear_evolutions = AsyncMock()
    rail._evolution_store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[high_record]))

    result = await rail.request_rebuild("test-skill")

    # Should still return prompt even if archive failed
    assert result is not None
    rail._evolution_store.load_full_evolution_log.assert_called_once_with("test-skill")
    rail._evolution_store.clear_evolutions.assert_not_called()


# =============================================================================
# Experience sharing integration
# =============================================================================


@pytest.mark.asyncio
async def test_on_approve_runs_qc_after_approval(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail._evolution_store.append_record = AsyncMock()

    sharer = Mock()
    sharer.has_pending = Mock(return_value=True)
    sharer.flush_pending_uploads = AsyncMock()
    rail._experience_sharer = sharer

    stager = Mock()
    stager.screen_and_stage = AsyncMock()
    rail._share_stager = stager
    rail._keyword_extractor = Mock()

    request = _stage_approval_request(rail, "skill-a", [record])
    await rail._emit_generated_records(ctx, "skill-a", request)
    events = _approval_events(await rail.drain_pending_approval_events())
    request_id = events[0].payload["request_id"]

    await rail.on_approve(request_id)

    stager.screen_and_stage.assert_awaited_once_with(
        skill_name="skill-a",
        records=[record],
        messages=None,
    )
    sharer.flush_pending_uploads.assert_awaited_once_with("skill-a")


@pytest.mark.asyncio
async def test_approve_record_stages_only_approved_records_for_share(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record_1 = _make_record("skill-a", content="approved")
    record_2 = _make_record("skill-a", content="rejected")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    sharer = Mock()
    sharer.has_pending = Mock(return_value=True)
    sharer.flush_pending_uploads = AsyncMock()
    rail._experience_sharer = sharer

    stager = Mock()
    stager.screen_and_stage = AsyncMock()
    rail._share_stager = stager
    rail._keyword_extractor = Mock()

    request = _stage_approval_request(rail, "skill-a", [record_1, record_2])
    await rail._emit_generated_records(ctx, "skill-a", request)

    await rail.approve_record(request.request_id, approved_record_ids=[record_1.id])

    stager.screen_and_stage.assert_awaited_once_with(
        skill_name="skill-a",
        records=[record_1],
        messages=None,
    )
    sharer.flush_pending_uploads.assert_awaited_once_with("skill-a")


@pytest.mark.asyncio
async def test_on_reject_does_not_upload_shared_queue(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    sharer = Mock()
    sharer.has_pending = Mock(return_value=True)
    sharer.flush_pending_uploads = AsyncMock()
    sharer.discard_pending_uploads = AsyncMock()
    rail._experience_sharer = sharer
    rail._share_stager = Mock()
    rail._keyword_extractor = Mock()

    request = _stage_approval_request(rail, "skill-a", [record])
    await rail._emit_generated_records(ctx, "skill-a", request)
    request_id = _approval_events(await rail.drain_pending_approval_events())[0].payload["request_id"]

    await rail.on_reject(request_id)

    sharer.flush_pending_uploads.assert_not_awaited()
    sharer.discard_pending_uploads.assert_not_called()


@pytest.mark.asyncio
async def test_on_approve_skips_upload_for_shared_hub_records(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail._evolution_store.append_record = AsyncMock()

    stager = Mock()
    stager.screen_and_stage = AsyncMock()
    rail._experience_sharer = Mock()
    rail._share_stager = stager
    rail._keyword_extractor = Mock()

    request = rail._manager.stage_records(
        "skill-a",
        [record],
        source="experience_sharing",
        is_shared_records=True,
    )
    await rail._emit_generated_records(ctx, "skill-a", request)
    approval_event = _approval_events(await rail.drain_pending_approval_events())[0]
    request_id = approval_event.payload["request_id"]
    assert approval_event.payload["questions"][0]["header"] == "在线共享经验审批"

    await rail.on_approve(request_id)

    stager.screen_and_stage.assert_not_awaited()


@pytest.mark.asyncio
async def test_is_sharing_enabled_requires_both_sharer_and_stager(tmp_path):
    rail = _make_rail(tmp_path)
    assert rail.is_sharing_enabled is False

    rail._experience_sharer = Mock()
    rail._share_stager = Mock()
    rail._keyword_extractor = Mock()
    assert rail.is_sharing_enabled is True


# =============================================================================
# disabled_skills Tests
# =============================================================================


def test_disabled_skills_constructor_parameter(tmp_path):
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        disabled_skills=["skill-a", "skill-b"],
    )
    assert rail.disabled_skills == {"skill-a", "skill-b"}


def test_disabled_skills_from_single_string(tmp_path):
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        disabled_skills="skill-a",
    )
    assert rail.disabled_skills == {"skill-a"}


def test_disabled_skills_defaults_to_empty(tmp_path):
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
    )
    assert rail.disabled_skills == set()


@pytest.mark.asyncio
async def test_run_evolution_filters_out_disabled_skills(tmp_path):
    """run_evolution should exclude disabled skills from the evolution scope."""
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True, disabled_skills=["disabled-skill"])

    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/active-skill/SKILL.md"}]},
        {"role": "tool", "content": "Error: command failed", "name": "bash"},
    ]
    trajectory = _trajectory_with_messages(messages)

    rail._evolution_store.list_skill_names = Mock(return_value=["active-skill", "disabled-skill"])
    rail._is_regular_skill = Mock(return_value=True)
    rail._collect_messages = AsyncMock(return_value=messages)
    rail._handle_evolution_from_signals = AsyncMock()

    await rail.run_evolution(trajectory, ctx=None, snapshot={"trajectory": trajectory, "messages": messages})

    rail._handle_evolution_from_signals.assert_awaited_once()
    call_kwargs = rail._handle_evolution_from_signals.call_args
    assert call_kwargs.kwargs["skill_name"] == "active-skill"


@pytest.mark.asyncio
async def test_run_evolution_all_skills_disabled(tmp_path):
    """run_evolution should skip when all skills are disabled."""
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True, disabled_skills=["skill-a", "skill-b"])

    messages = [{"role": "user", "content": "hello"}]
    trajectory = _trajectory_with_messages(messages)

    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a", "skill-b"])
    rail._is_regular_skill = Mock(return_value=True)
    rail._collect_messages = AsyncMock(return_value=messages)
    rail._handle_evolution_from_signals = AsyncMock()

    await rail.run_evolution(trajectory, ctx=None, snapshot={"trajectory": trajectory, "messages": messages})

    rail._handle_evolution_from_signals.assert_not_awaited()
