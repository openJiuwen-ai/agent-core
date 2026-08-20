# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest
import yaml

from openjiuwen.rsi.improver_evolution.policy import (
    VersionedImproverPolicy,
    canonical_policy_digest,
    default_improver_policy,
    load_improver_policy,
    propose_policy_candidates,
    propose_policy_update,
    score_static_priority,
    write_improver_policy,
)


def _change(field: str, operation: str, value: object) -> dict[str, object]:
    return {
        "field": field,
        "operation": operation,
        "value": value,
        "rationale": "Supported by repeated paired candidate evidence.",
    }


def _pattern(
    pattern_id: str,
    change: dict[str, object],
    *,
    support: int = 3,
    rate: float = 0.75,
) -> dict[str, object]:
    return {
        "pattern_id": pattern_id,
        "type": "candidate_selection_failure",
        "surface": "skill",
        "support": support,
        "opportunity": 4,
        "rate": rate,
        "evidence_cohort_ids": ["cohort_002", "cohort_001"],
        "recommended_policy_change": change,
    }


def _analysis(*patterns: dict[str, object]) -> dict[str, object]:
    return {
        "training_ledger_digest": "sha256:ledger123",
        "stable_patterns": list(patterns),
    }


def test_default_i0_matches_current_static_priority_score() -> None:
    policy = default_improver_policy()

    assert policy.version_id == "I0"
    assert policy.parent_version_id is None
    assert dict(policy.ranking_weights) == {
        "executable": 100.0,
        "coverage": 20.0,
        "atomicity": 5.0,
        "duplicate": -30.0,
    }
    assert score_static_priority(
        policy,
        {
            "executable": True,
            "coverage": 0.5,
            "atomicity": 0.25,
            "duplicate": True,
        },
    ) == pytest.approx(81.25)


def test_policy_is_recursively_immutable() -> None:
    policy = VersionedImproverPolicy(
        version_id="I_test",
        parent_version_id="I0",
        training_ledger_digest="sha256:test",
        generation_directives={"require_activation_evidence": {"skill": False}},
    )

    with pytest.raises(FrozenInstanceError):
        policy.version_id = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        policy.ranking_weights["coverage"] = 99.0  # type: ignore[index]
    with pytest.raises(TypeError):
        policy.generation_directives["require_activation_evidence"]["skill"] = True  # type: ignore[index]


def test_default_generation_policy_is_strictly_no_op() -> None:
    assert default_improver_policy().generation_directives == {}


def test_policy_constructor_does_not_retain_mutable_input() -> None:
    directives = {"require_activation_evidence": {"skill": False}}
    policy = VersionedImproverPolicy(
        version_id="I_test",
        parent_version_id="I0",
        training_ledger_digest="sha256:test",
        generation_directives=directives,
    )

    directives["require_activation_evidence"]["skill"] = True

    assert policy.generation_directives["require_activation_evidence"]["skill"] is False


@pytest.mark.parametrize(
    "weights",
    [
        {
            "executable": 100.0,
            "coverage": 20.0,
            "atomicity": 5.0,
            "duplicate": -30.0,
            "mystery": 1.0,
        },
        {
            "executable": 100.0,
            "coverage": 20.0,
            "atomicity": 5.0,
        },
        {
            "executable": 100.0,
            "coverage": math.inf,
            "atomicity": 5.0,
            "duplicate": -30.0,
        },
    ],
)
def test_policy_rejects_unknown_missing_and_non_finite_weights(weights: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="ranking weight|finite"):
        VersionedImproverPolicy(
            version_id="I_invalid",
            parent_version_id="I0",
            training_ledger_digest="sha256:test",
            ranking_weights=weights,
        )


@pytest.mark.parametrize(
    "budget_policy",
    [
        {"top_m": 0, "min_pattern_support": 2},
        {"top_m": 1, "min_pattern_support": True},
        {"top_m": 1},
    ],
)
def test_policy_rejects_invalid_selection_budget(budget_policy: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="budget policy"):
        VersionedImproverPolicy(
            version_id="I_invalid",
            parent_version_id="I0",
            training_ledger_digest="sha256:test",
            budget_policy=budget_policy,
        )


def test_score_rejects_unknown_or_non_finite_features() -> None:
    policy = default_improver_policy()
    features = {
        "executable": True,
        "coverage": 1.0,
        "atomicity": 1.0,
        "duplicate": False,
    }

    with pytest.raises(ValueError, match="unknown ranking features"):
        score_static_priority(policy, {**features, "unknown": 1.0})
    with pytest.raises(ValueError, match="must be finite"):
        score_static_priority(policy, {**features, "coverage": math.nan})


def test_canonical_digest_is_stable_across_mapping_order() -> None:
    first = default_improver_policy().to_dict()
    second = dict(reversed(list(first.items())))
    second["ranking_weights"] = dict(reversed(list(first["ranking_weights"].items())))

    assert canonical_policy_digest(first) == canonical_policy_digest(second)
    assert canonical_policy_digest(default_improver_policy()) == canonical_policy_digest(first)


def test_yaml_round_trip_is_atomic_and_serializable(tmp_path) -> None:
    destination = tmp_path / "nested" / "policy.yaml"
    policy = default_improver_policy()

    returned = write_improver_policy(destination, policy)

    assert returned == destination
    assert load_improver_policy(destination) == policy
    assert yaml.safe_load(destination.read_text(encoding="utf-8")) == policy.to_dict()
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_supported_nested_generation_change_creates_new_immutable_version() -> None:
    parent = default_improver_policy()
    analysis = _analysis(
        _pattern(
            "activation_skill",
            _change("generation_directives.require_activation_evidence.skill", "set", True),
        )
    )

    child = propose_policy_update(parent, analysis)

    assert child is not None
    assert child.parent_version_id == "I0"
    assert child.training_ledger_digest == "sha256:ledger123"
    assert child.generation_directives["require_activation_evidence"]["skill"] is True
    assert parent.generation_directives == {}
    assert child.evidence_refs == ("cohort_001", "cohort_002", "pattern:activation_skill")


def test_policy_version_id_is_deterministic() -> None:
    parent = default_improver_policy()
    pattern = _pattern(
        "unique",
        _change("generation_directives.require_unique_candidate_fingerprint", "set", True),
    )

    first = propose_policy_update(parent, _analysis(pattern))
    second = propose_policy_update(parent, _analysis(pattern))

    assert first is not None
    assert second is not None
    assert first.version_id == second.version_id
    assert first == second


def test_each_candidate_applies_only_one_supported_change_in_stable_order() -> None:
    parent = default_improver_policy()
    weaker = _pattern(
        "weaker",
        _change("generation_directives.avoid_target_regression.prompt", "set", True),
        support=2,
        rate=0.5,
    )
    stronger = _pattern(
        "stronger",
        _change("generation_directives.require_distinct_intervention_surfaces", "set", True),
        support=5,
        rate=0.9,
    )

    candidates = propose_policy_candidates(parent, _analysis(weaker, stronger))

    assert len(candidates) == 2
    assert candidates[0].generation_directives["require_distinct_intervention_surfaces"] is True
    assert "avoid_target_regression" not in candidates[0].generation_directives
    assert "require_distinct_intervention_surfaces" not in candidates[1].generation_directives
    assert candidates[1].generation_directives["avoid_target_regression"]["prompt"] is True


def test_budget_increase_and_ranking_weight_changes_are_supported() -> None:
    parent = default_improver_policy()
    candidates = propose_policy_candidates(
        parent,
        _analysis(
            _pattern("top_m", _change("budget_policy.top_m", "increase", 2), support=4),
            _pattern("coverage", _change("ranking_weights.coverage", "increase", 5.0), support=3),
        ),
    )

    assert len(candidates) == 2
    assert candidates[0].budget_policy["top_m"] == 3
    assert candidates[0].ranking_weights["coverage"] == 20.0
    assert candidates[1].budget_policy["top_m"] == 1
    assert candidates[1].ranking_weights["coverage"] == 25.0


def test_ranking_weight_decrease_uses_a_finite_positive_step() -> None:
    parent = default_improver_policy()

    child = propose_policy_update(
        parent,
        _analysis(_pattern("duplicate", _change("ranking_weights.duplicate", "decrease", 5.0))),
    )

    assert child is not None
    assert child.ranking_weights["duplicate"] == -35.0


def test_insufficient_support_or_non_concrete_change_produces_no_candidate() -> None:
    parent = default_improver_policy()
    unsupported = _pattern(
        "unsupported",
        _change("generation_directives.unknown", "set", True),
        support=9,
    )
    weak = _pattern(
        "weak",
        _change("generation_directives.require_unique_candidate_fingerprint", "set", True),
        support=1,
    )

    assert propose_policy_candidates(parent, _analysis(unsupported, weak)) == ()
    assert propose_policy_update(parent, _analysis(weak)) is None


def test_frozen_feedback_schema_uses_cohort_counts() -> None:
    parent = default_improver_policy()
    frozen_pattern = {
        "pattern_id": "frozen_schema",
        "type": "activation_gap",
        "surface": "tool",
        "support_cohorts": 3,
        "opportunity_cohorts": 5,
        "rate": 0.6,
        "evidence_cohort_ids": ["cohort_003"],
        "recommended_policy_change": _change(
            "generation_directives.require_activation_evidence.tool",
            "set",
            True,
        ),
    }

    child = propose_policy_update(parent, _analysis(frozen_pattern))

    assert child is not None
    assert child.generation_directives["require_activation_evidence"]["tool"] is True


def test_frozen_cohort_counts_take_precedence_over_legacy_aliases() -> None:
    parent = default_improver_policy()
    pattern = _pattern(
        "primary_is_authoritative",
        _change("generation_directives.require_unique_candidate_fingerprint", "set", True),
        support=99,
    )
    pattern["support_cohorts"] = 1
    pattern["opportunity_cohorts"] = 10

    assert propose_policy_candidates(parent, _analysis(pattern)) == ()


def test_frozen_opportunity_cohorts_control_equal_support_ordering() -> None:
    parent = default_improver_policy()
    lower = _pattern(
        "lower_primary_opportunity",
        _change("generation_directives.require_unique_candidate_fingerprint", "set", True),
        support=3,
        rate=0.5,
    )
    lower.update({"support_cohorts": 3, "opportunity_cohorts": 2, "opportunity": 100})
    higher = _pattern(
        "higher_primary_opportunity",
        _change("generation_directives.require_distinct_intervention_surfaces", "set", True),
        support=3,
        rate=0.5,
    )
    higher.update({"support_cohorts": 3, "opportunity_cohorts": 5, "opportunity": 1})

    candidates = propose_policy_candidates(parent, _analysis(lower, higher))

    assert candidates[0].generation_directives["require_distinct_intervention_surfaces"] is True


def test_duplicate_recommendations_produce_one_candidate() -> None:
    parent = default_improver_policy()
    change = _change("generation_directives.require_unique_candidate_fingerprint", "set", True)

    candidates = propose_policy_candidates(
        parent,
        _analysis(
            _pattern("first", change, support=4),
            _pattern("second", change, support=3),
        ),
    )

    assert len(candidates) == 1


def test_missing_ledger_digest_gets_stable_fallback_digest() -> None:
    parent = default_improver_policy()
    analysis = {
        "stable_patterns": [
            _pattern(
                "fallback",
                _change("generation_directives.require_unique_candidate_fingerprint", "set", True),
            )
        ]
    }

    first = propose_policy_update(parent, analysis)
    second = propose_policy_update(parent, analysis)

    assert first is not None
    assert second is not None
    assert first.training_ledger_digest.startswith("sha256:")
    assert first.training_ledger_digest == second.training_ledger_digest
