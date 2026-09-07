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
import inspect
from typing import Callable

import pytest

from openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer import (
    GENERATE_RECORDS_LLM_POLICY,
)
from openjiuwen.agent_evolving.signal import detect_tool_error_signals
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager
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
from openjiuwen.harness.rails.evolution.ttse.catalog import project_catalog
from openjiuwen.harness.rails.evolution.ttse.classify import parse_assignments
from openjiuwen.harness.rails.evolution.ttse.consult import render_consult_result
from openjiuwen.harness.rails.evolution.ttse.prompts import detect_judge_prompt
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

_POLICY = GENERATE_RECORDS_LLM_POLICY


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

    Production default is SignalBasedSuccessDetector + ``disk_catalog``. Existing
    rail tests rely on ``ttse_score`` / no-error defaults and pin
    ``legacy_system`` so classify LLM calls do not pollute induce/blame counts.
    """
    return TTSERail(
        llm=llm,
        model="dummy-model",
        ttse_config=cfg
        or TTSEConfig(store_path=str(tmp_path / "bank.json"), inject_mode="legacy_system"),
        success_detector=success_detector
        if success_detector is not None
        else TrajectoryErrorSuccessDetector(),
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


class _MockEmbedding:
    """Deterministic EmbeddingProvider for top-K injection tests."""

    def __init__(self):
        self.calls: list[str] = []

    async def embed_query(self, text: str):
        self.calls.append(text)
        # Axis-aligned vectors so cosine ranking is stable.
        table = {
            "login bug": [1.0, 0.0, 0.0],
            "relevant fact about login": [0.9, 0.1, 0.0],
            "unrelated weather tip": [0.0, 1.0, 0.0],
            "another login tip": [0.8, 0.2, 0.0],
        }
        return table.get(text, [0.0, 0.0, 1.0])

    async def embed_documents(self, texts: list[str]):
        return [await self.embed_query(t) for t in texts]


@pytest.mark.asyncio
async def test_injection_uses_top_k_when_config_embedding_set(tmp_path):
    provider = _MockEmbedding()
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        embedding=provider,
        top_k_facts=1,
        top_k_tips=1,
    )
    rail = TTSERail(llm=ScriptedLLM(lambda p: "NONE"), model="m", ttse_config=cfg)
    await rail._ttse_store.add_fact("relevant fact about login")
    await rail._ttse_store.add_fact("unrelated weather tip")
    await rail._ttse_store.add_tip("another login tip")

    body = await rail._resolve_injection_body("login bug")
    assert "relevant fact about login" in body
    assert "unrelated weather tip" not in body
    assert "another login tip" in body
    assert "login bug" in provider.calls


@pytest.mark.asyncio
async def test_constructor_embedding_syncs_to_config_and_enables_retrieval(tmp_path):
    provider = _MockEmbedding()
    cfg = TTSEConfig(store_path=str(tmp_path / "bank.json"), top_k_facts=1, top_k_tips=1)
    rail = TTSERail(
        llm=ScriptedLLM(lambda p: "NONE"),
        model="m",
        ttse_config=cfg,
        embedding=provider,
    )
    assert rail._ttse_config.embedding is provider
    assert rail._ttse_store.has_embedding_provider()
    await rail._ttse_store.add_fact("relevant fact about login")
    await rail._ttse_store.add_tip("another login tip")
    await rail._ttse_store.add_tip("unrelated weather tip")

    body = await rail._resolve_injection_body("login bug")
    assert "relevant fact about login" in body
    assert "another login tip" in body
    assert "unrelated weather tip" not in body


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
    cfg = TTSEConfig(
        store_path=str(tmp_path / "b.json"), batch_size=2, inject_mode="legacy_system"
    )
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
    cfg = TTSEConfig(
        store_path=str(tmp_path / "b.json"), batch_size=2, inject_mode="legacy_system"
    )
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
    cfg = TTSEConfig(
        store_path=str(tmp_path / "b.json"), batch_size=5, inject_mode="legacy_system"
    )
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
# SignalBasedSuccessDetector (default production detector)
# ----------------------------------------------------------------------


class _FakeSignalDetector:
    """Stand-in for ConversationSignalDetector returning fixed signal types."""

    def __init__(self, types):
        self._types = types

    def detect_trajectory_signals(self, trajectory, *, messages=None, signal_types=None):
        return [SimpleNamespace(signal_type=t) for t in self._types]


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
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg)
    out = await det.detect(None, _n_tool_messages(4), snapshot={"ttse_task_query": "q"})
    assert out.outcome == "skip"
    assert out.reason.startswith("gate:")
    assert llm.calls == []


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
async def test_signal_detector_artifact_paths_skip(tmp_path):
    llm = ScriptedLLM(lambda p: '{"outcome":"success","delivery":"answer","goals":[],"reason":"ok"}')
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg)
    out = await det.detect(
        None,
        _n_tool_messages(5, write_path="deck.pptx"),
        snapshot={"ttse_task_query": "make a ppt"},
    )
    assert out.outcome == "skip"
    assert out.reason.startswith("artifact_paths:")
    assert llm.calls == []


@pytest.mark.asyncio
async def test_signal_detector_reply_judge_once(tmp_path):
    llm = ScriptedLLM(
        lambda p: '{"goals":["answer"],"delivery":"answer","outcome":"success","reason":"complete"}'
    )
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg)
    out = await det.detect(
        None,
        _n_tool_messages(5),
        snapshot={"ttse_task_query": "what is 2+2?"},
    )
    assert out.outcome == "success"
    assert len(llm.calls) == 1
    assert "what is 2+2?" in llm.calls[0]
    assert "Here is the answer." in llm.calls[0]
    assert "OBSERVATION" not in llm.calls[0]


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
async def test_signal_detector_does_not_call_user_intent(tmp_path, monkeypatch):
    calls = {"intent": 0}

    async def _boom(*args, **kwargs):
        calls["intent"] += 1
        raise AssertionError("detect_user_intent must not be called")

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.signal.from_conv.ConversationSignalDetector.detect_user_intent",
        _boom,
    )
    llm = ScriptedLLM(
        lambda p: '{"goals":[],"delivery":"answer","outcome":"success","reason":"ok"}'
    )
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    det = SignalBasedSuccessDetector(llm=llm, model="m", config=cfg)
    messages = _n_tool_messages(5)
    messages.insert(1, {"role": "user", "content": "你做错了，重新来"})
    out = await det.detect(None, messages, snapshot={"ttse_task_query": "q"})
    assert out.outcome == "success"
    assert calls["intent"] == 0


@pytest.mark.asyncio
async def test_rail_skip_does_not_induce(tmp_path):
    llm = ScriptedLLM(lambda p: "[FACT] should not induce")
    cfg = TTSEConfig(store_path=str(tmp_path / "b.json"), detect_min_tool_calls=5)
    rail = TTSERail(llm=llm, model="m", ttse_config=cfg)  # default SignalBasedSuccessDetector
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
    rail = TTSERail(llm=llm, model="m", ttse_config=cfg)
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
# disk_catalog: default inject_mode (guidance section, post-write classify, consult)
# ----------------------------------------------------------------------


def test_default_inject_mode_is_disk_catalog():
    cfg = TTSEConfig()
    assert cfg.inject_mode == "disk_catalog"
    assert cfg.is_disk_catalog() is True


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
    assert "无参" in text
    assert text.strip() == DISK_CATALOG_GUIDANCE_CN.strip()
    assert "PresentBench grades slides.md" not in text
    assert "documents-office-and-records" not in text


@pytest.mark.asyncio
async def test_legacy_mode_does_not_classify_after_induce(tmp_path):
    llm = ScriptedLLM(lambda p: "[FACT] success fact" if "extracting" in p else "NONE")
    rail = _make_rail(
        tmp_path,
        llm,
        cfg=TTSEConfig(store_path=str(tmp_path / "bank.json"), inject_mode="legacy_system"),
    )
    snap = {
        "messages": [{"role": "user", "content": "do task"}, {"role": "assistant", "content": "done"}],
        "ttse_capabilities": "- grep",
        "ttse_task_query": "do task",
    }
    await rail._run_ttse_induction(None, ctx=None, snapshot=snap)
    assert len(llm.calls) == 1
    assert "category" not in rail._ttse_store.facts[0]


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
