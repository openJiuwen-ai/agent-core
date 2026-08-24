# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager prompt, response parsing, and unified tip/tool selection.

Selected ids are validated against the candidate pool — hallucinated ids are
silently dropped.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Tuple

from .schema import BaseTip, CodeTool, TipCategory

_CATEGORY_LABEL = {
    TipCategory.ENVIRONMENT: "ENVIRONMENT",
    TipCategory.EXECUTION_PLAN: "EXECUTION_PLAN",
    TipCategory.EXECUTION_PITFALL: "EXECUTION_PITFALL",
}

MANAGER_FILTER_PROMPT = """\
You are a Task Orchestrator. Select the exact set of text tips AND code tools \
the execution agent will need for the task below — in ONE consistent decision.

# Task
{query}

# Available Text Tips
Each tip has an id, a category, and advice content.
{formatted_tips}

# Available Code Tools
Each tool has an id, a signature, and a description (implementations omitted — \
rely on the signature + description).
{formatted_tools}

# Instructions
1. Selection: choose the tips AND tools needed for THIS task as one coherent set.
   - If you pick an EXECUTION_PLAN tip, also pick the code tools its procedure \
relies on.
   - If a pitfall warns "do not use tool X" and it applies here, drop X.
   - Exclude anything whose scope does not clearly fit a sub-step of this task — \
a wrongly selected tip or tool misleads the agent.
2. Fitness: for each selected tip, verify it truly applies to a sub-step (break \
the query down and check); for each selected tool, read its docstring for scope/ \
assumptions that may not hold. Drop anything unsuitable. Selecting nothing is fine.
3. Do not select tips that only concern task-completion mechanics (how/whether to \
signal the task is done) — that is executor policy, not reusable knowledge.

# Output
Reason in plain prose FIRST, then emit ONE JSON object with EXACTLY two fields:
{{"selected_tip_ids": ["..."], "selected_tool_ids": ["..."]}}
Use [] for either when none apply. IDs must be EXACTLY as listed above \
(case-sensitive); do not invent or alter ids.
"""


def tool_signature(tool: CodeTool) -> str:
    """Render one stored tool as a compact Python signature."""
    parts: List[str] = []
    for name, info in (tool.parameters or {}).items():
        info = info if isinstance(info, dict) else {}
        typ = info.get("type", "Any")
        if "default" in info:
            parts.append(f"{name}: {typ} = {info['default']!r}")
        else:
            parts.append(f"{name}: {typ}")
    return f"{tool.function_name}({', '.join(parts)}) -> {tool.return_annotation}"


def format_tips_for_filter(tips: List[BaseTip]) -> str:
    """Render live tips for the Manager selection prompt."""
    live = [tip for tip in tips if not tip.is_invalidated]
    if not live:
        return "(none)"
    return "\n".join(f"- {tip.id} [{_CATEGORY_LABEL.get(tip.category, tip.category)}]: {tip.content}" for tip in live)


def format_tools_for_filter(tools: List[CodeTool]) -> str:
    """Render tool signatures and summaries for Manager selection."""
    if not tools:
        return "(none)"
    lines = []
    for tool in tools:
        head = tool.docstring.strip().splitlines()[0] if tool.docstring.strip() else ""
        lines.append(f"- {tool.id}: `{tool_signature(tool)}`" + (f" — {head}" if head else ""))
    return "\n".join(lines)


def build_manager_prompt(query: str, tips: List[BaseTip], tools: List[CodeTool]) -> str:
    """Fill the Manager prompt with the complete live candidate library."""
    return MANAGER_FILTER_PROMPT.format(
        query=query,
        formatted_tips=format_tips_for_filter(tips),
        formatted_tools=format_tools_for_filter(tools),
    )


_DECODER = json.JSONDecoder()


def parse_unified_response(text: str) -> Tuple[List[str], List[str]]:
    """Parse ``{selected_tip_ids, selected_tool_ids}`` from the LLM output.

    Tolerant to fenced blocks and prose-before-JSON; either key may be absent.
    When multiple JSON objects are present, the last valid object containing a
    selection key wins. On parse failure returns ``([], [])`` (treat as "no
    knowledge" rather than crash).
    """
    candidates: List[dict] = []
    consumed_until = 0

    for match in re.finditer(r"\{", text):
        if match.start() < consumed_until:
            continue
        try:
            parsed, consumed_until = _DECODER.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and ("selected_tip_ids" in parsed or "selected_tool_ids" in parsed):
            candidates.append(parsed)

    if not candidates:
        return [], []

    obj = candidates[-1]
    tip_ids = obj.get("selected_tip_ids") or []
    tool_ids = obj.get("selected_tool_ids") or []
    if not isinstance(tip_ids, list):
        tip_ids = []
    if not isinstance(tool_ids, list):
        tool_ids = []
    return [str(x) for x in tip_ids], [str(x) for x in tool_ids]


async def manager_select(
    llm: Any,
    query: str,
    tips: List[BaseTip],
    tools: List[CodeTool],
) -> Tuple[List[str], List[str]]:
    """One LLM call selecting a coherent subset of ``tips`` + ``tools``.

    Returns ``(selected_tip_ids, selected_tool_ids)`` validated against the
    candidate pool. Invalidated tips are never offered or selected.
    """
    visible = [t for t in tips if not t.is_invalidated]
    if not visible and not tools:
        return [], []

    text = await llm.async_generate(prompt=build_manager_prompt(query, visible, tools))
    tip_ids, tool_ids = parse_unified_response(text or "")

    valid_tip = {t.id for t in visible}
    valid_tool = {t.id for t in tools}
    return (
        [i for i in tip_ids if i in valid_tip],
        [i for i in tool_ids if i in valid_tool],
    )


__all__ = [
    "MANAGER_FILTER_PROMPT",
    "build_manager_prompt",
    "format_tips_for_filter",
    "format_tools_for_filter",
    "manager_select",
    "parse_unified_response",
    "tool_signature",
]
