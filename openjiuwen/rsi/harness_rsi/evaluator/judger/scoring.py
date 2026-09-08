# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Dataset-neutral scoring, adapted from the original LLM-as-judge contract."""

from __future__ import annotations

import json
import math
from typing import Any

from openjiuwen.rsi.harness_rsi.evaluator.judger.base import _reference_answer
from openjiuwen.rsi.harness_rsi.evaluator.requirement_results import requirement_results_contract


def finite_number(value: Any, *, minimum: float, maximum: float, name: str) -> float:
    """Reject booleans, coercible strings and non-finite model scores."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be finite and between {minimum} and {maximum}")
    return float(value)


def _normalize_items(raw: Any, *, forbidden: bool = False) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("behavior requirements must be a list")
    items = []
    for index, value in enumerate(raw, 1):
        item = {"id": value, "description": value} if isinstance(value, str) else value
        if not isinstance(item, dict):
            raise ValueError("each behavior must be a string or object")
        item = dict(item)
        item.setdefault("id", item.get("description", f"behavior_{index}"))
        item.setdefault("description", item["id"])
        for key in ("id", "description"):
            if not isinstance(item[key], str) or not item[key].strip():
                raise ValueError(f"behavior {key} must be a non-empty string")
        if forbidden:
            item["penalty"] = finite_number(item.get("penalty", 0.3), minimum=0, maximum=1, name="penalty")
        else:
            item["weight"] = finite_number(item.get("weight", 1.0), minimum=0, maximum=1e6, name="weight")
            if not item["weight"]:
                raise ValueError("behavior weight must be positive")
        items.append(item)
    _unique_ids(items)
    return items


def _unique_ids(items: list[dict[str, Any]]) -> None:
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("behavior IDs must be unique")


def scoring_contract(case: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Accept answer/rubric and retain the old required/forbidden behavior format."""
    reference = case.get("reference", {})
    if not isinstance(reference, dict):
        raise ValueError("reference must be an object")
    behaviors = _normalize_items(reference.get("required_behaviors", []))
    rubric = reference.get("rubric", [])
    if not isinstance(rubric, list):
        raise ValueError("reference.rubric must be a list of strings")
    for index, text in enumerate(rubric, 1):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("reference.rubric must contain non-empty strings")
        behaviors.append({"id": f"rubric_{index:03d}", "description": text, "weight": 1.0})
    if _reference_answer(case) is not None:
        behaviors.insert(
            0,
            {
                "id": "reference_answer",
                "description": "The answer matches the reference and satisfies explicit output constraints.",
                "weight": 1.0,
            },
        )
    if not behaviors:
        raise ValueError("llm_as_judge requires reference.answer, reference.rubric or reference.required_behaviors")
    _unique_ids(behaviors)
    return behaviors, _normalize_items(reference.get("forbidden_behaviors", []), forbidden=True)


def parse_judge_output(raw: str) -> dict[str, Any]:
    """Allow a single fenced object, but never silently repair missing score fields."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[-1].strip() != "```":
            raise ValueError("judge JSON fence is incomplete")
        text = "\n".join(lines[1:-1])
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("judge output must be an object")
    return parsed


def _result_items(raw: Any, expected: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError(f"{name} must be a list of objects")
    ids = [item.get("id") for item in raw]
    if not all(isinstance(value, str) for value in ids):
        raise ValueError(f"{name} IDs must be strings")
    if len(ids) != len(set(ids)) or set(ids) != {item["id"] for item in expected}:
        raise ValueError(f"{name} must score every supplied ID exactly once, with no additional IDs")
    by_id = {item["id"]: dict(item) for item in raw}
    return [by_id[item["id"]] for item in expected]


def _required_text(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"judge {key} must be a non-empty string")
    return value


def score_judge_output(
    parsed: dict[str, Any], behaviors: list[dict[str, Any]], forbidden: list[dict[str, Any]]
) -> tuple[float, dict[str, Any], dict[str, Any]]:
    """Validate complete coverage before applying trusted weights and penalties."""
    _required_text(parsed, "overall_reason")
    results = _result_items(parsed.get("behaviors"), behaviors, "behaviors")
    hits = _result_items(parsed.get("forbidden_hits", []), forbidden, "forbidden_hits")
    for result in results:
        result["score"] = finite_number(result.get("score"), minimum=0, maximum=1, name="score")
        _required_text(result, "reason")
        _required_text(result, "evidence")
    for hit, criterion in zip(hits, forbidden):
        if not isinstance(hit.get("triggered"), bool):
            raise ValueError("forbidden triggered must be a boolean")
        _required_text(hit, "reason")
        _required_text(hit, "evidence")
        hit["penalty"] = criterion["penalty"]
    total_weight = sum(item["weight"] for item in behaviors)
    score = sum(result["score"] * item["weight"] for result, item in zip(results, behaviors)) / total_weight
    penalties = [hit["penalty"] for hit in hits if hit["triggered"]]
    if penalties:
        # Preserve the legacy ceiling: do not penalize the same defect twice.
        score = min(score, 1.0 - max(penalties))
    dimensions = compute_dimensions(results)
    normalized = {
        "overall_reason": parsed["overall_reason"],
        "overall_score": score,
        "behaviors": results,
        "forbidden_hits": hits,
        "dimensions": dimensions,
    }
    requirements = [
        {
            "requirement_id": item["id"],
            "group": "requirement",
            "score": item["score"],
            "passed": item["score"] == 1.0,
            "evidence": item["evidence"],
            "source": "llm_as_judge.behaviors",
        }
        for item in results
    ]
    requirements.extend(
        {
            "requirement_id": hit["id"],
            "group": "forbidden",
            "score": 0.0 if hit["triggered"] else 1.0,
            "passed": not hit["triggered"],
            "evidence": hit["evidence"],
            "source": "llm_as_judge.forbidden_hits",
        }
        for hit in hits
    )
    return score, normalized, requirement_results_contract(requirements)


def compute_dimensions(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep the historical Analyzer input contract without generating optimization advice."""
    scores = {item["id"]: item["score"] for item in results}
    low = [key for key, score in scores.items() if score < 1.0]
    return {
        "per_behavior_scores": scores,
        "low_score_behaviors": low,
        "avg_behavior_score": sum(scores.values()) / len(scores),
        "behavior_count": len(scores),
        "pass_count": len(scores) - len(low),
        "fail_count": len(low),
        "behavior_diagnostics": {
            item["id"]: {
                "reason": item["reason"],
                "failure_reason": item["reason"] if item["score"] < 1.0 else "",
                "evidence": item["evidence"],
            }
            for item in results
        },
    }
