# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Static dependency discovery and closure over the Metis tool graph.

Closure back-fills callees / plan-linked tools the Manager's independent
tip/tool selections would otherwise miss, avoiding NameErrors at injection.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from .schema import BaseTip, CodeTool, ExecutionPlan

_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def scan_tool_dependencies(
    implementation: str,
    known_tool_names: Iterable[str],
    self_name: Optional[str] = None,
) -> List[str]:
    """Return known tool names called inside one generated implementation."""
    known = {name for name in known_tool_names if name != self_name}
    if not known:
        return []
    found = {match.group(1) for match in _CALL_RE.finditer(implementation) if match.group(1) in known}
    return sorted(found)


def populate_dependencies(tools: List[CodeTool]) -> int:
    """Refresh every tool's dependency edges in place."""
    all_names = [tool.function_name for tool in tools]
    total = 0
    for tool in tools:
        tool.dependencies = scan_tool_dependencies(tool.implementation, all_names, self_name=tool.function_name)
        total += len(tool.dependencies)
    return total


def expand_plan_to_tools(
    selected_tip_ids: Iterable[str],
    all_tips: List[BaseTip],
    already_selected_tool_ids: Iterable[str],
    all_tools: List[CodeTool],
) -> List[str]:
    """Tool names pulled in by selected plan tips' ``dependent_tool_names``."""
    selected_tip_set = set(selected_tip_ids)
    already = set(already_selected_tool_ids)
    available = {t.function_name for t in all_tools}

    pulled: List[str] = []
    pulled_set: set = set()
    for tip in all_tips:
        if not isinstance(tip, ExecutionPlan) or tip.is_invalidated:
            continue
        if tip.id not in selected_tip_set:
            continue
        for tool_name in tip.dependent_tool_names or []:
            if tool_name in already or tool_name in pulled_set:
                continue
            if tool_name not in available:
                continue
            pulled.append(tool_name)
            pulled_set.add(tool_name)
    return pulled


def expand_code_dependencies(
    selected: List[CodeTool],
    all_tools: List[CodeTool],
) -> Tuple[List[CodeTool], List[str]]:
    """Transitive closure (BFS over ``CodeTool.dependencies``) to a fixed point.

    Returns ``(expanded_tools, added_ids)``; ids missing from ``all_tools`` are
    silently skipped (inject what is available rather than refuse to run).
    """
    by_id = {t.id: t for t in all_tools}
    selected_ids = {t.id for t in selected}

    queue: List[str] = []
    added_ids: List[str] = []
    for t in selected:
        queue.extend(t.dependencies or [])

    while queue:
        dep = queue.pop(0)
        if dep in selected_ids:
            continue
        if dep not in by_id:
            continue
        selected_ids.add(dep)
        added_ids.append(dep)
        queue.extend(by_id[dep].dependencies or [])

    expanded = list(selected) + [by_id[i] for i in added_ids]
    return expanded, added_ids


__all__ = [
    "expand_plan_to_tools",
    "expand_code_dependencies",
    "populate_dependencies",
    "scan_tool_dependencies",
]
