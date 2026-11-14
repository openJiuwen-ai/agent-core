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
    _supplement_exclude_deletes,
)
from openjiuwen.agent_evolving.skill_train.sleep.prompts import DEFAULTS, render
from openjiuwen.agent_evolving.skill_train.sleep.types import (
    EditRecord,
    ReplayResult,
    TaskRecord,
)


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


def test_reflect_prompt_prefers_delete_for_exclude_corrections() -> None:
    reflect = DEFAULTS["reflect"]
    assert "Op selection (match the user's correction intent)" in reflect
    assert "correction: exclude|add|replace" in reflect
    assert "MUST `delete`" in reflect
    assert "when the user asks to delete/omit" in reflect


def test_reflect_prompt_places_duplication_rules_before_current_doc() -> None:
    reflect = DEFAULTS["reflect"]
    lang = reflect.index("Language and anchoring rules")
    ops = reflect.index("Op selection (match the user's correction intent)")
    dup = reflect.index("Duplication rules")
    doc = reflect.index("# Current __TARGET__")
    failures = reflect.index("# Recurring failures")
    assert lang < ops < dup < doc < failures


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


def test_format_failure_block_includes_exclude_correction_labels() -> None:
    task = _task(
        reference="用户任务：杭州天气\n- 必须不包含：空气质量数据",
        tags=["correction:exclude", "exclude:空气质量数据"],
    )
    result = ReplayResult(id="t1", hard=0.0, response="AQI 62", fail_reason="仍含空气质量")
    block = _format_failure_block(task, result)
    assert "correction: exclude" in block
    assert "exclude_target: 空气质量数据" in block


def test_supplement_exclude_deletes_drops_meta_add_and_adds_delete() -> None:
    skill = (
        "# 天气\n\n"
        "- 用户点名\"空气质量数据\"时，最终回复必须出现 AQI\n"
        "- 回复前自检：空气质量必须出现\n"
        "- 缺数据时重新查询\n"
    )
    task = _task(
        reference="- 必须不包含：空气质量数据",
        tags=["correction:exclude", "exclude:空气质量数据"],
    )
    result = ReplayResult(id="t1", hard=0.0, fail_reason="仍含空气质量")
    edits = [
        EditRecord(
            target="skill",
            op="add",
            content="### 用户明确要求删除字段时必须完全省略\n- 当用户要求删除空气质量时……",
        ),
        EditRecord(target="skill", op="add", content="网络失败时仍须降级重试"),
    ]
    out = _supplement_exclude_deletes(
        skill,
        [(task, result)],
        edits,
        budget=4,
        target="skill",
    )
    assert all(
        not (
            (e.op or "").lower() == "add"
            and "用户明确要求删除" in (e.content or "")
        )
        for e in out
    )
    deletes = [e for e in out if (e.op or "").lower() == "delete"]
    assert deletes
    assert any("空气质量" in (e.anchor or "") for e in deletes)
    assert any((e.op or "").lower() == "add" and "降级重试" in (e.content or "") for e in out)


def test_supplement_exclude_deletes_fills_remaining_targets_when_model_partial() -> None:
    """Model delete for one exclude must not skip synthesizing the others."""
    skill = (
        "# 天气\n\n"
        "- 用户点名\"空气质量数据\"时，最终回复必须出现 AQI\n"
        "- 用户点名\"UV指数数据\"时，最终回复必须出现 UV\n"
        "- 回复前自检：空气质量与 UV 都必须出现\n"
    )
    task = _task(
        reference="- 必须不包含：空气质量数据\n- 必须不包含：UV指数数据",
        tags=[
            "correction:exclude",
            "exclude:空气质量数据",
            "exclude:UV指数数据",
        ],
    )
    result = ReplayResult(id="t1", hard=0.0, fail_reason="仍含排除字段")
    model_delete = EditRecord(
        target="skill",
        op="delete",
        content='- 用户点名"空气质量数据"时，最终回复必须出现 AQI',
        anchor='- 用户点名"空气质量数据"时，最终回复必须出现 AQI',
        rationale="exclude:空气质量数据",
    )
    out = _supplement_exclude_deletes(
        skill,
        [(task, result)],
        [model_delete, EditRecord(target="skill", op="add", content="无关补充")],
        budget=6,
        target="skill",
    )
    deletes = [e for e in out if (e.op or "").lower() == "delete"]
    assert any("空气质量" in (e.anchor or "") for e in deletes)
    assert any("UV" in (e.anchor or "") for e in deletes)
    assert len(deletes) >= 2


def test_supplement_exclude_deletes_prioritizes_deletes_within_budget() -> None:
    skill = (
        "# 天气\n\n"
        "- 用户点名\"空气质量数据\"时，最终回复必须出现 AQI\n"
        "- 回复前自检：空气质量必须出现\n"
    )
    task = _task(
        reference="- 必须不包含：空气质量数据",
        tags=["correction:exclude", "exclude:空气质量数据"],
    )
    result = ReplayResult(id="t1", hard=0.0, fail_reason="仍含空气质量")
    edits = [
        EditRecord(target="skill", op="replace", content="仅当用户点名时输出空气质量", anchor="旧"),
        EditRecord(target="skill", op="replace", content="自检排除空气质量", anchor="旧2"),
        EditRecord(target="skill", op="add", content="网络失败时仍须降级重试"),
        EditRecord(target="skill", op="add", content="另一条无关规则"),
    ]
    out = _supplement_exclude_deletes(
        skill,
        [(task, result)],
        edits,
        budget=3,
        target="skill",
    )
    assert out
    assert (out[0].op or "").lower() == "delete"
    assert sum(1 for e in out if (e.op or "").lower() == "delete") >= 1
    assert len(out) <= 3


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
