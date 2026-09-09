# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Dataset-neutral scoring, adapted from the original LLM-as-judge contract."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from openjiuwen.rsi.harness_rsi.data_loader.grading_contract import normalize_grading_case
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
            field_value = item.get(key)
            if not isinstance(field_value, str) or not field_value.strip():
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
    case = normalize_grading_case(case)
    reference = case.get("reference", {})
    if not isinstance(reference, dict):
        raise ValueError("reference must be an object")
    answer_role = reference.get("answer_role", "criterion")
    if answer_role not in {"criterion", "reference"}:
        raise ValueError("reference.answer_role must be criterion or reference")
    if reference.get("penalty_mode", "ceiling") not in {"ceiling", "subtract"}:
        raise ValueError("reference.penalty_mode must be ceiling or subtract")
    behaviors = _normalize_items(reference.get("required_behaviors", []))
    rubric = reference.get("rubric", [])
    if not isinstance(rubric, list):
        raise ValueError("reference.rubric must be a list of strings")
    for index, text in enumerate(rubric, 1):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("reference.rubric must contain non-empty strings")
        behaviors.append({"id": f"rubric_{index:03d}", "description": text, "weight": 1.0})
    if _reference_answer(case) is not None and answer_role == "criterion":
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


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate judge JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite judge JSON value: {value}")


def parse_judge_output(raw: str) -> dict[str, Any]:
    """Accept one complete JSON verdict, optionally fenced with surrounding prose."""
    text = raw.strip()
    if not text.startswith(("{", "[")) and "```" in text:
        match = re.search(
            r"^```(?:json)?[ \t]*\r?\n(.*?)^```[ \t]*(?:\r?\n|$)",
            text,
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        if match is None:
            raise ValueError("judge output must contain one complete JSON fence")
        outside = text[:match.start()] + text[match.end():]
        if "```" in outside or any(char in outside for char in "{}[]"):
            raise ValueError("ambiguous judge output: more than one structured payload")
        text = match[1].strip()
    parsed = json.loads(text, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
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
    parsed: dict[str, Any],
    behaviors: list[dict[str, Any]],
    forbidden: list[dict[str, Any]],
    *,
    penalty_mode: str = "ceiling",
) -> tuple[float, dict[str, Any], dict[str, Any]]:
    """Validate complete coverage before applying trusted weights and penalties."""
    _required_text(parsed, "overall_reason")
    if penalty_mode not in {"ceiling", "subtract"}:
        raise ValueError("penalty_mode must be ceiling or subtract")
    results = _result_items(parsed.get("behaviors"), behaviors, "behaviors")
    hits = _result_items(parsed.get("forbidden_hits", []), forbidden, "forbidden_hits")
    for result, criterion in zip(results, behaviors):
        result["score"] = finite_number(result.get("score"), minimum=0, maximum=1, name="score")
        _required_text(result, "reason")
        _required_text(result, "evidence")
        result["description"] = criterion["description"]
        result["weight"] = criterion["weight"]
    for hit, criterion in zip(hits, forbidden):
        if not isinstance(hit.get("triggered"), bool):
            raise ValueError("forbidden triggered must be a boolean")
        _required_text(hit, "reason")
        _required_text(hit, "evidence")
        hit["penalty"] = criterion["penalty"]
        hit["description"] = criterion["description"]
    total_weight = sum(item["weight"] for item in behaviors)
    score = sum(result["score"] * item["weight"] for result, item in zip(results, behaviors)) / total_weight
    base_score = score
    penalties = [hit["penalty"] for hit in hits if hit["triggered"]]
    if penalties:
        if penalty_mode == "subtract":
            score = max(0.0, score - math.fsum(penalties))
        else:
            # Existing callers retain the legacy ceiling, not cumulative deductions.
            score = min(score, 1.0 - max(penalties))
    dimensions = compute_dimensions(results)
    dimensions["triggered_forbidden_behaviors"] = [hit["id"] for hit in hits if hit["triggered"]]
    normalized = {
        "overall_reason": parsed["overall_reason"],
        "overall_score": score,
        "base_score": base_score,
        "penalty_mode": penalty_mode,
        "total_deduction": base_score - score,
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
