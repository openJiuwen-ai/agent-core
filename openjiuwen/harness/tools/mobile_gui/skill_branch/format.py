# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Dict, Mapping


def format_planner_tool_message(
    skill_name: str,
    planner: Mapping[str, str],
    *,
    stage1_note: str = "",
) -> str:
    """Format branch planner output as the skill_tool ToolMessage body."""
    lines = [
        f"Skill consult: {skill_name}",
        f"Applicability: {planner.get('skill_applicability', 'unknown')}",
        f"Subgoal: {planner.get('subgoal', '')}",
        f"Plan: {planner.get('plan', '')}",
        f"Do not do: {planner.get('do_not_do', '')}",
        f"Fallback if no progress: {planner.get('fallback_if_no_progress', '')}",
        f"Expected state: {planner.get('expected_state', '')}",
        f"Completion scope: {planner.get('completion_scope', 'needs_verification')}",
    ]
    if stage1_note.strip():
        lines.insert(1, f"Visual selection: {stage1_note.strip()}")
    return "\n".join(lines)


def format_branch_failure_tool_message(
    skill_name: str,
    error: str,
    *,
    skill_excerpt: str = "",
    max_excerpt_chars: int = 800,
) -> str:
    """Fallback ToolMessage when the branch fails."""
    lines = [
        f"Skill consult: {skill_name}",
        f"Branch consult failed: {error}",
    ]
    excerpt = (skill_excerpt or "").strip()
    if excerpt:
        if len(excerpt) > max_excerpt_chars:
            excerpt = excerpt[: max_excerpt_chars - 3].rstrip() + "..."
        lines.append(f"Skill excerpt:\n{excerpt}")
    return "\n".join(lines)
