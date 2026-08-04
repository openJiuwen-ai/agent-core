# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pure scoring helpers for LLM-as-judge outputs."""

from __future__ import annotations

import json
import re
from typing import Any

_BEHAVIOR_PASS_THRESHOLD = 0.5


def parse_judge_output(raw: str) -> dict[str, Any] | None:
    """Extract structured judge JSON from raw LLM text."""
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if match:
        candidate = match.group(1)
    else:
        start = raw.find("{")
        if start == -1:
            return None
        depth = 0
        end = -1
        for index, char in enumerate(raw[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end == -1:
            return None
        candidate_end = end + 1
        candidate = raw[start:candidate_end]

    try:
        data = json.loads(candidate)
    except ValueError:
        return None

    if not isinstance(data, dict) or "behaviors" not in data:
        return None
    return data


def normalize_behaviors(raw: list[str | dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize required_behaviors to dicts with id, description, and weight."""
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            result.append({"id": item, "description": item, "weight": 1.0})
        elif isinstance(item, dict):
            behavior = dict(item)
            behavior.setdefault("id", behavior.get("description", f"behavior_{index}"))
            behavior.setdefault("description", behavior["id"])
            behavior.setdefault("weight", 1.0)
            result.append(behavior)
    return result


def normalize_forbidden(raw: list[str | dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize forbidden_behaviors to dicts with id, description, and penalty."""
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            result.append({"id": item, "description": item, "penalty": 0.3})
        elif isinstance(item, dict):
            forbidden = dict(item)
            forbidden.setdefault("id", forbidden.get("description", f"forbidden_{index}"))
            forbidden.setdefault("description", forbidden["id"])
            forbidden.setdefault("penalty", 0.3)
            result.append(forbidden)
    return result


def aggregate_score(
    behavior_results: list[dict[str, Any]],
    forbidden_hits: list[dict[str, Any]],
    behaviors: list[dict[str, Any]],
) -> float:
    """Compute overall_score from behavior scores and forbidden penalties."""
    if not behavior_results:
        return 0.0

    weight_map = {item["id"]: float(item.get("weight", 1.0)) for item in behaviors}
    total_weight = sum(weight_map.get(str(result.get("id", "")), 1.0) for result in behavior_results)
    if total_weight <= 0:
        return 0.0

    weighted_sum = sum(
        float(result.get("score", 0.0)) * weight_map.get(str(result.get("id", "")), 1.0) for result in behavior_results
    )
    overall = weighted_sum / total_weight

    penalties = [float(hit.get("penalty", 0.3)) for hit in forbidden_hits if hit.get("triggered", False)]
    if penalties:
        # Generated rubrics commonly express the same contract twice: once as
        # a required behavior and once as its forbidden inverse. Subtracting a
        # penalty after the behavior score already fell double-counts one defect.
        # Treat forbidden penalties as score ceilings instead: a hit still caps
        # an otherwise high score, while an already-low behavior score is not
        # reduced a second time.
        overall = min(overall, 1.0 - max(penalties))
    return max(0.0, min(1.0, overall))


def compute_dimensions(
    behavior_results: list[dict[str, Any]],
    threshold: float = _BEHAVIOR_PASS_THRESHOLD,
) -> dict[str, Any]:
    """Compute per-behavior statistics for downstream signal extraction."""
    per_behavior_scores = {
        str(result.get("id", f"behavior_{index}")): float(result.get("score", 0.0))
        for index, result in enumerate(behavior_results)
    }
    low_score_behaviors = [behavior_id for behavior_id, score in per_behavior_scores.items() if score < threshold]
    behavior_count = len(per_behavior_scores)
    average_score = sum(per_behavior_scores.values()) / behavior_count if behavior_count > 0 else 0.0
    pass_count = sum(1 for score in per_behavior_scores.values() if score >= threshold)
    behavior_diagnostics = {
        str(result.get("id", f"behavior_{index}")): _behavior_diagnostic(result)
        for index, result in enumerate(behavior_results)
    }
    return {
        "per_behavior_scores": per_behavior_scores,
        "low_score_behaviors": low_score_behaviors,
        "avg_behavior_score": round(average_score, 4),
        "behavior_count": behavior_count,
        "pass_count": pass_count,
        "fail_count": behavior_count - pass_count,
        "behavior_diagnostics": behavior_diagnostics,
    }


def _behavior_diagnostic(result: dict[str, Any]) -> dict[str, str]:
    return {
        "reason": str(result.get("reason", "") or ""),
        "failure_reason": str(result.get("failure_reason", "") or ""),
        "missing_capability": str(result.get("missing_capability", "") or ""),
        "suggested_surface_hint": str(result.get("suggested_surface_hint", "") or ""),
        "evidence": str(result.get("evidence", "") or ""),
    }


__all__ = [
    "aggregate_score",
    "compute_dimensions",
    "normalize_behaviors",
    "normalize_forbidden",
    "parse_judge_output",
]
