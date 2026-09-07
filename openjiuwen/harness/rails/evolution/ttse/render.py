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


DISK_CATALOG_GUIDANCE_CN = """\
## 经验目录（强制）

末尾附件是经验类目和条数，不是 FACT/TIP 正文。闲聊可忽略该附件。
非闲聊任务、在选择 skill、调用 `skill_acceleration_exec` 或动手之前：附件中若有与当前任务相关的类，必须先调用 `ttse_consult(category=该类id)`，根据返回的 FACT/TIP 再规划。不要一次打开无关类。
附件为 `(empty)`，或没有任何相关类时，直接执行。
不要无参调用 `ttse_consult` 再要一遍目录。
经验是历史启发式，与当前工具证据冲突时以当前证据为准。
禁止用 bash 或 `read_file` 读取经验库。
"""

DISK_CATALOG_GUIDANCE_EN = """\
## Experience catalog (required)

The trailing attachment lists experience categories and counts, not FACT/TIP bodies. Ignore it for chitchat.
On a non-trivial task, before choosing a skill, calling `skill_acceleration_exec`, or acting: if a listed category applies, you MUST call `ttse_consult(category=<id>)` and plan from the returned FACT/TIP. Do not dump unrelated classes.
If the attachment is `(empty)` or none apply, proceed without it.
Do not call `ttse_consult` with no arguments to re-list the catalog.
These are historical heuristics; if they conflict with current tool evidence, trust the current evidence.
Do not use bash or `read_file` to read the experience bank.
"""

# Backward-compatible alias (English). Prefer the _CN / _EN constants in new code.
DISK_CATALOG_GUIDANCE = DISK_CATALOG_GUIDANCE_EN


__all__ = [
    "render_facts_md",
    "render_tips_md",
    "build_section_text",
    "rules_numbered",
    "DISK_CATALOG_GUIDANCE",
    "DISK_CATALOG_GUIDANCE_CN",
    "DISK_CATALOG_GUIDANCE_EN",
]
