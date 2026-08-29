# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from openjiuwen.rsi.improver_evolution.meta_validation import (
    MetaValidationThresholds,
    paired_meta_validate,
)


def _metrics(**overrides: float) -> dict[str, float]:
    metrics = {
        "best_of_k_gain": 0.30,
        "top_m_gain": 0.20,
        "selection_regret": 0.10,
        "final_harness_gain_per_budget": 0.002,
        "regression_failure_rate": 0.05,
        "infrastructure_failure_rate": 0.02,
    }
    metrics.update(overrides)
    return metrics


def _record(checkpoint_id: str, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "checkpoint_id": checkpoint_id,
        "split": "meta_test",
        "improver_version_id": "improver-v1",
        "improver_policy_digest": "sha256:baseline",
        "base_harness_id": f"harness_{checkpoint_id}",
        "failure_evidence_id": f"evidence_{checkpoint_id}",
        "base_model_id": "deepseek-v4",
        "k": 3,
        "top_m": 2,
        "token_budget": 100_000,
        "tool_budget": 40,
        "runtime_budget": 1800,
        "verifier_id": "workbuddy-office-v1",
        "protocol_id": "paired-live-v1",
        "metrics": _metrics(),
    }
    record.update(overrides)
    return record


def _paired_live(count: int = 3) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    baseline = [_record(f"cp_{index}") for index in range(count)]
    candidate: list[dict[str, object]] = []
    for item in baseline:
        changed = deepcopy(item)
        changed["improver_version_id"] = "improver-v2-candidate"
        changed["improver_policy_digest"] = "sha256:candidate"
        changed["metrics"] = _metrics(
            best_of_k_gain=0.31,
            top_m_gain=0.22,
            selection_regret=0.07,
            final_harness_gain_per_budget=0.0022,
            regression_failure_rate=0.04,
            infrastructure_failure_rate=0.01,
        )
        candidate.append(changed)
    return baseline, candidate


def test_live_validation_emits_paired_deltas_macro_averages_and_eligible_decision() -> None:
    baseline, candidate = _paired_live()

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=list(reversed(candidate)),
        mode="live_generation",
    )

    assert report["validation"] == {
        "status": "accepted",
        "checkpoint_count": 3,
        "unseen_checkpoint_count": 3,
        "reason_codes": [],
        "issues": [],
        "metrics_status": "complete",
    }
    assert report["checkpoint_ids"] == ["cp_0", "cp_1", "cp_2"]
    assert report["baseline_improver"] == {
        "version_id": "improver-v1",
        "policy_digest": "sha256:baseline",
    }
    assert report["candidate_improver"] == {
        "version_id": "improver-v2-candidate",
        "policy_digest": "sha256:candidate",
    }
    assert report["checkpoints"][0]["deltas"]["top_m_gain"] == pytest.approx(0.02)
    assert report["checkpoints"][0]["directional_improvements"]["selection_regret"] == pytest.approx(0.03)
    assert report["macro_averages"]["selection_regret"] == {
        "baseline": pytest.approx(0.10),
        "candidate": pytest.approx(0.07),
        "delta": pytest.approx(-0.03),
        "improvement": pytest.approx(0.03),
        "direction": "lower_is_better",
    }
    assert report["promotion"] == {
        "status": "eligible",
        "scope": "full_improver",
        "reason_codes": ["promotion_thresholds_satisfied"],
    }
    json.dumps(report)


def test_offline_rerank_uses_only_ranker_metrics_and_never_promotes_full_improver() -> None:
    baseline, candidate = _paired_live()
    for item in baseline + candidate:
        item["metrics"].pop("best_of_k_gain")
        item["metrics"].pop("final_harness_gain_per_budget")
        item["metrics"].pop("regression_failure_rate")
        item["metrics"].pop("infrastructure_failure_rate")

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="offline_rerank",
    )

    assert report["required_metrics"] == ["top_m_gain", "selection_regret"]
    assert set(report["macro_averages"]) == {"top_m_gain", "selection_regret"}
    assert report["promotion"] == {
        "status": "inconclusive",
        "scope": "ranker_evidence_only",
        "offline_ranker_assessment": "eligible",
        "reason_codes": ["offline_rerank_cannot_promote_full_improver"],
    }


def test_missing_any_required_live_metric_is_inconclusive() -> None:
    baseline, candidate = _paired_live()
    candidate[1]["metrics"].pop("best_of_k_gain")

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="live_generation",
    )

    assert report["validation"]["status"] == "accepted"
    assert report["validation"]["metrics_status"] == "incomplete"
    assert report["validation"]["reason_codes"] == ["missing_required_metric"]
    assert report["promotion"] == {
        "status": "inconclusive",
        "scope": "full_improver",
        "reason_codes": ["missing_required_metric"],
    }
    assert report["checkpoints"] == []
    assert report["macro_averages"] == {}


@pytest.mark.parametrize(
    "field",
    [
        "base_harness_id",
        "failure_evidence_id",
        "base_model_id",
        "k",
        "top_m",
        "token_budget",
        "tool_budget",
        "runtime_budget",
        "verifier_id",
        "protocol_id",
    ],
)
def test_rejects_every_frozen_pair_mismatch(field: str) -> None:
    baseline, candidate = _paired_live()
    candidate[0][field] = 4 if field == "k" else f"different_{field}"

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="live_generation",
    )

    assert report["validation"]["status"] == "rejected"
    assert "frozen_field_mismatch" in report["validation"]["reason_codes"]
    assert report["promotion"]["status"] == "inconclusive"
    assert report["checkpoints"] == []


def test_rejects_checkpoint_set_mismatch_without_computing_partial_statistics() -> None:
    baseline, candidate = _paired_live()
    candidate.pop()

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="live_generation",
    )

    assert report["validation"]["reason_codes"] == ["checkpoint_set_mismatch"]
    assert report["macro_averages"] == {}


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("field", ["improver_version_id", "improver_policy_digest"])
def test_rejects_missing_improver_identity_field(side: str, field: str) -> None:
    baseline, candidate = _paired_live()
    records = baseline if side == "baseline" else candidate
    records[0].pop(field)

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="live_generation",
    )

    assert report["validation"]["status"] == "rejected"
    assert "missing_improver_identity_field" in report["validation"]["reason_codes"]
    assert report["promotion"]["status"] == "inconclusive"


@pytest.mark.parametrize("field", ["improver_version_id", "improver_policy_digest"])
def test_rejects_identity_that_changes_between_checkpoints_on_one_side(field: str) -> None:
    baseline, candidate = _paired_live()
    candidate[1][field] = f"different-{field}"

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="live_generation",
    )

    assert "inconsistent_improver_identity" in report["validation"]["reason_codes"]
    assert report["candidate_improver"]["version_id" if field == "improver_version_id" else "policy_digest"] == ""
    assert report["promotion"]["status"] == "inconclusive"


def test_rejects_same_policy_digest_even_when_version_ids_differ() -> None:
    baseline, candidate = _paired_live()
    for item in candidate:
        item["improver_policy_digest"] = "sha256:baseline"

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="live_generation",
    )

    assert report["validation"]["reason_codes"] == ["identical_improver_policy_digest"]
    assert report["promotion"]["status"] == "inconclusive"


@pytest.mark.parametrize("pollution", ["split", "known_train_id"])
def test_rejects_meta_train_pollution(pollution: str) -> None:
    baseline, candidate = _paired_live()
    train_ids: set[str] = set()
    if pollution == "split":
        baseline[0]["split"] = "meta_train"
        candidate[0]["split"] = "meta_train"
    else:
        train_ids.add("cp_0")

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="live_generation",
        meta_train_checkpoint_ids=train_ids,
    )

    expected = "non_meta_test_checkpoint" if pollution == "split" else "meta_train_contamination"
    assert expected in report["validation"]["reason_codes"]
    assert report["promotion"]["status"] == "inconclusive"


def test_live_requires_configured_minimum_unseen_checkpoint_count() -> None:
    baseline, candidate = _paired_live(count=2)

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="live_generation",
    )

    assert report["promotion"] == {
        "status": "inconclusive",
        "scope": "full_improver",
        "reason_codes": ["insufficient_unseen_checkpoints"],
    }


def test_k_one_cannot_supply_search_meta_validation_evidence() -> None:
    baseline, candidate = _paired_live()
    for item in baseline + candidate:
        item["k"] = 1
        item["top_m"] = 1

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="live_generation",
    )

    assert report["validation"]["status"] == "rejected"
    assert report["validation"]["reason_codes"] == ["search_metrics_require_k_at_least_two"]
    assert report["promotion"]["status"] == "inconclusive"


@pytest.mark.parametrize(
    ("metric", "candidate_value", "reason"),
    [
        ("top_m_gain", 0.19, "top_m_gain_decreased"),
        ("selection_regret", 0.10, "selection_regret_not_improved"),
        ("best_of_k_gain", 0.29, "best_of_k_gain_decreased"),
        (
            "final_harness_gain_per_budget",
            0.0019,
            "final_harness_gain_per_budget_decreased",
        ),
        ("regression_failure_rate", 0.06, "regression_failure_rate_increased"),
        (
            "infrastructure_failure_rate",
            0.03,
            "infrastructure_failure_rate_increased",
        ),
    ],
)
def test_live_enforces_each_promotion_boundary(metric: str, candidate_value: float, reason: str) -> None:
    baseline, candidate = _paired_live()
    for item in candidate:
        item["metrics"][metric] = candidate_value

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="live_generation",
    )

    assert report["promotion"]["status"] == "ineligible"
    assert reason in report["promotion"]["reason_codes"]


def test_configured_non_degradation_tolerance_is_applied_to_macro_delta() -> None:
    baseline, candidate = _paired_live()
    for item in candidate:
        item["metrics"]["top_m_gain"] = 0.195

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="live_generation",
        thresholds={"top_m_gain_non_degradation_tolerance": 0.01},
    )

    assert report["promotion"]["status"] == "eligible"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda baseline, candidate: candidate[0]["metrics"].__setitem__("regression_failure_rate", 1.1),
        lambda baseline, candidate: baseline[0].__setitem__("top_m", 4),
        lambda baseline, candidate: baseline[0].__setitem__("token_budget", -1),
    ],
)
def test_invalid_values_reject_or_make_metrics_incomplete(mutator) -> None:
    baseline, candidate = _paired_live()
    mutator(baseline, candidate)

    report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="live_generation",
    )

    assert report["promotion"]["status"] == "inconclusive"
    assert report["validation"]["reason_codes"]


def test_duplicate_checkpoint_and_invalid_mode_are_explicitly_rejected() -> None:
    baseline, candidate = _paired_live()
    duplicate_report = paired_meta_validate(
        baseline_results=[*baseline, deepcopy(baseline[0])],
        candidate_results=candidate,
        mode="live_generation",
    )
    invalid_mode_report = paired_meta_validate(
        baseline_results=baseline,
        candidate_results=candidate,
        mode="not-a-mode",
    )

    assert duplicate_report["validation"]["reason_codes"] == ["duplicate_checkpoint_id"]
    assert invalid_mode_report["validation"]["reason_codes"] == ["invalid_mode"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_unseen_checkpoints": 0},
        {"min_unseen_checkpoints": 3.0},
        {"min_selection_regret_improvement": -0.1},
        {"max_regression_failure_rate_increase": float("nan")},
    ],
)
def test_thresholds_reject_invalid_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        MetaValidationThresholds(**kwargs)
