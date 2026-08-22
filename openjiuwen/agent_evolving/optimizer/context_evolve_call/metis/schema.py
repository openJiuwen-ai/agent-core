# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Metis working-state schema.

Tips use subclass polymorphism (``BaseTip`` + environment / pitfall / plan);
only ``ExecutionPlan`` carries plan-counter codification bookkeeping.
The same module owns their plain-dictionary serialization so the schema and
snapshot representation evolve together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type


class TipCategory:
    """Canonical tip categories."""

    ENVIRONMENT = "environment"
    EXECUTION_PLAN = "execution_plan"
    EXECUTION_PITFALL = "execution_pitfall"


@dataclass
class BaseTip:
    """Text tip injected into the agent prompt.

    ``is_invalidated`` soft-deletes it: kept for audit, filtered from every
    LLM-facing surface.
    """

    id: str
    source_task_ids: List[str]
    content: str
    category: str
    is_invalidated: bool = False


@dataclass
class EnvironmentTip(BaseTip):
    """Hard facts about the execution environment (API behaviour, constraints)."""


@dataclass
class ExecutionPitfall(BaseTip):
    """Generalizable warnings derived from failures or inefficiency."""


@dataclass
class TaskReference:
    """Query-only task reference for plan-level codification context."""

    task_id: str
    task_query: str


@dataclass
class ExecutionPlan(BaseTip):
    """Execution-plan tip with plan-counter codification evidence.

    ``codify_candidate_tasks`` buffers ``(task_id, task_query)`` snapshots used
    as evidence once the threshold is reached; ``related_tasks`` are query-only
    snapshots of consumed tasks; ``dependent_tool_names`` are
    ``CodeTool.function_name`` values pulled in alongside the plan at injection
    time.
    """

    related_tasks: List[TaskReference] = field(default_factory=list)
    codify_candidate_tasks: List[TaskReference] = field(default_factory=list)
    dependent_tool_names: List[str] = field(default_factory=list)


@dataclass
class TipUpdate:
    """Reflector directive to invalidate one or more tips and append a new one.

    1 target -> rewrite-in-spirit; N targets -> merge. Category is inferred at
    apply time from the resolved targets; ``new_dependent_tool_names`` applies
    only when that category is EXECUTION_PLAN.
    """

    target_ids: List[str]
    new_id: str
    new_content: str
    new_dependent_tool_names: List[str]
    source_task_ids: List[str]


@dataclass
class CodeTool:
    """Code-tool spec (flat).

    ``id == function_name`` (maintained by the parser); ``dependencies`` are
    other tool function-names, populated by static scan and used for transitive
    closure at injection time.
    """

    id: str
    source_task_ids: List[str]
    function_name: str
    docstring: str
    parameters: Dict  # name -> {"type": str, "description": str, "default"?: any}
    return_annotation: str
    implementation: str
    dependencies: List[str] = field(default_factory=list)
    source_plan_id: Optional[str] = None


_CATEGORY_TO_TIP_CLASS: Dict[str, Type[BaseTip]] = {
    TipCategory.ENVIRONMENT: EnvironmentTip,
    TipCategory.EXECUTION_PLAN: ExecutionPlan,
    TipCategory.EXECUTION_PITFALL: ExecutionPitfall,
}


def tip_class_for_category(category: str) -> Type[BaseTip]:
    """Map a category string to its tip subclass (``BaseTip`` if unknown)."""
    return _CATEGORY_TO_TIP_CLASS.get(category, BaseTip)


def tip_to_dict(tip: BaseTip) -> Dict[str, Any]:
    """Serialize one tip and its subtype-specific fields."""
    data: Dict[str, Any] = {
        "id": tip.id,
        "category": tip.category,
        "content": tip.content,
        "source_task_ids": list(tip.source_task_ids),
        "is_invalidated": tip.is_invalidated,
    }
    if isinstance(tip, ExecutionPlan):
        data.update(
            related_tasks=[{"task_id": ref.task_id, "task_query": ref.task_query} for ref in tip.related_tasks],
            codify_candidate_tasks=[
                {"task_id": ref.task_id, "task_query": ref.task_query} for ref in tip.codify_candidate_tasks
            ],
            dependent_tool_names=list(tip.dependent_tool_names),
        )
    return data


def tip_from_dict(data: Dict[str, Any]) -> BaseTip:
    """Deserialize one tip from its snapshot representation."""
    category = str(data.get("category", ""))
    cls = tip_class_for_category(category)
    tip_id = str(data.get("id", ""))
    source_task_ids = [str(source_id) for source_id in data.get("source_task_ids") or []]
    content = str(data.get("content", ""))
    is_invalidated = bool(data.get("is_invalidated", False))
    if cls is ExecutionPlan:
        return ExecutionPlan(
            id=tip_id,
            source_task_ids=source_task_ids,
            content=content,
            category=category,
            is_invalidated=is_invalidated,
            related_tasks=[
                TaskReference(task_id=item.get("task_id", ""), task_query=item.get("task_query", ""))
                for item in data.get("related_tasks") or []
            ],
            codify_candidate_tasks=[
                TaskReference(task_id=item.get("task_id", ""), task_query=item.get("task_query", ""))
                for item in data.get("codify_candidate_tasks") or []
            ],
            dependent_tool_names=[str(name) for name in data.get("dependent_tool_names") or []],
        )
    return cls(
        id=tip_id,
        source_task_ids=source_task_ids,
        content=content,
        category=category,
        is_invalidated=is_invalidated,
    )


def tool_to_dict(tool: CodeTool) -> Dict[str, Any]:
    """Serialize one distilled code tool for snapshot persistence."""
    return {
        "id": tool.id,
        "function_name": tool.function_name,
        "docstring": tool.docstring,
        "parameters": dict(tool.parameters),
        "return_annotation": tool.return_annotation,
        "implementation": tool.implementation,
        "dependencies": list(tool.dependencies),
        "source_task_ids": list(tool.source_task_ids),
        "source_plan_id": tool.source_plan_id,
    }


def tool_from_dict(data: Dict[str, Any]) -> CodeTool:
    """Deserialize one distilled code tool from a snapshot."""
    return CodeTool(
        id=str(data.get("id", "")),
        source_task_ids=[str(source_id) for source_id in data.get("source_task_ids") or []],
        function_name=str(data.get("function_name", "")),
        docstring=str(data.get("docstring", "")),
        parameters=dict(data.get("parameters") or {}),
        return_annotation=str(data.get("return_annotation", "Any")),
        implementation=str(data.get("implementation", "pass")),
        dependencies=[str(dependency) for dependency in data.get("dependencies") or []],
        source_plan_id=data.get("source_plan_id"),
    )


__all__ = [
    "TipCategory",
    "BaseTip",
    "EnvironmentTip",
    "ExecutionPitfall",
    "ExecutionPlan",
    "TaskReference",
    "TipUpdate",
    "CodeTool",
    "tip_class_for_category",
    "tip_from_dict",
    "tip_to_dict",
    "tool_from_dict",
    "tool_to_dict",
]
