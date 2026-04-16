# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt strategy tests for auto-harness competitor research order."""

from pathlib import Path


_PROMPTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "openjiuwen"
    / "auto_harness"
    / "prompts"
)


def test_assess_prompt_prefers_github_before_web_search():
    """Assess prompt should require GitHub-first competitor research."""
    content = (_PROMPTS_DIR / "assess.md").read_text(
        encoding="utf-8"
    )
    assert "优先通过 bash 工具使用" in content
    assert "`gh repo view`" in content
    assert "`gh api`" in content
    assert "网页搜索和页面抓取作为补充" in content


def test_assess_prompt_avoids_commits_1_for_empty_snapshots():
    """Assess prompt should avoid unstable delta checks on readonly snapshots."""
    content = (_PROMPTS_DIR / "assess.md").read_text(
        encoding="utf-8"
    )
    assert "make check COMMITS=1" in content
    assert "不要运行" in content
    assert "No Python files selected" in content
    assert "uv run ruff check <files>" in content
    assert "uv run mypy <files>" in content


def test_plan_prompt_prefers_github_evidence_for_competitor_tasks():
    """Plan prompt should ask for GitHub evidence before web-only claims."""
    content = (_PROMPTS_DIR / "plan.md").read_text(
        encoding="utf-8"
    )
    assert "优先通过 bash 工具使用 `gh repo view`" in content
    assert "`gh api`" in content
    assert "网页搜索和页面抓取仅作补充" in content


def test_identity_prompt_describes_github_first_policy():
    """Identity prompt should define the GitHub-first research policy."""
    content = (_PROMPTS_DIR / "identity.md").read_text(
        encoding="utf-8"
    )
    assert "优先用 `gh` 查看官方仓库" in content
    assert "网页搜索只作补充核对" in content
