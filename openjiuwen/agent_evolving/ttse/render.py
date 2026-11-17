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
## 经验目录与任务前置查询（必须先于技能加载）

末尾附件是经验分类目录，不是 FACT/TIP 正文。
当 `ttse_consult` 工具可用时，收到新的非闲聊任务后，第一次工具调用必须仅调用一次 `ttse_consult`。
必须收到并阅读查询结果后，才能加载技能、制定执行计划或调用其他工具。
即使用户明确指定某个技能，也必须先查经验，再调用 `skill_tool` 或技能加速工具。
不得将 `ttse_consult` 与 `skill_tool`、todo 工具或其他业务工具放在同一批调用中。
查询是技能执行流程的前置步骤；技能规范中的“先加载技能”“第一个工作工具”和 todo 顺序要求，均从查询完成后开始适用。

category 根据目录选择相关分类，无法判断时使用 `all`；不得因目录没有明显匹配分类或为 `(empty)` 而跳过查询。
query 应描述本次任务及已知环境，不必先加载技能来构造查询。
无命中或查询失败后正常继续，不要仅为满足流程反复重试。

同一任务已有查询结果，或已有运行时前置查询结果附件时，先阅读结果，无需重复查询；工具结果续跑、审批恢复也无需重查。
历史上其他任务的查询不能代替本次查询。加载技能后若发现需要更具体的经验，可以补充查询。
闲聊、工具不可用或用户明确要求不查询经验时，不要求调用。
经验是历史启发式；遵循用户明确要求，与当前工具证据冲突时以当前证据为准。
"""

DISK_CATALOG_GUIDANCE_EN = """\
## Experience catalog and task lookup (mandatory before loading skills)

The trailing attachment is a category catalog, not FACT/TIP bodies.
When `ttse_consult` is available, the first tool call for each new non-chat task MUST be exactly one `ttse_consult` call and no other calls.
Receive and read its result before loading skills, making an execution plan, or calling any other tool.
Even when the user specifies a skill, consult first, then call `skill_tool` or a skill acceleration tool.
Never batch the consult with skill, todo, or business tools.
The lookup precedes the skill workflow: skill-loading, first-work-tool, and todo ordering rules apply only after the lookup completes.

Choose a relevant catalog category; use `all` if uncertain. An unmatched or `(empty)` catalog is not a reason to skip the lookup.
Build the query from the current task and known environment without loading a skill first.
Continue normally after no matches or a lookup failure; do not repeatedly retry merely to satisfy this procedure.

If this task already has a consult result or a runtime preflight result attachment, read it without repeating the lookup; tool-result continuations and approval resumes do not need another lookup.
Results from other tasks do not satisfy this task's lookup. You may consult again for more specific experience after loading a skill.
Small talk, an unavailable tool, or an explicit user instruction not to consult is exempt.
Experience is historical guidance; follow explicit user instructions and prefer current tool evidence when it conflicts.
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
