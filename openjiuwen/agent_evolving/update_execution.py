# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared execution helpers for applying normalized evolution updates."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from openjiuwen.agent_evolving.types import ApplyResult, normalize_updates
from openjiuwen.core.operator import Operator


def execute_updates(
    operators: Mapping[str, Operator],
    updates: Mapping[tuple[str, str], Any],
) -> list[ApplyResult]:
    """Normalize and execute a batch of updates without persistence or approval."""
    results: list[ApplyResult] = []
    non_none_updates = {key: value for key, value in updates.items() if value is not None}

    for (operator_id, target), update in normalize_updates(non_none_updates).items():
        operator = operators.get(operator_id)
        if operator is None:
            results.append(
                ApplyResult(
                    operator_id=operator_id,
                    target=target,
                    applied=False,
                    mode=update.mode,
                    effect=update.effect,
                    value=update.payload,
                    change_type=update.change_type,
                    errors=[f"operator not found: {operator_id}"],
                    metadata=dict(update.metadata),
                )
            )
            continue

        results.append(operator.apply_update(target, update))

    for operator_id, target in (key for key, value in updates.items() if value is None):
        results.append(
            ApplyResult(
                operator_id=operator_id,
                target=target,
                applied=False,
                value=None,
                errors=["update value is None"],
            )
        )
    return results


def apply_updates(
    operators: Mapping[str, Operator],
    updates: Mapping[tuple[str, str], Any],
) -> list[ApplyResult]:
    """Compatibility alias for update execution."""
    return execute_updates(operators, updates)


def summarize_apply_results(results: Iterable[ApplyResult]) -> dict[str, int]:
    """Return simple aggregate counts for update execution."""
    result_list = list(results)
    applied = sum(1 for result in result_list if result.applied)
    failed = len(result_list) - applied
    return {
        "total": len(result_list),
        "applied": applied,
        "failed": failed,
    }


__all__ = [
    "execute_updates",
    "apply_updates",
    "summarize_apply_results",
]
