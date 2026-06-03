# coding: utf-8
"""Tests for skill_branch ToolMessage formatting helpers."""

from openjiuwen.harness.tools.mobile_gui.skill_branch.format import (
    format_branch_failure_tool_message,
    format_planner_tool_message,
)


def test_format_planner_tool_message_includes_all_planner_fields():
    body = format_planner_tool_message(
        "github-com",
        {
            "skill_applicability": "effective",
            "subgoal": "open repo",
            "plan": "Tap search.",
            "do_not_do": "Do not scroll aimlessly.",
            "fallback_if_no_progress": "Go back.",
            "expected_state": "Repo list visible.",
            "completion_scope": "local_only",
        },
        stage1_note="Layout reference from landing.png",
    )
    assert body.startswith("Skill consult: github-com")
    assert "Visual selection: Layout reference" in body
    assert "Applicability: effective" in body
    assert "Subgoal: open repo" in body
    assert "Completion scope: local_only" in body


def test_format_planner_tool_message_omits_empty_stage1_note():
    body = format_planner_tool_message(
        "demo",
        {
            "skill_applicability": "uncertain",
            "subgoal": "x",
            "plan": "y",
            "do_not_do": "z",
            "fallback_if_no_progress": "a",
            "expected_state": "b",
            "completion_scope": "needs_verification",
        },
    )
    assert "Visual selection:" not in body


def test_format_branch_failure_tool_message_includes_error():
    body = format_branch_failure_tool_message("demo", "Model timeout")
    assert "Skill consult: demo" in body
    assert "Branch consult failed: Model timeout" in body
    assert "Skill excerpt:" not in body


def test_format_branch_failure_tool_message_truncates_long_excerpt():
    long_skill = "x" * 1000
    body = format_branch_failure_tool_message(
        "demo",
        "Parse error",
        skill_excerpt=long_skill,
        max_excerpt_chars=100,
    )
    assert "Skill excerpt:" in body
    assert len(body) < len(long_skill)
    assert body.endswith("...")
