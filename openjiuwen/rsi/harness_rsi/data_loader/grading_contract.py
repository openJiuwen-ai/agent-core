# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Normalize grading aliases without inferring rules from task content."""

from __future__ import annotations

import math
import re
from typing import Any


_ITEM = re.compile(
    r"^\s*(?:\d+[.)\u3001]\s*)?\[\s*"
    r"(?P<kind>weight|deduct(?:ion)?|\u6743\u91cd|\u6263(?:\u5206)?)\s*[:\uff1a]?\s*"
    r"(?P<amount>\d+(?:\.\d+)?)\s*[%\uff05]\s*\]\s*(?P<description>.+)$",
    re.IGNORECASE,
)
_NUMBERED = re.compile(r"^\s*\d+[.)\u3001]\s*")
_HEADING = re.compile(r"^\s*(?:\u3010[^\u3011]+\u3011|#{1,6}\s)")


def parse_weighted_rubric(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read numbered percentage items; reject ambiguous weights instead of guessing.

    Supports [weight 10%] / [deduct 6%] and the equivalent Chinese labels.
    Descriptive preambles, headings and multiline item descriptions are retained
    by the source field; only explicitly annotated items become scoring rules.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("judge_rubrics must be non-empty percentage-annotated text")
    positive: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []
    current = None
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _ITEM.fullmatch(line)
        if match:
            amount = float(match["amount"]) / 100
            is_weight = match["kind"].lower() in {"weight", "\u6743\u91cd"}
            if not math.isfinite(amount) or not 0 < amount <= 1:
                raise ValueError("rubric percentages must be greater than 0 and at most 100")
            items = positive if is_weight else negative
            current = {
                "id": f"{'rubric' if is_weight else 'deduction'}_{len(items) + 1:03d}",
                "description": match["description"].strip(),
                "weight" if is_weight else "penalty": amount,
            }
            items.append(current)
        elif _NUMBERED.match(line) or line.lstrip().startswith("["):
            raise ValueError("unrecognized rubric item; use explicit [weight N%] or [deduct N%] annotations")
        elif _HEADING.match(line):
            current = None
        elif current is not None:
            current["description"] += "\n" + line.strip()
    if not positive and not negative:
        raise ValueError("judge_rubrics contains no supported percentage-annotated items")
    if positive and not math.isclose(math.fsum(item["weight"] for item in positive), 1.0, abs_tol=1e-9):
        raise ValueError("positive rubric percentages must sum to 100; weights will not be silently rescaled")
    return positive, negative


def _set_consistent(target: dict[str, Any], field: str, value: Any) -> None:
    if field in target and target[field] != value:
        raise ValueError(f"conflicting grading fields for reference.{field}")
    target[field] = value


def normalize_grading_case(case: dict[str, Any]) -> dict[str, Any]:
    """Map explicit aliases into the existing reference contract, without mutation."""
    normalized = dict(case)
    if "case_id" not in normalized and "id" in normalized:
        normalized["case_id"] = normalized["id"]
    if "reference_solution" not in case and "judge_rubrics" not in case:
        return normalized
    raw_reference = case.get("reference", {})
    if not isinstance(raw_reference, dict):
        raise ValueError("reference must be an object")
    reference = dict(raw_reference)
    if "reference_solution" in case:
        _set_consistent(reference, "answer", case["reference_solution"])
    if "judge_rubrics" in case:
        positive, negative = parse_weighted_rubric(case["judge_rubrics"])
        if reference.get("rubric"):
            raise ValueError("judge_rubrics cannot be combined with reference.rubric")
        _set_consistent(reference, "required_behaviors", positive)
        _set_consistent(reference, "forbidden_behaviors", negative)
        _set_consistent(reference, "penalty_mode", "subtract")
        _set_consistent(reference, "answer_role", "reference" if positive else "criterion")
    normalized["reference"] = reference
    return normalized
