# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt strategy tests for auto-harness competitor research order."""

from pathlib import Path


_PROMPTS_DIR = (
    next(
        p
        for p in Path(__file__).resolve().parents
        if (p / "openjiuwen").is_dir()
    )
    / "openjiuwen"
    / "auto_harness"
    / "prompts"
)
_SKILLS_DIR = (
    next(
        p
        for p in Path(__file__).resolve().parents
        if (p / "openjiuwen").is_dir()
    )
    / "openjiuwen"
    / "auto_harness"
    / "skills"
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


def test_plan_prompt_and_skill_merge_dependent_tasks():
    """Plan guidance should keep dependent work inside one task/worktree."""
    prompt = (_PROMPTS_DIR / "plan.md").read_text(
        encoding="utf-8"
    )
    skill = (
        _SKILLS_DIR / "plan" / "SKILL.md"
    ).read_text(encoding="utf-8")
    for content in (prompt, skill):
        assert "直接依赖关系" in content or "直接代码依赖" in content
        assert "同一个 worktree" in content or "同一个 worktree 内" in content
        assert "不要拆成多个任务" in content
        assert "链式任务组" in content or "A -> B -> C" in content


def test_plan_prompt_and_skill_require_single_task_output():
    """Plan guidance should force one task per planning round."""
    prompt = (_PROMPTS_DIR / "plan.md").read_text(
        encoding="utf-8"
    )
    skill = (
        _SKILLS_DIR / "plan" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "本轮只输出 1 个任务" in prompt
    assert "数组中只能有 1 个任务对象" in prompt
    assert "本轮只允许输出 1 个 task" in skill
    assert "JSON 数组中只能有 1 个任务对象" in skill


def test_assess_and_plan_prompts_define_repo_edit_scope():
    """Assess/plan prompts should define the narrowed edit scope."""
    assess = (_PROMPTS_DIR / "assess.md").read_text(
        encoding="utf-8"
    )
    plan = (_PROMPTS_DIR / "plan.md").read_text(
        encoding="utf-8"
    )
    for content in (assess, plan):
        assert "`openjiuwen/harness/**`" in content
        assert "`openjiuwen/core/**`" in content
        assert "`openjiuwen/harness/cli/README.md`" in content
        assert "`tests/**`" in content
        assert "`examples/**`" in content
        assert "`docs/en/`" in content
        assert "`docs/zh/`" in content
        assert "`openjiuwen/auto_harness/**`" in content


def test_implement_skill_defines_repo_edit_scope():
    """Implement skill should explicitly forbid out-of-scope edits."""
    content = (
        _SKILLS_DIR / "implement" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "`openjiuwen/harness/**`" in content
    assert "`openjiuwen/core/**`" in content
    assert "`openjiuwen/harness/cli/README.md`" in content
    assert "`tests/**`" in content
    assert "`examples/**`" in content
    assert "`docs/en/`" in content
    assert "`docs/zh/`" in content
    assert "`openjiuwen/auto_harness/**`" in content
    assert "范围冲突" in content


def test_identity_prompt_describes_github_first_policy():
    """Identity prompt should define the GitHub-first research policy."""
    content = (_PROMPTS_DIR / "identity.md").read_text(
        encoding="utf-8"
    )
    assert "优先用 `gh` 查看官方仓库" in content
    assert "网页搜索只作补充核对" in content
