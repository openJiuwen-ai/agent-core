# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the Reflect LLM de-duplication substrate.

De-duplication of already-adopted experiences is fully delegated to the
Reflect LLM: the prompt injects the current document plus explicit rules
forbidding restated rules or re-proposed covered failures. These tests pin
the full substrate of that mechanism: the prompt template, its rendering,
the LLM payload parsing that turns raw output into edits, and the failure
blocks the optimizer reads.
"""

from __future__ import annotations

from openjiuwen.agent_evolving.skill_train.sleep.model_backend import (
    _edits_from_payload,
    _format_failure_block,
)
from openjiuwen.agent_evolving.skill_train.sleep.prompts import DEFAULTS, render
from openjiuwen.agent_evolving.skill_train.sleep.types import ReplayResult, TaskRecord


def _task(**overrides) -> TaskRecord:
    base = {
        "id": "t1",
        "project": "demo",
        "intent": "查询杭州天气",
        "reference": "",
    }
    base.update(overrides)
    return TaskRecord(**base)


# ---------------------------------------------------------------------------
# Prompt template: duplication rules
# ---------------------------------------------------------------------------


def test_reflect_prompt_contains_duplication_rules() -> None:
    reflect = DEFAULTS["reflect"]
    assert "Duplication rules" in reflect
    assert "already covers a failure" in reflect
    assert "synonymous with" in reflect


def test_reflect_prompt_places_duplication_rules_before_current_doc() -> None:
    reflect = DEFAULTS["reflect"]
    lang = reflect.index("Language and anchoring rules")
    dup = reflect.index("Duplication rules")
    doc = reflect.index("# Current __TARGET__")
    failures = reflect.index("# Recurring failures")
    assert lang < dup < doc < failures


def test_reflect_prompt_keeps_failure_contract_placeholders() -> None:
    reflect = DEFAULTS["reflect"]
    for placeholder in (
        "__EDIT_BUDGET__",
        "__TARGET__",
        "__CUR_DOC__",
        "__PREFS__",
        "__FAILURES__",
    ):
        assert placeholder in reflect


def test_defaults_expose_all_sleep_prompts() -> None:
    assert set(DEFAULTS) == {"attempt", "judge", "reflect", "rubric"}
    assert "__SKILL__" in DEFAULTS["attempt"]
    assert "__RUBRIC__" in DEFAULTS["judge"]
    assert "__FOLLOW_UPS__" in DEFAULTS["rubric"]


def test_render_substitutes_all_fields() -> None:
    rendered = render(
        "reflect",
        {
            "__EDIT_BUDGET__": "4",
            "__TARGET__": "skill",
            "__CUR_DOC__": "# Doc\n- rule",
            "__PREFS__": "",
            "__FAILURES__": "- intent: 查询杭州天气",
        },
    )
    assert "Propose at most 4 bounded edits" in rendered
    assert "# Current skill" in rendered
    assert "# Doc\n- rule" in rendered
    assert "- intent: 查询杭州天气" in rendered
    assert "__EDIT_BUDGET__" not in rendered
    assert "__CUR_DOC__" not in rendered
    assert "__FAILURES__" not in rendered


def test_render_keeps_unknown_placeholders_untouched() -> None:
    rendered = render("reflect", {"__CUR_DOC__": "# Doc"})
    # 未提供的占位符保留原样，便于上层发现配置缺口
    assert "__FAILURES__" in rendered


# ---------------------------------------------------------------------------
# LLM payload parsing: _edits_from_payload
# ---------------------------------------------------------------------------


def test_edits_from_payload_parses_plain_json_array() -> None:
    raw = (
        '[{"op": "add", "content": "Rule-A", "anchor": "", "rationale": "why"},'
        '{"op": "replace", "content": "Rule-B", "anchor": "- 旧规则", "rationale": "fix"}]'
    )
    edits = _edits_from_payload(raw, budget=4, target="skill")
    assert len(edits) == 2
    assert edits[0].op == "add"
    assert edits[0].content == "Rule-A"
    assert edits[0].target == "skill"
    assert edits[1].op == "replace"
    assert edits[1].anchor == "- 旧规则"


def test_edits_from_payload_parses_dict_with_edits_key() -> None:
    raw = '{"edits": [{"op": "delete", "content": "", "anchor": "- 旧规则"}]}'
    edits = _edits_from_payload(raw, budget=4, target="skill")
    assert len(edits) == 1
    assert edits[0].op == "delete"
    assert edits[0].content == ""


def test_edits_from_payload_applies_missing_field_defaults() -> None:
    raw = '[{"content": "Rule-A"}, {"op": "add"}]'
    edits = _edits_from_payload(raw, budget=4, target="skill")
    assert len(edits) == 2
    assert edits[0].op == "add"
    assert edits[0].anchor == ""
    assert edits[0].rationale == ""
    assert edits[1].content == ""


def test_edits_from_payload_clamps_to_budget() -> None:
    raw = '[{"content": "a"}, {"content": "b"}, {"content": "c"}]'
    edits = _edits_from_payload(raw, budget=2, target="skill")
    assert [edit.content for edit in edits] == ["a", "b"]
    assert _edits_from_payload(raw, budget=0, target="skill") == []


def test_edits_from_payload_skips_junk_and_non_lists() -> None:
    junk = '[{"content": "a"}, 42, "text", null, {"content": "b"}]'
    edits = _edits_from_payload(junk, budget=8, target="skill")
    assert [edit.content for edit in edits] == ["a", "b"]
    assert _edits_from_payload("not json at all", budget=4, target="skill") == []
    assert _edits_from_payload("null", budget=4, target="skill") == []
    assert _edits_from_payload('{"unexpected": true}', budget=4, target="skill") == []


def test_edits_from_payload_budget_counts_junk_entries() -> None:
    # 切片先于过滤：垃圾项也消耗预算名额（items[:budget] 先生效）
    junk = '[{"content": "a"}, 42, "text", null, {"content": "b"}]'
    edits = _edits_from_payload(junk, budget=4, target="skill")
    assert [edit.content for edit in edits] == ["a"]


# ---------------------------------------------------------------------------
# Failure blocks: what the optimizer reads
# ---------------------------------------------------------------------------


def test_format_failure_block_includes_core_lines() -> None:
    task = _task(reference="回答必须包含AQI")
    result = ReplayResult(
        id="t1",
        hard=0.0,
        response="杭州今天晴",
        fail_reason="缺少空气质量数据",
    )
    block = _format_failure_block(task, result)
    assert "- intent: 查询杭州天气" in block
    assert "rubric: 回答必须包含AQI" in block
    assert "response: 杭州今天晴" in block
    assert "why: 缺少空气质量数据" in block


def test_format_failure_block_prefers_fail_reason_over_rationale() -> None:
    task = _task()
    result = ReplayResult(
        id="t1",
        hard=0.0,
        fail_reason="primary reason",
        judge_rationale="secondary reason",
    )
    block = _format_failure_block(task, result)
    assert "why: primary reason" in block
    assert "secondary reason" not in block


def test_format_failure_block_truncates_long_rubric() -> None:
    long_reference = "要" * 500
    task = _task(reference=long_reference)
    result = ReplayResult(id="t1", hard=0.0, response="", fail_reason="x")
    block = _format_failure_block(task, result)
    assert "rubric: " + "要" * 400 in block
    assert "要" * 401 not in block


def test_format_failure_block_truncates_long_response() -> None:
    task = _task()
    long_response = "R" * 500
    result = ReplayResult(id="t1", hard=0.0, response=long_response, fail_reason="")
    block = _format_failure_block(task, result)
    assert "response: " + "R" * 300 in block
    assert "R" * 301 not in block
