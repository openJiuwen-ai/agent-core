# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Metis evolve orchestration: one pass per finished task.

Strategy order is code-first, text-last:

    record candidate -> plan-only codify (if threshold) -> text reflect -> curate

Each sub-step is an isolated failure domain (logged + contained) so one failure
never drops the others. Candidate query snapshots live on the plan tips
themselves; tool dependencies are refreshed by a static scan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from openjiuwen.core.common.logging import logger

from .dependencies import populate_dependencies
from .reflection import reflect_code_plan_only, reflect_text
from .schema import (
    BaseTip,
    CodeTool,
    ExecutionPlan,
    TaskReference,
    TipUpdate,
    tip_class_for_category,
)

_RECENT_QUERIES_MAX = 10
_RECENT_QUERY_LINE_MAX = 300
_MAX_RELATED_TASKS_PER_PLAN = 20


def record_plan_candidates(plan_tips: List[BaseTip], task_id: str, task_query: str) -> None:
    """Append a task snapshot to each selected live execution plan."""
    for tip in plan_tips:
        if not isinstance(tip, ExecutionPlan) or tip.is_invalidated:
            continue
        if all(ref.task_id != task_id for ref in tip.codify_candidate_tasks):
            tip.codify_candidate_tasks.append(TaskReference(task_id=task_id, task_query=task_query))


def candidate_refs(plan: ExecutionPlan) -> List[TaskReference]:
    """Return the candidate buffer de-duplicated by task id, in order."""
    by_id: dict = {}
    for ref in plan.codify_candidate_tasks:
        by_id.setdefault(ref.task_id, ref)
    return list(by_id.values())


def consume_codify_candidates(plan: ExecutionPlan, consumed: List[TaskReference]) -> None:
    """Move consumed candidates into the bounded related-task history."""
    by_id = {ref.task_id: ref for ref in plan.related_tasks}
    ordered_ids = [ref.task_id for ref in plan.related_tasks]

    for ref in consumed:
        if ref.task_id not in by_id:
            ordered_ids.append(ref.task_id)
        by_id[ref.task_id] = ref

    ordered_ids = ordered_ids[-_MAX_RELATED_TASKS_PER_PLAN:]
    plan.related_tasks = [by_id[task_id] for task_id in ordered_ids]

    consumed_ids = {ref.task_id for ref in consumed}
    plan.codify_candidate_tasks = [ref for ref in plan.codify_candidate_tasks if ref.task_id not in consumed_ids]


def apply_tip_updates(  # pylint: disable=too-many-locals
    tips: List[BaseTip],
    tools: List[CodeTool],
    updates: List[TipUpdate],
) -> int:
    """Apply valid tip updates by invalidating targets and appending replacements."""
    available_tool_names = {tool.function_name for tool in tools}
    applied = 0

    for update in updates:
        live_by_id = {tip.id: tip for tip in tips if not tip.is_invalidated}

        missing = [target_id for target_id in update.target_ids if target_id not in live_by_id]
        if missing:
            logger.debug("[tip-update] drop id=%r: unresolved targets %r", update.new_id, missing)
            continue

        target_tips = [live_by_id[target_id] for target_id in update.target_ids]
        categories = {tip.category for tip in target_tips}
        if len(categories) > 1:
            logger.debug("[tip-update] drop id=%r: targets span categories %r", update.new_id, categories)
            continue
        update_category = categories.pop()

        target_id_set = set(update.target_ids)
        live_non_targets = {tip_id for tip_id in live_by_id if tip_id not in target_id_set}
        if update.new_id in live_non_targets:
            logger.debug("[tip-update] drop: new_id=%r collides with a live tip", update.new_id)
            continue

        is_plan = isinstance(target_tips[0], ExecutionPlan)
        merged_source_task_ids: List[str] = []
        merged_related: List[TaskReference] = []
        merged_candidates: List[TaskReference] = []
        seen_sources: set = set()
        seen_related: set = set()
        seen_candidates: set = set()
        for tip in target_tips:
            tip.is_invalidated = True
            for source_id in tip.source_task_ids:
                if source_id not in seen_sources:
                    seen_sources.add(source_id)
                    merged_source_task_ids.append(source_id)
            if isinstance(tip, ExecutionPlan):
                for reference in tip.related_tasks or []:
                    if reference.task_id not in seen_related:
                        seen_related.add(reference.task_id)
                        merged_related.append(reference)
                for reference in tip.codify_candidate_tasks or []:
                    if reference.task_id not in seen_candidates:
                        seen_candidates.add(reference.task_id)
                        merged_candidates.append(reference)
        for source_id in update.source_task_ids:
            if source_id not in seen_sources:
                seen_sources.add(source_id)
                merged_source_task_ids.append(source_id)

        if is_plan:
            new_tip: BaseTip = ExecutionPlan(
                id=update.new_id,
                source_task_ids=merged_source_task_ids,
                content=update.new_content,
                category=update_category,
                related_tasks=merged_related,
                codify_candidate_tasks=merged_candidates,
                dependent_tool_names=[name for name in update.new_dependent_tool_names if name in available_tool_names],
            )
        else:
            new_tip = tip_class_for_category(update_category)(
                id=update.new_id,
                source_task_ids=merged_source_task_ids,
                content=update.new_content,
                category=update_category,
            )
        tips.append(new_tip)

        if is_plan:
            for tool in tools:
                if tool.source_plan_id in target_id_set:
                    tool.source_plan_id = update.new_id

        applied += 1

    return applied


@dataclass
class EvolveState:
    """The evolving knowledge library + cross-task context for one evolve pass."""

    tips: List[BaseTip] = field(default_factory=list)
    tools: List[CodeTool] = field(default_factory=list)
    recent_queries: List[str] = field(default_factory=list)

    def live_tips(self) -> List[BaseTip]:
        """Return tips that have not been invalidated."""
        return [t for t in self.tips if not t.is_invalidated]


def _sanitize_query(query: str) -> str:
    one_line = " ".join(query.split())
    if len(one_line) > _RECENT_QUERY_LINE_MAX:
        one_line = one_line[: _RECENT_QUERY_LINE_MAX - 3] + "..."
    return one_line


def group_codify_by_plan(new_tools: List[CodeTool], tips: List[BaseTip]) -> List[Tuple[ExecutionPlan, List[CodeTool]]]:
    """Bucket new tools by their ``source_plan_id`` (live plan tips only)."""
    plan_lookup = {t.id: t for t in tips if isinstance(t, ExecutionPlan) and not t.is_invalidated}
    bucket: Dict[str, List[CodeTool]] = {}
    order: List[str] = []
    for tool in new_tools:
        pid = tool.source_plan_id
        if not pid or pid not in plan_lookup:
            continue
        if pid not in bucket:
            bucket[pid] = []
            order.append(pid)
        bucket[pid].append(tool)
    return [(plan_lookup[pid], bucket[pid]) for pid in order]


async def _codify_plan_only(
    llm: Any,
    *,
    task_tips: List[BaseTip],
    state: EvolveState,
    threshold: int,
    executor_context: str = "",
) -> List[CodeTool]:
    """Codify each selected plan whose candidate buffer reached ``threshold``.

    Per-plan failure containment: a plan that raises is skipped (buffer kept),
    plans already codified this round are retained.
    """
    new_tools: List[CodeTool] = []
    for plan in task_tips:
        if not isinstance(plan, ExecutionPlan) or plan.is_invalidated:
            continue
        cands = candidate_refs(plan)
        if len(cands) < threshold:
            continue
        try:
            ordered = sorted(cands, key=lambda ref: ref.task_id)
            tools = await reflect_code_plan_only(
                llm,
                plan=plan,
                candidate_pairs=[(ref.task_id, ref.task_query) for ref in ordered],
                related_tasks=plan.related_tasks,
                existing_tools=state.tools + new_tools,
                executor_context=executor_context,
            )
        except Exception as exc:
            logger.warning("plan-only codify FAILED for plan %s (%s); buffer kept", plan.id, exc)
            continue

        new_tools.extend(tools)
        consume_codify_candidates(plan, cands)

    return new_tools


async def evolve_after_task(  # pylint: disable=too-many-locals
    llm: Any,
    *,
    task_id: str,
    query: str,
    trajectory: str,
    selected_tip_ids: List[str],
    state: EvolveState,
    threshold: int,
    outcome: str = "Unknown",
    executor_context: str = "",
) -> None:
    """Run codification / reflection on one task's trajectory; mutate ``state``."""
    selected = set(selected_tip_ids)
    task_tips = [t for t in state.tips if t.id in selected]

    # 1. record candidates on selected plan tips — success only: failed or
    #    unknown runs must not accumulate codify evidence.
    if outcome.strip().lower() == "success":
        record_plan_candidates(task_tips, task_id, query)

    # 2. plan-counter codification (code-first).
    update_context: List[Tuple[ExecutionPlan, List[CodeTool]]] = []
    try:
        new_tools = await _codify_plan_only(
            llm,
            task_tips=task_tips,
            state=state,
            threshold=threshold,
            executor_context=executor_context,
        )
        if new_tools:
            state.tools.extend(new_tools)
            populate_dependencies(state.tools)
            update_context = group_codify_by_plan(new_tools, state.tips)
    except Exception as exc:
        logger.warning("plan-counter codify FAILED for task %s: %s", task_id, exc)

    # 3. text reflection (last, so it sees post-codify tools).
    try:
        new_items = await reflect_text(
            llm,
            task_id=task_id,
            query=query,
            trajectory=trajectory,
            existing_tips=state.tips,
            existing_tools=state.tools,
            recent_queries=state.recent_queries,
            recently_codified=update_context,
            outcome=outcome,
            executor_context=executor_context,
        )
        new_tips = [it for it in new_items if isinstance(it, BaseTip)]
        new_updates = [it for it in new_items if isinstance(it, TipUpdate)]

        # A `create` whose id collides with a LIVE tip is dropped (should have
        # been an `update`).
        live_ids = {t.id for t in state.tips if not t.is_invalidated}
        for t in new_tips:
            if t.id in live_ids:
                logger.warning("dropping create id=%s: collides with a live tip", t.id)
                continue
            live_ids.add(t.id)
            state.tips.append(t)

        if new_updates:
            apply_tip_updates(state.tips, state.tools, new_updates)
    except Exception as exc:
        logger.warning("text reflection FAILED for task %s: %s", task_id, exc)

    # 4. push this task's query into the recent-queries FIFO.
    state.recent_queries = (state.recent_queries + [_sanitize_query(query)])[-_RECENT_QUERIES_MAX:]


__all__ = ["EvolveState", "evolve_after_task", "group_codify_by_plan"]
