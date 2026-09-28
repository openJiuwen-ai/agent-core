"""Unit tests for programmatic markdown section merge."""

from __future__ import annotations

from openjiuwen.harness.personal_context.distill.merge import merge_markdown


def test_merge_no_existing_uses_candidate():
    assert merge_markdown("", "## A\n- new\n") == "## A\n- new"


def test_merge_no_candidate_keeps_existing():
    assert merge_markdown("## A\n- old\n", "") == "## A\n- old"


def test_merge_new_heading_appends():
    merged = merge_markdown("## 职责\n- 前端\n", "## 协作\n- 对齐\n")
    assert "## 职责" in merged
    assert "前端" in merged
    assert "## 协作" in merged
    assert "对齐" in merged


def test_merge_same_heading_same_body_keeps_existing():
    existing = "## 职责\n- 前端\n"
    merged = merge_markdown(existing, "## 职责\n- 前端\n")
    assert merged == "## 职责\n- 前端\n"
    assert "待确认" not in merged


def test_merge_conflict_goes_to_pending():
    existing = "## 职责\n- 前端\n"
    candidate = "## 职责\n- 后端\n## 协作\n- 对齐接口\n"
    merged = merge_markdown(existing, candidate)
    assert "## 职责" in merged
    assert "前端" in merged
    assert "## 协作" in merged
    assert "待确认" in merged
    assert "后端" in merged
    assert "### 职责" in merged
