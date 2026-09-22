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
    if retrieved:
        lines = ["# FACT", ""]
    else:
        lines = [
            "# Environment Facts",
            "",
            "Confirmed observations about what the environment is like, learned from prior tasks. Treat as true.",
            "",
        ]
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
    if retrieved:
        if not use:
            return ""
        lines = ["# TIP", ""]
        for i, t in enumerate(use, 1):
            lines.append(f"{i}. {t['text']}")
        return "\n".join(lines) + "\n"
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
        lines.append("## Tactics")
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


QUERY_GUIDANCE_CN = (
    "把用户问题改写成检索 query：抽出关键词拼在一起，不要整句粘贴原文。"
    "例：`write_file matplotlib 中文折线图 Open-Meteo Windows`。"
)
QUERY_GUIDANCE_EN = (
    "Rewrite the user request into query by joining keywords; do not paste the full message. "
    "Example: `write_file matplotlib Chinese line chart Open-Meteo Windows`."
)

DISK_CATALOG_GUIDANCE_CN = """\
## 经验目录

末尾附件是按类目统计的经验目录，不是 FACT/TIP 正文。闲聊可忽略。
非闲聊且附件中有与当前任务相关的类时，动手前用 `ttse_consult` 取回该类经验再规划；没有相关类或附件为 `(empty)` 时直接执行。
经验是历史启发式，与当前工具证据冲突时以当前证据为准。
"""

DISK_CATALOG_GUIDANCE_EN = """\
## Experience catalog

The trailing attachment is the category listing and counts, not FACT/TIP bodies. Ignore it for chitchat.
On a non-trivial task, if a listed category applies, call `ttse_consult` before acting and plan from the returned experience. If none apply or the attachment is `(empty)`, proceed.
These are historical heuristics; if they conflict with current tool evidence, trust the current evidence.
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
    "QUERY_GUIDANCE_CN",
    "QUERY_GUIDANCE_EN",
]
