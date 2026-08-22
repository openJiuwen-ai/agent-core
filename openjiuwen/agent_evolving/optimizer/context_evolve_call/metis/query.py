# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Metis read side: manager-select -> dependency closure -> render.

Runtime context injection is a rail concern, not an optimizer concern; this
service mirrors the position ``ExperienceQueryService`` holds for skill
experiences. Logic ported from metis_init's retrieve op.
"""

from __future__ import annotations

from typing import Any, List

from openjiuwen.agent_evolving.optimizer.context_evolve_call.contracts import ContextRetrievalResult
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis.dependencies import (
    expand_code_dependencies,
    expand_plan_to_tools,
)
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis.manager_select import manager_select, tool_signature
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis.schema import BaseTip, CodeTool, TipCategory
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis.store import MetisMemoryStore
from openjiuwen.core.common.logging import logger

_CATEGORY_LABEL = {
    TipCategory.ENVIRONMENT: "Environment",
    TipCategory.EXECUTION_PLAN: "Plans",
    TipCategory.EXECUTION_PITFALL: "Pitfalls",
}
_CATEGORY_ORDER = (TipCategory.ENVIRONMENT, TipCategory.EXECUTION_PLAN, TipCategory.EXECUTION_PITFALL)


def _tool_block(tool: CodeTool) -> str:
    """One tool's injected text: signature heading, docstring, full source."""
    lines = [f"### `{tool_signature(tool)}`"]
    if tool.docstring.strip():
        lines.append(tool.docstring.strip())
    lines.append("```python")
    lines.append(f"def {tool_signature(tool)}:")
    body = tool.implementation or "pass"
    lines.extend("    " + ln for ln in body.splitlines())
    lines.append("```")
    return "\n".join(lines)


def render_memory_string(tips: List[BaseTip], tools: List[CodeTool]) -> str:
    """Injected knowledge: tips as ``[N] content`` by category, tools as source."""
    blocks = []
    live_tips = [t for t in tips if not t.is_invalidated]
    if live_tips:
        lines = ["## Distilled Knowledge"]
        n = 1
        for cat in _CATEGORY_ORDER:
            cat_tips = [t for t in live_tips if t.category == cat]
            if not cat_tips:
                continue
            lines.append(f"\n### {_CATEGORY_LABEL[cat]}")
            for tip in cat_tips:
                lines.append(f"[{n}] {tip.content}")
                n += 1
        blocks.append("\n".join(lines))
    if tools:
        lines = ["## Distilled Tools"]
        for tool in tools:
            lines.append("\n" + _tool_block(tool))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


class MetisQueryService:
    """Manager selection + dependency closure over a ``MetisMemoryStore``.

    Implements the dimension's ``ContextRetriever`` protocol: the rendered
    memory string travels as ``content``; ``selected_tip_ids`` (the write
    side's curation input) rides in ``evolution_context``.
    """

    def __init__(
        self,
        *,
        store: MetisMemoryStore,
        llm: Any,
    ) -> None:
        """Bind the memory store and Manager-compatible LLM adapter."""
        self._store = store
        self._llm = llm

    async def retrieve(self, scope_id: str, query: str) -> ContextRetrievalResult:
        """Select, expand, and render memories relevant to one task query."""
        cand_tips, cand_tools = await self._store.load_candidates(scope_id)
        if not cand_tips and not cand_tools:
            return ContextRetrievalResult(content="", evolution_context={"selected_tip_ids": []})

        sel_tip_ids, sel_tool_ids = await manager_select(self._llm, query, cand_tips, cand_tools)

        selected_tools = [t for t in cand_tools if t.id in set(sel_tool_ids)]
        by_name = {t.function_name: t for t in cand_tools}
        pulled = expand_plan_to_tools(sel_tip_ids, cand_tips, [t.function_name for t in selected_tools], cand_tools)
        selected_tools = selected_tools + [by_name[n] for n in pulled if n in by_name]
        expanded, _ = expand_code_dependencies(selected_tools, cand_tools)
        selected_tips = [t for t in cand_tips if t.id in set(sel_tip_ids)]

        logger.info(
            "[MetisQueryService] scope=%s: %d tips, %d tools injected (%d via closure)",
            scope_id,
            len(selected_tips),
            len(expanded),
            len(expanded) - len(sel_tool_ids),
        )
        return ContextRetrievalResult(
            content=render_memory_string(selected_tips, expanded),
            evolution_context={"selected_tip_ids": [t.id for t in selected_tips]},
            metadata={
                "selected_tool_ids": [t.id for t in expanded],
                "tips_injected": len(selected_tips),
                "tools_injected": len(expanded),
            },
        )


__all__ = ["MetisQueryService", "render_memory_string"]
