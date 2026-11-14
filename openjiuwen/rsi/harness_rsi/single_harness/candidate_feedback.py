# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic candidate ranking and compact feedback-ledger helpers."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any

from openjiuwen.rsi.harness_rsi.improver_evolution.policy import (
    VersionedImproverPolicy,
    default_improver_policy,
    score_static_priority,
)

_RANKING_POLICY = "static_priority_v1"
_INSTRUMENTED_GROUPS = {"skill", "tool"}
_INSTRUMENTED_OPERATIONS = {"add", "modify", "update"}
_NON_EXECUTABLE_STATUSES = {
    "blocked",
    "failed",
    "inactionable",
    "invalid",
    "no_candidate",
    "unsupported",
    "unsupported_capability_request",
}
_UNINSTRUMENTED_GROUPS = {"config", "control", "prompt", "rail"}


def canonical_candidate_fingerprint(capabilities: list[dict[str, Any]]) -> str:
    """Return a semantic fingerprint that excludes action IDs and file names."""
    semantic_capabilities = [_semantic_capability(item) for item in capabilities if isinstance(item, dict)]
    semantic_capabilities.sort(key=_canonical_json)
    payload = _canonical_json(semantic_capabilities).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def rank_candidate_proposals(
    proposals: list[dict[str, Any]],
    *,
    frozen_target_case_ids: set[str],
    improver_policy: VersionedImproverPolicy | None = None,
) -> list[dict[str, Any]]:
    """Freeze ``static_priority_v1`` ranks while preserving proposal input order."""
    policy = improver_policy or default_improver_policy()
    frozen_targets = {_text(case_id) for case_id in frozen_target_case_ids if _text(case_id)}
    prepared: list[dict[str, Any]] = []
    for position, proposal in enumerate(proposals):
        capabilities = _capabilities(proposal)
        candidate_index = _candidate_index(proposal, position)
        covered_targets = _proposal_target_case_ids(proposal, capabilities) & frozen_targets
        coverage_ratio = len(covered_targets) / len(frozen_targets) if frozen_targets else 0.0
        action_count = len(capabilities)
        atomicity = 1.0 / action_count if action_count else 0.0
        prepared.append(
            {
                "position": position,
                "proposal": proposal,
                "candidate_id": _candidate_id(proposal, candidate_index),
                "candidate_index": candidate_index,
                "candidate_fingerprint": canonical_candidate_fingerprint(capabilities),
                "capabilities": capabilities,
                "is_executable": _is_executable(proposal, capabilities),
                "covered_target_case_ids": sorted(covered_targets),
                "coverage_ratio": coverage_ratio,
                "action_count": action_count,
                "atomicity": atomicity,
            }
        )

    first_by_fingerprint: dict[str, dict[str, Any]] = {}
    for item in sorted(
        prepared,
        key=lambda value: (not value["is_executable"], value["candidate_index"], value["position"]),
    ):
        first_by_fingerprint.setdefault(item["candidate_fingerprint"], item)

    for item in prepared:
        first = first_by_fingerprint[item["candidate_fingerprint"]]
        duplicate = first is not item
        score_features = {
            "executable": item["is_executable"],
            "coverage": item["coverage_ratio"],
            "atomicity": item["atomicity"],
            "duplicate": duplicate,
        }
        predicted_score = score_static_priority(
            policy,
            score_features,
        )
        item["duplicate"] = duplicate
        item["duplicate_of_candidate_id"] = first["candidate_id"] if duplicate else ""
        item["predicted_score"] = round(predicted_score, 6)

    ranked = sorted(
        prepared,
        key=lambda value: (-value["predicted_score"], value["candidate_index"], value["position"]),
    )
    ranks = {item["position"]: rank for rank, item in enumerate(ranked, start=1)}

    output: list[dict[str, Any]] = []
    for item in prepared:
        output.append(
            {
                **dict(item["proposal"]),
                "candidate_id": item["candidate_id"],
                "candidate_index": item["candidate_index"],
                "candidate_fingerprint": item["candidate_fingerprint"],
                "predicted_score": item["predicted_score"],
                "predicted_rank": ranks[item["position"]],
                "ranking_policy": policy.ranking_policy,
                "improver_version_id": policy.version_id,
                "improver_policy_digest": policy.canonical_digest,
                "rank_frozen": True,
                "ranking_features": {
                    "executable": float(item["is_executable"]),
                    "coverage": round(item["coverage_ratio"], 6),
                    "atomicity": round(item["atomicity"], 6),
                    "duplicate": float(item["duplicate"]),
                    "covered_target_case_ids": item["covered_target_case_ids"],
                    "action_count": item["action_count"],
                    "duplicate_of_candidate_id": item["duplicate_of_candidate_id"],
                },
            }
        )
    return output


def build_candidate_feedback_cohort(
    *,
    cohort: dict[str, Any],
    candidates: list[dict[str, Any]],
    selected_candidate_id: str,
    top_m: int = 1,
) -> dict[str, Any]:
    """Build one compact Ledger v1 cohort from already evaluated candidates."""
    if top_m < 1:
        raise ValueError("top_m must be at least 1")

    parent_harness_ref = _first_text(
        cohort,
        "parent_harness_ref",
        "source_harness_ref",
        "before_harness_refs_path",
    )
    source_eval_ref = _first_text(
        cohort,
        "source_eval_ref",
        "source_eval_ref_path",
        "paired_source_eval_ref_path",
    )
    target_case_ids = _case_ids(cohort.get("frozen_target_case_ids", cohort.get("target_case_ids", [])))
    evaluation_protocol = _first_text(
        cohort,
        "evaluation_protocol",
        "evaluation_protocol_id",
        "protocol_id",
    )
    rank_frozen = cohort.get("rank_frozen") is True
    ranking_policy = _text(cohort.get("ranking_policy")) or _RANKING_POLICY

    candidate_records = [
        _candidate_feedback_record(
            candidate,
            position=position,
            cohort_parent_harness_ref=parent_harness_ref,
            cohort_source_eval_ref=source_eval_ref,
            cohort_target_case_ids=target_case_ids,
            cohort_evaluation_protocol=evaluation_protocol,
            cohort_rank_frozen=rank_frozen,
            cohort_ranking_policy=ranking_policy,
            selected_candidate_id=selected_candidate_id,
        )
        for position, candidate in enumerate(candidates)
    ]
    cohort_id = _text(cohort.get("cohort_id")) or _cohort_id(
        parent_harness_ref,
        source_eval_ref,
        target_case_ids,
        evaluation_protocol,
    )
    unavailable_reason = _search_metric_unavailable_reason(
        parent_harness_ref=parent_harness_ref,
        source_eval_ref=source_eval_ref,
        target_case_ids=target_case_ids,
        evaluation_protocol=evaluation_protocol,
        rank_frozen=rank_frozen,
        ranking_policy=ranking_policy,
        candidates=candidate_records,
    )
    search_metrics = _search_metrics(
        candidate_records,
        top_m=top_m,
        unavailable_reason=unavailable_reason,
    )

    return {
        "schema_version": 1,
        "record_type": "candidate_feedback_cohort",
        "cohort": {
            "cohort_id": cohort_id,
            "run_id": _text(cohort.get("run_id")),
            "parent_harness_ref": parent_harness_ref,
            "source_eval_ref": source_eval_ref,
            "target_case_ids": sorted(target_case_ids),
            "evaluation_protocol": evaluation_protocol,
            "rank_frozen": rank_frozen,
            "ranking_policy": ranking_policy,
            "candidate_count": len(candidate_records),
            "improver_version_id": _text(cohort.get("improver_version_id")),
            "improver_policy_digest": _text(cohort.get("improver_policy_digest")),
        },
        "candidates": candidate_records,
        "selection": {
            "selected_candidate_id": selected_candidate_id,
            "selected_candidate_found": any(
                item["candidate_id"] == selected_candidate_id for item in candidate_records
            ),
            "top_m": top_m,
            "promotion_policy": "best_realized_qualified_candidate",
            "top_m_role": "counterfactual_metric_only",
        },
        "metrics": search_metrics,
    }


def _semantic_capability(capability: dict[str, Any]) -> dict[str, Any]:
    lever_decision = capability.get("lever_decision")
    lever_decision = lever_decision if isinstance(lever_decision, dict) else {}
    hypotheses: list[Any] = []
    for key in ("optimization_hypothesis_ids", "hypothesis_ids", "optimization_contract_sha256"):
        value = capability.get(key, [])
        if isinstance(value, (list, tuple, set)):
            hypotheses.extend(value)
        elif value:
            hypotheses.append(value)
    for key in ("hypothesis_id", "hypothesis"):
        value = capability.get(key)
        if value:
            hypotheses.append(value)
    normalized_hypotheses = sorted({_canonical_json(_normalized_value(item)) for item in hypotheses})
    return {
        "action_group": _normalize_text(capability.get("action_group")),
        "operation": _normalize_text(capability.get("operation")),
        "role": _normalize_text(capability.get("role")),
        "target_family": _target_family(capability),
        "expected_effect": _normalize_text(capability.get("expected_effect")),
        "lever": _normalize_text(
            capability.get("lever") or capability.get("selected_lever") or lever_decision.get("selected_lever")
        ),
        "hypotheses": normalized_hypotheses,
    }


def _target_family(capability: dict[str, Any]) -> str:
    explicit = capability.get("target_family") or capability.get("target_ref")
    if explicit:
        return _normalize_text(explicit)
    action_group = _normalize_text(capability.get("action_group"))
    target_path = _normalize_text(capability.get("target_path")).replace("\\", "/")
    path_parts = [part for part in target_path.split("/")[:-1] if part]
    aliases = {
        "prompt_sections": "prompt",
        "prompts": "prompt",
        "rails": "rail",
        "skills": "skill",
        "tools": "tool",
    }
    for part in path_parts:
        if part in aliases:
            return aliases[part]
    return action_group


def _normalized_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _normalize_text(key): _normalized_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if key not in {"action_id", "target_path", "runtime_name"}
        }
    if isinstance(value, (list, tuple, set)):
        return sorted((_normalized_value(item) for item in value), key=_canonical_json)
    if isinstance(value, str):
        return _normalize_text(value)
    return value


def _candidate_feedback_record(
    candidate: dict[str, Any],
    *,
    position: int,
    cohort_parent_harness_ref: str,
    cohort_source_eval_ref: str,
    cohort_target_case_ids: set[str],
    cohort_evaluation_protocol: str,
    cohort_rank_frozen: bool,
    cohort_ranking_policy: str,
    selected_candidate_id: str,
) -> dict[str, Any]:
    candidate_index = _candidate_index(candidate, position)
    candidate_id = _candidate_id(candidate, candidate_index)
    capabilities = _capabilities(candidate)
    target_case_ids = _case_ids(candidate.get("target_case_ids", [])) or set(cohort_target_case_ids)
    source_target_score = _number(candidate.get("source_target_score"))
    candidate_target_score = _number(candidate.get("candidate_target_score"))
    target_gain = _number(candidate.get("target_gain"))
    if target_gain is None and source_target_score is not None and candidate_target_score is not None:
        target_gain = candidate_target_score - source_target_score

    parent_harness_ref = (
        _first_text(
            candidate,
            "parent_harness_ref",
            "source_harness_ref",
            "before_harness_refs_path",
        )
        or cohort_parent_harness_ref
    )
    source_eval_ref = (
        _first_text(
            candidate,
            "source_eval_ref",
            "source_eval_ref_path",
            "paired_source_eval_ref_path",
        )
        or cohort_source_eval_ref
    )
    evaluation_protocol = (
        _first_text(
            candidate,
            "evaluation_protocol",
            "evaluation_protocol_id",
            "protocol_id",
        )
        or cohort_evaluation_protocol
    )
    ranking_policy = _text(candidate.get("ranking_policy")) or cohort_ranking_policy
    rank_frozen = candidate.get("rank_frozen", cohort_rank_frozen) is True

    return {
        "candidate_id": candidate_id,
        "candidate_index": candidate_index,
        "candidate_fingerprint": _text(candidate.get("candidate_fingerprint"))
        or canonical_candidate_fingerprint(capabilities),
        "selected": candidate_id == selected_candidate_id,
        "identity": {
            "parent_harness_ref": parent_harness_ref,
            "source_eval_ref": source_eval_ref,
            "target_case_ids": sorted(target_case_ids),
            "evaluation_protocol": evaluation_protocol,
        },
        "proposal_rank": {
            "predicted_score": _number(candidate.get("predicted_score")),
            "predicted_rank": _positive_int(candidate.get("predicted_rank")),
            "ranking_policy": ranking_policy,
            "rank_frozen": rank_frozen,
            "ranking_features": (
                dict(candidate.get("ranking_features", {}))
                if isinstance(candidate.get("ranking_features"), dict)
                else {}
            ),
        },
        "outcome": {
            "source_target_score": source_target_score,
            "candidate_target_score": candidate_target_score,
            "target_gain": target_gain,
            "status": _text(candidate.get("status")),
            "reason": _text(candidate.get("reason")),
            "failure_class": _text(candidate.get("failure_class")),
            "causal_failure_class": _text(candidate.get("causal_failure_class")),
        },
        "causal_experiment": _causal_experiment_summary(candidate, target_case_ids),
        "activation": _activation_summary(candidate, capabilities, target_case_ids),
        "regression": {
            "target": _target_regression(candidate, target_case_ids),
            "non_target": {
                "availability": "not_evaluated",
                "regressed_case_count": None,
                "rate": None,
                "reason": "candidate_feedback_is_target_local",
            },
        },
        "verifier_summary": _verifier_summary(candidate),
        "cost": {
            "availability": "not_instrumented",
            "calls": None,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "total_cost": None,
        },
        "evidence_refs": _evidence_refs(candidate),
    }


def _causal_experiment_summary(candidate: dict[str, Any], target_case_ids: set[str]) -> dict[str, Any]:
    contracts: list[dict[str, Any]] = []
    for item in candidate.get("causal_intervention_contracts", []):
        if not isinstance(item, dict):
            continue
        contract_case_ids = _case_ids(item.get("target_case_ids", []))
        if not contract_case_ids or target_case_ids & contract_case_ids:
            contracts.append(dict(item))
    diagnoses = candidate.get("candidate_failure_diagnoses", {})
    diagnoses = diagnoses if isinstance(diagnoses, dict) else {}
    assessments: dict[str, list[dict[str, Any]]] = {}
    for case_id in sorted(target_case_ids):
        raw = diagnoses.get(case_id, [])
        raw = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
        values: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            assessment = item.get("prior_experiment_assessment")
            if isinstance(assessment, dict):
                values.append(dict(assessment))
        if values:
            assessments[case_id] = values
    return {
        "availability": "observed" if contracts else "not_instrumented",
        "prediction_recorded_before_evaluation": bool(contracts),
        "intervention_contracts": contracts,
        "assessment_by_case": assessments,
    }


def _activation_summary(
    candidate: dict[str, Any],
    capabilities: list[dict[str, Any]],
    fallback_target_case_ids: set[str],
) -> dict[str, Any]:
    surfaces = {_normalize_text(item.get("action_group")) for item in capabilities}
    expected_pairs: set[tuple[str, str, str]] = set()
    missing_runtime_name = False
    for capability in capabilities:
        action_group = _normalize_text(capability.get("action_group"))
        operation = _normalize_text(capability.get("operation"))
        if action_group not in _INSTRUMENTED_GROUPS or operation not in _INSTRUMENTED_OPERATIONS:
            continue
        runtime_name = _text(capability.get("runtime_name"))
        if not runtime_name:
            missing_runtime_name = True
            continue
        target_case_ids = _case_ids(capability.get("target_case_ids", [])) or fallback_target_case_ids
        expected_pairs.update((action_group, runtime_name, case_id) for case_id in target_case_ids)

    if missing_runtime_name:
        return _unavailable_activation("missing_artifact", surfaces, "expected_runtime_name_missing")
    if not expected_pairs:
        if surfaces & _UNINSTRUMENTED_GROUPS:
            observed = _behavioral_activation_from_diagnoses(candidate, fallback_target_case_ids)
            if observed is not None:
                observed["surfaces"] = sorted(surfaces & _UNINSTRUMENTED_GROUPS)
                return observed
            return _unavailable_activation("not_instrumented", surfaces, "surface_has_no_activation_event")
        return _unavailable_activation("not_applicable", surfaces, "no_added_or_modified_runtime_capability")

    observed_maps: dict[str, dict[str, Any]] = {}
    for action_group in _INSTRUMENTED_GROUPS:
        key = f"pre_edit_invoked_{action_group}_names_by_case"
        raw = candidate.get(key)
        if raw is not None and isinstance(raw, dict):
            observed_maps[action_group] = raw
    if any(group not in observed_maps or case_id not in observed_maps[group] for group, _, case_id in expected_pairs):
        return _unavailable_activation("missing_artifact", surfaces, "pre_edit_invocation_evidence_missing")

    observed_count = 0
    for action_group, runtime_name, case_id in expected_pairs:
        raw_names = observed_maps[action_group].get(case_id, [])
        names = raw_names if isinstance(raw_names, (list, tuple, set)) else []
        if any(_runtime_names_match(action_group, runtime_name, str(name)) for name in names):
            observed_count += 1
    expected_count = len(expected_pairs)
    return {
        "availability": "observed",
        "state": "triggered" if observed_count == expected_count else "not_triggered",
        "surfaces": sorted(surfaces & _INSTRUMENTED_GROUPS),
        "expected_pair_count": expected_count,
        "observed_pre_edit_pair_count": observed_count,
        "missing_pair_count": expected_count - observed_count,
        "trigger_rate": observed_count / expected_count,
    }


def _behavioral_activation_from_diagnoses(
    candidate: dict[str, Any],
    target_case_ids: set[str],
) -> dict[str, Any] | None:
    """Use the paired Analyzer assessment for surfaces without runtime call events."""
    diagnoses = candidate.get("candidate_failure_diagnoses", {})
    if not isinstance(diagnoses, dict):
        return None
    assessments: list[dict[str, Any]] = []
    for case_id in sorted(target_case_ids):
        raw = diagnoses.get(case_id, [])
        items = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
        assessments.extend(
            dict(item["prior_experiment_assessment"])
            for item in items
            if isinstance(item, dict) and isinstance(item.get("prior_experiment_assessment"), dict)
        )
    if not assessments:
        return None

    activation_values = {_normalize_text(item.get("intervention_activated")) for item in assessments}
    behavior_values = {_normalize_text(item.get("predicted_behavior_occurred")) for item in assessments}
    outcome_values = {_normalize_text(item.get("predicted_outcome_occurred")) for item in assessments}
    if activation_values == {"yes"}:
        state = "triggered"
    elif "no" in activation_values:
        state = "not_triggered"
    else:
        state = "unknown"
    return {
        "availability": "observed",
        "state": state,
        "observation_source": "candidate_failure_analysis",
        "assessment_count": len(assessments),
        "intervention_activated": _single_observation(activation_values),
        "predicted_behavior_occurred": _single_observation(behavior_values),
        "predicted_outcome_occurred": _single_observation(outcome_values),
    }


def _single_observation(values: set[str]) -> str:
    values.discard("")
    return next(iter(values)) if len(values) == 1 else "mixed" if values else "unknown"


def _unavailable_activation(availability: str, surfaces: set[str], reason: str) -> dict[str, Any]:
    return {
        "availability": availability,
        "state": availability,
        "surfaces": sorted(surface for surface in surfaces if surface),
        "expected_pair_count": None,
        "observed_pre_edit_pair_count": None,
        "missing_pair_count": None,
        "trigger_rate": None,
        "reason": reason,
    }


def _target_regression(candidate: dict[str, Any], target_case_ids: set[str]) -> dict[str, Any]:
    if isinstance(candidate.get("regressed_target_case_ids"), (list, tuple, set)):
        regressed = _case_ids(candidate["regressed_target_case_ids"]) & target_case_ids
    else:
        source_scores = candidate.get("source_case_scores")
        candidate_scores = candidate.get("candidate_case_scores")
        if not isinstance(source_scores, dict) or not isinstance(candidate_scores, dict):
            return {
                "availability": "missing_artifact",
                "regressed_case_count": None,
                "rate": None,
            }
        if any(case_id not in source_scores or case_id not in candidate_scores for case_id in target_case_ids):
            return {
                "availability": "missing_artifact",
                "regressed_case_count": None,
                "rate": None,
            }
        regressed: set[str] = set()
        for case_id in target_case_ids:
            candidate_score = _number(candidate_scores.get(case_id))
            source_score = _number(source_scores.get(case_id))
            if candidate_score is not None and source_score is not None and candidate_score < source_score:
                regressed.add(case_id)
    return {
        "availability": "observed",
        "regressed_case_count": len(regressed),
        "rate": len(regressed) / len(target_case_ids) if target_case_ids else None,
    }


def _verifier_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    deltas = candidate.get("verifier_deltas_by_case")
    if not isinstance(deltas, dict) or not deltas:
        return {"availability": "missing_artifact", "case_count": 0}
    list_fields = (
        "newly_passed_requirements",
        "remaining_failed_requirements",
        "regressed_requirements",
        "newly_passed_fail_to_pass",
        "remaining_failed_fail_to_pass",
        "regressed_fail_to_pass",
        "regressed_pass_to_pass",
        "newly_passed_atomic_checks",
        "remaining_failed_atomic_checks",
        "regressed_atomic_checks",
    )
    summary = {field: 0 for field in list_fields}
    partial_progress_cases = 0
    observed_cases = 0
    for delta in deltas.values():
        if not isinstance(delta, dict):
            continue
        observed_cases += 1
        partial_progress_cases += int(delta.get("partial_progress") is True)
        for field in list_fields:
            value = delta.get(field, [])
            if isinstance(value, (list, tuple, set)):
                summary[field] += len(value)
    return {
        "availability": "observed",
        "case_count": observed_cases,
        "partial_progress_case_count": partial_progress_cases,
        **{f"{field}_count": count for field, count in summary.items()},
    }


def _evidence_refs(candidate: dict[str, Any]) -> dict[str, str]:
    aliases = {
        "source_eval_ref": ("source_eval_ref", "source_eval_ref_path", "paired_source_eval_ref_path"),
        "candidate_eval_ref": ("candidate_eval_ref", "candidate_eval_ref_path"),
        "candidate_harness_ref": ("candidate_harness_ref", "candidate_harness_refs_path"),
        "member_optimization_ref": ("member_optimization_ref", "member_optimization_ref_path"),
        "failure_analysis_ref": ("failure_analysis_ref", "candidate_failure_analysis_ref_path"),
    }
    refs: dict[str, str] = {}
    for output_key, keys in aliases.items():
        value = _first_text(candidate, *keys)
        if value:
            refs[output_key] = value
    return refs


def _search_metric_unavailable_reason(
    *,
    parent_harness_ref: str,
    source_eval_ref: str,
    target_case_ids: set[str],
    evaluation_protocol: str,
    rank_frozen: bool,
    ranking_policy: str,
    candidates: list[dict[str, Any]],
) -> str:
    if len(candidates) < 2:
        return "requires_at_least_two_comparable_candidates"
    required = {
        "parent_harness_ref": parent_harness_ref,
        "source_eval_ref": source_eval_ref,
        "target_case_ids": target_case_ids,
        "evaluation_protocol": evaluation_protocol,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        return f"missing_cohort_field:{','.join(sorted(missing))}"
    if not rank_frozen:
        return "rank_not_frozen_before_evaluation"
    if not ranking_policy:
        return "ranking_policy_missing"

    candidate_ids = [item["candidate_id"] for item in candidates]
    if len(set(candidate_ids)) != len(candidate_ids):
        return "duplicate_candidate_id"
    ranks = [item["proposal_rank"]["predicted_rank"] for item in candidates]
    if any(rank is None for rank in ranks) or set(ranks) != set(range(1, len(candidates) + 1)):
        return "predicted_ranks_are_not_a_frozen_permutation"
    for item in candidates:
        identity = item["identity"]
        if identity["parent_harness_ref"] != parent_harness_ref:
            return "parent_harness_mismatch"
        if identity["source_eval_ref"] != source_eval_ref:
            return "source_evaluation_mismatch"
        if set(identity["target_case_ids"]) != target_case_ids:
            return "target_case_set_mismatch"
        if identity["evaluation_protocol"] != evaluation_protocol:
            return "evaluation_protocol_mismatch"
        rank = item["proposal_rank"]
        if rank["rank_frozen"] is not True:
            return "candidate_rank_not_frozen_before_evaluation"
        if rank["ranking_policy"] != ranking_policy:
            return "candidate_ranking_policy_mismatch"
        if item["outcome"]["target_gain"] is None:
            return "target_gain_missing"
    source_scores = {item["outcome"]["source_target_score"] for item in candidates}
    if None in source_scores:
        return "source_target_score_missing"
    if len(source_scores) != 1:
        return "source_target_score_mismatch"
    return ""


def _search_metrics(
    candidates: list[dict[str, Any]],
    *,
    top_m: int,
    unavailable_reason: str,
) -> dict[str, Any]:
    if unavailable_reason:
        unavailable = _unavailable_metric(unavailable_reason)
        return {
            "best_of_k_gain": {**unavailable, "k": len(candidates)},
            "top_m_gain": {**unavailable, "requested_m": top_m, "effective_m": None},
            "selection_regret": dict(unavailable),
        }

    by_gain = sorted(
        candidates,
        key=lambda item: (
            -float(item["outcome"]["target_gain"]),
            int(item["proposal_rank"]["predicted_rank"]),
            item["candidate_index"],
        ),
    )
    best = by_gain[0]
    effective_m = min(top_m, len(candidates))
    top_candidates = [item for item in candidates if int(item["proposal_rank"]["predicted_rank"]) <= effective_m]
    top_best = sorted(
        top_candidates,
        key=lambda item: (
            -float(item["outcome"]["target_gain"]),
            int(item["proposal_rank"]["predicted_rank"]),
            item["candidate_index"],
        ),
    )[0]
    predicted_top1 = next(item for item in candidates if int(item["proposal_rank"]["predicted_rank"]) == 1)
    selection_regret = {
        "status": "available",
        "value": float(best["outcome"]["target_gain"]) - float(predicted_top1["outcome"]["target_gain"]),
        "best_candidate_id": best["candidate_id"],
        "predicted_top1_candidate_id": predicted_top1["candidate_id"],
    }
    return {
        "best_of_k_gain": {
            "status": "available",
            "value": best["outcome"]["target_gain"],
            "candidate_id": best["candidate_id"],
            "k": len(candidates),
        },
        "top_m_gain": {
            "status": "available",
            "value": top_best["outcome"]["target_gain"],
            "candidate_id": top_best["candidate_id"],
            "requested_m": top_m,
            "effective_m": effective_m,
        },
        "selection_regret": selection_regret,
    }


def _unavailable_metric(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "value": None, "reason": reason}


def _proposal_target_case_ids(
    proposal: dict[str, Any],
    capabilities: list[dict[str, Any]],
) -> set[str]:
    target_case_ids = _case_ids(proposal.get("target_case_ids", []))
    for capability in capabilities:
        target_case_ids.update(_case_ids(capability.get("target_case_ids", [])))
    return target_case_ids


def _is_executable(proposal: dict[str, Any], capabilities: list[dict[str, Any]]) -> bool:
    for key in ("is_executable", "executable"):
        if isinstance(proposal.get(key), bool):
            return bool(proposal[key])
    status = _normalize_text(proposal.get("proposal_status") or proposal.get("status"))
    if status in _NON_EXECUTABLE_STATUSES:
        return False
    return bool(capabilities) and all(
        _text(capability.get("action_group")) and _text(capability.get("operation")) for capability in capabilities
    )


def _capabilities(value: dict[str, Any]) -> list[dict[str, Any]]:
    capabilities = value.get("capabilities", [])
    return [dict(item) for item in capabilities if isinstance(item, dict)] if isinstance(capabilities, list) else []


def _candidate_index(candidate: dict[str, Any], fallback: int) -> int:
    value = candidate.get("candidate_index")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return fallback


def _candidate_id(candidate: dict[str, Any], candidate_index: int) -> str:
    return _text(candidate.get("candidate_id")) or f"candidate_{candidate_index:03d}"


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit() and int(value.strip()) > 0:
        return int(value.strip())
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _case_ids(value: Any) -> set[str]:
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {_text(item) for item in value if _text(item)}


def _first_text(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = _text(value.get(key))
        if text:
            return text
    return ""


def _runtime_names_match(action_group: str, planned: str, observed: str) -> bool:
    planned_name = _normalize_text(planned).replace("-", "_")
    observed_name = _normalize_text(observed).replace("-", "_")
    if action_group == "tool":
        planned_name = planned_name.removesuffix("_tool")
        observed_name = observed_name.removesuffix("_tool")
    return bool(planned_name) and planned_name == observed_name


def _cohort_id(
    parent_harness_ref: str,
    source_eval_ref: str,
    target_case_ids: set[str],
    evaluation_protocol: str,
) -> str:
    payload = _canonical_json(
        {
            "parent_harness_ref": parent_harness_ref,
            "source_eval_ref": source_eval_ref,
            "target_case_ids": sorted(target_case_ids),
            "evaluation_protocol": evaluation_protocol,
        }
    )
    return f"cohort_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", _text(value)).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
