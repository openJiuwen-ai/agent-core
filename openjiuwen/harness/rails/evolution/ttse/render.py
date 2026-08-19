# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Render the FACT/TIP bank into a system-prompt section.

Ported from the reference ``render.py``; the only difference is the input is a
plain list (provided by :class:`TTSERecordStore`) instead of a ``Bank`` object,
and the output feeds a :class:`PromptSection` rather than being written to a
markdown file.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple, Union

RuleRecord = dict
RuleInput = Union[RuleRecord, str]


def _coerce(rules: Optional[Sequence[RuleInput]]) -> List[RuleRecord]:
    out: List[RuleRecord] = []
    for r in rules or []:
        if isinstance(r, str):
            out.append({"text": r, "count": 1})
        elif isinstance(r, dict):
            out.append({"text": r.get("text", ""), "count": r.get("count", 1)})
    return [r for r in out if r["text"]]


def _sorted(rules: List[RuleRecord]) -> List[RuleRecord]:
    return sorted(rules, key=lambda x: -x.get("count", 0))


def render_facts_md(facts: Optional[Sequence[RuleInput]], *, retrieved: bool = False) -> str:
    use = _sorted(_coerce(facts))
    if not use:
        return ""
    header = (
        "# Environment Facts",
        "",
        (
            "The most relevant confirmed observations for THIS task (retrieved from the full bank). Treat as true."
            if retrieved
            else "Confirmed observations about this benchmark environment, learned from prior tasks. Treat as true."
        ),
        "",
    )
    lines = list(header)
    for i, f in enumerate(use, 1):
        lines.append(f"{i}. {f['text']}")
    return "\n".join(lines) + "\n"


def render_tips_md(
    tips: Optional[Sequence[RuleInput]],
    *,
    retrieved: bool = False,
    capabilities_md: str = "",
) -> str:
    use = _sorted(_coerce(tips))
    lines = [
        "# Task Tactics",
        "",
        "Conditional tactics learned from prior tasks. When a condition matches your task, follow the action.",
        "Each tactic names a capability - a skill (load its SKILL.md with the read tool when the description"
        " matches) or a basic tool (always available).",
        "",
    ]
    if capabilities_md:
        lines += ["## Available capabilities", "", capabilities_md, ""]
    if not use:
        lines += ["## Tactics", "", "(No tactics learned yet.)"]
    else:
        lines.append("## Tactics" + (" (most relevant to this task)" if retrieved else ""))
        lines.append("")
        for i, t in enumerate(use, 1):
            lines.append(f"{i}. {t['text']}")
    return "\n".join(lines) + "\n"


def build_section_text(
    facts: Optional[Sequence[RuleInput]] = None,
    tips: Optional[Sequence[RuleInput]] = None,
    *,
    retrieved: bool = False,
    capabilities_md: str = "",
) -> str:
    """Combine facts + tips into a single section body (empty if bank is empty)."""
    parts: List[str] = []
    facts_md = render_facts_md(facts, retrieved=retrieved)
    if facts_md:
        parts.append(facts_md)
    tips_md = render_tips_md(tips, retrieved=retrieved, capabilities_md=capabilities_md)
    if tips_md:
        parts.append(tips_md)
    return "\n\n".join(parts)


def rules_numbered(flat: Sequence[Tuple[str, str]]) -> str:
    """Number facts-then-tips snapshot for the blame/synthesize prompts.

    Mirrors the reference ``_flat_numbered``: the rtype tag is upper-cased
    (``[FACT]`` / ``[TIP]``) so the blame/synthesize templates render exactly.
    """
    return "\n".join(f"{i}. [{rtype.upper()}] {text}" for i, (text, rtype) in enumerate(flat, 1))


__all__ = ["render_facts_md", "render_tips_md", "build_section_text", "rules_numbered"]
