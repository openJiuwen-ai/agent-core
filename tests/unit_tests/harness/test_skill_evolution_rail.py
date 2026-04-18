# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.signal import (
    SignalDetector,
    EvolutionSignal,
    EvolutionCategory,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.skill_evolution_rail import (
    SkillEvolutionRail,
    _MAX_PROCESSED_SIGNAL_KEYS,
)


def _make_rail(tmp_path, *, auto_scan: bool = True, auto_save: bool = True) -> SkillEvolutionRail:
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        auto_scan=auto_scan,
        auto_save=auto_save,
    )
    rail._evolution_store = Mock()
    rail._evolver = Mock()
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


def _make_signal(skill_name: str | None, *, excerpt: str = "signal excerpt") -> EvolutionSignal:
    return EvolutionSignal(
        signal_type="tool_failure",
        evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
        section="Troubleshooting",
        excerpt=excerpt,
        tool_name="bash",
        skill_name=skill_name,
    )


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


def test_properties_and_clear_processed_signals(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail.processed_signal_keys.add(("a", "b"))
    rail.auto_scan = False
    rail.auto_save = False
    rail.clear_processed_signals()

    assert rail.auto_scan is False
    assert rail.auto_save is False
    assert rail.processed_signal_keys == set()


# =============================================================================
# after_tool_call Tests
# =============================================================================


@pytest.mark.asyncio
async def test_after_tool_call_injects_body_experience(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="\nnew experience")

    inputs = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": r"C:\skills\invoice-parser\SKILL.md"},
        tool_msg=_DummyToolMsg("original"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=inputs, session=None)

    await rail.after_tool_call(ctx)

    assert inputs.tool_msg.content == "original\nnew experience"
    rail._evolution_store.format_body_experience_text.assert_awaited_once_with("invoice-parser")


@pytest.mark.asyncio
async def test_after_tool_call_skips_invalid_cases(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="\nnew experience")
    cases = [
        ToolCallInputs(tool_name="list_skill", tool_args={"file_path": "/a/s/SKILL.md"}, tool_msg=_DummyToolMsg("x")),
        ToolCallInputs(tool_name="read_file", tool_args={"file_path": "/a/s/README.md"}, tool_msg=_DummyToolMsg("x")),
        ToolCallInputs(tool_name="read_file", tool_args={"file_path": "/a/s/SKILL.md"}, tool_msg=None),
    ]
    for item in cases:
        ctx = AgentCallbackContext(agent=None, inputs=item, session=None)
        await rail.after_tool_call(ctx)

    rail._evolution_store.format_body_experience_text.assert_awaited_once_with("s")
    assert cases[0].tool_msg.content == "x"
    assert cases[1].tool_msg.content == "x"

    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="")
    empty_case = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": "/a/demo/SKILL.md"},
        tool_msg=_DummyToolMsg("z"),
    )
    await rail.after_tool_call(AgentCallbackContext(agent=None, inputs=empty_case, session=None))
    assert empty_case.tool_msg.content == "z"


# =============================================================================
# after_invoke Tests
# =============================================================================


@pytest.mark.asyncio
async def test_run_evolution_returns_immediately_when_auto_scan_disabled(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=False)
    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "x"}])
    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))
    rail._collect_parsed_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_auto_save_appends_records(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    signals = [_make_signal("skill-a")]
    records = [_make_record("skill-a"), _make_record("skill-a")]

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = Mock(return_value=signals)
    rail._generate_experience_for_skill = AsyncMock(return_value=records)
    rail._evolution_store.append_record = AsyncMock()
    rail._emit_generated_records = AsyncMock()
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail.run_evolution(None, ctx)

    assert rail._evolution_store.append_record.await_count == 2
    rail._evolution_store.append_record.assert_any_await("skill-a", records[0])
    rail._evolution_store.append_record.assert_any_await("skill-a", records[1])
    rail._emit_generated_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_auto_save_false_emits_events(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=False)
    signals = [_make_signal("skill-a")]
    parsed_messages = [{"role": "user", "content": "hello"}]

    rail._collect_parsed_messages = AsyncMock(return_value=parsed_messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = Mock(return_value=signals)
    rail._generate_experience_via_optimizer = AsyncMock(return_value=True)
    rail._emit_generated_records = AsyncMock()
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail.run_evolution(None, ctx)

    rail._generate_experience_via_optimizer.assert_awaited_once_with("skill-a", signals, parsed_messages)
    rail._emit_generated_records.assert_awaited_once_with(ctx, "skill-a")


@pytest.mark.asyncio
async def test_run_evolution_filters_empty_skill_name_and_swallow_exceptions(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = Mock(return_value=[_make_signal(None), _make_signal("skill-a")])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))
    rail._generate_experience_for_skill.assert_awaited_once()
    assert rail._generate_experience_for_skill.await_args.args[0] == "skill-a"

    rail2 = _make_rail(tmp_path, auto_scan=True)
    rail2._collect_parsed_messages = AsyncMock(side_effect=RuntimeError("boom"))
    await rail2.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))


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
# _collect_parsed_messages Tests
# =============================================================================


@pytest.mark.asyncio
async def test_collect_parsed_messages_prefers_ctx_context(tmp_path):
    rail = _make_rail(tmp_path)
    ctx = AgentCallbackContext(
        agent=None,
        inputs=None,
        session=None,
        context=_MsgContext(messages=[{"role": "user", "content": "q"}]),
    )
    parsed = await rail._collect_parsed_messages(ctx)
    assert parsed == [{"role": "user", "content": "q"}]


@pytest.mark.asyncio
async def test_collect_parsed_messages_fallback_to_inner_context_engine(tmp_path):
    rail = _make_rail(tmp_path)
    inner_context = _MsgContext(messages=[{"role": "assistant", "content": "ok"}])
    context_engine = SimpleNamespace(create_context=AsyncMock(return_value=inner_context))
    inner_agent = SimpleNamespace(context_engine=context_engine)
    agent = SimpleNamespace(_react_agent=inner_agent)

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=None,
        session=object(),
        context=_MsgContext(messages=[]),
    )
    parsed = await rail._collect_parsed_messages(ctx)
    assert parsed == [{"role": "assistant", "content": "ok"}]
    context_engine.create_context.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_parsed_messages_returns_empty_on_both_paths_failure(tmp_path):
    rail = _make_rail(tmp_path)
    bad_context = _MsgContext(raise_error=True)
    bad_engine = SimpleNamespace(create_context=AsyncMock(side_effect=RuntimeError("context fail")))
    agent = SimpleNamespace(_react_agent=SimpleNamespace(context_engine=bad_engine))
    ctx = AgentCallbackContext(agent=agent, inputs=None, session=object(), context=bad_context)

    parsed = await rail._collect_parsed_messages(ctx)
    assert parsed == []


# =============================================================================
# _generate_experience_for_skill / event buffer Tests
# =============================================================================


@pytest.mark.asyncio
async def test_generate_experience_for_skill_builds_context(tmp_path):
    rail = _make_rail(tmp_path)
    signal = _make_signal("skill-a")
    old_desc = _make_record("skill-a", content="old desc")
    old_body = _make_record("skill-a", content="old body")
    new_record = _make_record("skill-a", content="new")

    rail._evolution_store.read_skill_content = AsyncMock(return_value="# skill")
    rail._evolution_store.get_pending_records = AsyncMock(side_effect=[[old_desc], [old_body]])
    rail._evolver.generate_records = AsyncMock(return_value=[new_record])

    result = await rail._generate_experience_for_skill("skill-a", [signal], [{"role": "user", "content": "x"}])

    assert result == [new_record]
    evo_ctx = rail._evolver.generate_records.await_args.args[0]
    assert evo_ctx.skill_name == "skill-a"
    assert evo_ctx.signals == [signal]
    assert evo_ctx.skill_content == "# skill"
    assert evo_ctx.existing_desc_records == [old_desc]
    assert evo_ctx.existing_body_records == [old_body]


@pytest.mark.asyncio
async def test_generate_experience_for_skill_returns_empty_on_evolver_exception(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.read_skill_content = AsyncMock(return_value="# skill")
    rail._evolution_store.get_pending_records = AsyncMock(return_value=[])
    rail._evolver.generate_records = AsyncMock(side_effect=RuntimeError("llm fail"))

    result = await rail._generate_experience_for_skill("skill-a", [_make_signal("skill-a")], [])
    assert result == []


@pytest.mark.asyncio
async def test_emit_generated_records_and_drain_pending_events(tmp_path):
    from openjiuwen.core.operator.skill_call import SkillCallOperator

    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a", content="x" * 1200)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    # Set up SkillCallOperator with staged records as the new path does
    skill_op = SkillCallOperator("skill-a")
    skill_op._staged_records = [record]
    rail._skill_ops["skill-a"] = skill_op

    await rail._emit_generated_records(ctx, "skill-a")
    events = rail.drain_pending_approval_events()

    assert len(events) == 1
    event = events[0]
    assert event.type == "chat.ask_user_question"
    request_id = event.payload["request_id"]
    # request_id uses the "skill_evolve_" prefix (PendingChange.change_id)
    assert request_id.startswith("skill_evolve_")
    assert event.payload["questions"][0]["header"] == "技能演进审批"
    assert event.payload["_evolution_meta"]["skill_name"] == "skill-a"
    assert event.payload["_evolution_meta"]["request_id"] == request_id
    # Records should have been snapshotted (moved out of staged queue)
    assert skill_op._staged_records == []
    assert request_id in rail._pending_approval_snapshots
    assert rail.drain_pending_approval_events() == []


@pytest.mark.asyncio
async def test_on_approve_flushes_snapshot_records(tmp_path):
    from openjiuwen.core.operator.skill_call import SkillCallOperator

    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    skill_op = SkillCallOperator("skill-a")
    skill_op._staged_records = [record]
    rail._skill_ops["skill-a"] = skill_op
    rail._evolution_store.append_record = AsyncMock()
    rail._evolution_store.solidify = AsyncMock()

    await rail._emit_generated_records(ctx, "skill-a")
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    await rail.on_approve(request_id)

    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record)
    rail._evolution_store.solidify.assert_awaited_once_with("skill-a")
    assert request_id not in rail._pending_approval_snapshots


@pytest.mark.asyncio
async def test_on_reject_discards_snapshot_records(tmp_path):
    from openjiuwen.core.operator.skill_call import SkillCallOperator

    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    skill_op = SkillCallOperator("skill-a")
    skill_op._staged_records = [record]
    rail._skill_ops["skill-a"] = skill_op
    rail._evolution_store.append_record = AsyncMock()
    rail._evolution_store.solidify = AsyncMock()

    await rail._emit_generated_records(ctx, "skill-a")
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    await rail.on_reject(request_id)

    rail._evolution_store.append_record.assert_not_awaited()
    rail._evolution_store.solidify.assert_not_awaited()
    assert request_id not in rail._pending_approval_snapshots


@pytest.mark.asyncio
async def test_on_approve_partial_failure_retains_pending_change(tmp_path):
    """If append_record fails mid-batch, PendingChange must be kept for retry."""
    from openjiuwen.core.operator.skill_call import SkillCallOperator

    rail = _make_rail(tmp_path, auto_save=False)
    record_1 = _make_record("skill-a", content="first")
    record_2 = _make_record("skill-a", content="second")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    skill_op = SkillCallOperator("skill-a")
    skill_op._staged_records = [record_1, record_2]
    rail._skill_ops["skill-a"] = skill_op
    rail._evolution_store.solidify = AsyncMock()
    # First call succeeds, second raises → partial failure
    rail._evolution_store.append_record = AsyncMock(side_effect=[None, OSError("disk full")])

    await rail._emit_generated_records(ctx, "skill-a")
    request_id = rail.drain_pending_approval_events()[0].payload["request_id"]

    await rail.on_approve(request_id)

    # Only first record was written; second is still pending
    assert rail._evolution_store.append_record.await_count == 2
    rail._evolution_store.solidify.assert_not_awaited()
    # PendingChange must be retained so host can retry
    assert request_id in rail._pending_approval_snapshots
    pending = rail._pending_approval_snapshots[request_id]
    assert len(pending.payload) == 1  # one record still remaining

    # Host retries: now second record succeeds
    rail._evolution_store.append_record = AsyncMock()
    await rail.on_approve(request_id)
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record_2)
    rail._evolution_store.solidify.assert_awaited_once_with("skill-a")
    assert request_id not in rail._pending_approval_snapshots


@pytest.mark.asyncio
async def test_on_approve_solidify_failure_retains_pending_change(tmp_path):
    """If solidify() fails after flush succeeds, PendingChange must be kept for retry."""
    from openjiuwen.core.operator.skill_call import SkillCallOperator

    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a", content="experience")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    skill_op = SkillCallOperator("skill-a")
    skill_op._staged_records = [record]
    rail._skill_ops["skill-a"] = skill_op
    rail._evolution_store.append_record = AsyncMock()
    rail._evolution_store.solidify = AsyncMock(side_effect=OSError("permission denied"))

    await rail._emit_generated_records(ctx, "skill-a")
    request_id = rail.drain_pending_approval_events()[0].payload["request_id"]

    await rail.on_approve(request_id)

    # append_record succeeded; solidify raised
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record)
    rail._evolution_store.solidify.assert_awaited_once_with("skill-a")
    # Snapshot must be preserved so host can retry on_approve
    assert request_id in rail._pending_approval_snapshots

    # Host retries: flush is a no-op (payload empty), solidify succeeds
    rail._evolution_store.solidify = AsyncMock()
    await rail.on_approve(request_id)
    rail._evolution_store.solidify.assert_awaited_once_with("skill-a")
    assert request_id not in rail._pending_approval_snapshots


@pytest.mark.asyncio
async def test_concurrent_approval_batches_are_independent(tmp_path):
    """Two approval prompts for the same skill must operate on disjoint record batches."""
    from openjiuwen.core.operator.skill_call import SkillCallOperator

    rail = _make_rail(tmp_path, auto_save=False)
    record_a = _make_record("skill-a", content="batch-1")
    record_b = _make_record("skill-a", content="batch-2")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    skill_op = SkillCallOperator("skill-a")
    rail._skill_ops["skill-a"] = skill_op
    rail._evolution_store.append_record = AsyncMock()
    rail._evolution_store.solidify = AsyncMock()

    # First batch: stage record_a and emit prompt
    skill_op._staged_records = [record_a]
    await rail._emit_generated_records(ctx, "skill-a")

    # Second batch: stage record_b and emit another prompt (same skill)
    skill_op._staged_records = [record_b]
    await rail._emit_generated_records(ctx, "skill-a")

    events = rail.drain_pending_approval_events()
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


# =============================================================================
# Zero-signal fallback & unattributed signal tests
# =============================================================================


@pytest.mark.asyncio
async def test_run_evolution_zero_signals_creates_conversation_review(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    rail._collect_parsed_messages = AsyncMock(
        return_value=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"arguments": "/skills/skill-a/SKILL.md"},
                ],
            },
            {"role": "user", "content": "hello"},
        ]
    )
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = Mock(return_value=[])
    rail._infer_primary_skill = Mock(return_value="skill-a")
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))

    rail._generate_experience_for_skill.assert_awaited_once()
    call_args = rail._generate_experience_for_skill.await_args
    assert call_args.args[0] == "skill-a"
    signals_passed = call_args.args[1]
    assert len(signals_passed) == 1
    assert signals_passed[0].signal_type == "conversation_review"


@pytest.mark.asyncio
async def test_run_evolution_zero_signals_no_primary_skill_returns(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = Mock(return_value=[])
    rail._infer_primary_skill = Mock(return_value=None)
    rail._generate_experience_for_skill = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))

    rail._generate_experience_for_skill.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_unattributed_signals_get_fallback_skill(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    attributed = _make_signal("skill-a", excerpt="attributed")
    unattributed = _make_signal(None, excerpt="unattributed")

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = Mock(return_value=[attributed, unattributed])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))

    assert unattributed.skill_name == "skill-a"
    rail._generate_experience_for_skill.assert_awaited_once()
    call_args = rail._generate_experience_for_skill.await_args
    assert len(call_args.args[1]) == 2


@pytest.mark.asyncio
async def test_run_evolution_multiple_attributed_skills_no_fallback(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    sig_a = _make_signal("skill-a", excerpt="a")
    sig_b = _make_signal("skill-b", excerpt="b")
    sig_none = _make_signal(None, excerpt="none")

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a", "skill-b"])
    rail._detect_signals = Mock(return_value=[sig_a, sig_b, sig_none])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))

    assert sig_none.skill_name is None
    assert rail._generate_experience_for_skill.await_count == 2


# =============================================================================
# _should_propose_new_skill Tests
# =============================================================================


@pytest.mark.asyncio
async def test_should_propose_new_skill_meets_thresholds(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "read_file", "arguments": ""},
                {"name": "write_file", "arguments": ""},
                {"name": "run_bash", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "write_file", "arguments": ""},
            ],
        },
    ]
    result = await rail._should_propose_new_skill(messages)
    assert result is True


@pytest.mark.asyncio
async def test_should_propose_new_skill_too_few_tool_calls(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "read_file", "arguments": ""},
                {"name": "write_file", "arguments": ""},
            ],
        },
    ]
    result = await rail._should_propose_new_skill(messages)
    assert result is False


@pytest.mark.asyncio
async def test_should_propose_new_skill_insufficient_diversity(tmp_path):
    # 5 tool calls but all the same tool
    rail = _make_rail(tmp_path)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
            ],
        },
    ]
    # default diversity threshold is 2, only 1 unique tool → False
    result = await rail._should_propose_new_skill(messages)
    assert result is False


@pytest.mark.asyncio
async def test_should_propose_new_skill_no_tool_calls(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [{"role": "user", "content": "no tools used"}]
    result = await rail._should_propose_new_skill(messages)
    assert result is False


# =============================================================================
# _check_skill_overlap Tests
# =============================================================================


@pytest.mark.asyncio
async def test_check_skill_overlap_detects_containment(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.list_skill_names = Mock(return_value=["data-analysis", "report-gen"])
    # "data" is contained in "data-analysis"
    result = await rail._check_skill_overlap("data", "analyze data")
    assert result is True


@pytest.mark.asyncio
async def test_check_skill_overlap_no_overlap(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.list_skill_names = Mock(return_value=["data-analysis", "report-gen"])
    result = await rail._check_skill_overlap("code-review", "review source code")
    assert result is False


@pytest.mark.asyncio
async def test_check_skill_overlap_empty_name(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.list_skill_names = Mock(return_value=["data-analysis"])
    # Empty proposal name is contained in "data-analysis" as empty string
    result = await rail._check_skill_overlap("", "some description")
    assert result is True


@pytest.mark.asyncio
async def test_check_skill_overlap_no_existing_skills(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.list_skill_names = Mock(return_value=[])
    result = await rail._check_skill_overlap("brand-new-skill", "something new")
    assert result is False


# =============================================================================
# _emit_new_skill_approval Tests
# =============================================================================


@pytest.mark.asyncio
async def test_emit_new_skill_approval_buffers_event(tmp_path):
    rail = _make_rail(tmp_path)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {
        "name": "my-skill",
        "description": "Does something",
        "body": "## Instructions\nDo stuff",
        "reason": "Useful pattern",
    }
    await rail._emit_new_skill_approval(ctx, proposal)

    events = rail.drain_pending_approval_events()
    assert len(events) == 1
    event = events[0]
    assert event.type == "chat.ask_user_question"
    payload = event.payload
    assert payload["_new_skill_data"]["name"] == "my-skill"
    request_id = payload["request_id"]
    # UUID-based prefix for skill creation
    assert request_id.startswith("skill_create_")
    assert request_id in rail._pending_skill_proposals


@pytest.mark.asyncio
async def test_emit_new_skill_approval_unique_ids(tmp_path):
    """Two concurrent proposals must not share the same request_id."""
    rail = _make_rail(tmp_path)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal_a = {"name": "skill-a", "description": "A", "body": "", "reason": ""}
    proposal_b = {"name": "skill-b", "description": "B", "body": "", "reason": ""}

    await rail._emit_new_skill_approval(ctx, proposal_a)
    await rail._emit_new_skill_approval(ctx, proposal_b)

    events = rail.drain_pending_approval_events()
    ids = {e.payload["request_id"] for e in events}
    assert len(ids) == 2  # must be distinct


# =============================================================================
# on_approve_new_skill / on_reject_new_skill Tests
# =============================================================================


@pytest.mark.asyncio
async def test_on_approve_new_skill_creates_skill(tmp_path):
    rail = _make_rail(tmp_path)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {"name": "new-skill", "description": "desc", "body": "body", "reason": "reason"}

    await rail._emit_new_skill_approval(ctx, proposal)
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    rail._evolution_store.create_skill = AsyncMock(return_value=tmp_path / "new-skill")

    result = await rail.on_approve_new_skill(request_id)

    rail._evolution_store.create_skill.assert_awaited_once_with(
        name="new-skill",
        description="desc",
        body="body",
    )
    assert result == "new-skill"
    # Proposal should be removed
    assert request_id not in rail._pending_skill_proposals


@pytest.mark.asyncio
async def test_on_approve_new_skill_unknown_request_id(tmp_path):
    rail = _make_rail(tmp_path)
    result = await rail.on_approve_new_skill("ns_nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_on_approve_new_skill_store_failure_returns_none(tmp_path):
    rail = _make_rail(tmp_path)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {"name": "fail-skill", "description": "d", "body": "b", "reason": "r"}

    await rail._emit_new_skill_approval(ctx, proposal)
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    rail._evolution_store.create_skill = AsyncMock(return_value=None)
    result = await rail.on_approve_new_skill(request_id)
    assert result is None


@pytest.mark.asyncio
async def test_on_approve_new_skill_exception_returns_none(tmp_path):
    rail = _make_rail(tmp_path)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {"name": "exc-skill", "description": "d", "body": "b", "reason": "r"}

    await rail._emit_new_skill_approval(ctx, proposal)
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    rail._evolution_store.create_skill = AsyncMock(side_effect=OSError("disk full"))
    result = await rail.on_approve_new_skill(request_id)
    assert result is None


@pytest.mark.asyncio
async def test_on_reject_new_skill_discards_proposal(tmp_path):
    rail = _make_rail(tmp_path)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {"name": "rej-skill", "description": "d", "body": "b", "reason": "r"}

    await rail._emit_new_skill_approval(ctx, proposal)
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    await rail.on_reject_new_skill(request_id)
    assert request_id not in rail._pending_skill_proposals


@pytest.mark.asyncio
async def test_on_reject_new_skill_unknown_id_is_noop(tmp_path):
    rail = _make_rail(tmp_path)
    await rail.on_reject_new_skill("ns_does_not_exist")
    # No exception should be raised


# =============================================================================
# Constructor Validation Tests (Issue #4)
# =============================================================================


def test_init_invalid_eval_interval_raises():
    with pytest.raises(ValueError, match="eval_interval"):
        SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="m", eval_interval=0)


def test_init_invalid_tool_threshold_raises():
    with pytest.raises(ValueError, match="new_skill_tool_threshold"):
        SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="m", new_skill_tool_threshold=0)


def test_init_invalid_tool_diversity_raises():
    with pytest.raises(ValueError, match="new_skill_tool_diversity"):
        SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="m", new_skill_tool_diversity=-1)


def test_init_valid_params_no_error():
    rail = SkillEvolutionRail(
        skills_dir="skills",
        llm=Mock(),
        model="m",
        eval_interval=3,
        new_skill_tool_threshold=4,
        new_skill_tool_diversity=2,
    )
    assert rail._eval_interval == 3
    assert rail._new_skill_tool_threshold == 4
    assert rail._new_skill_tool_diversity == 2


# =============================================================================
# Session-level State Isolation Tests
# =============================================================================


def test_session_presented_records_isolated_per_session(tmp_path):
    from types import SimpleNamespace

    rail = _make_rail(tmp_path)
    session_a = SimpleNamespace()
    session_b = SimpleNamespace()

    # Set different values for different sessions
    rail._set_session_presented_records(session_a, [("skill-a", _make_record("skill-a"), "snippet-a")])
    rail._set_session_presented_records(session_b, [("skill-b", _make_record("skill-b"), "snippet-b")])

    records_a = rail._get_session_presented_records(session_a)
    records_b = rail._get_session_presented_records(session_b)

    assert len(records_a) == 1
    assert records_a[0][0] == "skill-a"
    assert records_a[0][2] == "snippet-a"
    assert len(records_b) == 1
    assert records_b[0][0] == "skill-b"
    assert records_b[0][2] == "snippet-b"


def test_session_eval_counter_isolated_per_session(tmp_path):
    from types import SimpleNamespace

    rail = _make_rail(tmp_path)
    session_a = SimpleNamespace()
    session_b = SimpleNamespace()

    rail._set_session_eval_counter(session_a, 3)
    rail._set_session_eval_counter(session_b, 7)

    assert rail._get_session_eval_counter(session_a) == 3
    assert rail._get_session_eval_counter(session_b) == 7


def test_session_helpers_with_none_session(tmp_path):
    rail = _make_rail(tmp_path)
    assert rail._get_session_presented_records(None) == []
    assert rail._get_session_eval_counter(None) == 0
    # Setting with None session must not raise
    rail._set_session_presented_records(None, [])
    rail._set_session_eval_counter(None, 5)


# =============================================================================
# Fix #1: Path B reachable when no skill is inferred
# =============================================================================


@pytest.mark.asyncio
async def test_run_evolution_path_b_reached_when_no_primary_skill(tmp_path):
    """When no signals and no primary skill, Path B should still be evaluated."""
    rail = _make_rail(tmp_path)
    rail._auto_scan = True
    rail._new_skill_detection = True

    parsed_messages = [
        {
            "role": "user",
            "content": "do something",
            "tool_calls": [
                {"name": "bash", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "write_file", "arguments": ""},
                {"name": "grep", "arguments": ""},
                {"name": "ls", "arguments": ""},
                {"name": "python", "arguments": ""},
            ],
        },
    ]
    rail._evolution_store.list_skill_names = Mock(return_value=["existing_skill"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    # No SKILL.md reads → _infer_primary_skill returns None
    # Enough tool calls to pass _should_propose_new_skill threshold

    should_propose_called = []

    async def fake_should_propose(msgs):
        should_propose_called.append(True)
        return False  # Don't actually emit; just confirm Path B was entered

    rail._should_propose_new_skill = fake_should_propose
    rail._collect_parsed_messages = AsyncMock(return_value=parsed_messages)
    rail._trigger_async_evaluation = AsyncMock()

    from openjiuwen.agent_evolving.trajectory import Trajectory

    traj = Mock(spec=Trajectory)
    ctx = Mock()
    ctx.session = None

    await rail.run_evolution(traj, ctx)

    assert should_propose_called, "Path B (_should_propose_new_skill) must be reached when no primary skill"


@pytest.mark.asyncio
async def test_run_evolution_path_b_not_reached_when_path_a_handled(tmp_path):
    """Path B should NOT run when Path A already handled at least one skill."""
    rail = _make_rail(tmp_path)
    rail._auto_scan = True
    rail._new_skill_detection = True
    rail._auto_save = True

    parsed_messages = [
        {"role": "tool", "content": "read /skills/my_skill/SKILL.md", "tool_calls": []},
    ]
    # Arrange: detect a signal attributed to an existing skill
    from openjiuwen.agent_evolving.signal import EvolutionSignal, EvolutionCategory

    dummy_signal = EvolutionSignal(
        signal_type="execution_failure",
        evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
        section="",
        excerpt="err",
        skill_name="my_skill",
    )

    rail._evolution_store.list_skill_names = Mock(return_value=["my_skill"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._collect_parsed_messages = AsyncMock(return_value=parsed_messages)
    rail._detect_signals = Mock(return_value=[dummy_signal])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._trigger_async_evaluation = AsyncMock()

    path_b_called = []

    async def fake_should_propose(msgs):
        path_b_called.append(True)
        return False

    rail._should_propose_new_skill = fake_should_propose

    from openjiuwen.agent_evolving.trajectory import Trajectory

    traj = Mock(spec=Trajectory)
    ctx = Mock()
    ctx.session = None

    await rail.run_evolution(traj, ctx)

    assert not path_b_called, "Path B must not run when Path A already handled signals"


# =============================================================================
# Fix #2: Presented records carry per-presentation snippet
# =============================================================================


def test_session_presented_records_store_snippet(tmp_path):
    """Each entry stored in session must include the presentation-time snippet."""
    rail = _make_rail(tmp_path)
    session = SimpleNamespace()

    record = _make_record("sk")
    rail._set_session_presented_records(session, [("sk", record, "my_snippet")])

    entries = rail._get_session_presented_records(session)
    assert len(entries) == 1
    skill_name, stored_record, snippet = entries[0]
    assert skill_name == "sk"
    assert stored_record is record
    assert snippet == "my_snippet"


@pytest.mark.asyncio
async def test_trigger_async_evaluation_uses_per_record_snippet(tmp_path):
    """Scorer must be called with the snippet bound to each record, not current messages."""
    rail = _make_rail(tmp_path)
    rail._eval_interval = 1

    record_a = _make_record("skill_a")
    record_b = _make_record("skill_b")

    session = SimpleNamespace()
    # Two records from different conversations with different snippets
    rail._set_session_presented_records(
        session,
        [
            ("skill_a", record_a, "snippet_from_turn_1"),
            ("skill_b", record_b, "snippet_from_turn_2"),
        ],
    )
    rail._set_session_eval_counter(session, 0)

    captured_snippets: list[str] = []

    async def fake_evaluate(snippet, records):
        captured_snippets.append(snippet)
        return []

    rail._scorer = Mock()
    rail._scorer.evaluate = fake_evaluate

    ctx = Mock()
    ctx.session = session

    # Pass a completely different current-round messages list
    await rail._trigger_async_evaluation(ctx, [{"role": "user", "content": "new unrelated message"}])

    # Must have evaluated with the stored snippets, not the current messages
    assert "snippet_from_turn_1" in captured_snippets
    assert "snippet_from_turn_2" in captured_snippets
    assert not any("new unrelated" in s for s in captured_snippets)


# =============================================================================
# Fix #3: Only BODY records are tracked as presented
# =============================================================================


@pytest.mark.asyncio
async def test_track_presented_records_only_body_records(tmp_path):
    """_track_presented_records must only update BODY records, not DESCRIPTION records."""
    rail = _make_rail(tmp_path)

    body_record = _make_record("sk")
    body_record.target = EvolutionTarget.BODY
    body_record.score = 0.8

    desc_record = _make_record("sk", content="desc exp")
    desc_record.target = EvolutionTarget.DESCRIPTION
    desc_record.score = 0.9  # Higher score but wrong type

    rail._evolution_store.get_records_by_score = AsyncMock(return_value=[desc_record, body_record])
    rail._evolution_store.update_record_scores = AsyncMock()

    session = SimpleNamespace()
    ctx = Mock()
    ctx.session = session

    await rail._track_presented_records(ctx, "sk", "some_snippet")

    # update_record_scores must only contain the body record id
    call_args = rail._evolution_store.update_record_scores.call_args
    updates: dict = call_args[0][1]
    assert body_record.id in updates
    assert desc_record.id not in updates

    # Session must only contain the body record
    entries = rail._get_session_presented_records(session)
    assert len(entries) == 1
    assert entries[0][1].id == body_record.id


@pytest.mark.asyncio
async def test_track_presented_records_skips_when_no_body_records(tmp_path):
    """_track_presented_records must be a no-op when there are no BODY records."""
    rail = _make_rail(tmp_path)

    desc_record = _make_record("sk")
    desc_record.target = EvolutionTarget.DESCRIPTION
    desc_record.score = 0.9

    rail._evolution_store.get_records_by_score = AsyncMock(return_value=[desc_record])
    rail._evolution_store.update_record_scores = AsyncMock()

    session = SimpleNamespace()
    ctx = Mock()
    ctx.session = session

    await rail._track_presented_records(ctx, "sk", "snip")

    rail._evolution_store.update_record_scores.assert_not_called()
    assert rail._get_session_presented_records(session) == []


@pytest.mark.asyncio
async def test_after_tool_call_no_track_when_no_body_text(tmp_path):
    """after_tool_call must not call _track_presented_records when body injection is empty."""
    rail = _make_rail(tmp_path, auto_scan=True)

    # Simulate reading a SKILL.md with no body experiences
    tool_inputs = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": "/workspace/skills/my_skill/SKILL.md"},
    )
    ctx = Mock()
    ctx.inputs = tool_inputs
    ctx.session = None

    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="")
    track_called = []

    async def fake_track(ctx_, skill, snippet):
        track_called.append(skill)

    rail._track_presented_records = fake_track

    await rail.after_tool_call(ctx)

    assert not track_called, "_track_presented_records must not be called when no body text was injected"


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
