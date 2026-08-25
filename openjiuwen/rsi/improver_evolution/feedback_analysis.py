# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic analysis of Candidate Feedback Ledger v1 artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations
from pathlib import Path
from typing import Any, TypeAlias

import yaml

LedgerSource: TypeAlias = Mapping[str, Any] | str | Path

_LEDGER_TYPE = "single_harness_sibling_candidate_feedback"
_COHORT_TYPE = "candidate_feedback_cohort"
_METRIC_NAMES = ("best_of_k_gain", "top_m_gain", "selection_regret")
_METRIC_STATUSES = {"available", "unavailable"}
_ACTIVATION_AVAILABILITIES = {
    "missing_artifact",
    "not_applicable",
    "not_instrumented",
    "observed",
}
_ACTIVATION_STATES = {
    "missing_artifact",
    "not_applicable",
    "not_instrumented",
    "not_triggered",
    "triggered",
}
_REGRESSION_AVAILABILITIES = {
    "missing_artifact",
    "not_evaluated",
    "not_instrumented",
    "observed",
}


def analyze_candidate_feedback_ledgers(
    sources: LedgerSource | Sequence[LedgerSource],
    *,
    min_support_cohorts: int = 2,
    high_value_gain_threshold: float = 0.0,
) -> dict[str, Any]:
    """Validate and summarize one or more Candidate Feedback Ledger v1 inputs.

    Inputs may be an in-memory ledger/cohort mapping, a YAML/JSON path, or a
    sequence containing either form. Unavailable evidence is counted by its
    explicit state and never converted to a negative observation.
    """
    if isinstance(min_support_cohorts, bool) or min_support_cohorts < 1:
        raise ValueError("min_support_cohorts must be at least 1")
    if not _is_number(high_value_gain_threshold):
        raise ValueError("high_value_gain_threshold must be a finite number")

    normalized_sources = _normalize_sources(sources)
    cohorts: list[dict[str, Any]] = []
    for source_index, source in enumerate(normalized_sources):
        cohorts.extend(_load_and_validate_source(source, source_index=source_index))
    _require_unique_cohort_ids(cohorts)

    comparable_ids: list[str] = []
    non_comparable_reasons: Counter[str] = Counter()
    non_comparable_ids_by_reason: dict[str, list[str]] = defaultdict(list)
    metric_availability = {
        name: {"available_count": 0, "unavailable_count": 0, "unavailable_reasons": Counter()} for name in _METRIC_NAMES
    }
    nonpositive_ids: list[str] = []
    high_value_opportunity_ids: list[str] = []
    high_value_outside_ids: list[str] = []
    duplicate_candidate_count = 0
    duplicate_opportunity_count = 0
    duplicate_cohort_ids: list[str] = []
    trigger_by_surface: dict[str, _SurfaceEvidence] = {}
    regression_by_surface: dict[str, _SurfaceEvidence] = {}
    feature_evidence: dict[str, _FeatureEvidence] = {}
    positive_selection_regret_ids: list[str] = []
    partial_repair_opportunity_ids: set[str] = set()
    partial_repair_support_ids: set[str] = set()

    for cohort in cohorts:
        cohort_id = cohort["cohort"]["cohort_id"]
        reason = _comparability_reason(cohort)
        if reason:
            non_comparable_reasons[reason] += 1
            non_comparable_ids_by_reason[reason].append(cohort_id)
        else:
            comparable_ids.append(cohort_id)
            gains = [float(candidate["outcome"]["target_gain"]) for candidate in cohort["candidates"]]
            if gains and max(gains) <= 0.0:
                nonpositive_ids.append(cohort_id)
            _collect_high_value_outside_top_m(
                cohort,
                threshold=float(high_value_gain_threshold),
                opportunity_ids=high_value_opportunity_ids,
                support_ids=high_value_outside_ids,
            )
            _collect_feature_evidence(cohort, feature_evidence)

        selection_regret = cohort["metrics"]["selection_regret"]
        if selection_regret["status"] == "available" and float(selection_regret["value"]) > 0.0:
            positive_selection_regret_ids.append(cohort_id)

        for metric_name in _METRIC_NAMES:
            metric = cohort["metrics"][metric_name]
            if metric["status"] == "available":
                metric_availability[metric_name]["available_count"] += 1
            else:
                metric_availability[metric_name]["unavailable_count"] += 1
                metric_availability[metric_name]["unavailable_reasons"][metric["reason"]] += 1

        candidate_count, cohort_duplicate_count = _cohort_duplicate_counts(cohort)
        duplicate_opportunity_count += candidate_count
        duplicate_candidate_count += cohort_duplicate_count
        if cohort_duplicate_count:
            duplicate_cohort_ids.append(cohort_id)

        for candidate in cohort["candidates"]:
            surfaces = candidate["activation"]["surfaces"]
            for surface in surfaces:
                trigger = trigger_by_surface.setdefault(surface, _SurfaceEvidence())
                trigger.observe_trigger(cohort_id, candidate["activation"])
                regression = regression_by_surface.setdefault(surface, _SurfaceEvidence())
                regression.observe_regression(cohort_id, candidate["regression"]["target"])
            verifier = candidate.get("verifier_summary")
            if isinstance(verifier, Mapping) and verifier.get("availability") == "observed":
                remaining = verifier.get("remaining_failed_requirements_count")
                newly_passed = verifier.get("newly_passed_requirements_count")
                regressed = verifier.get("regressed_requirements_count")
                partial_progress_cases = verifier.get("partial_progress_case_count")
                if _is_nonnegative_int(remaining) and remaining > 0:
                    partial_repair_opportunity_ids.add(cohort_id)
                    newly_passed_observed = _is_nonnegative_int(newly_passed) and newly_passed > 0
                    no_regression = _is_nonnegative_int(regressed) and regressed == 0
                    partial_progress_observed = (
                        _is_nonnegative_int(partial_progress_cases) and partial_progress_cases > 0
                    )
                    if newly_passed_observed and no_regression and partial_progress_observed:
                        partial_repair_support_ids.add(cohort_id)

    stable_patterns = _stable_patterns(
        min_support_cohorts=min_support_cohorts,
        comparable_ids=comparable_ids,
        nonpositive_ids=nonpositive_ids,
        high_value_opportunity_ids=high_value_opportunity_ids,
        high_value_outside_ids=high_value_outside_ids,
        duplicate_opportunity_cohorts=[cohort["cohort"]["cohort_id"] for cohort in cohorts],
        duplicate_cohort_ids=duplicate_cohort_ids,
        trigger_by_surface=trigger_by_surface,
        regression_by_surface=regression_by_surface,
        feature_evidence=feature_evidence,
        positive_selection_regret_ids=positive_selection_regret_ids,
        partial_repair_opportunity_ids=sorted(partial_repair_opportunity_ids),
        partial_repair_support_ids=sorted(partial_repair_support_ids),
    )

    return {
        "schema_version": 1,
        "analysis_type": "candidate_feedback_analysis",
        "training_ledger_digest": _training_ledger_digest(cohorts),
        "parameters": {
            "min_support_cohorts": min_support_cohorts,
            "high_value_gain_threshold": float(high_value_gain_threshold),
        },
        "input_summary": {
            "ledger_count": len(normalized_sources),
            "cohort_count": len(cohorts),
            "candidate_count": sum(len(cohort["candidates"]) for cohort in cohorts),
        },
        "comparability": {
            "comparable_cohort_count": len(comparable_ids),
            "comparable_cohort_ids": sorted(comparable_ids),
            "non_comparable_cohort_count": len(cohorts) - len(comparable_ids),
            "non_comparable_reasons": _counter_dict(non_comparable_reasons),
            "non_comparable_cohort_ids_by_reason": {
                reason: sorted(ids) for reason, ids in sorted(non_comparable_ids_by_reason.items())
            },
        },
        "metric_availability": {
            name: {
                "available_count": values["available_count"],
                "unavailable_count": values["unavailable_count"],
                "unavailable_reasons": _counter_dict(values["unavailable_reasons"]),
            }
            for name, values in metric_availability.items()
        },
        "outcomes": {
            "all_candidates_nonpositive": _cohort_rate(nonpositive_ids, comparable_ids),
            "high_value_candidate_outside_top_m": _cohort_rate(
                high_value_outside_ids,
                high_value_opportunity_ids,
            ),
            "positive_selection_regret": _cohort_rate(
                positive_selection_regret_ids,
                [
                    cohort["cohort"]["cohort_id"]
                    for cohort in cohorts
                    if cohort["metrics"]["selection_regret"]["status"] == "available"
                ],
            ),
            "partial_candidate_residual_repair": _cohort_rate(
                sorted(partial_repair_support_ids),
                sorted(partial_repair_opportunity_ids),
            ),
        },
        "duplicates": {
            "availability": "observed",
            "duplicate_candidate_count": duplicate_candidate_count,
            "opportunity_candidate_count": duplicate_opportunity_count,
            "rate": _rate(duplicate_candidate_count, duplicate_opportunity_count),
            "support_cohort_count": len(set(duplicate_cohort_ids)),
            "evidence_cohort_ids": sorted(set(duplicate_cohort_ids)),
        },
        "surfaces": {
            surface: {
                "trigger_observed_failure": evidence.trigger_summary(),
                "target_regression": regression_by_surface[surface].regression_summary(),
            }
            for surface, evidence in sorted(trigger_by_surface.items())
        },
        "feature_outcome_alignment": {
            feature: evidence.summary() for feature, evidence in sorted(feature_evidence.items())
        },
        "stable_patterns": stable_patterns,
    }


class _SurfaceEvidence:
    def __init__(self) -> None:
        self.trigger_availability: Counter[str] = Counter()
        self.trigger_opportunity_candidates = 0
        self.trigger_failure_candidates = 0
        self.trigger_opportunity_cohorts: set[str] = set()
        self.trigger_failure_cohorts: set[str] = set()
        self.regression_availability: Counter[str] = Counter()
        self.regression_opportunity_candidates = 0
        self.regression_failure_candidates = 0
        self.regression_opportunity_cohorts: set[str] = set()
        self.regression_failure_cohorts: set[str] = set()

    def observe_trigger(self, cohort_id: str, activation: dict[str, Any]) -> None:
        availability = activation["availability"]
        self.trigger_availability[availability] += 1
        if availability != "observed":
            return
        self.trigger_opportunity_candidates += 1
        self.trigger_opportunity_cohorts.add(cohort_id)
        if activation["state"] == "not_triggered":
            self.trigger_failure_candidates += 1
            self.trigger_failure_cohorts.add(cohort_id)

    def observe_regression(self, cohort_id: str, regression: dict[str, Any]) -> None:
        availability = regression["availability"]
        self.regression_availability[availability] += 1
        if availability != "observed":
            return
        self.regression_opportunity_candidates += 1
        self.regression_opportunity_cohorts.add(cohort_id)
        if regression["regressed_case_count"] > 0:
            self.regression_failure_candidates += 1
            self.regression_failure_cohorts.add(cohort_id)

    def trigger_summary(self) -> dict[str, Any]:
        return {
            "availability_counts": _counter_dict(self.trigger_availability),
            "observed_opportunity_candidate_count": self.trigger_opportunity_candidates,
            "failure_candidate_count": self.trigger_failure_candidates,
            "rate": _rate(self.trigger_failure_candidates, self.trigger_opportunity_candidates),
            "opportunity_cohort_count": len(self.trigger_opportunity_cohorts),
            "support_cohort_count": len(self.trigger_failure_cohorts),
            "evidence_cohort_ids": sorted(self.trigger_failure_cohorts),
        }

    def regression_summary(self) -> dict[str, Any]:
        return {
            "availability_counts": _counter_dict(self.regression_availability),
            "observed_opportunity_candidate_count": self.regression_opportunity_candidates,
            "regression_candidate_count": self.regression_failure_candidates,
            "rate": _rate(self.regression_failure_candidates, self.regression_opportunity_candidates),
            "opportunity_cohort_count": len(self.regression_opportunity_cohorts),
            "support_cohort_count": len(self.regression_failure_cohorts),
            "evidence_cohort_ids": sorted(self.regression_failure_cohorts),
        }


class _FeatureEvidence:
    def __init__(self) -> None:
        self.comparable_pair_count = 0
        self.comparable_cohorts: set[str] = set()
        self.aligned_pair_count = 0
        self.aligned_cohorts: set[str] = set()
        self.opposite_pair_count = 0
        self.opposite_cohorts: set[str] = set()
        self.increase_pair_count = 0
        self.increase_cohorts: set[str] = set()
        self.decrease_pair_count = 0
        self.decrease_cohorts: set[str] = set()

    def observe(self, cohort_id: str, *, aligned: bool, adjustment: str | None) -> None:
        self.comparable_pair_count += 1
        self.comparable_cohorts.add(cohort_id)
        if aligned:
            self.aligned_pair_count += 1
            self.aligned_cohorts.add(cohort_id)
            return
        self.opposite_pair_count += 1
        self.opposite_cohorts.add(cohort_id)
        if adjustment == "increase":
            self.increase_pair_count += 1
            self.increase_cohorts.add(cohort_id)
        elif adjustment == "decrease":
            self.decrease_pair_count += 1
            self.decrease_cohorts.add(cohort_id)

    def summary(self) -> dict[str, Any]:
        return {
            "comparable_pair_count": self.comparable_pair_count,
            "comparable_cohort_count": len(self.comparable_cohorts),
            "comparable_cohort_ids": sorted(self.comparable_cohorts),
            "aligned_pair_count": self.aligned_pair_count,
            "aligned_cohort_count": len(self.aligned_cohorts),
            "aligned_cohort_ids": sorted(self.aligned_cohorts),
            "opposite_pair_count": self.opposite_pair_count,
            "opposite_cohort_count": len(self.opposite_cohorts),
            "opposite_cohort_ids": sorted(self.opposite_cohorts),
            "increase_weight_evidence": {
                "pair_count": self.increase_pair_count,
                "cohort_count": len(self.increase_cohorts),
                "cohort_ids": sorted(self.increase_cohorts),
            },
            "decrease_weight_evidence": {
                "pair_count": self.decrease_pair_count,
                "cohort_count": len(self.decrease_cohorts),
                "cohort_ids": sorted(self.decrease_cohorts),
            },
        }


def _normalize_sources(sources: LedgerSource | Sequence[LedgerSource]) -> list[LedgerSource]:
    if isinstance(sources, (Mapping, str, Path)):
        return [sources]
    if not isinstance(sources, Sequence) or isinstance(sources, (bytes, bytearray)):
        raise TypeError("sources must be a ledger mapping, path, or sequence of them")
    normalized = list(sources)
    if not all(isinstance(source, (Mapping, str, Path)) for source in normalized):
        raise TypeError("each source must be a ledger mapping or path")
    return normalized


def _load_and_validate_source(source: LedgerSource, *, source_index: int) -> list[dict[str, Any]]:
    value = _load_source(source)
    location = f"source[{source_index}]"
    _require_mapping(value, location)
    if value.get("record_type") == _COHORT_TYPE:
        cohorts = [dict(value)]
    else:
        _require_exact(value.get("schema_version"), 1, f"{location}.schema_version")
        _require_exact(value.get("ledger_type"), _LEDGER_TYPE, f"{location}.ledger_type")
        raw_cohorts = value.get("cohorts")
        if not isinstance(raw_cohorts, list):
            raise ValueError(f"{location}.cohorts must be a list")
        cohorts = raw_cohorts
    for cohort_index, cohort in enumerate(cohorts):
        _validate_cohort(cohort, f"{location}.cohorts[{cohort_index}]")
    return cohorts


def _load_source(source: LedgerSource) -> Any:
    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source)
    if not path.is_file():
        raise ValueError(f"candidate feedback ledger does not exist: {path}")
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if path.suffix.casefold() == ".json" else yaml.safe_load(text)
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"failed to read candidate feedback ledger {path}: {exc}") from exc


def _validate_cohort(value: Any, location: str) -> None:
    cohort = _require_mapping(value, location)
    _require_exact(cohort.get("schema_version"), 1, f"{location}.schema_version")
    _require_exact(cohort.get("record_type"), _COHORT_TYPE, f"{location}.record_type")
    identity = _require_mapping(cohort.get("cohort"), f"{location}.cohort")
    _require_nonempty_text(identity.get("cohort_id"), f"{location}.cohort.cohort_id")
    candidates = cohort.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError(f"{location}.candidates must be a list")
    if identity.get("candidate_count") != len(candidates):
        raise ValueError(f"{location}.cohort.candidate_count must match candidates length")
    for candidate_index, candidate in enumerate(candidates):
        _validate_candidate(candidate, f"{location}.candidates[{candidate_index}]")
    metrics = _require_mapping(cohort.get("metrics"), f"{location}.metrics")
    for metric_name in _METRIC_NAMES:
        metric = _require_mapping(metrics.get(metric_name), f"{location}.metrics.{metric_name}")
        status = metric.get("status")
        if status not in _METRIC_STATUSES:
            raise ValueError(f"{location}.metrics.{metric_name}.status has unsupported value {status!r}")
        if status == "available":
            if not _is_number(metric.get("value")):
                raise ValueError(f"{location}.metrics.{metric_name}.value must be finite when available")
        else:
            if metric.get("value") is not None:
                raise ValueError(f"{location}.metrics.{metric_name}.value must be null when unavailable")
            _require_nonempty_text(metric.get("reason"), f"{location}.metrics.{metric_name}.reason")


def _validate_candidate(value: Any, location: str) -> None:
    candidate = _require_mapping(value, location)
    _require_nonempty_text(candidate.get("candidate_id"), f"{location}.candidate_id")
    _require_nonempty_text(candidate.get("candidate_fingerprint"), f"{location}.candidate_fingerprint")
    identity = _require_mapping(candidate.get("identity"), f"{location}.identity")
    for field in ("parent_harness_ref", "source_eval_ref", "evaluation_protocol"):
        _require_nonempty_text(identity.get(field), f"{location}.identity.{field}")
    if not isinstance(identity.get("target_case_ids"), list):
        raise ValueError(f"{location}.identity.target_case_ids must be a list")
    rank = _require_mapping(candidate.get("proposal_rank"), f"{location}.proposal_rank")
    if not isinstance(rank.get("rank_frozen"), bool):
        raise ValueError(f"{location}.proposal_rank.rank_frozen must be a boolean")
    _require_nonempty_text(rank.get("ranking_policy"), f"{location}.proposal_rank.ranking_policy")
    if "ranking_features" in rank and not isinstance(rank["ranking_features"], dict):
        raise ValueError(f"{location}.proposal_rank.ranking_features must be a mapping when present")
    outcome = _require_mapping(candidate.get("outcome"), f"{location}.outcome")
    gain = outcome.get("target_gain")
    if gain is not None and not _is_number(gain):
        raise ValueError(f"{location}.outcome.target_gain must be finite or null")

    activation = _require_mapping(candidate.get("activation"), f"{location}.activation")
    availability = activation.get("availability")
    state = activation.get("state")
    if availability not in _ACTIVATION_AVAILABILITIES:
        raise ValueError(f"{location}.activation.availability has unsupported value {availability!r}")
    if state not in _ACTIVATION_STATES:
        raise ValueError(f"{location}.activation.state has unsupported value {state!r}")
    if availability == "observed" and state not in {"triggered", "not_triggered"}:
        raise ValueError(f"{location}.activation.state must be triggered or not_triggered when observed")
    if availability != "observed" and state != availability:
        raise ValueError(f"{location}.activation.state must preserve unavailable state {availability!r}")
    surfaces = activation.get("surfaces")
    if not isinstance(surfaces, list) or any(not isinstance(surface, str) or not surface for surface in surfaces):
        raise ValueError(f"{location}.activation.surfaces must be a list of non-empty strings")

    regression = _require_mapping(candidate.get("regression"), f"{location}.regression")
    target = _require_mapping(regression.get("target"), f"{location}.regression.target")
    target_availability = target.get("availability")
    if target_availability not in _REGRESSION_AVAILABILITIES:
        raise ValueError(f"{location}.regression.target.availability has unsupported value {target_availability!r}")
    count = target.get("regressed_case_count")
    if target_availability == "observed":
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"{location}.regression.target.regressed_case_count must be non-negative")
    elif count is not None:
        raise ValueError(f"{location}.regression.target.regressed_case_count must be null when unavailable")


def _comparability_reason(cohort: dict[str, Any]) -> str:
    candidates = cohort["candidates"]
    if len(candidates) < 2:
        return "requires_at_least_two_comparable_candidates"
    identity = cohort["cohort"]
    required_fields = ("parent_harness_ref", "source_eval_ref", "evaluation_protocol")
    if any(not identity.get(field) for field in required_fields) or not identity.get("target_case_ids"):
        return "cohort_identity_missing"
    if identity.get("rank_frozen") is not True:
        return "rank_not_frozen_before_evaluation"
    candidate_ids = [candidate["candidate_id"] for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        return "duplicate_candidate_id"
    ranks = [candidate["proposal_rank"].get("predicted_rank") for candidate in candidates]
    if any(isinstance(rank, bool) or not isinstance(rank, int) for rank in ranks):
        return "predicted_rank_missing"
    if set(ranks) != set(range(1, len(candidates) + 1)):
        return "predicted_ranks_not_permutation"
    for candidate in candidates:
        candidate_identity = candidate["identity"]
        for field in required_fields:
            if candidate_identity[field] != identity[field]:
                return f"{field}_mismatch"
        if set(candidate_identity["target_case_ids"]) != set(identity["target_case_ids"]):
            return "target_case_set_mismatch"
        if candidate["proposal_rank"]["rank_frozen"] is not True:
            return "candidate_rank_not_frozen"
        if candidate["proposal_rank"]["ranking_policy"] != identity.get("ranking_policy"):
            return "candidate_ranking_policy_mismatch"
        if not _is_number(candidate["outcome"].get("target_gain")):
            return "target_gain_missing"
    if any(cohort["metrics"][name]["status"] != "available" for name in ("best_of_k_gain", "top_m_gain")):
        return "search_metric_unavailable"
    return ""


def _collect_high_value_outside_top_m(
    cohort: dict[str, Any],
    *,
    threshold: float,
    opportunity_ids: list[str],
    support_ids: list[str],
) -> None:
    best = cohort["metrics"]["best_of_k_gain"]
    top_m = cohort["metrics"]["top_m_gain"]
    if float(best["value"]) <= threshold:
        return
    cohort_id = cohort["cohort"]["cohort_id"]
    opportunity_ids.append(cohort_id)
    candidate_id = best.get("candidate_id")
    candidate = next((item for item in cohort["candidates"] if item["candidate_id"] == candidate_id), None)
    if candidate is None:
        raise ValueError(f"cohort {cohort_id!r} best_of_k_gain candidate_id is not in candidates")
    effective_m = top_m.get("effective_m")
    if isinstance(effective_m, bool) or not isinstance(effective_m, int) or effective_m < 1:
        raise ValueError(f"cohort {cohort_id!r} top_m_gain.effective_m must be a positive integer")
    if candidate["proposal_rank"]["predicted_rank"] > effective_m:
        support_ids.append(cohort_id)


def _cohort_duplicate_counts(cohort: dict[str, Any]) -> tuple[int, int]:
    fingerprints = [candidate["candidate_fingerprint"] for candidate in cohort["candidates"]]
    return len(fingerprints), len(fingerprints) - len(set(fingerprints))


def _collect_feature_evidence(
    cohort: dict[str, Any],
    feature_evidence: dict[str, _FeatureEvidence],
) -> None:
    cohort_id = cohort["cohort"]["cohort_id"]
    for first, second in combinations(cohort["candidates"], 2):
        first_features = _numeric_ranking_features(first)
        second_features = _numeric_ranking_features(second)
        if not first_features or first_features.keys() != second_features.keys():
            continue
        differing = [feature for feature in first_features if first_features[feature] != second_features[feature]]
        if len(differing) != 1:
            continue
        feature = differing[0]
        first_gain = float(first["outcome"]["target_gain"])
        second_gain = float(second["outcome"]["target_gain"])
        first_score = first["proposal_rank"].get("predicted_score")
        second_score = second["proposal_rank"].get("predicted_score")
        if first_gain == second_gain or not _is_number(first_score) or not _is_number(second_score):
            continue
        if float(first_score) == float(second_score):
            continue

        predicted_winner_is_first = float(first_score) > float(second_score)
        realized_winner_is_first = first_gain > second_gain
        aligned = predicted_winner_is_first == realized_winner_is_first
        adjustment: str | None = None
        if not aligned:
            higher_feature_is_first = first_features[feature] > second_features[feature]
            higher_feature_predicted = predicted_winner_is_first == higher_feature_is_first
            adjustment = "decrease" if higher_feature_predicted else "increase"
        feature_evidence.setdefault(feature, _FeatureEvidence()).observe(
            cohort_id,
            aligned=aligned,
            adjustment=adjustment,
        )


def _numeric_ranking_features(candidate: dict[str, Any]) -> dict[str, float]:
    raw = candidate["proposal_rank"].get("ranking_features")
    if not isinstance(raw, dict):
        return {}
    return {str(key): float(value) for key, value in raw.items() if _is_number(value)}


def _stable_patterns(
    *,
    min_support_cohorts: int,
    comparable_ids: list[str],
    nonpositive_ids: list[str],
    high_value_opportunity_ids: list[str],
    high_value_outside_ids: list[str],
    duplicate_opportunity_cohorts: list[str],
    duplicate_cohort_ids: list[str],
    trigger_by_surface: dict[str, _SurfaceEvidence],
    regression_by_surface: dict[str, _SurfaceEvidence],
    feature_evidence: dict[str, _FeatureEvidence],
    positive_selection_regret_ids: list[str],
    partial_repair_opportunity_ids: list[str],
    partial_repair_support_ids: list[str],
) -> list[dict[str, Any]]:
    patterns: list[dict[str, Any]] = []
    _append_pattern(
        patterns,
        min_support_cohorts=min_support_cohorts,
        pattern_id="all_candidates_nonpositive",
        pattern_type="all_candidates_nonpositive",
        surface=None,
        support_ids=nonpositive_ids,
        opportunity_ids=comparable_ids,
        policy_change={
            "field": "generation_directives.require_distinct_intervention_surfaces",
            "operation": "set",
            "value": True,
            "rationale": "Comparable cohorts repeatedly produced no positive-gain candidate.",
        },
    )
    _append_pattern(
        patterns,
        min_support_cohorts=min_support_cohorts,
        pattern_id="partial_candidate_has_residual_requirements",
        pattern_type="partial_candidate_has_residual_requirements",
        surface=None,
        support_ids=partial_repair_support_ids,
        opportunity_ids=partial_repair_opportunity_ids,
        policy_change={
            "field": "generation_directives.preserve_partial_progress_and_target_residual",
            "operation": "set",
            "value": True,
            "rationale": (
                "Candidates repeatedly improved some requirements without regression while residual "
                "requirements remained; repair should preserve verified gains and target only the residue."
            ),
        },
    )
    _append_pattern(
        patterns,
        min_support_cohorts=min_support_cohorts,
        pattern_id="high_value_candidate_outside_top_m",
        pattern_type="high_value_candidate_outside_top_m",
        surface=None,
        support_ids=high_value_outside_ids,
        opportunity_ids=high_value_opportunity_ids,
        policy_change={
            "field": "budget_policy.top_m",
            "operation": "increase",
            "value": 1,
            "rationale": (
                "The realized best positive-gain candidate repeatedly ranked outside the current Top-m budget."
            ),
        },
    )
    _append_pattern(
        patterns,
        min_support_cohorts=min_support_cohorts,
        pattern_id="duplicate_candidates",
        pattern_type="duplicate_candidates",
        surface=None,
        support_ids=duplicate_cohort_ids,
        opportunity_ids=duplicate_opportunity_cohorts,
        policy_change={
            "field": "generation_directives.require_unique_candidate_fingerprint",
            "operation": "set",
            "value": True,
            "rationale": "Multiple cohorts spent evaluation budget on semantically duplicate candidates.",
        },
    )
    for surface, evidence in sorted(trigger_by_surface.items()):
        _append_pattern(
            patterns,
            min_support_cohorts=min_support_cohorts,
            pattern_id=f"trigger_observed_failure:{surface}",
            pattern_type="trigger_observed_failure",
            surface=surface,
            support_ids=list(evidence.trigger_failure_cohorts),
            opportunity_ids=list(evidence.trigger_opportunity_cohorts),
            policy_change={
                "field": f"generation_directives.require_activation_evidence.{surface}",
                "operation": "set",
                "value": True,
                "rationale": f"Observed {surface} candidates repeatedly failed to trigger on all expected case pairs.",
            },
        )
        regression = regression_by_surface[surface]
        _append_pattern(
            patterns,
            min_support_cohorts=min_support_cohorts,
            pattern_id=f"target_regression:{surface}",
            pattern_type="target_regression",
            surface=surface,
            support_ids=list(regression.regression_failure_cohorts),
            opportunity_ids=list(regression.regression_opportunity_cohorts),
            policy_change={
                "field": f"generation_directives.avoid_target_regression.{surface}",
                "operation": "set",
                "value": True,
                "rationale": f"Observed {surface} candidates repeatedly regressed at least one target case.",
            },
        )
    positive_regret_cohorts = set(positive_selection_regret_ids)
    for feature, evidence in sorted(feature_evidence.items()):
        increase_support = evidence.increase_cohorts & positive_regret_cohorts
        decrease_support = evidence.decrease_cohorts & positive_regret_cohorts
        if len(increase_support) == len(decrease_support):
            continue
        operation = "increase" if len(increase_support) > len(decrease_support) else "decrease"
        support = increase_support if operation == "increase" else decrease_support
        opportunities = evidence.comparable_cohorts & positive_regret_cohorts
        _append_pattern(
            patterns,
            min_support_cohorts=min_support_cohorts,
            pattern_id=f"ranking_misalignment:{feature}:{operation}",
            pattern_type="ranking_misalignment",
            surface=None,
            support_ids=list(support),
            opportunity_ids=list(opportunities),
            policy_change={
                "field": f"ranking_weights.{feature}",
                "operation": operation,
                "value": 0.1,
                "rationale": (
                    f"Isolated {feature} pairs repeatedly opposed realized ordering in cohorts with positive "
                    "selection regret."
                ),
            },
        )
    return patterns


def _append_pattern(
    patterns: list[dict[str, Any]],
    *,
    min_support_cohorts: int,
    pattern_id: str,
    pattern_type: str,
    surface: str | None,
    support_ids: list[str],
    opportunity_ids: list[str],
    policy_change: dict[str, Any],
) -> None:
    support = sorted(set(support_ids))
    opportunity = sorted(set(opportunity_ids))
    if len(support) < min_support_cohorts:
        return
    patterns.append(
        {
            "pattern_id": pattern_id,
            "pattern_type": pattern_type,
            "surface": surface,
            "support_cohorts": len(support),
            "opportunity_cohorts": len(opportunity),
            "rate": _rate(len(support), len(opportunity)),
            "evidence_cohort_ids": support,
            "recommended_policy_change": policy_change,
        }
    )


def _cohort_rate(support_ids: list[str], opportunity_ids: list[str]) -> dict[str, Any]:
    support = sorted(set(support_ids))
    opportunity = sorted(set(opportunity_ids))
    return {
        "support_cohort_count": len(support),
        "opportunity_cohort_count": len(opportunity),
        "rate": _rate(len(support), len(opportunity)),
        "evidence_cohort_ids": support,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _training_ledger_digest(cohorts: list[dict[str, Any]]) -> str:
    ordered = sorted(cohorts, key=lambda item: item["cohort"]["cohort_id"])
    payload = json.dumps(
        ordered,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _require_unique_cohort_ids(cohorts: list[dict[str, Any]]) -> None:
    ids = [cohort["cohort"]["cohort_id"] for cohort in cohorts]
    duplicates = sorted(cohort_id for cohort_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate cohort_id across inputs: {', '.join(duplicates)}")


def _require_mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be a mapping")
    return value


def _require_exact(value: Any, expected: Any, location: str) -> None:
    if value != expected:
        raise ValueError(f"{location} must be {expected!r}, got {value!r}")


def _require_nonempty_text(value: Any, location: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be a non-empty string")


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
