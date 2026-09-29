# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TTSE induction, success detect, catalog, and adapters."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Callable

import pytest

from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.ttse.capabilities import (
    BASIC_TOOLS,
    list_capability_names,
    parse_capability_names_from_text,
    render_capabilities,
)
from openjiuwen.agent_evolving.ttse.catalog import project_catalog, render_catalog_markdown
from openjiuwen.agent_evolving.ttse.categories import OTHER_CATEGORY
from openjiuwen.agent_evolving.ttse.classify import classify_rules, parse_assignments
from openjiuwen.agent_evolving.ttse.config import TTSEConfig
from openjiuwen.agent_evolving.ttse.induction import (
    blame,
    induce,
    induce_batch,
    parse_reason,
    parse_rules,
    parse_synthesis,
    parse_verdict,
    synthesize,
)
from openjiuwen.agent_evolving.ttse.stores import TTSERecordStore, _new_record
from openjiuwen.agent_evolving.ttse.success import (
    SignalBasedSuccessDetector,
    _outcome_from_judge,
    _parse_judge_json,
)
from openjiuwen.agent_evolving.ttse.trajectory_adapter import (
    count_tool_calls,
    extract_final_reply,
    extract_output_paths,
    messages_to_trajectory_text,
)

_POLICY = LLMInvokePolicy(attempt_timeout_secs=5, total_budget_secs=10, max_attempts=1)


class ScriptedLLM:
    def __init__(self, handler: Callable[[str], object]):
        self.handler = handler
        self.calls: list[str] = []

    async def invoke(self, *, model, messages, temperature=None, timeout=None, **kwargs):
        prompt = messages[0]["content"] if messages else ""
        self.calls.append(prompt)
        return self.handler(prompt)


class QuietSignals:
    def detect_trajectory_signals(self, *args, **kwargs):
        return []

    async def detect_user_intent(self, messages):
        return []


class ExecutionFailureSignals(QuietSignals):
    def detect_trajectory_signals(self, *args, **kwargs):
        return [SimpleNamespace(signal_type="execution_failure")]


def _tool_msg(name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"name": name, "arguments": args}],
    }


# ----------------------------------------------------------------------
# induction parse + LLM wrappers
# ----------------------------------------------------------------------


def test_parse_rules_extracts_fact_and_tip_edge_cases():
    facts, tips = parse_rules(
        "preamble\n"
        "[FACT] disk is NTFS\n"
        "[fact]: also lowercase\n"
        "[FACT] NONE\n"
        "[TIP] When compiling: use cl /utf-8\n"
        "[TIP]:\n"
        "[TIP] NONE\n"
        "junk line\n"
        "NONE\n"
    )
    assert facts == ["disk is NTFS", "also lowercase"]
    assert tips == ["When compiling: use cl /utf-8"]
    assert parse_rules("") == ([], [])
    assert parse_rules("NONE") == ([], [])
    assert parse_rules("none") == ([], [])


def test_parse_verdict_reason_and_synthesis():
    assert parse_verdict("REASON: stale\nVERDICT: 2", 3) == 2
    assert parse_verdict("VERDICT: NONE", 3) is None
    assert parse_verdict("VERDICT: 9", 3) is None
    assert parse_verdict("", 3) is None
    assert parse_reason("VERDICT: 1\nREASON: contradicted by tool output") == "contradicted by tool output"
    assert parse_synthesis("NONE") is None
    assert parse_synthesis("[TIP] When PDFs fail: use python_exec") == "When PDFs fail: use python_exec"


@pytest.mark.asyncio
async def test_induce_and_induce_batch_parse_scripted_llm():
    llm = ScriptedLLM(lambda _prompt: "[FACT] workspace uses utf-8\n[TIP] When encoding: use cl /utf-8")
    facts, tips = await induce(
        llm=llm,
        model="m",
        policy=_POLICY,
        task_prompt="compile",
        traj_text="USER: build\nACTION: bash()",
        capabilities="- tool `bash`",
        existing_facts=[],
        existing_tips=[],
    )
    assert facts == ["workspace uses utf-8"]
    assert tips == ["When encoding: use cl /utf-8"]
    assert llm.calls

    batch_llm = ScriptedLLM(lambda _prompt: "[FACT] batch fact")
    facts, tips = await induce_batch(
        llm=batch_llm,
        model="m",
        policy=_POLICY,
        group=[
            {
                "task_id": "t1",
                "task_prompt": "q",
                "traj_text": "USER: hi",
                "outcome": "success",
            }
        ],
        capabilities="",
        existing_facts=["old"],
        existing_tips=[],
    )
    assert facts == ["batch fact"]
    assert tips == []


@pytest.mark.asyncio
async def test_blame_and_synthesize_scripted_llm():
    blamed = await blame(
        llm=ScriptedLLM(lambda _p: "REASON: stale path\nVERDICT: 1"),
        model="m",
        policy=_POLICY,
        task_prompt="task",
        traj_text="fail",
        rules_numbered="1. [FACT] old path",
        n_rules=1,
    )
    assert blamed == (1, "stale path")
    assert await blame(
        llm=ScriptedLLM(lambda _p: "VERDICT: 1"),
        model="m",
        policy=_POLICY,
        task_prompt="task",
        traj_text="fail",
        rules_numbered="",
        n_rules=1,
    ) == (None, "no active rules during this task")

    tip = await synthesize(
        llm=ScriptedLLM(lambda _p: "[TIP] When both match: keep the newer path"),
        model="m",
        policy=_POLICY,
        rules_numbered="1. a\n2. b",
        capabilities="",
    )
    assert tip == "When both match: keep the newer path"
    assert (
        await synthesize(
            llm=ScriptedLLM(lambda _p: "[TIP] x"),
            model="m",
            policy=_POLICY,
            rules_numbered="",
            capabilities="",
        )
        is None
    )


# ----------------------------------------------------------------------
# success detect / judge JSON
# ----------------------------------------------------------------------


def test_parse_judge_json_fenced_braced_and_raw():
    raw = _parse_judge_json('{"outcome": "success", "reason": "raw"}')
    assert raw == {"outcome": "success", "reason": "raw"}

    fenced = _parse_judge_json(
        "here you go\n```json\n{\"outcome\": \"partial\", \"reason\": \"fenced\"}\n```\n"
    )
    assert fenced == {"outcome": "partial", "reason": "fenced"}

    braced = _parse_judge_json('preamble {"outcome": "fail", "reason": "braced"} trailing')
    assert braced == {"outcome": "fail", "reason": "braced"}

    assert _parse_judge_json("") is None
    assert _parse_judge_json("not json") is None
    assert _parse_judge_json("[1, 2]") is None


def test_outcome_from_judge_maps_scores():
    assert _outcome_from_judge({"outcome": "SUCCESS", "reason": "done"}).outcome == "success"
    assert _outcome_from_judge({"outcome": "partial"}).score == 0.5
    assert _outcome_from_judge({"outcome": "fail"}).score == 0.0
    assert _outcome_from_judge({"outcome": "skip"}) is None


@pytest.mark.asyncio
async def test_signal_detector_explicit_score_and_gates():
    cfg = TTSEConfig(detect_min_tool_calls=1, detect_llm_policy=_POLICY)
    detector = SignalBasedSuccessDetector(
        llm=ScriptedLLM(lambda _p: '{"outcome": "success", "reason": "ok"}'),
        model="m",
        config=cfg,
        signal_detector=QuietSignals(),
    )
    scored = await detector.detect([], [], snapshot={"ttse_score": 1.0})
    assert scored.outcome == "success"
    assert scored.reason == "explicit-score"

    failed = SignalBasedSuccessDetector(
        llm=ScriptedLLM(lambda _p: "{}"),
        model="m",
        config=cfg,
        signal_detector=ExecutionFailureSignals(),
    )
    out = await failed.detect([], [{"role": "user", "content": "hi"}])
    assert out.outcome == "partial"
    assert out.reason == "signal:execution_failure"

    skipped = await detector.detect([], [{"role": "user", "content": "hi"}])
    assert skipped.outcome == "skip"
    assert "tool_calls" in skipped.reason

    judged = await detector.detect(
        [],
        [
            {"role": "user", "content": "write slides"},
            _tool_msg("write_file", {"path": "slides.md"}),
            {"role": "assistant", "content": "done"},
        ],
    )
    assert judged.outcome == "success"
    assert judged.reason.startswith("judge:")


@pytest.mark.asyncio
async def test_signal_detector_bad_judge_json_skips():
    cfg = TTSEConfig(detect_min_tool_calls=1, detect_llm_policy=_POLICY)
    detector = SignalBasedSuccessDetector(
        llm=ScriptedLLM(lambda _p: "I cannot produce JSON"),
        model="m",
        config=cfg,
        signal_detector=QuietSignals(),
    )
    out = await detector.detect([], [_tool_msg("bash", {"cmd": "ls"}), {"role": "assistant", "content": "ok"}])
    assert out.outcome == "skip"
    assert out.reason == "judge_bad_json"


# ----------------------------------------------------------------------
# capabilities
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_and_list_capabilities_live_and_fallback():
    fallback = await render_capabilities(None)
    assert "BASIC TOOLS" in fallback
    assert "`bash`" in fallback
    names = await list_capability_names(None)
    assert names == {name for name, _ in BASIC_TOOLS}

    class Ability:
        async def list_tool_info(self):
            return [{"name": "custom_tool", "description": "does stuff"}]

    class Skills:
        def get_all(self):
            return [{"name": "office", "description": "docs"}]

    agent = SimpleNamespace(ability_manager=Ability(), skill_manager=Skills())
    rendered = await render_capabilities(agent)
    assert "`office`" in rendered
    assert "`custom_tool`" in rendered
    live = await list_capability_names(agent)
    assert live == {"office", "custom_tool"}


def test_parse_capability_names_from_text():
    names = parse_capability_names_from_text("- skill `office`: docs\n- tool `bash`: shell")
    assert names == {"office", "bash"}
    assert parse_capability_names_from_text("") == {name for name, _ in BASIC_TOOLS}
    assert parse_capability_names_from_text("no backticks here") == {name for name, _ in BASIC_TOOLS}


# ----------------------------------------------------------------------
# classify
# ----------------------------------------------------------------------


def test_parse_assignments_fenced_json_and_unknown_to_other():
    items = [("slides.md is the source", "fact"), ("When editing: use edit_file", "tip")]
    parsed = parse_assignments(
        '```json\n{"assignments": {"1": "documents-office-and-records", "2": "not-a-real-id"}}\n```',
        items,
    )
    assert parsed[0] == ("slides.md is the source", "fact", "documents-office-and-records")
    assert parsed[1] == ("When editing: use edit_file", "tip", OTHER_CATEGORY)

    missing = parse_assignments("not json", items)
    assert missing == [
        ("slides.md is the source", "fact", OTHER_CATEGORY),
        ("When editing: use edit_file", "tip", OTHER_CATEGORY),
    ]


@pytest.mark.asyncio
async def test_classify_rules_uses_llm_and_swallows_failure():
    ok = await classify_rules(
        llm=ScriptedLLM(lambda _p: '{"assignments": {"1": "software-engineering-devops"}}'),
        model="m",
        policy=_POLICY,
        items=[("use pytest", "tip")],
    )
    assert ok == [("use pytest", "tip", "software-engineering-devops")]

    class BoomLLM:
        async def invoke(self, **kwargs):
            raise RuntimeError("llm down")

    failed = await classify_rules(
        llm=BoomLLM(),
        model="m",
        policy=_POLICY,
        items=[("use pytest", "tip")],
    )
    assert failed == [("use pytest", "tip", OTHER_CATEGORY)]
    assert await classify_rules(llm=ScriptedLLM(lambda _p: "{}"), model="m", policy=_POLICY, items=[]) == []


# ----------------------------------------------------------------------
# catalog
# ----------------------------------------------------------------------


def test_render_catalog_markdown_empty_and_nonzero():
    empty = render_catalog_markdown({})
    assert "(empty)" in empty
    listed = render_catalog_markdown({"documents-office-and-records": 2, "other": 0})
    assert "`documents-office-and-records`" in listed
    assert "other" not in listed


def test_project_catalog_writes_markdown_and_by_cat(tmp_path):
    path = tmp_path / "bank.json"
    store = TTSERecordStore(TTSEConfig(store_path=str(path)))
    rec = _new_record("slides.md is the source")
    rec["category"] = "documents-office-and-records"
    store.facts = [rec]
    project_catalog(store)
    catalog = (tmp_path / "CATALOG.md").read_text(encoding="utf-8")
    assert "`documents-office-and-records`" in catalog
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["counts"]["documents-office-and-records"] == 1
    summary = (tmp_path / "by_cat" / "documents-office-and-records" / "SUMMARY.md").read_text(encoding="utf-8")
    assert "slides.md is the source" in summary


# ----------------------------------------------------------------------
# trajectory adapter
# ----------------------------------------------------------------------


def test_messages_to_trajectory_text_filters_system_and_truncates_tail():
    messages = [
        {"role": "system", "content": "secret scaffold"},
        {"role": "user", "content": "AAA-user-head"},
        {
            "role": "assistant",
            "content": "BBB-thought",
            "tool_calls": [{"name": "bash", "arguments": {"cmd": "ls"}}],
        },
        {"role": "tool", "name": "bash", "content": "CCC-obs"},
    ]
    text = messages_to_trajectory_text(messages)
    assert "secret scaffold" not in text
    assert "USER: AAA-user-head" in text
    assert "THOUGHT: BBB-thought" in text
    assert "ACTION: bash" in text
    assert "OBSERVATION [bash]: CCC-obs" in text

    tiny = messages_to_trajectory_text(messages, budget=18)
    assert tiny == text[-18:]
    assert "AAA-user-head" not in tiny


def test_count_tool_calls_and_extract_output_paths_and_final_reply():
    messages = [
        {"role": "user", "content": "make slides"},
        {
            "role": "assistant",
            "tool_calls": [
                {"name": "write_file", "arguments": '{"path": "slides.md"}'},
                {"function": {"name": "edit_file", "arguments": {"file_path": "slides.md"}}},
                {"name": "bash", "arguments": {"cmd": "ls"}},
            ],
        },
        {"role": "assistant", "content": "wrote slides.md and we are done here"},
    ]
    assert count_tool_calls(messages) == 3
    assert extract_output_paths(messages) == ["slides.md"]
    assert extract_output_paths(messages, max_paths=1) == ["slides.md"]
    assert extract_final_reply(messages, max_chars=11) == "wrote slide"
    assert extract_final_reply([_tool_msg("bash", {"cmd": "ls"})]) == ""
    assert count_tool_calls([]) == 0
