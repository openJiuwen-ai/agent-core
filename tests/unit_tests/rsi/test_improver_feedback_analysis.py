# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from openjiuwen.rsi.improver_evolution.feedback_analysis import analyze_candidate_feedback_ledgers
from openjiuwen.rsi.improver_evolution.policy import (
    default_improver_policy,
    propose_policy_candidates,
)
from openjiuwen.rsi.single_harness.candidate_feedback import build_candidate_feedback_cohort


def _raw_candidate(
    cohort_id: str,
    candidate_index: int,
    predicted_rank: int,
    gain: float,
    *,
    surface: str = "skill",
    triggered: bool = True,
    regressed: bool = False,
    semantic_variant: str = "",
    partial_requirements: bool = False,
) -> dict:
    candidate_id = f"{cohort_id}_candidate_{candidate_index}"
    runtime_name = f"runtime_{cohort_id}_{candidate_index}"
    capability = {
        "action_id": f"action_{candidate_index}",
        "role": "office_worker",
        "action_group": surface,
        "operation": "add",
        "target_path": f"{surface}s/{runtime_name}/artifact.md",
        "runtime_name": runtime_name if surface in {"skill", "tool"} else "",
        "target_case_ids": [f"{cohort_id}_case"],
        "expected_effect": f"effect_{semantic_variant or candidate_index}",
        "optimization_hypothesis_ids": [f"hypothesis_{semantic_variant or candidate_index}"],
    }
    candidate = {
        "candidate_id": candidate_id,
        "candidate_index": candidate_index,
        "predicted_rank": predicted_rank,
        "predicted_score": 100.0 - predicted_rank,
        "ranking_policy": "static_priority_v1",
        "rank_frozen": True,
        "parent_harness_ref": f"harness/{cohort_id}",
        "source_eval_ref": f"eval/{cohort_id}",
        "target_case_ids": [f"{cohort_id}_case"],
        "evaluation_protocol": "paired_target_v1",
        "source_target_score": 0.2,
        "candidate_target_score": 0.2 + gain,
        "capabilities": [capability],
        "regressed_target_case_ids": [f"{cohort_id}_case"] if regressed else [],
        "verifier_deltas_by_case": {
            f"{cohort_id}_case": {
                "partial_progress": partial_requirements,
                "newly_passed_requirements": ([{"requirement_id": "r1"}] if partial_requirements else []),
                "remaining_failed_requirements": ([{"requirement_id": "r2"}] if partial_requirements else []),
                "regressed_requirements": [],
            }
        },
    }
    if surface in {"skill", "tool"}:
        candidate[f"pre_edit_invoked_{surface}_names_by_case"] = {
            f"{cohort_id}_case": [runtime_name] if triggered else []
        }
    return candidate


def _cohort(
    cohort_id: str,
    gains: list[float],
    *,
    top_m: int = 1,
    selected_index: int = 0,
    trigger_failure_index: int | None = None,
    regression_index: int | None = None,
    surface: str = "skill",
    partial_requirements: bool = False,
) -> dict:
    candidates = [
        _raw_candidate(
            cohort_id,
            index,
            index + 1,
            gain,
            surface=surface,
            triggered=index != trigger_failure_index,
            regressed=index == regression_index,
            partial_requirements=partial_requirements,
        )
        for index, gain in enumerate(gains)
    ]
    return build_candidate_feedback_cohort(
        cohort={
            "cohort_id": cohort_id,
            "run_id": "run_001",
            "parent_harness_ref": f"harness/{cohort_id}",
            "source_eval_ref": f"eval/{cohort_id}",
            "target_case_ids": [f"{cohort_id}_case"],
            "evaluation_protocol": "paired_target_v1",
            "rank_frozen": True,
            "ranking_policy": "static_priority_v1",
        },
        candidates=candidates,
        selected_candidate_id=candidates[selected_index]["candidate_id"],
        top_m=top_m,
    )


def _ledger(*cohorts: dict) -> dict:
    return {
        "schema_version": 1,
        "ledger_type": "single_harness_sibling_candidate_feedback",
        "cohorts": list(cohorts),
    }


def test_analyzes_multiple_ledgers_and_emits_stable_evidence_backed_patterns(tmp_path: Path) -> None:
    first = _cohort(
        "cohort_a",
        [0.1, 0.2, 0.5],
        top_m=2,
        trigger_failure_index=1,
        regression_index=2,
    )
    second = _cohort(
        "cohort_b",
        [0.05, 0.4],
        top_m=1,
        trigger_failure_index=0,
        regression_index=1,
    )
    path = tmp_path / "second.yaml"
    path.write_text(yaml.safe_dump(_ledger(second), sort_keys=False), encoding="utf-8")

    analysis = analyze_candidate_feedback_ledgers(
        [_ledger(first), path],
        min_support_cohorts=2,
    )

    assert analysis["input_summary"] == {"ledger_count": 2, "cohort_count": 2, "candidate_count": 5}
    assert analysis["training_ledger_digest"].startswith("sha256:")
    assert analysis["comparability"]["comparable_cohort_count"] == 2
    assert analysis["metric_availability"]["best_of_k_gain"]["available_count"] == 2
    outside = analysis["outcomes"]["high_value_candidate_outside_top_m"]
    assert outside == {
        "support_cohort_count": 2,
        "opportunity_cohort_count": 2,
        "rate": 1.0,
        "evidence_cohort_ids": ["cohort_a", "cohort_b"],
    }

    patterns = {item["pattern_id"]: item for item in analysis["stable_patterns"]}
    assert set(patterns) == {
        "high_value_candidate_outside_top_m",
        "target_regression:skill",
        "trigger_observed_failure:skill",
    }
    assert patterns["high_value_candidate_outside_top_m"]["recommended_policy_change"] == {
        "field": "budget_policy.top_m",
        "operation": "increase",
        "value": 1,
        "rationale": "The realized best positive-gain candidate repeatedly ranked outside the current Top-m budget.",
    }
    assert patterns["trigger_observed_failure:skill"]["support_cohorts"] == 2
    assert patterns["trigger_observed_failure:skill"]["opportunity_cohorts"] == 2
    assert patterns["trigger_observed_failure:skill"]["evidence_cohort_ids"] == ["cohort_a", "cohort_b"]
    json.dumps(analysis)


def test_unavailable_evidence_is_counted_but_never_treated_as_failure() -> None:
    cohort = _cohort("cohort_prompt", [0.1], surface="prompt")
    candidate = cohort["candidates"][0]
    candidate["regression"]["target"] = {
        "availability": "not_evaluated",
        "regressed_case_count": None,
        "rate": None,
        "reason": "target_pair_not_run",
    }

    analysis = analyze_candidate_feedback_ledgers(_ledger(cohort), min_support_cohorts=1)

    assert analysis["comparability"]["comparable_cohort_count"] == 0
    assert analysis["metric_availability"]["best_of_k_gain"] == {
        "available_count": 0,
        "unavailable_count": 1,
        "unavailable_reasons": {"requires_at_least_two_comparable_candidates": 1},
    }
    prompt = analysis["surfaces"]["prompt"]
    assert prompt["trigger_observed_failure"]["availability_counts"] == {"not_instrumented": 1}
    assert prompt["trigger_observed_failure"]["observed_opportunity_candidate_count"] == 0
    assert prompt["trigger_observed_failure"]["failure_candidate_count"] == 0
    assert prompt["trigger_observed_failure"]["rate"] is None
    assert prompt["target_regression"]["availability_counts"] == {"not_evaluated": 1}
    assert prompt["target_regression"]["observed_opportunity_candidate_count"] == 0
    assert analysis["stable_patterns"] == []


def test_partial_requirement_progress_evolves_residual_repair_directive() -> None:
    first = _cohort("partial_a", [0.2, 0.1], top_m=2, partial_requirements=True)
    second = _cohort("partial_b", [0.3, 0.1], top_m=2, partial_requirements=True)

    analysis = analyze_candidate_feedback_ledgers(
        _ledger(first, second),
        min_support_cohorts=2,
    )

    pattern = next(
        item
        for item in analysis["stable_patterns"]
        if item["pattern_id"] == "partial_candidate_has_residual_requirements"
    )
    assert pattern["support_cohorts"] == 2
    assert pattern["opportunity_cohorts"] == 2
    assert pattern["recommended_policy_change"]["field"] == (
        "generation_directives.preserve_partial_progress_and_target_residual"
    )
    candidates = propose_policy_candidates(default_improver_policy(), analysis)
    evolved = next(
        candidate
        for candidate in candidates
        if candidate.generation_directives.get("preserve_partial_progress_and_target_residual") is True
    )
    assert evolved.parent_version_id == "I0"


def test_invalid_partial_progress_count_is_not_treated_as_policy_evidence() -> None:
    cohort = _cohort("partial_invalid", [0.2, 0.1], top_m=2, partial_requirements=True)
    for candidate in cohort["candidates"]:
        candidate["verifier_summary"]["partial_progress_case_count"] = "unknown"

    analysis = analyze_candidate_feedback_ledgers(_ledger(cohort), min_support_cohorts=1)

    outcome = analysis["outcomes"]["partial_candidate_residual_repair"]
    assert outcome["opportunity_cohort_count"] == 1
    assert outcome["support_cohort_count"] == 0
    assert all(
        item["pattern_id"] != "partial_candidate_has_residual_requirements" for item in analysis["stable_patterns"]
    )


def test_all_nonpositive_and_duplicates_require_minimum_cohort_support() -> None:
    first = _cohort("cohort_a", [-0.1, 0.0])
    second = _cohort("cohort_b", [-0.2, -0.1])
    for cohort in (first, second):
        cohort["candidates"][1]["candidate_fingerprint"] = cohort["candidates"][0]["candidate_fingerprint"]

    one_support = analyze_candidate_feedback_ledgers(_ledger(first), min_support_cohorts=2)
    analysis = analyze_candidate_feedback_ledgers(_ledger(first, second), min_support_cohorts=2)

    assert one_support["stable_patterns"] == []
    assert analysis["outcomes"]["all_candidates_nonpositive"]["support_cohort_count"] == 2
    assert analysis["duplicates"] == {
        "availability": "observed",
        "duplicate_candidate_count": 2,
        "opportunity_candidate_count": 4,
        "rate": 0.5,
        "support_cohort_count": 2,
        "evidence_cohort_ids": ["cohort_a", "cohort_b"],
    }
    patterns = {item["pattern_id"]: item for item in analysis["stable_patterns"]}
    assert patterns["all_candidates_nonpositive"]["recommended_policy_change"]["field"].startswith(
        "generation_directives."
    )
    assert patterns["duplicate_candidates"]["recommended_policy_change"]["field"] == (
        "generation_directives.require_unique_candidate_fingerprint"
    )


def test_comparability_is_recomputed_instead_of_trusting_available_metrics() -> None:
    cohort = _cohort("cohort_bad", [0.1, 0.2])
    cohort["candidates"][1]["identity"]["source_eval_ref"] = "eval/other"

    analysis = analyze_candidate_feedback_ledgers(cohort)

    assert analysis["metric_availability"]["best_of_k_gain"]["available_count"] == 1
    assert analysis["comparability"]["comparable_cohort_count"] == 0
    assert analysis["comparability"]["non_comparable_reasons"] == {"source_eval_ref_mismatch": 1}
    assert analysis["outcomes"]["all_candidates_nonpositive"]["rate"] is None


def test_emits_ranking_weight_change_only_for_repeated_isolated_feature_misalignment() -> None:
    cohorts = []
    for cohort_id in ("cohort_a", "cohort_b"):
        cohort = _cohort(cohort_id, [0.1, 0.4], selected_index=0)
        cohort["candidates"][0]["proposal_rank"]["ranking_features"] = {
            "coverage": 1.0,
            "atomicity": 1.0,
        }
        cohort["candidates"][1]["proposal_rank"]["ranking_features"] = {
            "coverage": 0.5,
            "atomicity": 1.0,
        }
        cohorts.append(cohort)

    analysis = analyze_candidate_feedback_ledgers(_ledger(*cohorts), min_support_cohorts=2)

    evidence = analysis["feature_outcome_alignment"]["coverage"]
    assert evidence["comparable_pair_count"] == 2
    assert evidence["aligned_pair_count"] == 0
    assert evidence["opposite_pair_count"] == 2
    assert evidence["decrease_weight_evidence"] == {
        "pair_count": 2,
        "cohort_count": 2,
        "cohort_ids": ["cohort_a", "cohort_b"],
    }
    pattern = next(item for item in analysis["stable_patterns"] if item["pattern_type"] == "ranking_misalignment")
    assert pattern["evidence_cohort_ids"] == ["cohort_a", "cohort_b"]
    assert pattern["recommended_policy_change"] == {
        "field": "ranking_weights.coverage",
        "operation": "decrease",
        "value": 0.1,
        "rationale": (
            "Isolated coverage pairs repeatedly opposed realized ordering in cohorts with positive selection regret."
        ),
    }
    policy_candidates = propose_policy_candidates(default_improver_policy(), analysis)
    assert len(policy_candidates) == 2
    assert any(candidate.ranking_weights["coverage"] == 19.9 for candidate in policy_candidates)


def test_does_not_guess_ranking_weight_without_features_or_positive_regret() -> None:
    no_features = _cohort("cohort_no_features", [0.1, 0.4], selected_index=0)
    no_regret = _cohort("cohort_no_regret", [0.4, 0.1], selected_index=0)
    for candidate, coverage in zip(no_regret["candidates"], (1.0, 0.5), strict=True):
        candidate["proposal_rank"]["ranking_features"] = {
            "coverage": coverage,
            "atomicity": 1.0,
        }

    analysis = analyze_candidate_feedback_ledgers(
        _ledger(no_features, no_regret),
        min_support_cohorts=1,
    )

    assert analysis["feature_outcome_alignment"]["coverage"]["aligned_pair_count"] == 1
    assert not any(item["pattern_type"] == "ranking_misalignment" for item in analysis["stable_patterns"])


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda ledger: ledger.update(schema_version=2), "schema_version"),
        (
            lambda ledger: ledger["cohorts"][0]["candidates"][0]["activation"].update(availability="unknown"),
            "activation.availability",
        ),
        (
            lambda ledger: ledger["cohorts"][0]["metrics"]["top_m_gain"].update(
                status="unavailable",
                value=0.0,
                reason="missing",
            ),
            "value must be null",
        ),
    ],
)
def test_strict_schema_rejects_invalid_or_ambiguous_availability(mutate, message: str) -> None:
    ledger = _ledger(_cohort("cohort_a", [0.1, 0.2]))
    mutate(ledger)

    with pytest.raises(ValueError, match=message):
        analyze_candidate_feedback_ledgers(ledger)


def test_rejects_duplicate_cohort_ids_across_inputs() -> None:
    cohort = _cohort("cohort_a", [0.1, 0.2])

    with pytest.raises(ValueError, match="duplicate cohort_id"):
        analyze_candidate_feedback_ledgers([_ledger(cohort), deepcopy(cohort)])


def test_reads_json_path_and_validates_parameters(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(_ledger(_cohort("cohort_a", [0.1, 0.2]))), encoding="utf-8")

    analysis = analyze_candidate_feedback_ledgers(path)

    assert analysis["input_summary"]["cohort_count"] == 1
    with pytest.raises(ValueError, match="min_support_cohorts"):
        analyze_candidate_feedback_ledgers(path, min_support_cohorts=0)
    with pytest.raises(ValueError, match="high_value_gain_threshold"):
        analyze_candidate_feedback_ledgers(path, high_value_gain_threshold=float("nan"))
