# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Single-turn text reflection and recurring-plan code codification."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from openjiuwen.core.common.logging import logger

from .prompt import build_code_reflect_prompt, build_text_reflect_prompt
from .schema import (
    BaseTip,
    CodeTool,
    ExecutionPlan,
    TaskReference,
    TipCategory,
    TipUpdate,
    tip_class_for_category,
)

_JSON_FENCE_OPEN = re.compile(r"```json\s*")
_JSON_DECODER = json.JSONDecoder()
_LABEL_TO_CATEGORY = {
    "ENVIRONMENT": TipCategory.ENVIRONMENT,
    "EXECUTION_PLAN": TipCategory.EXECUTION_PLAN,
    "EXECUTION_PITFALL": TipCategory.EXECUTION_PITFALL,
}


def _parse_fenced_array(text: str) -> Optional[list]:
    """Return the last valid JSON array following a ``json`` fence."""
    last: Optional[list] = None
    for match in _JSON_FENCE_OPEN.finditer(text):
        start = match.end()
        chunk = text[start:].lstrip()
        try:
            value, _ = _JSON_DECODER.raw_decode(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            last = value
    return last


def parse_reflection(  # pylint: disable=too-many-locals
    parsed: List[Dict[str, Any]],
    *,
    task_id: str,
    query: str,
    existing_tool_names: List[str],
) -> List[Union[BaseTip, TipUpdate]]:
    """Turn reflector JSON entries into new tips or tip-update directives."""
    existing = set(existing_tool_names)
    out: List[Union[BaseTip, TipUpdate]] = []

    for index, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            continue
        action = str(entry.get("action", "")).strip().lower()

        if action == "update":
            target_ids = entry.get("target_ids")
            new_id = entry.get("id")
            if not isinstance(target_ids, list) or not target_ids or not new_id:
                logger.debug("[reflect] drop update: missing target_ids/id")
                continue
            dependencies = [name for name in (entry.get("dependent_tools") or []) if name in existing]
            out.append(
                TipUpdate(
                    target_ids=[str(target_id) for target_id in target_ids],
                    new_id=str(new_id),
                    new_content=str(entry.get("content", "")),
                    new_dependent_tool_names=dependencies,
                    source_task_ids=[task_id],
                )
            )
            continue

        if action != "create":
            continue
        category = _LABEL_TO_CATEGORY.get(str(entry.get("label", "")).strip().upper())
        content = entry.get("content")
        if category is None or not content:
            logger.debug("[reflect] drop create: bad label/content")
            continue
        tip_id = str(entry.get("id") or f"{category}_{task_id}_{index}")
        tip_class = tip_class_for_category(category)
        if tip_class is ExecutionPlan:
            dependencies = [name for name in (entry.get("dependent_tools") or []) if name in existing]
            out.append(
                ExecutionPlan(
                    id=tip_id,
                    source_task_ids=[task_id],
                    content=str(content),
                    category=category,
                    codify_candidate_tasks=[TaskReference(task_id=task_id, task_query=query)],
                    dependent_tool_names=dependencies,
                )
            )
        else:
            out.append(
                tip_class(
                    id=tip_id,
                    source_task_ids=[task_id],
                    content=str(content),
                    category=category,
                )
            )

    return out


async def reflect_text(
    llm: Any,
    *,
    task_id: str,
    query: str,
    trajectory: str,
    existing_tips: List[BaseTip],
    existing_tools: List[CodeTool],
    recent_queries: List[str],
    recently_codified: Optional[Sequence[Tuple[BaseTip, Sequence[CodeTool]]]] = None,
    outcome: str = "Unknown",
    executor_context: str = "",
) -> List[Union[BaseTip, TipUpdate]]:
    """Reflect one trajectory into reusable tips and tip updates."""
    prompt = build_text_reflect_prompt(
        query=query,
        trajectory=trajectory,
        existing_tips=existing_tips,
        existing_tools=existing_tools,
        recent_queries=recent_queries,
        recently_codified=recently_codified,
        outcome=outcome,
        executor_context=executor_context,
    )
    text = await llm.async_generate(prompt=prompt)
    parsed = _parse_fenced_array(text or "")
    if not isinstance(parsed, list):
        return []
    return parse_reflection(
        parsed,
        task_id=task_id,
        query=query,
        existing_tool_names=[tool.function_name for tool in existing_tools],
    )


def parse_code_tools(
    parsed: List[Dict[str, Any]],
    *,
    source_task_ids: List[str],
    source_plan_id: Optional[str],
) -> List[CodeTool]:
    """Turn code-reflector JSON entries into tool specifications."""
    tools: List[CodeTool] = []
    for index, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            continue
        function_name = str(entry.get("function_name") or f"tool_{index}")
        tools.append(
            CodeTool(
                id=function_name,
                source_task_ids=list(source_task_ids),
                function_name=function_name,
                docstring=str(entry.get("docstring", "")),
                parameters=entry.get("parameters") or {},
                return_annotation=str(entry.get("return_annotation", "Any")),
                implementation=str(entry.get("implementation", "pass")),
                source_plan_id=source_plan_id,
            )
        )
    return tools


async def reflect_code_plan_only(
    llm: Any,
    *,
    plan: ExecutionPlan,
    candidate_pairs: List[Tuple[str, str]],
    related_tasks: List[TaskReference],
    existing_tools: List[CodeTool],
    executor_context: str = "",
) -> List[CodeTool]:
    """Codify one recurring plan into reusable helper tools."""
    if not candidate_pairs:
        return []
    prompt = build_code_reflect_prompt(
        plan_content=plan.content,
        candidate_queries=[query for _, query in candidate_pairs],
        related_queries=[reference.task_query for reference in related_tasks],
        existing_tools=existing_tools,
        executor_context=executor_context,
    )
    text = await llm.async_generate(prompt=prompt)
    parsed = _parse_fenced_array(text or "")
    if not isinstance(parsed, list):
        return []
    return parse_code_tools(
        parsed,
        source_task_ids=[task_id for task_id, _ in candidate_pairs],
        source_plan_id=plan.id,
    )


__all__ = [
    "parse_code_tools",
    "parse_reflection",
    "reflect_code_plan_only",
    "reflect_text",
]
