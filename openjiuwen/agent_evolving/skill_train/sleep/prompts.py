# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt templates for sleep attempt / judge / reflect."""

from __future__ import annotations

from typing import Dict


_ATTEMPT = (
    "Complete the following task for the user. Follow the skill and memory "
    "guidance below, including any output-format and length requirements.\n\n"
    "# Skill\n__SKILL__\n\n# Memory\n__MEMORY__\n\n"
    "# Task\n__INTENT__\n\n__CONTEXT__\n\n"
    "Return ONLY the final answer text, nothing else."
)

_JUDGE = (
    "Score how well the response satisfies the rubric, 0..1. "
    'Return ONLY JSON {"score": <0..1>, "reason": "..."}.\n\n'
    "Grading notes:\n"
    "- Check the rubric item by item; the response does NOT need to repeat the rubric text.\n"
    "- The task is the user's request; the rubric lists what a good answer must satisfy.\n"
    "- For live data you cannot verify (weather, prices, ...), only check that the requested "
    "fields are present and well-formed, not their factual values.\n\n"
    "# Task\n__INTENT__\n\n# Rubric\n__RUBRIC__\n\n# Response\n__RESPONSE__"
)

_RUBRIC = (
    "You write grading rubrics for replayed agent tasks. Return ONLY JSON "
    '{"rubric": ["<check 1>", "<check 2>", ...]}.\n\n'
    "Turn the user's request and the follow-ups they raised in the same session into "
    "3 to 6 short, checkable requirements a good answer must satisfy. Rules:\n"
    "- Write in the same language as the user.\n"
    "- Only use facts and requirements that appear in the conversation; never invent new ones.\n"
    "- Each follow-up correction or question must map to at least one requirement.\n"
    "- Do not require specific live values (temperatures, prices); require the field/format instead.\n\n"
    "# User request\n__INTENT__\n\n# Follow-ups from the same session\n__FOLLOW_UPS__\n\n"
    "# Agent's original reply\n__ATTEMPTED__\n\n# Heuristic rubric (baseline)\n__HEURISTIC__"
)

_REFLECT = (
    "You are the skill_train optimizer. The agent keeps failing the recurring "
    "tasks below. Propose at most __EDIT_BUDGET__ bounded edits to the "
    "__TARGET__ document so it stops failing. Each edit MUST be a short, "
    "GENERAL, reusable rule or preference (never task-specific).\n"
    'Return ONLY a JSON array: '
    '[{"op":"add|replace|delete","content":"<rule>","anchor":"<optional>","rationale":"<why>"}].\n\n'
    "# Current __TARGET__\n__CUR_DOC__\n"
    "__PREFS__\n\n"
    "# Recurring failures\n__FAILURES__"
)

DEFAULTS: Dict[str, str] = {
    "attempt": _ATTEMPT,
    "judge": _JUDGE,
    "reflect": _REFLECT,
    "rubric": _RUBRIC,
}


def render(name: str, mapping: Dict[str, str]) -> str:
    text = DEFAULTS[name]
    for key, value in mapping.items():
        text = text.replace(key, value)
    return text
