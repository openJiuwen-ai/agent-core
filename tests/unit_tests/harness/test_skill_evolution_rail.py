# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.online.schema import (
    EvolutionCategory,
    EvolutionPatch,
    EvolutionRecord,
    EvolutionSignal,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.signal import SignalDetector
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
    rail._store = Mock()
    rail._evolver = Mock()
    return rail


def _make_record(skill_name: str, *, content: str = "经验内容") -> EvolutionRecord:
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
    rail._store.format_body_experience_text = AsyncMock(return_value="\n新增经验")

    inputs = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": r"C:\skills\invoice-parser\SKILL.md"},
        tool_msg=_DummyToolMsg("original"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=inputs, session=None)

    await rail.after_tool_call(ctx)

    assert inputs.tool_msg.content == "original\n新增经验"
    rail._store.format_body_experience_text.assert_awaited_once_with("invoice-parser")


@pytest.mark.asyncio
async def test_after_tool_call_skips_invalid_cases(tmp_path):
    rail = _make_rail(tmp_path)
    rail._store.format_body_experience_text = AsyncMock(return_value="\n新增经验")
    cases = [
        ToolCallInputs(tool_name="list_skill", tool_args={"file_path": "/a/s/SKILL.md"}, tool_msg=_DummyToolMsg("x")),
        ToolCallInputs(tool_name="read_file", tool_args={"file_path": "/a/s/README.md"}, tool_msg=_DummyToolMsg("x")),
        ToolCallInputs(tool_name="read_file", tool_args={"file_path": "/a/s/SKILL.md"}, tool_msg=None),
    ]
    for item in cases:
        ctx = AgentCallbackContext(agent=None, inputs=item, session=None)
        await rail.after_tool_call(ctx)

    rail._store.format_body_experience_text.assert_awaited_once_with("s")
    assert cases[0].tool_msg.content == "x"
    assert cases[1].tool_msg.content == "x"

    rail._store.format_body_experience_text = AsyncMock(return_value="")
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
async def test_after_invoke_returns_immediately_when_auto_scan_disabled(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=False)
    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "x"}])
    await rail.after_invoke(AgentCallbackContext(agent=None, inputs=None, session=None))
    rail._collect_parsed_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_invoke_auto_save_appends_records(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    signals = [_make_signal("skill-a")]
    records = [_make_record("skill-a"), _make_record("skill-a")]

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
    rail._store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = Mock(return_value=signals)
    rail._generate_experience_for_skill = AsyncMock(return_value=records)
    rail._store.append_record = AsyncMock()
    rail._emit_generated_records = AsyncMock()
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail.after_invoke(ctx)

    assert rail._store.append_record.await_count == 2
    rail._store.append_record.assert_any_await("skill-a", records[0])
    rail._store.append_record.assert_any_await("skill-a", records[1])
    rail._emit_generated_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_invoke_auto_save_false_emits_events(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=False)
    signals = [_make_signal("skill-a")]
    records = [_make_record("skill-a")]

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
    rail._store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = Mock(return_value=signals)
    rail._generate_experience_for_skill = AsyncMock(return_value=records)
    rail._store.append_record = AsyncMock()
    rail._emit_generated_records = AsyncMock()
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail.after_invoke(ctx)

    rail._store.append_record.assert_not_awaited()
    rail._emit_generated_records.assert_awaited_once_with(ctx, skill_name="skill-a", records=records)


@pytest.mark.asyncio
async def test_after_invoke_filters_empty_skill_name_and_swallow_exceptions(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
    rail._store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = Mock(return_value=[_make_signal(None), _make_signal("skill-a")])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._store.append_record = AsyncMock()

    await rail.after_invoke(AgentCallbackContext(agent=None, inputs=None, session=None))
    rail._generate_experience_for_skill.assert_awaited_once()
    assert rail._generate_experience_for_skill.await_args.args[0] == "skill-a"

    rail2 = _make_rail(tmp_path, auto_scan=True)
    rail2._collect_parsed_messages = AsyncMock(side_effect=RuntimeError("boom"))
    await rail2.after_invoke(AgentCallbackContext(agent=None, inputs=None, session=None))


# =============================================================================
# _detect_signals Tests
# =============================================================================


def test_detect_signals_deduplicates_with_processed_keys(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path)
    rail._store.skill_exists = Mock(return_value=True)
    signal = _make_signal("skill-a", excerpt="same-excerpt")
    monkeypatch.setattr(SignalDetector, "detect", lambda self, messages: [signal])

    first = rail._detect_signals([{"role": "user", "content": "x"}], ["skill-a"])
    second = rail._detect_signals([{"role": "user", "content": "x"}], ["skill-a"])

    assert len(first) == 1
    assert len(second) == 0
    assert ("tool_failure", "bash", "skill-a", "same-excerpt") in rail.processed_signal_keys


def test_detect_signals_clears_processed_keys_when_exceed_limit(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path)
    rail._store.skill_exists = Mock(return_value=True)
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

    rail._store.read_skill_content = AsyncMock(return_value="# skill")
    rail._store.get_pending_records = AsyncMock(side_effect=[[old_desc], [old_body]])
    rail._evolver.generate_skill_experience = AsyncMock(return_value=[new_record])

    result = await rail._generate_experience_for_skill("skill-a", [signal], [{"role": "user", "content": "x"}])

    assert result == [new_record]
    evo_ctx = rail._evolver.generate_skill_experience.await_args.args[0]
    assert evo_ctx.skill_name == "skill-a"
    assert evo_ctx.signals == [signal]
    assert evo_ctx.skill_content == "# skill"
    assert evo_ctx.existing_desc_records == [old_desc]
    assert evo_ctx.existing_body_records == [old_body]


@pytest.mark.asyncio
async def test_generate_experience_for_skill_returns_empty_on_evolver_exception(tmp_path):
    rail = _make_rail(tmp_path)
    rail._store.read_skill_content = AsyncMock(return_value="# skill")
    rail._store.get_pending_records = AsyncMock(return_value=[])
    rail._evolver.generate_skill_experience = AsyncMock(side_effect=RuntimeError("llm fail"))

    result = await rail._generate_experience_for_skill("skill-a", [_make_signal("skill-a")], [])
    assert result == []


@pytest.mark.asyncio
async def test_emit_generated_records_and_drain_pending_events(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a", content="x" * 1200)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail._emit_generated_records(ctx, skill_name="skill-a", records=[record])
    events = rail.drain_pending_approval_events()

    assert len(events) == 1
    event = events[0]
    assert event.type == "chat.ask_user_question"
    assert event.payload["request_id"].startswith("skill_evolve_approve_")
    assert event.payload["questions"][0]["header"] == "技能演进审批"
    assert event.payload["_evolution_data"]["skill_name"] == "skill-a"
    assert len(event.payload["_evolution_data"]["records"]) == 1
    assert rail.drain_pending_approval_events() == []


# =============================================================================
# _infer_primary_skill Tests
# =============================================================================


def test_infer_primary_skill_picks_most_frequent(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"arguments": "/skills/skill-a/SKILL.md"},
            {"arguments": "/skills/skill-b/SKILL.md"},
        ]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"arguments": "/skills/skill-a/SKILL.md"},
        ]},
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
async def test_after_invoke_zero_signals_creates_conversation_review(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    rail._collect_parsed_messages = AsyncMock(return_value=[
        {"role": "assistant", "content": "", "tool_calls": [
            {"arguments": "/skills/skill-a/SKILL.md"},
        ]},
        {"role": "user", "content": "hello"},
    ])
    rail._store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = Mock(return_value=[])
    rail._infer_primary_skill = Mock(return_value="skill-a")
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._store.append_record = AsyncMock()

    await rail.after_invoke(AgentCallbackContext(agent=None, inputs=None, session=None))

    rail._generate_experience_for_skill.assert_awaited_once()
    call_args = rail._generate_experience_for_skill.await_args
    assert call_args.args[0] == "skill-a"
    signals_passed = call_args.args[1]
    assert len(signals_passed) == 1
    assert signals_passed[0].signal_type == "conversation_review"


@pytest.mark.asyncio
async def test_after_invoke_zero_signals_no_primary_skill_returns(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = Mock(return_value=[])
    rail._infer_primary_skill = Mock(return_value=None)
    rail._generate_experience_for_skill = AsyncMock()

    await rail.after_invoke(AgentCallbackContext(agent=None, inputs=None, session=None))

    rail._generate_experience_for_skill.assert_not_awaited()


@pytest.mark.asyncio
async def test_after_invoke_unattributed_signals_get_fallback_skill(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    attributed = _make_signal("skill-a", excerpt="attributed")
    unattributed = _make_signal(None, excerpt="unattributed")

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = Mock(return_value=[attributed, unattributed])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._store.append_record = AsyncMock()

    await rail.after_invoke(AgentCallbackContext(agent=None, inputs=None, session=None))

    assert unattributed.skill_name == "skill-a"
    rail._generate_experience_for_skill.assert_awaited_once()
    call_args = rail._generate_experience_for_skill.await_args
    assert len(call_args.args[1]) == 2


@pytest.mark.asyncio
async def test_after_invoke_multiple_attributed_skills_no_fallback(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    sig_a = _make_signal("skill-a", excerpt="a")
    sig_b = _make_signal("skill-b", excerpt="b")
    sig_none = _make_signal(None, excerpt="none")

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._store.list_skill_names = Mock(return_value=["skill-a", "skill-b"])
    rail._detect_signals = Mock(return_value=[sig_a, sig_b, sig_none])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._store.append_record = AsyncMock()

    await rail.after_invoke(AgentCallbackContext(agent=None, inputs=None, session=None))

    assert sig_none.skill_name is None
    assert rail._generate_experience_for_skill.await_count == 2
