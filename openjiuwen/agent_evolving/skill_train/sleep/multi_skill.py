# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Consolidate several skill groups independently in one sleep night."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from openjiuwen.agent_evolving.skill_train.sleep.backend import Backend
from openjiuwen.agent_evolving.skill_train.sleep.consolidate import ConsolidationResult, consolidate
from openjiuwen.agent_evolving.skill_train.sleep.types import SkillGroupReport, TaskRecord

CONSOLIDATED = "consolidated"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass
class SkillGroup:
    skill_name: str
    skill: str = ""
    tasks: List[TaskRecord] = field(default_factory=list)


@dataclass
class GroupConsolidation:
    skill_name: str
    status: str
    result: Optional[ConsolidationResult] = None
    reason: str = ""
    n_tasks: int = 0

    @property
    def accepted(self) -> bool:
        return bool(self.result and self.result.accepted)


def consolidate_groups(
    backend: Backend,
    groups: Sequence[SkillGroup],
    memory: str = "",
    *,
    consolidate_fn: Callable[..., ConsolidationResult] = consolidate,
    **consolidate_kwargs: object,
) -> Dict[str, GroupConsolidation]:
    """Consolidate each skill group independently; isolate per-group failures."""
    kwargs = dict(consolidate_kwargs)
    kwargs["evolve_memory"] = False
    out: Dict[str, GroupConsolidation] = {}
    for group in groups:
        name = (group.skill_name or "").strip()
        if not name:
            out.setdefault(
                "",
                GroupConsolidation(
                    "",
                    SKIPPED,
                    reason="group has no skill name",
                    n_tasks=len(group.tasks),
                ),
            )
            continue
        if name in out:
            continue
        if not group.tasks:
            out[name] = GroupConsolidation(name, SKIPPED, reason="no mined tasks for this skill")
            continue
        try:
            result = consolidate_fn(
                backend,
                list(group.tasks),
                group.skill,
                memory,
                **kwargs,
            )
        except Exception as exc:
            out[name] = GroupConsolidation(
                name,
                FAILED,
                reason=f"{type(exc).__name__}: {exc}"[:300],
                n_tasks=len(group.tasks),
            )
            continue
        out[name] = GroupConsolidation(name, CONSOLIDATED, result=result, n_tasks=len(group.tasks))
    return out


def skill_group_reports(outcomes: Dict[str, GroupConsolidation]) -> List[SkillGroupReport]:
    rows: List[SkillGroupReport] = []
    for name, outcome in outcomes.items():
        row = SkillGroupReport(
            skill_name=name,
            status=outcome.status,
            reason=outcome.reason,
            n_tasks=outcome.n_tasks,
        )
        result = outcome.result
        if result is not None:
            row.accepted = result.accepted
            row.gate_action = result.gate_action
            row.baseline_score = result.baseline_score
            row.candidate_score = result.candidate_score
            row.n_applied_edits = len(result.applied_edits)
            row.n_rejected_edits = len(result.rejected_edits)
        rows.append(row)
    return rows


def accepted_group_skills(outcomes: Dict[str, GroupConsolidation]) -> Dict[str, str]:
    return {
        name: outcome.result.new_skill
        for name, outcome in outcomes.items()
        if outcome.accepted and outcome.result is not None
    }
