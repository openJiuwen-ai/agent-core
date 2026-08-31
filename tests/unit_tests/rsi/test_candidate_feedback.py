# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from copy import deepcopy

import pytest

from openjiuwen.rsi.harness_rsi.single_harness.candidate_feedback import (
    build_candidate_feedback_cohort,
    canonical_candidate_fingerprint,
    rank_candidate_proposals,
)
from openjiuwen.rsi.harness_rsi.improver_evolution.policy import VersionedImproverPolicy


def _capability(**overrides):
    capability = {
        "action_id": "act_001",
        "role": "office_worker",
        "action_group": "skill",
        "operation": "add",
        "target_path": "skills/precision_a/SKILL.md",
        "runtime_name": "precision_a",
        "target_case_ids": ["case_a"],
        "expected_effect": "Preserve full numeric precision before writing output.",
        "lever_decision": {"selected_lever": "investigation_method"},
        "optimization_hypothesis_ids": ["hyp_precision"],
    }
    capability.update(overrides)
    return capability


def _cohort(**overrides):
    cohort = {
        "cohort_id": "cohort_001",
        "run_id": "run_001",
        "parent_harness_ref": "harness/source",
        "source_eval_ref": "eval/source",
        "target_case_ids": ["case_a", "case_b"],
        "evaluation_protocol": "paired_target_v1",
        "rank_frozen": True,
        "ranking_policy": "static_priority_v1",
    }
    cohort.update(overrides)
    return cohort


def _candidate(
    candidate_id: str,
    candidate_index: int,
    predicted_rank: int,
    gain: float,
    **overrides,
):
    candidate = {
        "candidate_id": candidate_id,
        "candidate_index": candidate_index,
        "predicted_rank": predicted_rank,
        "predicted_score": 100.0 - predicted_rank,
        "ranking_policy": "static_priority_v1",
        "rank_frozen": True,
        "parent_harness_ref": "harness/source",
        "source_eval_ref": "eval/source",
        "target_case_ids": ["case_a", "case_b"],
        "evaluation_protocol": "paired_target_v1",
        "source_target_score": 0.2,
        "candidate_target_score": 0.2 + gain,
        "capabilities": [
            {
                **_capability(),
                "target_case_ids": ["case_a", "case_b"],
            }
        ],
        "pre_edit_invoked_skill_names_by_case": {
            "case_a": ["precision_a"],
            "case_b": ["precision_a"],
        },
        "regressed_target_case_ids": [],
        "verifier_deltas_by_case": {
            "case_a": {"newly_passed_atomic_checks": ["check_a"]},
            "case_b": {"remaining_failed_atomic_checks": ["check_b"]},
        },
    }
    candidate.update(overrides)
    return candidate


def test_fingerprint_ignores_action_id_and_filename_noise() -> None:
    first = _capability()
    renamed = _capability(
        action_id="act_completely_different",
        target_path="skills/renamed_package/ANOTHER_NAME.md",
        runtime_name="renamed_runtime",
    )

    assert canonical_candidate_fingerprint([first]) == canonical_candidate_fingerprint([renamed])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action_group", "tool"),
        ("operation", "modify"),
        ("target_family", "output_contract"),
        ("expected_effect", "Validate formulas before submission."),
        ("lever_decision", {"selected_lever": "verification_policy"}),
        ("optimization_hypothesis_ids", ["hyp_other"]),
    ],
)
def test_fingerprint_covers_required_semantics(field: str, value: object) -> None:
    baseline = canonical_candidate_fingerprint([_capability()])

    assert canonical_candidate_fingerprint([_capability(**{field: value})]) != baseline


def test_fingerprint_is_independent_of_capability_order() -> None:
    first = _capability()
    second = _capability(
        action_group="tool",
        operation="modify",
        target_path="tools/checker.py",
        runtime_name="checker",
        expected_effect="Check the workbook before submission.",
    )

    assert canonical_candidate_fingerprint([first, second]) == canonical_candidate_fingerprint([second, first])


def test_rank_candidate_proposals_freezes_static_rank_but_preserves_input_order() -> None:
    duplicate = {
        "candidate_id": "duplicate",
        "candidate_index": 30,
        "capabilities": [_capability(target_case_ids=["case_a", "case_b"])],
    }
    atomic = {
        "candidate_id": "atomic",
        "candidate_index": 20,
        "capabilities": [
            _capability(
                target_family="different_family",
                optimization_hypothesis_ids=["hyp_different"],
                target_case_ids=["case_a", "case_b"],
            )
        ],
    }
    original = {
        "candidate_id": "original",
        "candidate_index": 10,
        "capabilities": [_capability(target_case_ids=["case_a", "case_b"])],
    }
    non_executable = {
        "candidate_id": "blocked",
        "candidate_index": 5,
        "is_executable": False,
        "capabilities": [_capability(target_case_ids=["case_a", "case_b"])],
    }
    proposals = [duplicate, atomic, original, non_executable]
    untouched = deepcopy(proposals)

    ranked = rank_candidate_proposals(proposals, frozen_target_case_ids={"case_a", "case_b"})

    assert proposals == untouched
    assert [item["candidate_id"] for item in ranked] == ["duplicate", "atomic", "original", "blocked"]
    by_id = {item["candidate_id"]: item for item in ranked}
    assert by_id["original"]["predicted_rank"] == 1
    assert by_id["atomic"]["predicted_rank"] == 2
    assert by_id["duplicate"]["predicted_rank"] == 3
    assert by_id["blocked"]["predicted_rank"] == 4
    assert by_id["duplicate"]["ranking_features"]["duplicate_of_candidate_id"] == "original"
    assert all(item["ranking_policy"] == "static_priority_v1" for item in ranked)
    assert all(item["rank_frozen"] is True for item in ranked)


def test_rank_uses_candidate_index_as_stable_tie_breaker() -> None:
    proposals = [
        {
            "candidate_id": "later",
            "candidate_index": 8,
            "capabilities": [_capability(target_family="family_later")],
        },
        {
            "candidate_id": "earlier",
            "candidate_index": 3,
            "capabilities": [_capability(target_family="family_earlier")],
        },
    ]

    ranked = rank_candidate_proposals(proposals, frozen_target_case_ids={"case_a"})

    by_id = {item["candidate_id"]: item for item in ranked}
    assert by_id["earlier"]["predicted_rank"] == 1
    assert by_id["later"]["predicted_rank"] == 2


def test_versioned_policy_changes_pre_execution_rank_without_using_outcomes() -> None:
    proposals = [
        {
            "candidate_id": "broad",
            "candidate_index": 1,
            "capabilities": [
                _capability(target_case_ids=["case_a", "case_b"]),
                _capability(
                    action_group="tool",
                    operation="modify",
                    target_case_ids=["case_a", "case_b"],
                    target_family="verification",
                ),
            ],
        },
        {
            "candidate_id": "atomic",
            "candidate_index": 2,
            "capabilities": [_capability(target_case_ids=["case_a"])],
        },
    ]
    policy = VersionedImproverPolicy(
        version_id="I_coverage_down",
        parent_version_id="I0",
        training_ledger_digest="sha256:ledger",
        ranking_weights={
            "executable": 100.0,
            "coverage": -20.0,
            "atomicity": 5.0,
            "duplicate": -30.0,
        },
    )

    ranked = rank_candidate_proposals(
        proposals,
        frozen_target_case_ids={"case_a", "case_b"},
        improver_policy=policy,
    )

    by_id = {item["candidate_id"]: item for item in ranked}
    assert by_id["atomic"]["predicted_rank"] == 1
    assert by_id["broad"]["predicted_rank"] == 2
    assert {item["improver_version_id"] for item in ranked} == {"I_coverage_down"}
    assert all(item["improver_policy_digest"] == policy.canonical_digest for item in ranked)


def test_build_cohort_computes_search_metrics_only_for_comparable_frozen_candidates() -> None:
    candidates = [
        _candidate("predicted_best", 0, 1, 0.2),
        _candidate("selected", 1, 2, 0.1),
        _candidate("oracle_best", 2, 3, 0.4),
    ]

    ledger = build_candidate_feedback_cohort(
        cohort=_cohort(),
        candidates=candidates,
        selected_candidate_id="selected",
        top_m=2,
    )

    assert ledger["schema_version"] == 1
    assert ledger["metrics"]["best_of_k_gain"] == {
        "status": "available",
        "value": pytest.approx(0.4),
        "candidate_id": "oracle_best",
        "k": 3,
    }
    assert ledger["metrics"]["top_m_gain"] == {
        "status": "available",
        "value": pytest.approx(0.2),
        "candidate_id": "predicted_best",
        "requested_m": 2,
        "effective_m": 2,
    }
    assert ledger["metrics"]["selection_regret"] == {
        "status": "available",
        "value": pytest.approx(0.2),
        "best_candidate_id": "oracle_best",
        "predicted_top1_candidate_id": "predicted_best",
    }
    assert ledger["selection"]["selected_candidate_id"] == "selected"
    assert ledger["selection"]["promotion_policy"] == "best_realized_qualified_candidate"
    assert ledger["selection"]["top_m_role"] == "counterfactual_metric_only"


def test_build_cohort_preserves_pre_evaluation_causal_prediction_and_assessment() -> None:
    candidate = _candidate(
        "candidate",
        1,
        1,
        0.0,
        causal_intervention_contracts=[
            {
                "action_id": "act_001",
                "target_case_ids": ["case_a", "case_b"],
                "source_causal_hypothesis_id": "h_artifact",
                "predicted_behavior_and_outcome": "the selected behavior changes and the target passes",
                "prediction_recorded_before_evaluation": True,
            }
        ],
        candidate_failure_diagnoses={
            "case_a": [
                {
                    "prior_experiment_assessment": {
                        "availability": "available",
                        "intervention_activated": "yes",
                        "predicted_behavior_occurred": "yes",
                        "predicted_outcome_occurred": "no",
                        "causal_hypothesis_status": "falsified",
                    }
                }
            ]
        },
    )

    ledger = build_candidate_feedback_cohort(
        cohort=_cohort(),
        candidates=[candidate],
        selected_candidate_id="",
        top_m=1,
    )

    experiment = ledger["candidates"][0]["causal_experiment"]
    assert experiment["prediction_recorded_before_evaluation"] is True
    assert experiment["intervention_contracts"][0]["source_causal_hypothesis_id"] == "h_artifact"
    assert experiment["assessment_by_case"]["case_a"][0]["causal_hypothesis_status"] == "falsified"


def test_prompt_activation_uses_paired_analyzer_behavior_assessment() -> None:
    candidate = _candidate(
        "candidate",
        1,
        1,
        0.0,
        capabilities=[
            _capability(
                action_group="prompt",
                operation="modify",
                target_path="system_prompt.md",
                runtime_name="",
                target_case_ids=["case_a"],
            )
        ],
        candidate_failure_diagnoses={
            "case_a": [
                {
                    "prior_experiment_assessment": {
                        "intervention_activated": "unknown",
                        "predicted_behavior_occurred": "no",
                        "predicted_outcome_occurred": "no",
                    }
                }
            ]
        },
    )

    ledger = build_candidate_feedback_cohort(
        cohort=_cohort(target_case_ids=["case_a"]),
        candidates=[candidate],
        selected_candidate_id="",
        top_m=1,
    )

    activation = ledger["candidates"][0]["activation"]
    assert activation["availability"] == "observed"
    assert activation["state"] == "unknown"
    assert activation["observation_source"] == "candidate_failure_analysis"
    assert activation["predicted_behavior_occurred"] == "no"


def test_build_cohort_accepts_a_versioned_ranking_policy_and_records_improver() -> None:
    cohort = _cohort(
        ranking_policy="improver_policy_v2",
        improver_version_id="I_2",
        improver_policy_digest="sha256:policy-v2",
    )
    candidates = [
        _candidate("first", 0, 1, 0.1, ranking_policy="improver_policy_v2"),
        _candidate("second", 1, 2, 0.2, ranking_policy="improver_policy_v2"),
    ]

    ledger = build_candidate_feedback_cohort(
        cohort=cohort,
        candidates=candidates,
        selected_candidate_id="second",
    )

    assert ledger["cohort"]["improver_version_id"] == "I_2"
    assert ledger["cohort"]["improver_policy_digest"] == "sha256:policy-v2"
    assert ledger["metrics"]["best_of_k_gain"]["status"] == "available"


def test_build_cohort_marks_k_one_search_metrics_unavailable() -> None:
    ledger = build_candidate_feedback_cohort(
        cohort=_cohort(),
        candidates=[_candidate("only", 0, 1, 0.3)],
        selected_candidate_id="only",
    )

    for metric in ledger["metrics"].values():
        assert metric["status"] == "unavailable"
        assert metric["value"] is None
        assert metric["reason"] == "requires_at_least_two_comparable_candidates"


@pytest.mark.parametrize(
    ("candidate_override", "cohort_override", "reason"),
    [
        ({"parent_harness_ref": "harness/other"}, {}, "parent_harness_mismatch"),
        ({"source_eval_ref": "eval/other"}, {}, "source_evaluation_mismatch"),
        ({"target_case_ids": ["case_a"]}, {}, "target_case_set_mismatch"),
        ({"evaluation_protocol": "other_protocol"}, {}, "evaluation_protocol_mismatch"),
        ({}, {"rank_frozen": False}, "rank_not_frozen_before_evaluation"),
    ],
)
def test_build_cohort_rejects_non_comparable_search_metrics(
    candidate_override: dict[str, object],
    cohort_override: dict[str, object],
    reason: str,
) -> None:
    candidates = [
        _candidate("first", 0, 1, 0.1),
        _candidate("second", 1, 2, 0.2, **candidate_override),
    ]

    ledger = build_candidate_feedback_cohort(
        cohort=_cohort(**cohort_override),
        candidates=candidates,
        selected_candidate_id="first",
    )

    assert ledger["metrics"]["best_of_k_gain"]["status"] == "unavailable"
    assert ledger["metrics"]["best_of_k_gain"]["reason"] == reason


def test_build_cohort_reports_observed_pre_edit_trigger_without_claiming_causality() -> None:
    candidate = _candidate(
        "partial_trigger",
        0,
        1,
        0.2,
        pre_edit_invoked_skill_names_by_case={
            "case_a": ["precision-a"],
            "case_b": [],
        },
    )

    ledger = build_candidate_feedback_cohort(
        cohort=_cohort(),
        candidates=[candidate],
        selected_candidate_id="partial_trigger",
    )
    activation = ledger["candidates"][0]["activation"]

    assert activation == {
        "availability": "observed",
        "state": "not_triggered",
        "surfaces": ["skill"],
        "expected_pair_count": 2,
        "observed_pre_edit_pair_count": 1,
        "missing_pair_count": 1,
        "trigger_rate": 0.5,
    }


def test_build_cohort_does_not_turn_prompt_activation_into_false() -> None:
    candidate = _candidate(
        "prompt",
        0,
        1,
        0.1,
        capabilities=[
            _capability(
                action_group="prompt",
                target_path="prompt_sections/output.md",
                runtime_name="",
            )
        ],
        pre_edit_invoked_skill_names_by_case={},
    )

    ledger = build_candidate_feedback_cohort(
        cohort=_cohort(),
        candidates=[candidate],
        selected_candidate_id="prompt",
    )
    activation = ledger["candidates"][0]["activation"]

    assert activation["availability"] == "not_instrumented"
    assert activation["state"] == "not_instrumented"
    assert activation["trigger_rate"] is None


def test_build_cohort_distinguishes_missing_trigger_artifact_from_not_triggered() -> None:
    candidate = _candidate("missing_trace", 0, 1, 0.1)
    candidate.pop("pre_edit_invoked_skill_names_by_case")

    ledger = build_candidate_feedback_cohort(
        cohort=_cohort(),
        candidates=[candidate],
        selected_candidate_id="missing_trace",
    )

    assert ledger["candidates"][0]["activation"]["availability"] == "missing_artifact"
    assert ledger["candidates"][0]["activation"]["trigger_rate"] is None


def test_build_cohort_never_attributes_epoch_or_non_target_regression_to_candidate() -> None:
    candidate = _candidate(
        "regression",
        0,
        1,
        -0.1,
        regressed_target_case_ids=["case_b"],
        regressed_non_target_case_ids=["unrelated"],
        epoch_checkpoint_outcome={"correlated_regression_case_ids": ["another_case"]},
    )

    ledger = build_candidate_feedback_cohort(
        cohort=_cohort(),
        candidates=[candidate],
        selected_candidate_id="regression",
    )
    regression = ledger["candidates"][0]["regression"]

    assert regression["target"] == {
        "availability": "observed",
        "regressed_case_count": 1,
        "rate": 0.5,
    }
    assert regression["non_target"] == {
        "availability": "not_evaluated",
        "regressed_case_count": None,
        "rate": None,
        "reason": "candidate_feedback_is_target_local",
    }


def test_build_cohort_keeps_verifier_and_cost_evidence_compact() -> None:
    candidate = _candidate(
        "compact",
        0,
        1,
        0.1,
        candidate_failure_diagnoses={"case_a": [{"root_cause": "large generated claim"}]},
        candidate_patch_excerpts_by_case={"case_a": "large patch body"},
    )
    candidate["verifier_deltas_by_case"]["case_a"].update(
        {
            "newly_passed_requirements": ["criterion:a", "criterion:b"],
            "remaining_failed_requirements": ["criterion:c"],
        }
    )
    candidate["verifier_deltas_by_case"]["case_b"]["regressed_requirements"] = ["criterion:d"]

    ledger = build_candidate_feedback_cohort(
        cohort=_cohort(),
        candidates=[candidate],
        selected_candidate_id="compact",
    )
    record = ledger["candidates"][0]

    assert record["verifier_summary"]["newly_passed_atomic_checks_count"] == 1
    assert record["verifier_summary"]["remaining_failed_atomic_checks_count"] == 1
    assert record["verifier_summary"]["newly_passed_requirements_count"] == 2
    assert record["verifier_summary"]["remaining_failed_requirements_count"] == 1
    assert record["verifier_summary"]["regressed_requirements_count"] == 1
    assert record["cost"]["availability"] == "not_instrumented"
    serialized = str(record)
    assert "large generated claim" not in serialized
    assert "large patch body" not in serialized


def test_build_cohort_requires_positive_top_m() -> None:
    with pytest.raises(ValueError, match="top_m must be at least 1"):
        build_candidate_feedback_cohort(
            cohort=_cohort(),
            candidates=[],
            selected_candidate_id="",
            top_m=0,
        )
