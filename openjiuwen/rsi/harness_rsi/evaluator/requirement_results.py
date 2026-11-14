# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Dataset-neutral per-requirement evaluation results.

Evaluators should materialize ``requirement_results`` in evaluation metadata.
The compatibility reader also converts the historical SWE-Bench
``instance_report.tests_status`` and WorkBuddy ``atomic_checks`` shapes so the
RSI control loop does not need benchmark-specific parsing.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

REQUIREMENT_RESULTS_SCHEMA_VERSION = 1
REQUIREMENT_GROUP = "requirement"
ATOMIC_CHECK_GROUP = "atomic_check"
FAIL_TO_PASS_GROUP = "fail_to_pass"
PASS_TO_PASS_GROUP = "pass_to_pass"


def requirement_results_contract(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build the serializable, versioned requirement-result contract."""
    return {
        "schema_version": REQUIREMENT_RESULTS_SCHEMA_VERSION,
        "items": normalize_requirement_results(items),
    }


def requirement_results_from_judge_criteria(
    criteria: Any,
    *,
    source: str = "judge_detail.criteria",
) -> dict[str, Any]:
    """Adapt criterion scores in the normalized ``[0, 1]`` range."""
    items: list[dict[str, Any]] = []
    if isinstance(criteria, list):
        for index, raw in enumerate(criteria, start=1):
            if not isinstance(raw, Mapping):
                continue
            requirement_id = str(raw.get("criterion_id") or raw.get("verifier_id") or f"criterion_{index}").strip()
            if not requirement_id:
                continue
            score = _finite_float(raw.get("score"))
            explicit_passed = raw.get("passed")
            passed: bool | None = explicit_passed if isinstance(explicit_passed, bool) else None
            if passed is None and score is not None:
                passed = score >= 1.0
            items.append(
                {
                    "requirement_id": requirement_id,
                    "group": REQUIREMENT_GROUP,
                    "passed": passed,
                    "score": score,
                    "evidence": str(raw.get("rationale") or raw.get("evidence") or ""),
                    "status": str(raw.get("status") or ""),
                    "source": str(raw.get("source") or source),
                }
            )
    return requirement_results_contract(items)


def evaluation_requirement_results(
    metadata: Mapping[str, Any],
    *,
    case_id: str = "",
) -> list[dict[str, Any]]:
    """Read the common contract, falling back to legacy evaluator metadata."""
    if "requirement_results" in metadata:
        return normalize_requirement_results(metadata.get("requirement_results"))

    items = _legacy_atomic_check_results(metadata.get("atomic_checks"))
    items.extend(_legacy_swe_test_results(metadata.get("instance_report"), case_id=case_id))
    return normalize_requirement_results(items)


def normalize_requirement_results(value: Any) -> list[dict[str, Any]]:
    """Normalize a versioned contract or a bare item list.

    Invalid entries are ignored. Requirement identity is the pair of ``group``
    and ``requirement_id``; the last occurrence wins so an adapter can replace
    an earlier incomplete observation deterministically.
    """
    raw_items = value.get("items") if isinstance(value, Mapping) else value
    if not isinstance(raw_items, list):
        return []

    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        requirement_id = str(raw.get("requirement_id") or raw.get("name") or raw.get("id") or "").strip()
        if not requirement_id:
            continue
        group = str(raw.get("group") or REQUIREMENT_GROUP).strip() or REQUIREMENT_GROUP
        passed = raw.get("passed")
        if not isinstance(passed, bool):
            passed = None
        score = _finite_float(raw.get("score"))
        by_identity[(group, requirement_id)] = {
            "requirement_id": requirement_id,
            "group": group,
            "passed": passed,
            "score": score,
            "evidence": str(raw.get("evidence") or raw.get("detail") or raw.get("rationale") or ""),
            "status": str(raw.get("status") or ""),
            "source": str(raw.get("source") or ""),
        }
    return [by_identity[key] for key in sorted(by_identity)]


def _legacy_atomic_check_results(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        requirement_id = str(raw.get("name") or raw.get("requirement_id") or "").strip()
        if not requirement_id:
            continue
        explicit_passed = raw.get("passed")
        items.append(
            {
                "requirement_id": requirement_id,
                "group": ATOMIC_CHECK_GROUP,
                # Absence is not failure evidence. Preserve an unknown verdict
                # so missing instrumentation cannot become a failed requirement.
                "passed": explicit_passed if isinstance(explicit_passed, bool) else None,
                "score": _finite_float(raw.get("score")),
                "evidence": str(raw.get("detail") or raw.get("evidence") or ""),
                "status": str(raw.get("status") or ""),
                "source": "evaluation.metadata.atomic_checks",
            }
        )
    return items


def _legacy_swe_test_results(value: Any, *, case_id: str) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    report = value.get(case_id) if case_id else None
    if not isinstance(report, Mapping) and len(value) == 1:
        report = next(iter(value.values()))
    if not isinstance(report, Mapping):
        return []
    tests_status = report.get("tests_status")
    if not isinstance(tests_status, Mapping):
        return []

    items: list[dict[str, Any]] = []
    for legacy_group, group in (
        ("FAIL_TO_PASS", FAIL_TO_PASS_GROUP),
        ("PASS_TO_PASS", PASS_TO_PASS_GROUP),
    ):
        group_status = tests_status.get(legacy_group)
        if not isinstance(group_status, Mapping):
            continue
        for outcome, passed in (("success", True), ("failure", False)):
            raw_ids = group_status.get(outcome)
            if not isinstance(raw_ids, (list, tuple, set)):
                continue
            for raw_id in raw_ids:
                requirement_id = str(raw_id).strip()
                if not requirement_id:
                    continue
                items.append(
                    {
                        "requirement_id": requirement_id,
                        "group": group,
                        "passed": passed,
                        "score": 1.0 if passed else 0.0,
                        "evidence": "",
                        "status": outcome,
                        "source": f"evaluation.metadata.instance_report.tests_status.{legacy_group}",
                    }
                )
    return items


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


__all__ = [
    "ATOMIC_CHECK_GROUP",
    "FAIL_TO_PASS_GROUP",
    "PASS_TO_PASS_GROUP",
    "REQUIREMENT_GROUP",
    "REQUIREMENT_RESULTS_SCHEMA_VERSION",
    "evaluation_requirement_results",
    "normalize_requirement_results",
    "requirement_results_contract",
    "requirement_results_from_judge_criteria",
]
