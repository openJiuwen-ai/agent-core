# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the TTSE rail (Two-Track Self-Evolution).

The frozen TTSE algorithm is exercised end-to-end with a scripted LLM:
FACT/TIP induction, the fail path (blame -> retire -> synthesize -> induce),
store dedup/persistence, success detection, catalog inject, and the
configure/unconfigure API.
"""

from __future__ import annotations

from types import SimpleNamespace
import inspect
from typing import Callable

import pytest

from openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer import (
    GENERATE_RECORDS_LLM_POLICY,
)
from openjiuwen.agent_evolving.signal import detect_tool_error_signals
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint, PreparedEvolutionInput
from openjiuwen.harness.rails.evolution.ttse import (
    TTSEConfig,
    TTSERail,
    TTSERecordStore,
    SignalBasedSuccessDetector,
    TrajectoryErrorSuccessDetector,
    configure_ttse_evolution,
    unconfigure_ttse_evolution,
)
from openjiuwen.harness.rails.evolution.ttse.stores import reset_shared_stores, shared_store
from openjiuwen.harness.rails.evolution.ttse.catalog import project_catalog
from openjiuwen.harness.rails.evolution.ttse.classify import parse_assignments
from openjiuwen.harness.rails.evolution.ttse.consult import (
    MAX_CONSULT_CATEGORIES,
    parse_consult_categories,
    render_consult_result,
)
from openjiuwen.harness.rails.evolution.ttse.prompts import FACT_TIP_DEFINITION, detect_judge_prompt
from openjiuwen.harness.rails.evolution.ttse.render import DISK_CATALOG_GUIDANCE_CN
from openjiuwen.harness.rails.evolution.ttse.trajectory_adapter import (
    count_tool_calls,
    extract_final_reply,
    extract_output_paths,
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
from openjiuwen.harness.rails.evolution.ttse.ttse_rail import _TTSEPreparedEvolutionInput

_POLICY = GENERATE_RECORDS_LLM_POLICY
_PROCESSOR = TrajectorySpanProcessor()


def _empty_trajectory(*, execution_id: str = "e1", session_id: str = "s1") -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map(
                            {TRAJECTORY_ID: execution_id, SESSION_ID: session_id}
                        )
                    },
                    "scopeSpans": [{"scope": {"name": "test"}, "spans": []}],
                }
            ]
        }
    )

class ScriptedLLM:
    """LLM whose ``invoke`` dispatches on the prompt via a handler callable."""

    def __init__(self, handler: Callable[[str], object]):
        self.handler = handler
        self.calls: list[str] = []

    async def invoke(self, *, model, messages, temperature=None, timeout=None, **kwargs):
        prompt = messages[0]["content"] if messages else ""
        self.calls.append(prompt)
        result = self.handler(prompt)
        if inspect.isawaitable(result):
            result = await result
        return result


def _make_rail(tmp_path, llm, *, cfg=None, success_detector=None) -> TTSERail:
    """Induce/blame regression helper: inject TrajectoryErrorSuccessDetector by default.

    Production default is SignalBasedSuccessDetector. Classify runs after bank
    writes; scripted handlers that do not match the assignment prompt return NONE.
    """
    return TTSERail(
        llm=llm,
        model="dummy-model",
        ttse_config=cfg or TTSEConfig(store_path=str(tmp_path / "bank.json")),
        success_detector=success_detector
        if success_detector is not None
        else TrajectoryErrorSuccessDetector(),
        trajectory_span_processor=_PROCESSOR,
    )


def _n_tool_messages(n: int, *, write_path: str | None = None) -> list[dict]:
    """Build ``n`` assistant tool_calls (+ optional write_file and a final reply)."""
    messages: list[dict] = [{"role": "user", "content": "answer the question"}]
    for i in range(n):
        name = "write_file" if write_path and i == 0 else "bash"
        args = {"file_path": write_path, "content": "x"} if name == "write_file" else {"command": f"echo {i}"}
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"tc{i}", "name": name, "arguments": args}],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"tc{i}", "name": name, "content": "ok"})
    messages.append({"role": "assistant", "content": "Here is the answer."})
    return messages


# ----------------------------------------------------------------------
# Parsers (frozen TTSE logic)
# ----------------------------------------------------------------------


def test_fact_tip_definition_uses_jiuwen_capability_names():
    """Induce examples must match BASIC_TOOLS names, not OpenClaw aliases."""
    text = FACT_TIP_DEFINITION
    for stale in ("python3", "`shell`", "`jq`", "session-logs"):
        assert stale not in text, f"stale capability example still present: {stale}"
    for name in ("python_exec", "bash", "read_file"):
        assert name in text


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


def test_shared_store_same_path_is_one_object(tmp_path):
    reset_shared_stores()
    path = str(tmp_path / "bank.json")
    a = shared_store(TTSEConfig(store_path=path))
    b = shared_store(TTSEConfig(store_path=path))
    assert a is b
    reset_shared_stores()


@pytest.mark.asyncio
async def test_two_rails_same_path_do_not_wipe_each_others_rules(tmp_path):
    """Stale per-session snapshots used to save() over the whole bank."""
    reset_shared_stores()
    path = str(tmp_path / "bank.json")
    cfg = TTSEConfig(store_path=path)
    rail_a = TTSERail(
        llm=ScriptedLLM(lambda p: "NONE"),
        model="m",
        ttse_config=cfg,
        trajectory_span_processor=_PROCESSOR,
    )
    await rail_a._ttse_store.add_fact("chinese excel fact")
    rail_b = TTSERail(
        llm=ScriptedLLM(lambda p: "NONE"),
        model="m",
        ttse_config=TTSEConfig(store_path=path),
        trajectory_span_processor=_PROCESSOR,
    )
    assert rail_b._ttse_store is rail_a._ttse_store
    await rail_b._ttse_store.add_tip("chinese csv tip")
    assert "chinese excel fact" in rail_b._ttse_store.facts_texts()
    reloaded = TTSERecordStore(TTSEConfig(store_path=path))
    assert reloaded.facts_texts() == ["chinese excel fact"]
    assert reloaded.tips_texts() == ["chinese csv tip"]
    reset_shared_stores()


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
    assert len(llm.calls) == 2  # induce + classify


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
    assert len(llm.calls) == 5  # blame -> synth -> classify tip -> induce -> classify fact


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
# Injection (before_model_call adds catalog guidance, not the rule body)
# ----------------------------------------------------------------------


class _MockEmbedding:
    """Deterministic EmbeddingProvider for constructor wiring tests."""

    async def embed_query(self, text: str):
        return [1.0, 0.0, 0.0]

    async def embed_documents(self, texts: list[str]):
        return [await self.embed_query(t) for t in texts]


@pytest.mark.asyncio
async def test_constructor_embedding_syncs_to_config(tmp_path):
    provider = _MockEmbedding()
    cfg = TTSEConfig(store_path=str(tmp_path / "bank.json"))
    rail = TTSERail(
        llm=ScriptedLLM(lambda p: "NONE"),
        model="m",
        ttse_config=cfg,
        embedding=provider,
        trajectory_span_processor=_PROCESSOR,
    )
    assert rail._ttse_config.embedding is provider
    assert rail._ttse_store.has_embedding_provider()


# ----------------------------------------------------------------------
# Success detector
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trajectory_success_detector_signals():
    det = TrajectoryErrorSuccessDetector(success_threshold=0.9)
    assert (await det.detect(None, None, snapshot={"ttse_score": 0.0})).outcome == "fail"
    assert (await det.detect(None, None, snapshot={"ttse_score": 0.4})).outcome == "partial"
    assert (await det.detect(None, None, snapshot={"ttse_score": 0.89})).outcome == "partial"
    assert (await det.detect(None, None, snapshot={"ttse_score": 0.9})).outcome == "success"
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
    configure_ttse_evolution(
        agent,
        llm=llm,
        model="m",
        ttse_config=cfg,
        trajectory_span_processor=_PROCESSOR,
    )
    ttse = [r for r in agent.rails if isinstance(r, TTSERail)]
    assert len(ttse) == 1

    configure_ttse_evolution(agent, llm=llm, model="m", trajectory_span_processor=_PROCESSOR)
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
    assert len(llm.calls) == 2  # induce_batch + classify
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
    assert len(llm.calls) == 3  # blame + induce_batch + classify
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
    assert len(llm.calls) == 2  # induce_batch + classify
    await rail.flush()  # empty buffer -> no-op
    assert len(llm.calls) == 2


# ----------------------------------------------------------------------
# SignalBasedSuccessDetector (default production detector)
# ----------------------------------------------------------------------


class _FakeSignalDetector:
    """Stand-in for ConversationSignalDetector returning fixed signal types."""

    def __init__(self, types, *, user_intent_signals=None):
        self._types = types
        self._user_intent_signals = user_intent_signals
        self.user_intent_calls = 0

    def detect_trajectory_signals(self, trajectory, *, messages=None, signal_types=None):
        return [SimpleNamespace(signal_type=t) for t in self._types]

    async def detect_user_intent(self, *args, **kwargs):
        self.user_intent_calls += 1
        if self._user_intent_signals is None:
            return []
        return list(self._user_intent_signals)


def test_adapter_count_and_extract_helpers():
    msgs = _n_tool_messages(3, write_path="out/a.txt")
    assert count_tool_calls(msgs) == 3
    assert extract_output_paths(msgs) == ["out/a.txt"]
    assert extract_final_reply(msgs) == "Here is the answer."
    # read_file paths are ignored
    read_only = [
        {
            "role": "assistant",
            "tool_calls": [{"name": "read_file", "arguments": {"file_path": "x.txt"}}],
        }
    ]
    assert extract_output_paths(read_only) == []


def test_detect_judge_prompt_is_reply_only():
    text = detect_judge_prompt("what is 2+2?", "4")
    assert "what is 2+2?" in text
    assert "4" in text
    assert "impartial judge" in text
    assert "Goal decomposition" in text
    assert "SATISFIED" in text
    assert "UNSATISFIED" in text
    assert "output_path" not in text.lower()
    assert "OBSERVATION" not in text


@pytest.mark.asyncio
async def test_signal_detector_gate_skips_below_min_tool_calls(tmp_path):
    llm = ScriptedLLM(lambda p: '{"outcome":"success","delivery":"answer","goals":[],"reason":"ok"}')
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(
        llm=llm,
        model="m",
        config=cfg,
        signal_detector=_FakeSignalDetector([]),
    )
    out = await det.detect(None, _n_tool_messages(4), snapshot={"ttse_task_query": "q"})
    assert out.outcome == "skip"
    assert out.reason.startswith("gate:")
    assert llm.calls == []


@pytest.mark.asyncio
async def test_signal_detector_gate_uses_per_invoke_tool_calls_over_messages(tmp_path):
    """Session-cumulative messages must not pass the gate when this invoke is short."""
    llm = ScriptedLLM(lambda p: '{"outcome":"success","delivery":"answer","goals":[],"reason":"ok"}')
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(
        llm=llm,
        model="m",
        config=cfg,
        signal_detector=_FakeSignalDetector([]),
    )
    out = await det.detect(
        None,
        _n_tool_messages(10),
        snapshot={"ttse_task_query": "q", "ttse_invoke_tool_calls": 2},
    )
    assert out.outcome == "skip"
    assert out.reason.startswith("gate:")
    assert llm.calls == []


@pytest.mark.asyncio
async def test_signal_detector_execution_failure_beats_tool_gate(tmp_path):
    """Signals run before the tool gate: few tools + failure still -> partial."""
    llm = ScriptedLLM(lambda p: '{"outcome":"success","delivery":"answer","goals":[],"reason":"ok"}')
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(
        llm=llm,
        model="m",
        config=cfg,
        signal_detector=_FakeSignalDetector(["execution_failure"]),
    )
    out = await det.detect(
        None,
        _n_tool_messages(2),
        snapshot={"ttse_task_query": "q", "ttse_invoke_tool_calls": 2},
    )
    assert out.outcome == "partial"
    assert out.reason == "signal:execution_failure"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_signal_detector_user_intent_beats_tool_gate(tmp_path):
    """Signals run before the tool gate: few tools + user_intent still -> partial."""
    llm = ScriptedLLM(lambda p: '{"outcome":"success","delivery":"answer","goals":[],"reason":"ok"}')
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    fake = _FakeSignalDetector([], user_intent_signals=[SimpleNamespace(signal_type="user_intent")])
    det = SignalBasedSuccessDetector(
        llm=llm,
        model="m",
        config=cfg,
        signal_detector=fake,
    )
    out = await det.detect(
        None,
        _n_tool_messages(1),
        snapshot={"ttse_task_query": "q", "ttse_invoke_tool_calls": 1},
    )
    assert out.outcome == "partial"
    assert out.reason == "signal:user_intent"
    assert fake.user_intent_calls == 1
    assert llm.calls == []


@pytest.mark.asyncio
async def test_signal_detector_gate_passes_when_invoke_tool_calls_meet_min(tmp_path):
    llm = ScriptedLLM(lambda p: '{"outcome":"success","delivery":"answer","goals":[],"reason":"ok"}')
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(
        llm=llm,
        model="m",
        config=cfg,
        signal_detector=_FakeSignalDetector(["execution_failure"]),
    )
    # Messages alone would be below min; invoke count must win.
    out = await det.detect(
        None,
        _n_tool_messages(2),
        snapshot={"ttse_task_query": "q", "ttse_invoke_tool_calls": 5},
    )
    assert out.outcome == "partial"
    assert out.reason == "signal:execution_failure"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_prepare_evolution_input_counts_tool_calls_from_messages(tmp_path, monkeypatch):
    llm = ScriptedLLM(lambda p: "NONE")
    rail = _make_rail(tmp_path, llm)
    messages = _n_tool_messages(2)
    traj = _empty_trajectory()
    ctx = AgentCallbackContext(
        agent=None,
        inputs=InvokeInputs(query="round-2", conversation_id="s1"),
    )

    async def _parent_prepare(self, trajectory, ctx):
        return PreparedEvolutionInput(trajectory=trajectory, messages=tuple(messages))

    monkeypatch.setattr(EvolutionRail, "_prepare_evolution_input", _parent_prepare)
    prepared = await rail._prepare_evolution_input(traj, ctx)

    assert prepared is not None
    assert prepared.ttse_invoke_tool_calls == 2
    assert prepared.ttse_task_query == "round-2"
    snap = rail._snapshot_from_prepared(prepared)
    assert snap["ttse_invoke_tool_calls"] == 2
    assert snap["ttse_task_query"] == "round-2"


@pytest.mark.asyncio
async def test_run_evolution_uses_prepared_messages(tmp_path):
    calls: list[dict] = []
    llm = ScriptedLLM(lambda p: "NONE")
    rail = _make_rail(tmp_path, llm)
    messages = _n_tool_messages(2)
    traj = _empty_trajectory()

    async def _capture(trajectory, ctx=None, *, snapshot=None):
        calls.append(snapshot or {})

    rail._run_ttse_induction = _capture  # type: ignore[method-assign]
    prepared = _TTSEPreparedEvolutionInput(
        trajectory=traj,
        messages=tuple(messages),
        skill_name="ttse",
        ttse_capabilities="bash",
        ttse_task_query="round-2",
        ttse_invoke_tool_calls=2,
    )
    await rail.run_evolution(prepared)
    assert len(calls) == 1
    assert calls[0]["ttse_task_query"] == "round-2"
    assert calls[0]["ttse_invoke_tool_calls"] == 2
    assert calls[0]["ttse_capabilities"] == "bash"
    assert len(calls[0]["messages"]) == len(messages)


@pytest.mark.asyncio
async def test_signal_detector_execution_failure_partial_no_judge(tmp_path):
    llm = ScriptedLLM(lambda p: '{"outcome":"fail","delivery":"answer","goals":[],"reason":"x"}')
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(
        llm=llm,
        model="m",
        config=cfg,
        signal_detector=_FakeSignalDetector(["execution_failure"]),
    )
    out = await det.detect(None, _n_tool_messages(5), snapshot={"ttse_task_query": "q"})
    assert out.outcome == "partial"
    assert out.reason == "signal:execution_failure"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_signal_detector_reads_real_failure_from_messages(tmp_path):
    llm = ScriptedLLM(lambda p: '{"outcome":"fail","delivery":"answer","goals":[],"reason":"x"}')
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg)
    messages = _n_tool_messages(5)
    messages[2] = {
        "role": "tool",
        "tool_call_id": "tc0",
        "name": "bash",
        "content": "Error: file not found",
    }
    out = await det.detect(None, messages, snapshot={"ttse_task_query": "q"})
    assert out.outcome == "partial"
    assert out.reason == "signal:execution_failure"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_signal_detector_script_artifact_alone_does_not_fast_path(tmp_path):
    llm = ScriptedLLM(
        lambda p: '{"goals":[],"delivery":"answer","outcome":"success","reason":"ok"}'
    )
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(
        llm=llm,
        model="m",
        config=cfg,
        signal_detector=_FakeSignalDetector(["script_artifact"]),
    )
    out = await det.detect(None, _n_tool_messages(5), snapshot={"ttse_task_query": "q"})
    assert out.outcome == "success"
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_signal_detector_artifact_paths_also_judged(tmp_path):
    llm = ScriptedLLM(
        lambda p: (
            '{"is_feedback": false}'
            if "is_feedback" in p or "反馈" in p or "feedback" in p.lower()
            else '{"goals":[],"delivery":"answer","outcome":"success","reason":"ok"}'
        )
    )
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg)
    out = await det.detect(
        None,
        _n_tool_messages(5, write_path="deck.pptx"),
        snapshot={"ttse_task_query": "make a ppt"},
    )
    assert out.outcome == "success"
    # skillless user_intent LLM + reply judge
    assert len(llm.calls) == 2
    judge_prompt = llm.calls[1]
    assert "make a ppt" in judge_prompt
    assert "Here is the answer." in judge_prompt
    assert "Claimed artifact output paths" not in judge_prompt
    assert "deck.pptx" not in judge_prompt


@pytest.mark.asyncio
async def test_signal_detector_reply_judge_once(tmp_path):
    llm = ScriptedLLM(
        lambda p: (
            '{"is_feedback": false}'
            if "is_feedback" in p or "反馈" in p or "feedback" in p.lower()
            else '{"goals":["answer"],"delivery":"answer","outcome":"success","reason":"complete"}'
        )
    )
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg)
    out = await det.detect(
        None,
        _n_tool_messages(5),
        snapshot={"ttse_task_query": "what is 2+2?"},
    )
    assert out.outcome == "success"
    # skillless user_intent LLM + one reply judge
    assert len(llm.calls) == 2
    judge_prompt = llm.calls[1]
    assert "what is 2+2?" in judge_prompt
    assert "Here is the answer." in judge_prompt
    assert "OBSERVATION" not in judge_prompt


@pytest.mark.asyncio
async def test_signal_detector_honors_ttse_score(tmp_path):
    llm = ScriptedLLM(
        lambda p: '{"goals":[],"delivery":"answer","outcome":"partial","reason":"incomplete"}'
    )
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg)

    ok = await det.detect(
        None,
        _n_tool_messages(2),  # below gate; score must still win
        snapshot={"ttse_task_query": "q", "ttse_score": 1.0},
    )
    assert ok.outcome == "success"
    assert ok.reason == "explicit-score"
    assert ok.score == 1.0

    fail = await det.detect(
        None,
        _n_tool_messages(5),
        snapshot={"ttse_task_query": "q", "ttse_score": 0.0},
    )
    assert fail.outcome == "fail"
    assert fail.reason == "explicit-score"

    partial = await det.detect(
        None,
        _n_tool_messages(5),
        snapshot={"ttse_task_query": "q", "ttse_score": 0.5},
    )
    assert partial.outcome == "partial"
    assert partial.reason == "explicit-score"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_signal_detector_bad_json_skips(tmp_path):
    llm = ScriptedLLM(lambda p: "not-json")
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg)
    out = await det.detect(None, _n_tool_messages(5), snapshot={"ttse_task_query": "q"})
    assert out.outcome == "skip"
    assert out.reason == "judge_bad_json"


@pytest.mark.asyncio
async def test_signal_detector_user_intent_partial_no_judge(tmp_path):
    """Secondary corrective user turn -> partial; Judge LLM is not called."""
    llm = ScriptedLLM(
        lambda p: '{"goals":[],"delivery":"answer","outcome":"success","reason":"ok"}'
    )
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    fake = _FakeSignalDetector([], user_intent_signals=[SimpleNamespace(signal_type="user_intent")])
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg, signal_detector=fake)
    messages = _n_tool_messages(5)
    messages.insert(-1, {"role": "user", "content": "你做错了，重新来"})
    out = await det.detect(None, messages, snapshot={"ttse_task_query": "q"})
    assert out.outcome == "partial"
    assert out.reason == "signal:user_intent"
    assert fake.user_intent_calls == 1
    assert llm.calls == []


@pytest.mark.asyncio
async def test_signal_detector_failure_short_circuits_user_intent(tmp_path):
    llm = ScriptedLLM(lambda p: '{"outcome":"fail","delivery":"answer","goals":[],"reason":"x"}')
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    fake = _FakeSignalDetector(
        ["execution_failure"],
        user_intent_signals=[SimpleNamespace(signal_type="user_intent")],
    )
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg, signal_detector=fake)
    messages = _n_tool_messages(5)
    messages.insert(-1, {"role": "user", "content": "你做错了，重新来"})
    out = await det.detect(None, messages, snapshot={"ttse_task_query": "q"})
    assert out.outcome == "partial"
    assert out.reason == "signal:execution_failure"
    assert fake.user_intent_calls == 0
    assert llm.calls == []


@pytest.mark.asyncio
async def test_signal_detector_user_intent_beats_artifact_skip(tmp_path):
    llm = ScriptedLLM(lambda p: '{"outcome":"success","delivery":"answer","goals":[],"reason":"ok"}')
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    fake = _FakeSignalDetector([], user_intent_signals=[SimpleNamespace(signal_type="user_intent")])
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg, signal_detector=fake)
    messages = _n_tool_messages(5, write_path="deck.pptx")
    messages.insert(-1, {"role": "user", "content": "不对，版式错了"})
    out = await det.detect(None, messages, snapshot={"ttse_task_query": "make a ppt"})
    assert out.outcome == "partial"
    assert out.reason == "signal:user_intent"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_signal_detector_mock_user_intent_empty_continues_to_judge(tmp_path):
    llm = ScriptedLLM(
        lambda p: '{"goals":[],"delivery":"answer","outcome":"success","reason":"ok"}'
    )
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    fake = _FakeSignalDetector([], user_intent_signals=[])
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg, signal_detector=fake)
    out = await det.detect(None, _n_tool_messages(5), snapshot={"ttse_task_query": "q"})
    assert out.outcome == "success"
    assert fake.user_intent_calls == 1
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_signal_detector_real_skillless_feedback_partial(tmp_path):
    """End-to-end skillless path: LLM feedback judgment + no injected detector."""
    llm = ScriptedLLM(
        lambda p: (
            '{"is_feedback": true, "excerpt": "你做错了"}'
            if "is_feedback" in p or "反馈" in p or "feedback" in p.lower()
            else '{"goals":[],"delivery":"answer","outcome":"success","reason":"ok"}'
        )
    )
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg)
    messages = _n_tool_messages(5)
    messages.insert(-1, {"role": "user", "content": "你做错了，重新来"})
    out = await det.detect(None, messages, snapshot={"ttse_task_query": "q"})
    assert out.outcome == "partial"
    assert out.reason == "signal:user_intent"


@pytest.mark.asyncio
async def test_rail_skip_does_not_induce(tmp_path):
    llm = ScriptedLLM(lambda p: "[FACT] should not induce")
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(
        llm=llm,
        model="m",
        config=cfg,
        signal_detector=_FakeSignalDetector([]),
    )
    rail = TTSERail(
        llm=llm,
        model="m",
        ttse_config=cfg,
        success_detector=det,
        trajectory_span_processor=_PROCESSOR,
    )
    snap = {
        "messages": _n_tool_messages(2),
        "ttse_capabilities": "- grep",
        "ttse_task_query": "q",
    }
    await rail._run_ttse_induction(None, ctx=None, snapshot=snap)
    assert rail._ttse_store.facts_texts() == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_rail_partial_from_failure_induces_without_blame(tmp_path):
    def handler(p: str) -> str:
        if "extracting" in p:
            return "[FACT] lesson from error"
        if "diagnosing" in p:
            return "VERDICT: 1\nREASON: should not blame"
        return "NONE"

    llm = ScriptedLLM(handler)
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    rail = TTSERail(
        llm=llm,
        model="m",
        ttse_config=cfg,
        trajectory_span_processor=_PROCESSOR,
    )
    await rail._ttse_store.add_fact("existing")
    messages = _n_tool_messages(5)
    messages[2] = {
        "role": "tool",
        "tool_call_id": "tc0",
        "name": "bash",
        "content": "Error: file not found",
    }
    snap = {
        "messages": messages,
        "ttse_capabilities": "- grep",
        "ttse_task_query": "q",
    }
    await rail._run_ttse_induction(None, ctx=None, snapshot=snap)
    assert "lesson from error" in rail._ttse_store.facts_texts()
    assert rail._ttse_store.retired == []
    assert not any("diagnosing" in c for c in llm.calls)


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


# ----------------------------------------------------------------------
# disk_catalog inject: guidance section, post-write classify, consult
# ----------------------------------------------------------------------


def _disk_catalog_cfg(tmp_path) -> TTSEConfig:
    return TTSEConfig(store_path=str(tmp_path / "bank.json"))


@pytest.mark.asyncio
async def test_disk_catalog_injects_guidance_not_rule_body(tmp_path):
    rail = _make_rail(tmp_path, ScriptedLLM(lambda p: "NONE"), cfg=_disk_catalog_cfg(tmp_path))
    await rail._ttse_store.add_fact("PresentBench grades slides.md")
    builder = SystemPromptBuilder()
    ctx = SimpleNamespace(
        inputs=SimpleNamespace(
            system_prompt_builder=builder,
            query="make slides",
            messages=[{"role": "user", "content": "make slides"}],
        )
    )
    await rail.before_model_call(ctx)
    section = builder.get_section(SectionName.TTSE_FACTS_TIPS)
    text = section.content["cn"]
    assert "ttse_consult(category=" in text
    assert "query=" in text
    assert "无参" in text
    assert text.strip() == DISK_CATALOG_GUIDANCE_CN.strip()
    assert "PresentBench grades slides.md" not in text
    assert "documents-office-and-records" not in text


@pytest.mark.asyncio
async def test_disk_catalog_classifies_after_bank_write(tmp_path):
    def handler(p: str) -> str:
        if "TTSE category assignment pass" in p:
            return '{"assignments": {"1": "documents-office-and-records"}}'
        if "extracting" in p:
            return "[FACT] PresentBench grades slides.md, not a .pptx file"
        return "NONE"

    llm = ScriptedLLM(handler)
    rail = _make_rail(tmp_path, llm, cfg=_disk_catalog_cfg(tmp_path))
    snap = {
        "messages": [{"role": "user", "content": "slides"}, {"role": "assistant", "content": "done"}],
        "ttse_capabilities": "- read_file",
        "ttse_task_query": "slides",
    }
    await rail._run_ttse_induction(None, ctx=None, snapshot=snap)
    assert len(llm.calls) == 2
    assert "extracting" in llm.calls[0]
    assert "TTSE category assignment pass" in llm.calls[1]
    assert "PresentBench grades slides.md" in rail._ttse_store.facts_texts()[0]
    assert rail._ttse_store.facts[0]["category"] == "documents-office-and-records"
    catalog = tmp_path / "CATALOG.md"
    assert catalog.is_file()
    assert "documents-office-and-records" in catalog.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_merge_inherits_existing_category(tmp_path):
    store = TTSERecordStore(TTSEConfig(store_path=str(tmp_path / "b.json")))
    assert await store.add_fact("the csv grader is case-sensitive") is True
    await store.set_categories([("the csv grader is case-sensitive", "fact", "documents-office-and-records")])
    assert await store.add_fact("the csv grader is case-sensitive") is False
    assert store.facts[0]["count"] == 2
    assert store.facts[0]["category"] == "documents-office-and-records"


def test_parse_assignments_illegal_id_becomes_other():
    items = [("a fact", "fact"), ("a tip", "tip")]
    parsed = parse_assignments(
        '{"assignments": {"1": "not-a-real-id", "2": "software-engineering-devops"}}',
        items,
    )
    assert parsed[0][2] == "other"
    assert parsed[1][2] == "software-engineering-devops"


@pytest.mark.asyncio
async def test_consult_lists_catalog_and_opens_category(tmp_path):
    store = TTSERecordStore(TTSEConfig(store_path=str(tmp_path / "bank.json")))
    await store.add_fact("PresentBench grades slides.md")
    await store.set_categories([("PresentBench grades slides.md", "fact", "documents-office-and-records")])
    listing = render_consult_result(store)
    assert "documents-office-and-records" in listing
    assert "PresentBench grades slides.md" not in listing
    opened = render_consult_result(store, category="documents-office-and-records")
    assert "PresentBench grades slides.md" in opened
    unknown = render_consult_result(store, category="world.pptx")
    assert "Unknown category" in unknown
    assert "trailing catalog" in unknown
    project_catalog(store)
    assert (tmp_path / "by_cat" / "documents-office-and-records" / "SUMMARY.md").is_file()


def test_parse_consult_categories_splits_comma_list_and_json():
    assert parse_consult_categories("documents-office-and-records") == [
        "documents-office-and-records"
    ]
    assert parse_consult_categories(
        "documents-office-and-records, software-engineering-devops"
    ) == ["documents-office-and-records", "software-engineering-devops"]
    assert parse_consult_categories(
        ["documents-office-and-records", "software-engineering-devops"]
    ) == ["documents-office-and-records", "software-engineering-devops"]
    assert parse_consult_categories(
        '["documents-office-and-records","other"]'
    ) == ["documents-office-and-records", "other"]
    assert parse_consult_categories("") == []


@pytest.mark.asyncio
async def test_consult_opens_multiple_categories_in_one_call(tmp_path):
    store = TTSERecordStore(TTSEConfig(store_path=str(tmp_path / "bank.json")))
    await store.add_fact("csv bom needed")
    await store.add_tip("When compiling C++: use cl /utf-8")
    await store.set_categories(
        [
            ("csv bom needed", "fact", "documents-office-and-records"),
            ("When compiling C++: use cl /utf-8", "tip", "software-engineering-devops"),
        ]
    )
    opened = render_consult_result(
        store,
        category="documents-office-and-records, software-engineering-devops",
    )
    assert "csv bom needed" in opened
    assert "When compiling C++: use cl /utf-8" in opened
    assert "`documents-office-and-records`" in opened
    assert "`software-engineering-devops`" in opened


@pytest.mark.asyncio
async def test_consult_caps_categories_and_skips_unknown(tmp_path):
    store = TTSERecordStore(TTSEConfig(store_path=str(tmp_path / "bank.json")))
    await store.add_fact("csv bom needed")
    await store.add_fact("go run works")
    await store.add_fact("pptx timeout")
    await store.set_categories(
        [
            ("csv bom needed", "fact", "documents-office-and-records"),
            ("go run works", "fact", "software-engineering-devops"),
            ("pptx timeout", "fact", "other"),
        ]
    )
    mixed = render_consult_result(
        store,
        category="documents-office-and-records, not-a-real-id",
    )
    assert "csv bom needed" in mixed
    assert "Unknown category `not-a-real-id`" in mixed
    ids = [
        "documents-office-and-records",
        "software-engineering-devops",
        "other",
        "skill-agent-meta-workflows",
    ]
    assert len(ids) > MAX_CONSULT_CATEGORIES
    capped = render_consult_result(store, category=", ".join(ids))
    assert "csv bom needed" in capped
    assert "go run works" in capped
    assert "pptx timeout" in capped
    assert "Opened the first 3 categories" in capped
    assert "`skill-agent-meta-workflows`" in capped


@pytest.mark.asyncio
async def test_disk_catalog_trails_listing_not_rule_body(tmp_path):
    rail = _make_rail(tmp_path, ScriptedLLM(lambda p: "NONE"), cfg=_disk_catalog_cfg(tmp_path))
    await rail._ttse_store.add_fact("PresentBench grades slides.md")
    await rail._ttse_store.set_categories(
        [("PresentBench grades slides.md", "fact", "documents-office-and-records")]
    )
    manager = PromptAttachmentManager()
    builder = SystemPromptBuilder()
    ctx = SimpleNamespace(
        session=SimpleNamespace(session_id="sess-ttse"),
        agent=SimpleNamespace(prompt_attachment_manager=manager),
        inputs=SimpleNamespace(
            system_prompt_builder=builder,
            query="make slides",
            messages=[{"role": "user", "content": "make slides"}],
        ),
    )
    await rail.before_model_call(ctx)
    sys_text = builder.get_section(SectionName.TTSE_FACTS_TIPS).content["cn"]
    assert "PresentBench grades slides.md" not in sys_text
    assert "documents-office-and-records" not in sys_text
    attached = await manager.collect_for_session("sess-ttse")
    assert len(attached) == 1
    assert attached[0].section == "ttse_catalog"
    body = attached[0].content or ""
    assert "documents-office-and-records" in body
    assert "ttse_consult(category=" in body
    assert "PresentBench grades slides.md" not in body
    rendered = manager.render(attached)
    assert "documents-office-and-records" in rendered
    assert "<prompt-attachment" in rendered


@pytest.mark.asyncio
async def test_disk_catalog_trails_empty_listing(tmp_path):
    rail = _make_rail(tmp_path, ScriptedLLM(lambda p: "NONE"), cfg=_disk_catalog_cfg(tmp_path))
    manager = PromptAttachmentManager()
    builder = SystemPromptBuilder()
    ctx = SimpleNamespace(
        session=SimpleNamespace(session_id="sess-empty"),
        agent=SimpleNamespace(prompt_attachment_manager=manager),
        inputs=SimpleNamespace(system_prompt_builder=builder, query="hi", messages=[]),
    )
    await rail.before_model_call(ctx)
    attached = await manager.collect_for_session("sess-empty")
    assert attached and "(empty)" in (attached[0].content or "")


@pytest.mark.asyncio
async def test_disk_catalog_attachment_uses_context_session_id(tmp_path):
    rail = _make_rail(tmp_path, ScriptedLLM(lambda p: "NONE"), cfg=_disk_catalog_cfg(tmp_path))
    manager = PromptAttachmentManager()
    builder = SystemPromptBuilder()
    ctx = SimpleNamespace(
        session=None,
        context=SimpleNamespace(session_id=lambda: "from-context"),
        agent=SimpleNamespace(prompt_attachment_manager=manager),
        inputs=SimpleNamespace(system_prompt_builder=builder, query="hi", messages=[]),
    )
    await rail.before_model_call(ctx)
    attached = await manager.collect_for_session("from-context")
    assert attached and attached[0].section == "ttse_catalog"


def test_trajectory_adapter_reads_openai_tool_args_and_keeps_tail():
    from openjiuwen.harness.rails.evolution.ttse.trajectory_adapter import (
        messages_to_trajectory_text,
    )

    messages = [
        {"role": "user", "content": "BRIEF_HEAD " + "x" * 80},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": '{"path": "slides.md", "content": "deck"}',
                    },
                }
            ],
        },
        {"role": "tool", "name": "write_file", "content": "ok"},
    ]
    full = messages_to_trajectory_text(messages, budget=None)
    assert "ACTION: write_file(" in full
    assert "slides.md" in full
    assert "BRIEF_HEAD" in full
    assert messages_to_trajectory_text(messages) == full
    cut = messages_to_trajectory_text(messages, budget=40)
    assert "write_file" in cut
    assert "BRIEF_HEAD" not in cut
