# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Completion contract for dependency-aware member optimization actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openjiuwen.rsi.member_optimizer.schema import MemberOptimizationPlan


@dataclass(frozen=True, slots=True)
class ActionExecutionOutcome:
    """Whether one planned action was satisfied by the executed branch."""

    action_id: str
    satisfied: bool
    reason: str = ""


def action_bundle_key(action: Any) -> tuple[str, str] | None:
    """Return the role/issue key for an explicitly scoped action bundle."""
    issue_ids = [str(issue_id) for issue_id in _list_value(_get(action, "attributed_issue_ids", [])) if str(issue_id)]
    if len(issue_ids) != 1:
        return None
    role = str(_get(action, "role", "") or "")
    if not role:
        return None
    return role, issue_ids[0]


def evaluate_role_execution(
    plan: MemberOptimizationPlan,
    execution_results: list[Any],
    role: str,
) -> dict[str, ActionExecutionOutcome]:
    """Evaluate all planned actions for a role, including fallback branches."""
    planned = [action for action in plan.actions if action.role == role]
    rows_by_id: dict[str, list[Any]] = {}
    for row in execution_results:
        action_id = str(_get(row, "action_id", "") or "")
        if action_id:
            rows_by_id.setdefault(action_id, []).append(row)

    actions_by_id = {action.action_id: action for action in planned}
    direct_success = {
        action_id for action_id, rows in rows_by_id.items() if len(rows) == 1 and _is_merged_success(rows[0])
    }
    successful_fallbacks_by_dependency: dict[str, list[str]] = {}
    for action in planned:
        if action.run_if != "dependency_failed" or action.action_id not in direct_success:
            continue
        for dependency_id in action.depends_on:
            dependency = actions_by_id.get(dependency_id)
            if dependency is None or not _same_bundle_or_unscoped(dependency, action):
                continue
            successful_fallbacks_by_dependency.setdefault(dependency_id, []).append(action.action_id)

    outcomes: dict[str, ActionExecutionOutcome] = {}
    for action in planned:
        rows = rows_by_id.get(action.action_id, [])
        if not rows:
            outcomes[action.action_id] = ActionExecutionOutcome(
                action_id=action.action_id,
                satisfied=False,
                reason="missing execution result",
            )
            continue
        if len(rows) > 1:
            outcomes[action.action_id] = ActionExecutionOutcome(
                action_id=action.action_id,
                satisfied=False,
                reason=f"duplicate execution results: {len(rows)}",
            )
            continue

        row = rows[0]
        if _is_merged_success(row):
            outcomes[action.action_id] = ActionExecutionOutcome(
                action_id=action.action_id,
                satisfied=True,
            )
            continue

        status = str(_get(row, "status", "") or "")
        dependency_failed_action_skipped = action.run_if == "dependency_failed" and status == "skipped"
        all_dependencies_succeeded = bool(action.depends_on) and all(
            dependency_id in direct_success for dependency_id in action.depends_on
        )
        if dependency_failed_action_skipped and all_dependencies_succeeded:
            outcomes[action.action_id] = ActionExecutionOutcome(
                action_id=action.action_id,
                satisfied=True,
                reason="fallback not needed because dependencies succeeded",
            )
            continue

        successful_fallbacks = successful_fallbacks_by_dependency.get(action.action_id, [])
        if status == "failed" and successful_fallbacks:
            outcomes[action.action_id] = ActionExecutionOutcome(
                action_id=action.action_id,
                satisfied=True,
                reason=(f"failed primary action replaced by successful fallback(s): {sorted(successful_fallbacks)}"),
            )
            continue

        merge_status = str(_get(row, "merge_status", "") or "")
        error = str(_get(row, "error", "") or "")
        reason = f"execution status={status!r}, merge_status={merge_status!r}"
        if error:
            reason += f": {error}"
        outcomes[action.action_id] = ActionExecutionOutcome(
            action_id=action.action_id,
            satisfied=False,
            reason=reason,
        )
    return outcomes


def role_execution_errors(
    plan: MemberOptimizationPlan,
    execution_results: list[Any],
    role: str,
) -> list[str]:
    """Return blocking execution errors for one candidate role."""
    planned = [action for action in plan.actions if action.role == role]
    if not planned:
        return ["role has no planned actions"]
    return [
        f"{action_id}: {outcome.reason}"
        for action_id, outcome in evaluate_role_execution(plan, execution_results, role).items()
        if not outcome.satisfied
    ]


def _is_merged_success(row: Any) -> bool:
    return str(_get(row, "status", "") or "") == "succeeded" and str(_get(row, "merge_status", "") or "") == "merged"


def _same_bundle_or_unscoped(left: Any, right: Any) -> bool:
    left_key = action_bundle_key(left)
    right_key = action_bundle_key(right)
    return left_key is None or right_key is None or left_key == right_key


def _get(value: Any, key: str, default: Any) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


__all__ = [
    "ActionExecutionOutcome",
    "action_bundle_key",
    "evaluate_role_execution",
    "role_execution_errors",
]
