# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Continuous promotion rules and the evidence identities authorizing them."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError

EPSILON = 1e-9


def digest(value: Any) -> str:
    """Hash JSON-compatible provenance without rounding scores."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def package_fingerprint(refs_path: str) -> str:
    """Identify package bytes independently of the directory used to publish them."""
    refs = Path(refs_path).expanduser().resolve()
    payload = yaml.safe_load(refs.read_text(encoding="utf-8"))
    roles = {}
    for role, raw in payload.get("harness_refs", {}).items():
        root = Path(raw).expanduser()
        root = (refs.parent / root).resolve() if not root.is_absolute() else root.resolve()
        if not root.is_dir():
            raise EvaluationInfrastructureError("Harness package directory is unavailable")
        files = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if {".git", "__pycache__", ".pytest_cache"}.intersection(relative.parts) or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_file():
                if not path.resolve().is_relative_to(root):
                    raise EvaluationInfrastructureError("Harness package links outside its snapshot")
                files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        roles[str(role)] = files
    if not roles:
        raise EvaluationInfrastructureError("Harness refs contain no packages")
    return digest(roles)


def complete_scores(eval_ref_path: str, expected_case_ids: set[str]) -> dict[str, float]:
    """Never average missing, invalid, skipped, or legacy binary LLM grades."""
    source = Path(eval_ref_path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    scores = {}
    for row in payload.get("cases", []):
        case_id = str(row.get("case_id", ""))
        score = row.get("score")
        if case_id in scores or not case_id:
            raise EvaluationInfrastructureError("Evaluation contains duplicate or missing case identities")
        if (isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score) or not 0 <= score <= 1 or row.get("status") not in {"passed", "failed"}
                or (row.get("metadata") or {}).get("infrastructure_skip")):
            raise EvaluationInfrastructureError(f"Invalid correctness score for {case_id}")
        result_path = Path(row["result_path"])
        result_path = source.parent / result_path if not result_path.is_absolute() else result_path
        result = json.loads(result_path.read_text(encoding="utf-8"))
        evaluation = result.get("evaluation", {})
        if (result.get("execution_status") == "error" or result.get("status") not in {"passed", "failed"}
                or result.get("score") != score):
            raise EvaluationInfrastructureError(f"Inconsistent or failed execution for {case_id}")
        if evaluation.get("method") == "llm_as_judge" and evaluation.get("metadata", {}).get("score_mode") != "continuous":
            raise EvaluationInfrastructureError("Binary LLM grades cannot enter a continuous promotion run")
        scores[case_id] = float(score)
    if set(scores) != expected_case_ids or not scores:
        raise EvaluationInfrastructureError("Evaluation does not exactly cover its frozen case set")
    return scores


def mean_score(scores: dict[str, float]) -> float:
    """Average a previously validated nonempty set of continuous case scores."""
    if not scores:
        raise EvaluationInfrastructureError("Cannot score an empty evaluation")
    return sum(scores.values()) / len(scores)


def first_improved(score: float, baseline: float, threshold: float) -> bool:
    """The first gain must strictly exceed the configured absolute margin."""
    return score - baseline > threshold + EPSILON


def targets_improved(
    scores: dict[str, float], baseline: dict[str, float], targets: set[str], threshold: float,
) -> bool:
    """Every target must independently exceed its own frozen baseline margin."""
    return bool(targets) and all(
        case_id in scores and case_id in baseline
        and first_improved(scores[case_id], baseline[case_id], threshold)
        for case_id in targets
    )


def full_evaluation_decision(
    scores: dict[str, float], baseline: dict[str, float], targets: set[str],
    *, max_regression: float, proof: dict[str, Any], identity: str,
) -> dict[str, Any]:
    """Decide the second and final evaluation without averaging away case failures."""
    if (not baseline or set(scores) != set(baseline) or not targets or not targets <= set(baseline)
            or proof.get("identity") != identity or proof.get("qualified") is not True
            or set(proof.get("target_case_ids", [])) != targets
            or proof.get("baseline_scores_digest") != digest(baseline)):
        raise EvaluationInfrastructureError("Full evaluation lacks its matching target improvement proof")
    failed_targets = sorted(case_id for case_id in targets if scores[case_id] - baseline[case_id] <= EPSILON)
    regressed_others = sorted(case_id for case_id in set(baseline) - targets
                             if baseline[case_id] - scores[case_id] > max_regression + EPSILON)
    baseline_mean = mean_score(baseline)
    score = mean_score(scores)
    average_improved = score - baseline_mean > EPSILON
    reason = ("target_case_not_improved" if failed_targets else "non_target_case_regressed" if regressed_others
              else "full_score_not_improved" if not average_improved else "two_stage_improvement_confirmed")
    return {
        "accepted": not failed_targets and not regressed_others and average_improved,
        "reason": reason, "score": score, "baseline_score": baseline_mean,
        "full_score_delta": score - baseline_mean,
        "target_cases_improved": not failed_targets,
        "non_target_cases_retained": not regressed_others, "full_average_improved": average_improved,
        "failed_target_case_ids": failed_targets, "regressed_non_target_case_ids": regressed_others,
        "case_score_deltas": {case_id: scores[case_id] - baseline[case_id] for case_id in sorted(scores)},
    }
