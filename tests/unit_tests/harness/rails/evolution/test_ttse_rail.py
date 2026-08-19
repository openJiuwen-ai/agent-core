# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the TTSE rail (Two-Track Self-Evolution).

The frozen TTSE algorithm is exercised end-to-end with a scripted LLM:
FACT/TIP induction, the fail path (blame -> retire -> synthesize -> induce),
top-K injection wiring, store dedup/persistence, success detection, and the
configure/unconfigure API.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

import pytest

from openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer import (
    GENERATE_RECORDS_LLM_POLICY,
)
from openjiuwen.agent_evolving.signal import detect_tool_error_signals
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint
from openjiuwen.harness.rails.evolution.ttse import (
    TTSEConfig,
    TTSERail,
    TTSERecordStore,
    SignalBasedSuccessDetector,
    TrajectoryErrorSuccessDetector,
    configure_ttse_evolution,
    unconfigure_ttse_evolution,
)
from openjiuwen.harness.rails.evolution.ttse.induction import (
    blame,
    induce,
    induce_batch,
    parse_reason,
    parse_rules,
    parse_synthesis,
    parse_verdict,
    synthesize,
)

_POLICY = GENERATE_RECORDS_LLM_POLICY


class ScriptedLLM:
    """LLM whose ``invoke`` dispatches on the prompt via a handler callable."""

    def __init__(self, handler: Callable[[str], object]):
        self.handler = handler
        self.calls: list[str] = []

    async def invoke(self, *, model, messages, temperature=None, timeout=None, **kwargs):
        prompt = messages[0]["content"] if messages else ""
        self.calls.append(prompt)
        return self.handler(prompt)


def _make_rail(tmp_path, llm, *, cfg=None) -> TTSERail:
    return TTSERail(
        llm=llm,
        model="dummy-model",
        ttse_config=cfg or TTSEConfig(store_path=str(tmp_path / "bank.json")),
    )


# ----------------------------------------------------------------------
# Parsers (frozen TTSE logic)
# ----------------------------------------------------------------------


def test_parse_rules_classifies_fact_and_tip():
    facts, tips = parse_rules(
        "[FACT] the grader checks column names case-sensitively\n"
        "[TIP] When logs are large: use grep to scan before reading"
    )
    assert facts == ["the grader checks column names case-sensitively"]
    assert tips == ["When logs are large: use grep to scan before reading"]


def test_parse_rules_none_and_empty():
    assert parse_rules("NONE") == ([], [])
    assert parse_rules("") == ([], [])
    assert parse_rules("[TIP] NONE") == ([], [])


def test_parse_verdict_validates_range():
    assert parse_verdict("VERDICT: 2\nREASON: x", 3) == 2
    assert parse_verdict("VERDICT: NONE", 3) is None
    assert parse_verdict("VERDICT: 5", 3) is None  # out of range -> None
    assert parse_verdict("", 3) is None


def test_parse_reason_and_synthesis():
    assert parse_reason("VERDICT: 1\nREASON: it misled the agent") == "it misled the agent"
    assert parse_reason("nothing here") == ""
    assert parse_synthesis("[TIP] When a: use grep to b") == "When a: use grep to b"
    assert parse_synthesis("NONE") is None
    assert parse_synthesis("") is None


# ----------------------------------------------------------------------
# Induction primitives
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_induce_parses_rules():
    llm = ScriptedLLM(lambda p: "[FACT] env fact\n[TIP] When x: use grep to y" if "extracting" in p else "NONE")
    facts, tips = await induce(
        llm=llm,
        model="m",
        policy=_POLICY,
        task_prompt="t",
        traj_text="tr",
        capabilities="- grep",
        existing_facts=[],
        existing_tips=[],
        outcome="success",
    )
    assert facts == ["env fact"]
    assert tips == ["When x: use grep to y"]


@pytest.mark.asyncio
async def test_blame_returns_verdict_and_reason():
    llm = ScriptedLLM(lambda p: "VERDICT: 1\nREASON: rule 1 misled")
    idx, reason = await blame(
        llm=llm,
        model="m",
        policy=_POLICY,
        task_prompt="t",
        traj_text="tr",
        rules_numbered="1. [FACT] a\n2. [TIP] b",
        n_rules=2,
    )
    assert idx == 1
    assert reason == "rule 1 misled"


@pytest.mark.asyncio
async def test_blame_empty_rules_short_circuits():
    idx, reason = await blame(
        llm=ScriptedLLM(lambda p: "VERDICT: 1"),
        model="m",
        policy=_POLICY,
        task_prompt="t",
        traj_text="tr",
        rules_numbered="",
        n_rules=0,
    )
    assert idx is None
    assert "no active rules" in reason


@pytest.mark.asyncio
async def test_synthesize_parses_tip_or_none():
    assert (
        await synthesize(
            llm=ScriptedLLM(lambda p: "[TIP] When a: use grep to b"),
            model="m",
            policy=_POLICY,
            rules_numbered="1. [FACT] a\n2. [TIP] b",
            capabilities="- grep",
        )
        == "When a: use grep to b"
    )
    assert (
        await synthesize(
            llm=ScriptedLLM(lambda p: "NONE"),
            model="m",
            policy=_POLICY,
            rules_numbered="1. [FACT] a",
            capabilities="- grep",
        )
        is None
    )


# ----------------------------------------------------------------------
# Store: dedup, retire, persistence
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_substring_dedup_and_retire(tmp_path):
    store = TTSERecordStore(TTSEConfig(store_path=str(tmp_path / "b.json")))
    assert await store.add_fact("the csv grader is case-sensitive") is True
    assert await store.add_fact("the csv grader is case-sensitive") is False  # merged
    assert await store.add_fact("a totally different fact") is True
    assert store.stats()["facts"] == 2
    removed = await store.retire("the csv grader is case-sensitive", "fact", "blamed", "t1")
    assert removed == 1
    assert store.stats()["facts"] == 1
    assert store.stats()["retired"] == 1


@pytest.mark.asyncio
async def test_store_persistence_roundtrip(tmp_path):
    path = str(tmp_path / "bank.json")
    cfg = TTSEConfig(store_path=path)
    s1 = TTSERecordStore(cfg)
    await s1.add_fact("persisted fact")
    await s1.add_tip("persisted tip")
    s2 = TTSERecordStore(cfg)  # reload from disk
    assert s2.facts_texts() == ["persisted fact"]
    assert s2.tips_texts() == ["persisted tip"]


# ----------------------------------------------------------------------
# Rail: success path (induce only) and fail path (blame/retire/synth/induce)
# ----------------------------------------------------------------------


def test_rail_inherits_evolution_rail_and_triggers_when_enabled(tmp_path):
    rail = _make_rail(tmp_path, ScriptedLLM(lambda p: "NONE"))
    assert isinstance(rail, EvolutionRail)
    assert rail._allow_evolution_trigger(EvolutionTriggerPoint.AFTER_INVOKE, ctx=None) is True
    rail._ttse_config.evolve_enabled = False
    assert rail._allow_evolution_trigger(EvolutionTriggerPoint.AFTER_INVOKE, ctx=None) is False


@pytest.mark.asyncio
async def test_rail_success_path_induces_without_blame(tmp_path):
    llm = ScriptedLLM(lambda p: "[FACT] success fact" if "extracting" in p else "NONE")
    rail = _make_rail(tmp_path, llm)
    snap = {
        "messages": [{"role": "user", "content": "do task"}, {"role": "assistant", "content": "done"}],
        "ttse_capabilities": "- grep",
        "ttse_task_query": "do task",
    }
    await rail._run_ttse_induction(None, ctx=None, snapshot=snap)
    assert rail._ttse_store.facts_texts() == ["success fact"]
    assert rail._ttse_store.retired == []
    assert len(llm.calls) == 1  # induce only


@pytest.mark.asyncio
async def test_rail_fail_path_blame_retire_synthesize_induce(tmp_path):
    def handler(p: str) -> str:
        if "diagnosing" in p:
            return "VERDICT: 1\nREASON: rule 1 misled the agent"
        if "review a rule bank" in p:
            return "[TIP] When logs are large: use grep to scan before reading"
        if "extracting" in p:
            return "[FACT] lesson fact"
        return "NONE"

    llm = ScriptedLLM(handler)
    rail = _make_rail(tmp_path, llm)
    await rail._ttse_store.add_fact("F1 bad fact")
    await rail._ttse_store.add_fact("F2 keeper fact")
    await rail._ttse_store.add_tip("T1 keeper tip")  # so >= 2 rules remain after retire
    snap = {
        "messages": [{"role": "user", "content": "q"}],
        "ttse_capabilities": "- grep",
        "ttse_task_query": "q",
        "ttse_score": 0.0,  # force FAIL
    }
    await rail._run_ttse_induction(None, ctx=None, snapshot=snap)

    assert [r["text"] for r in rail._ttse_store.retired] == ["F1 bad fact"]
    assert "F1 bad fact" not in rail._ttse_store.facts_texts()
    assert "lesson fact" in rail._ttse_store.facts_texts()
    assert any("grep" in t for t in rail._ttse_store.tips_texts())
    assert len(llm.calls) == 3  # blame -> synthesize -> induce


@pytest.mark.asyncio
async def test_rail_blame_none_does_not_retire(tmp_path):
    def handler(p: str) -> str:
        if "diagnosing" in p:
            return "VERDICT: NONE\nREASON: no rule is at fault"
        if "review a rule bank" in p:
            return "NONE"
        if "extracting" in p:
            return "[FACT] lesson"
        return "NONE"

    rail = _make_rail(tmp_path, ScriptedLLM(handler))
    await rail._ttse_store.add_fact("F1")
    await rail._ttse_store.add_fact("F2")
    snap = {
        "messages": [{"role": "user", "content": "q"}],
        "ttse_capabilities": "- grep",
        "ttse_task_query": "q",
        "ttse_score": 0.0,
    }
    await rail._run_ttse_induction(None, ctx=None, snapshot=snap)
    assert rail._ttse_store.retired == []
    assert set(rail._ttse_store.facts_texts()) == {"F1", "F2", "lesson"}


# ----------------------------------------------------------------------
# Injection (before_model_call adds the TTSE_FACTS_TIPS section)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_model_call_injects_whole_bank_section(tmp_path):
    rail = _make_rail(tmp_path, ScriptedLLM(lambda p: "NONE"))
    await rail._ttse_store.add_fact("injected fact")
    await rail._ttse_store.add_tip("injected tip")

    builder = SystemPromptBuilder()
    ctx = SimpleNamespace(
        inputs=SimpleNamespace(
            system_prompt_builder=builder,
            query="some query",
            messages=[{"role": "user", "content": "some query"}],
        )
    )
    await rail.before_model_call(ctx)

    assert builder.has_section(SectionName.TTSE_FACTS_TIPS)
    section = builder.get_section(SectionName.TTSE_FACTS_TIPS)
    text = section.content["cn"]
    assert "injected fact" in text
    assert "injected tip" in text


@pytest.mark.asyncio
async def test_injection_per_invoke_cache_hits_same_query(tmp_path):
    rail = _make_rail(tmp_path, ScriptedLLM(lambda p: "NONE"))
    await rail._ttse_store.add_fact("cached fact")
    body1 = await rail._resolve_injection_body("same query")
    body2 = await rail._resolve_injection_body("same query")
    assert body1 == body2
    assert "cached fact" in body1


# ----------------------------------------------------------------------
# Success detector
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trajectory_success_detector_signals():
    det = TrajectoryErrorSuccessDetector(success_threshold=0.999)
    assert (await det.detect(None, None, snapshot={"ttse_score": 0.0})).outcome == "fail"
    assert (await det.detect(None, None, snapshot={"ttse_score": 1.0})).outcome == "success"
    assert (await det.detect(None, None, snapshot={})).outcome == "success"  # no signal
    step = SimpleNamespace(error={"message": "boom"}, detail=None)
    traj = SimpleNamespace(steps=[step])
    assert (await det.detect(traj, None, snapshot={})).outcome == "fail"


# ----------------------------------------------------------------------
# Configure / unconfigure API
# ----------------------------------------------------------------------


class _FakeAgent:
    def __init__(self):
        self.rails: list = []

    def find_rails_by_type(self, types):
        return [r for r in self.rails if isinstance(r, types)]

    def add_rail(self, rail):
        self.rails.append(rail)

    def strip_rails_by_type(self, types):
        before = len(self.rails)
        self.rails = [r for r in self.rails if not isinstance(r, types)]
        return before - len(self.rails)


@pytest.mark.asyncio
async def test_configure_and_unconfigure_ttse_evolution(tmp_path):
    agent = _FakeAgent()
    llm = ScriptedLLM(lambda p: "NONE")
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"))
    configure_ttse_evolution(agent, llm=llm, model="m", ttse_config=cfg)
    ttse = [r for r in agent.rails if isinstance(r, TTSERail)]
    assert len(ttse) == 1

    configure_ttse_evolution(agent, llm=llm, model="m")
    assert len([r for r in agent.rails if isinstance(r, TTSERail)]) == 1

    removed = unconfigure_ttse_evolution(agent)
    assert removed >= 1
    assert not any(isinstance(r, TTSERail) for r in agent.rails)


# ----------------------------------------------------------------------
# Batch induce (cost amortization): N tasks -> ONE induce call
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_induce_batch_parses_rules():
    llm = ScriptedLLM(lambda p: "[FACT] batch fact\n[TIP] When x: use grep to y" if "BATCH" in p else "NONE")
    group = [
        {"task_id": "t1", "task_prompt": "q1", "traj_text": "tr1", "outcome": "success"},
        {"task_id": "t2", "task_prompt": "q2", "traj_text": "tr2", "outcome": "fail"},
    ]
    facts, tips = await induce_batch(
        llm=llm,
        model="m",
        policy=_POLICY,
        group=group,
        capabilities="- grep",
        existing_facts=[],
        existing_tips=[],
    )
    assert facts == ["batch fact"]
    assert tips == ["When x: use grep to y"]


@pytest.mark.asyncio
async def test_rail_batch_induce_amortizes_to_one_call(tmp_path):
    """With batch_size=2, two tasks induce via a SINGLE LLM call."""
    llm = ScriptedLLM(lambda p: "[FACT] batch fact" if "BATCH" in p else "NONE")
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), batch_size=2)
    rail = _make_rail(tmp_path, llm, cfg=cfg)
    snap = {
        "messages": [{"role": "user", "content": "q"}],
        "ttse_capabilities": "- grep",
        "ttse_task_query": "q",
    }
    await rail._run_ttse_induction(None, ctx=None, snapshot=snap)  # buffer 1/2
    assert llm.calls == []  # not induced yet
    assert rail._ttse_store.facts_texts() == []
    await rail._run_ttse_induction(None, ctx=None, snapshot=snap)  # buffer 2/2 -> flush
    assert len(llm.calls) == 1  # ONE induce_batch call for both tasks
    assert rail._ttse_store.facts_texts() == ["batch fact"]


@pytest.mark.asyncio
async def test_rail_batch_blame_runs_per_failed_task_before_flush(tmp_path):
    """In batch mode blame/retire still fire per failed task (concentrated), not at flush."""

    def handler(p: str) -> str:
        if "diagnosing" in p:
            return "VERDICT: 1\nREASON: rule 1 misled the agent"
        if "BATCH" in p:
            return "[FACT] lesson"
        return "NONE"

    llm = ScriptedLLM(handler)
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), batch_size=2)
    rail = _make_rail(tmp_path, llm, cfg=cfg)
    await rail._ttse_store.add_fact("F1 bad fact")
    await rail._ttse_store.add_fact("F2 keeper")
    fail_snap = {
        "messages": [{"role": "user", "content": "q"}],
        "ttse_capabilities": "- grep",
        "ttse_task_query": "q",
        "ttse_score": 0.0,  # FAIL
    }
    await rail._run_ttse_induction(None, ctx=None, snapshot=fail_snap)  # buffer 1/2, fail
    assert [r["text"] for r in rail._ttse_store.retired] == ["F1 bad fact"]
    assert len(llm.calls) == 1  # blame only; no induce yet

    ok_snap = {
        "messages": [{"role": "user", "content": "q2"}],
        "ttse_capabilities": "- grep",
        "ttse_task_query": "q2",
    }
    await rail._run_ttse_induction(None, ctx=None, snapshot=ok_snap)  # buffer 2/2 -> flush
    assert len(llm.calls) == 2  # blame + one induce_batch (synthesize short-circuits: 1 rule)
    assert "lesson" in rail._ttse_store.facts_texts()


@pytest.mark.asyncio
async def test_rail_flush_induces_partial_buffer(tmp_path):
    llm = ScriptedLLM(lambda p: "[FACT] flushed fact" if "BATCH" in p else "NONE")
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), batch_size=5)
    rail = _make_rail(tmp_path, llm, cfg=cfg)
    snap = {
        "messages": [{"role": "user", "content": "q"}],
        "ttse_capabilities": "- grep",
        "ttse_task_query": "q",
    }
    await rail._run_ttse_induction(None, ctx=None, snapshot=snap)  # buffer 1/5
    assert rail._ttse_store.facts_texts() == []
    await rail.flush()
    assert rail._ttse_store.facts_texts() == ["flushed fact"]
    assert len(llm.calls) == 1
    await rail.flush()  # empty buffer -> no-op
    assert len(llm.calls) == 1


# ----------------------------------------------------------------------
# Signal-based success detector (consumes tool-error signals)
# ----------------------------------------------------------------------


class _FakeSignalDetector:
    """Stand-in for ConversationSignalDetector returning fixed signal types."""

    def __init__(self, types):
        self._types = types

    def detect_trajectory_signals(self, trajectory, *, messages=None, signal_types=None):
        return [SimpleNamespace(signal_type=t) for t in self._types]


@pytest.mark.asyncio
async def test_signal_detector_maps_signals_to_outcomes():
    fail_det = SignalBasedSuccessDetector(signal_detector=_FakeSignalDetector(["execution_failure"]))
    assert (await fail_det.detect(None, None, snapshot={})).outcome == "fail"

    # No failure (including a clean script_artifact) defaults to success.
    ok_det = SignalBasedSuccessDetector(signal_detector=_FakeSignalDetector(["script_artifact"]))
    out = await ok_det.detect(None, None, snapshot={})
    assert out.outcome == "success"
    assert out.reason == "no-failure-default"

    none_det = SignalBasedSuccessDetector(signal_detector=_FakeSignalDetector([]))
    out = await none_det.detect(None, None, snapshot={})
    assert out.outcome == "success"
    assert out.reason == "no-failure-default"


@pytest.mark.asyncio
async def test_signal_detector_explicit_score_wins_over_signals():
    det = SignalBasedSuccessDetector(signal_detector=_FakeSignalDetector(["execution_failure"]))
    out = await det.detect(None, None, snapshot={"ttse_score": 1.0})
    assert out.outcome == "success"
    assert out.reason == "explicit-score"


@pytest.mark.asyncio
async def test_signal_detector_reads_real_failure_from_messages():
    """End-to-end: detect_tool_error_signals flags a tool error as fail."""
    det = SignalBasedSuccessDetector()
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc1", "name": "bash", "args": {"command": "ls missing"}}],
        },
        {"role": "tool", "tool_call_id": "tc1", "name": "bash", "content": "Error: file not found"},
    ]
    out = await det.detect(None, messages, snapshot={})
    assert out.outcome == "fail"
    assert out.reason == "signal:execution_failure"


@pytest.mark.asyncio
async def test_signal_detector_defaults_to_success_without_failure():
    det = SignalBasedSuccessDetector()
    messages = [
        {"role": "tool", "name": "bash", "content": "ok"},
    ]
    out = await det.detect(None, messages, snapshot={})
    assert out.outcome == "success"
    assert out.reason == "no-failure-default"


# ----------------------------------------------------------------------
# Shared tool-error helper
# ----------------------------------------------------------------------


def test_detect_tool_error_signals_flags_file_not_found():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "tc1", "name": "bash"}],
        },
        {"role": "tool", "tool_call_id": "tc1", "name": "bash", "content": "Error: file not found"},
    ]
    signals = detect_tool_error_signals(messages)
    assert [s.signal_type for s in signals] == ["execution_failure"]
    assert signals[0].context.get("tool_name") == "bash"


def test_detect_tool_error_signals_flags_command_not_found():
    messages = [
        {"role": "tool", "name": "bash", "content": "command not found: foo"},
    ]
    signals = detect_tool_error_signals(messages)
    assert [s.signal_type for s in signals] == ["execution_failure"]


def test_detect_tool_error_signals_ignores_ok_output():
    messages = [{"role": "tool", "name": "bash", "content": "ok"}]
    assert detect_tool_error_signals(messages) == []


def test_detect_tool_error_signals_skips_data_fetch_tools():
    messages = [
        {"role": "tool", "name": "read_file", "content": "Error: file not found"},
        {"role": "tool", "name": "web_search", "content": "request failed"},
    ]
    assert detect_tool_error_signals(messages) == []
