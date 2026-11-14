# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic paired validation for candidate Improver policies.

The validator deliberately consumes already materialized checkpoint results. It
does not run an Improver, mutate policy state, or publish a policy version.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Collection, Literal, Mapping, Sequence

MetaValidationMode = Literal["offline_rerank", "live_generation"]

_MODES = {"offline_rerank", "live_generation"}
_FROZEN_FIELDS = (
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
)
_OFFLINE_METRICS = ("top_m_gain", "selection_regret")
_LIVE_METRICS = (
    "best_of_k_gain",
    "top_m_gain",
    "selection_regret",
    "final_harness_gain_per_budget",
    "regression_failure_rate",
    "infrastructure_failure_rate",
)
_RATE_METRICS = {"regression_failure_rate", "infrastructure_failure_rate"}


@dataclass(frozen=True)
class MetaValidationThresholds:
    """Promotion thresholds for paired macro averages.

    Tolerances are expressed in metric units. A non-degradation tolerance of
    ``0.01`` permits the candidate macro average to be at most 0.01 worse.
    """

    min_unseen_checkpoints: int = 3
    top_m_gain_non_degradation_tolerance: float = 0.0
    min_selection_regret_improvement: float = 1e-12
    best_of_k_gain_non_degradation_tolerance: float = 0.0
    final_harness_gain_per_budget_non_degradation_tolerance: float = 0.0
    max_regression_failure_rate_increase: float = 0.0
    max_infrastructure_failure_rate_increase: float = 0.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.min_unseen_checkpoints, bool)
            or not isinstance(self.min_unseen_checkpoints, int)
            or self.min_unseen_checkpoints < 1
        ):
            raise ValueError("min_unseen_checkpoints must be an integer of at least 1")
        for name, value in asdict(self).items():
            if name == "min_unseen_checkpoints":
                continue
            if not _is_finite_number(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")


def paired_meta_validate(
    *,
    baseline_results: Sequence[Mapping[str, Any]],
    candidate_results: Sequence[Mapping[str, Any]],
    mode: MetaValidationMode,
    thresholds: MetaValidationThresholds | Mapping[str, Any] | None = None,
    meta_train_checkpoint_ids: Collection[str] = (),
) -> dict[str, Any]:
    """Compare ``I_t`` and one candidate Improver on paired unseen checkpoints.

    Each checkpoint record uses this canonical shape::

        {
            "checkpoint_id": "meta_test_001",
            "split": "meta_test",
            "base_harness_id": "...",
            "failure_evidence_id": "...",
            "base_model_id": "...",
            "k": 3,
            "top_m": 2,
            "token_budget": 100000,
            "tool_budget": 40,
            "runtime_budget": 1800,
            "verifier_id": "...",
            "protocol_id": "...",
            "metrics": {...},
        }

    Pair identity and every frozen field must match exactly. Invalid pairings
    produce a rejected, non-promotable report rather than partial statistics.
    """
    resolved_thresholds = _thresholds(thresholds)
    report = _empty_report(mode=mode, thresholds=resolved_thresholds)

    if mode not in _MODES:
        return _reject(report, "invalid_mode", detail={"mode": mode})

    baseline_by_id, baseline_issues = _index_results(baseline_results, side="baseline")
    candidate_by_id, candidate_issues = _index_results(candidate_results, side="candidate")
    baseline_identity, baseline_identity_issues = _improver_identity(baseline_results, side="baseline")
    candidate_identity, candidate_identity_issues = _improver_identity(candidate_results, side="candidate")
    report["baseline_improver"] = baseline_identity
    report["candidate_improver"] = candidate_identity
    issues = baseline_issues + candidate_issues + baseline_identity_issues + candidate_identity_issues
    if not baseline_results or not candidate_results:
        issues.append({"code": "empty_checkpoint_results"})

    if (
        baseline_identity.get("policy_digest")
        and candidate_identity.get("policy_digest")
        and baseline_identity["policy_digest"] == candidate_identity["policy_digest"]
    ):
        issues.append({"code": "identical_improver_policy_digest"})

    baseline_ids = set(baseline_by_id)
    candidate_ids = set(candidate_by_id)
    if baseline_ids != candidate_ids:
        issues.append(
            {
                "code": "checkpoint_set_mismatch",
                "baseline_only": sorted(baseline_ids - candidate_ids),
                "candidate_only": sorted(candidate_ids - baseline_ids),
            }
        )

    train_ids = {str(item).strip() for item in meta_train_checkpoint_ids if str(item).strip()}
    for checkpoint_id in sorted(baseline_ids & candidate_ids):
        baseline = baseline_by_id[checkpoint_id]
        candidate = candidate_by_id[checkpoint_id]
        issues.extend(_pair_issues(checkpoint_id, baseline, candidate, train_ids=train_ids))

    if issues:
        report["validation"]["issues"] = issues
        report["validation"]["reason_codes"] = _reason_codes(issues)
        report["validation"]["checkpoint_count"] = len(baseline_ids & candidate_ids)
        report["promotion"]["reason_codes"] = list(report["validation"]["reason_codes"])
        return report

    required_metrics = _OFFLINE_METRICS if mode == "offline_rerank" else _LIVE_METRICS
    checkpoint_reports: list[dict[str, Any]] = []
    metric_issues: list[dict[str, Any]] = []
    for checkpoint_id in sorted(baseline_ids):
        baseline = baseline_by_id[checkpoint_id]
        candidate = candidate_by_id[checkpoint_id]
        baseline_metrics, baseline_metric_issues = _required_metrics(
            checkpoint_id,
            baseline,
            side="baseline",
            required_metrics=required_metrics,
        )
        candidate_metrics, candidate_metric_issues = _required_metrics(
            checkpoint_id,
            candidate,
            side="candidate",
            required_metrics=required_metrics,
        )
        metric_issues.extend(baseline_metric_issues)
        metric_issues.extend(candidate_metric_issues)
        if baseline_metric_issues or candidate_metric_issues:
            continue
        checkpoint_reports.append(
            _checkpoint_report(
                checkpoint_id,
                baseline,
                baseline_metrics=baseline_metrics,
                candidate_metrics=candidate_metrics,
                required_metrics=required_metrics,
            )
        )

    report["validation"] = {
        "status": "accepted",
        "checkpoint_count": len(baseline_ids),
        "unseen_checkpoint_count": len(baseline_ids),
        "reason_codes": [],
        "issues": [],
    }
    report["checkpoint_ids"] = sorted(baseline_ids)
    report["required_metrics"] = list(required_metrics)

    if metric_issues:
        report["validation"]["metrics_status"] = "incomplete"
        report["validation"]["issues"] = metric_issues
        report["validation"]["reason_codes"] = _reason_codes(metric_issues)
        report["promotion"]["reason_codes"] = list(report["validation"]["reason_codes"])
        return report

    report["validation"]["metrics_status"] = "complete"
    report["checkpoints"] = checkpoint_reports
    report["macro_averages"] = _macro_averages(checkpoint_reports, required_metrics)
    report["promotion"] = _promotion_decision(
        mode=mode,
        macro_averages=report["macro_averages"],
        checkpoint_count=len(checkpoint_reports),
        thresholds=resolved_thresholds,
    )
    return report


def _thresholds(
    value: MetaValidationThresholds | Mapping[str, Any] | None,
) -> MetaValidationThresholds:
    if value is None:
        return MetaValidationThresholds()
    if isinstance(value, MetaValidationThresholds):
        return value
    if isinstance(value, Mapping):
        return MetaValidationThresholds(**dict(value))
    raise TypeError("thresholds must be MetaValidationThresholds, a mapping, or None")


def _empty_report(*, mode: str, thresholds: MetaValidationThresholds) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "paired_improver_meta_validation",
        "mode": mode,
        "validation": {
            "status": "rejected",
            "checkpoint_count": 0,
            "unseen_checkpoint_count": 0,
            "reason_codes": [],
            "issues": [],
        },
        "checkpoint_ids": [],
        "required_metrics": [],
        "checkpoints": [],
        "macro_averages": {},
        "thresholds": asdict(thresholds),
        "baseline_improver": {},
        "candidate_improver": {},
        "promotion": {
            "status": "inconclusive",
            "scope": "full_improver",
            "reason_codes": [],
        },
    }


def _reject(report: dict[str, Any], code: str, *, detail: Mapping[str, Any]) -> dict[str, Any]:
    issue = {"code": code, **dict(detail)}
    report["validation"]["issues"] = [issue]
    report["validation"]["reason_codes"] = [code]
    report["promotion"]["reason_codes"] = [code]
    return report


def _index_results(
    results: Sequence[Mapping[str, Any]],
    *,
    side: str,
) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, Any]]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    for position, record in enumerate(results):
        if not isinstance(record, Mapping):
            issues.append({"code": "invalid_checkpoint_record", "side": side, "position": position})
            continue
        checkpoint_id = record.get("checkpoint_id")
        if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
            issues.append({"code": "missing_checkpoint_id", "side": side, "position": position})
            continue
        checkpoint_id = checkpoint_id.strip()
        if checkpoint_id in indexed:
            issues.append({"code": "duplicate_checkpoint_id", "side": side, "checkpoint_id": checkpoint_id})
            continue
        indexed[checkpoint_id] = record
    return indexed, issues


def _improver_identity(
    results: Sequence[Mapping[str, Any]],
    *,
    side: str,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    fields = ("improver_version_id", "improver_policy_digest")
    values_by_field: dict[str, set[str]] = {field: set() for field in fields}
    issues: list[dict[str, Any]] = []
    for position, record in enumerate(results):
        if not isinstance(record, Mapping):
            continue
        checkpoint_id = record.get("checkpoint_id")
        checkpoint_detail = checkpoint_id if isinstance(checkpoint_id, str) else ""
        for field in fields:
            value = record.get(field)
            if field not in record or value is None or value == "":
                issues.append(
                    {
                        "code": "missing_improver_identity_field",
                        "side": side,
                        "position": position,
                        "checkpoint_id": checkpoint_detail,
                        "field": field,
                    }
                )
            elif not isinstance(value, str) or not value.strip():
                issues.append(
                    {
                        "code": "invalid_improver_identity_field",
                        "side": side,
                        "position": position,
                        "checkpoint_id": checkpoint_detail,
                        "field": field,
                    }
                )
            else:
                values_by_field[field].add(value.strip())

    for field, values in values_by_field.items():
        if len(values) > 1:
            issues.append(
                {
                    "code": "inconsistent_improver_identity",
                    "side": side,
                    "field": field,
                }
            )

    version_ids = values_by_field["improver_version_id"]
    policy_digests = values_by_field["improver_policy_digest"]
    identity = {
        "version_id": next(iter(version_ids)) if len(version_ids) == 1 else "",
        "policy_digest": next(iter(policy_digests)) if len(policy_digests) == 1 else "",
    }
    return identity, issues


def _pair_issues(
    checkpoint_id: str,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    train_ids: set[str],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if checkpoint_id in train_ids:
        issues.append({"code": "meta_train_contamination", "checkpoint_id": checkpoint_id})
    for side, record in (("baseline", baseline), ("candidate", candidate)):
        split = record.get("split")
        if split != "meta_test":
            issues.append(
                {
                    "code": "non_meta_test_checkpoint",
                    "checkpoint_id": checkpoint_id,
                    "side": side,
                }
            )
        for field in _FROZEN_FIELDS:
            if field not in record or record[field] is None or record[field] == "":
                issues.append(
                    {
                        "code": "missing_frozen_field",
                        "checkpoint_id": checkpoint_id,
                        "side": side,
                        "field": field,
                    }
                )
        issues.extend(_frozen_value_issues(checkpoint_id, record, side=side))
    for field in _FROZEN_FIELDS:
        if field in baseline and field in candidate and baseline[field] != candidate[field]:
            issues.append(
                {
                    "code": "frozen_field_mismatch",
                    "checkpoint_id": checkpoint_id,
                    "field": field,
                }
            )
    if baseline.get("k") == candidate.get("k") == 1:
        issues.append({"code": "search_metrics_require_k_at_least_two", "checkpoint_id": checkpoint_id})
    return issues


def _frozen_value_issues(
    checkpoint_id: str,
    record: Mapping[str, Any],
    *,
    side: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for field in ("base_harness_id", "failure_evidence_id", "base_model_id", "verifier_id", "protocol_id"):
        if field in record and (not isinstance(record[field], str) or not record[field].strip()):
            issues.append(
                {
                    "code": "invalid_frozen_field",
                    "checkpoint_id": checkpoint_id,
                    "side": side,
                    "field": field,
                }
            )
    for field in ("k", "top_m"):
        value = record.get(field)
        if value is not None and not _is_positive_int(value):
            issues.append(
                {
                    "code": "invalid_frozen_field",
                    "checkpoint_id": checkpoint_id,
                    "side": side,
                    "field": field,
                }
            )
    k = record.get("k")
    top_m = record.get("top_m")
    if _is_positive_int(k) and _is_positive_int(top_m) and top_m > k:
        issues.append(
            {
                "code": "invalid_frozen_field",
                "checkpoint_id": checkpoint_id,
                "side": side,
                "field": "top_m",
                "reason": "top_m_exceeds_k",
            }
        )
    for field in ("token_budget", "tool_budget", "runtime_budget"):
        value = record.get(field)
        if value is not None and (not _is_finite_number(value) or value < 0):
            issues.append(
                {
                    "code": "invalid_frozen_field",
                    "checkpoint_id": checkpoint_id,
                    "side": side,
                    "field": field,
                }
            )
    return issues


def _required_metrics(
    checkpoint_id: str,
    record: Mapping[str, Any],
    *,
    side: str,
    required_metrics: Sequence[str],
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    metrics = record.get("metrics")
    if not isinstance(metrics, Mapping):
        return {}, [{"code": "missing_required_metrics", "checkpoint_id": checkpoint_id, "side": side}]
    output: dict[str, float] = {}
    issues: list[dict[str, Any]] = []
    for metric in required_metrics:
        if metric not in metrics or metrics[metric] is None:
            issues.append(
                {
                    "code": "missing_required_metric",
                    "checkpoint_id": checkpoint_id,
                    "side": side,
                    "metric": metric,
                }
            )
            continue
        value = metrics[metric]
        if not _is_finite_number(value) or (metric in _RATE_METRICS and not 0 <= value <= 1):
            issues.append(
                {
                    "code": "invalid_metric_value",
                    "checkpoint_id": checkpoint_id,
                    "side": side,
                    "metric": metric,
                }
            )
            continue
        output[metric] = float(value)
    return output, issues


def _checkpoint_report(
    checkpoint_id: str,
    baseline: Mapping[str, Any],
    *,
    baseline_metrics: Mapping[str, float],
    candidate_metrics: Mapping[str, float],
    required_metrics: Sequence[str],
) -> dict[str, Any]:
    deltas = {metric: _rounded(candidate_metrics[metric] - baseline_metrics[metric]) for metric in required_metrics}
    improvements = {
        metric: _rounded(
            baseline_metrics[metric] - candidate_metrics[metric]
            if metric in {"selection_regret", *_RATE_METRICS}
            else candidate_metrics[metric] - baseline_metrics[metric]
        )
        for metric in required_metrics
    }
    return {
        "checkpoint_id": checkpoint_id,
        "frozen_context": {field: baseline[field] for field in _FROZEN_FIELDS},
        "baseline_metrics": dict(baseline_metrics),
        "candidate_metrics": dict(candidate_metrics),
        "deltas": deltas,
        "directional_improvements": improvements,
    }


def _macro_averages(
    checkpoints: Sequence[Mapping[str, Any]],
    required_metrics: Sequence[str],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for metric in required_metrics:
        baseline = sum(item["baseline_metrics"][metric] for item in checkpoints) / len(checkpoints)
        candidate = sum(item["candidate_metrics"][metric] for item in checkpoints) / len(checkpoints)
        lower_is_better = metric in {"selection_regret", *_RATE_METRICS}
        output[metric] = {
            "baseline": _rounded(baseline),
            "candidate": _rounded(candidate),
            "delta": _rounded(candidate - baseline),
            "improvement": _rounded(baseline - candidate if lower_is_better else candidate - baseline),
            "direction": "lower_is_better" if lower_is_better else "higher_is_better",
        }
    return output


def _promotion_decision(
    *,
    mode: str,
    macro_averages: Mapping[str, Mapping[str, float]],
    checkpoint_count: int,
    thresholds: MetaValidationThresholds,
) -> dict[str, Any]:
    reasons: list[str] = []
    insufficient_evidence = checkpoint_count < thresholds.min_unseen_checkpoints
    if insufficient_evidence:
        reasons.append("insufficient_unseen_checkpoints")

    if macro_averages["top_m_gain"]["delta"] < -thresholds.top_m_gain_non_degradation_tolerance:
        reasons.append("top_m_gain_decreased")
    if macro_averages["selection_regret"]["improvement"] < thresholds.min_selection_regret_improvement:
        reasons.append("selection_regret_not_improved")

    if mode == "offline_rerank":
        offline_reasons = list(reasons)
        if insufficient_evidence:
            ranker_assessment = "inconclusive"
        else:
            ranker_assessment = "eligible" if not offline_reasons else "ineligible"
        return {
            "status": "inconclusive",
            "scope": "ranker_evidence_only",
            "offline_ranker_assessment": ranker_assessment,
            "reason_codes": [*offline_reasons, "offline_rerank_cannot_promote_full_improver"],
        }

    if macro_averages["best_of_k_gain"]["delta"] < -thresholds.best_of_k_gain_non_degradation_tolerance:
        reasons.append("best_of_k_gain_decreased")
    if (
        macro_averages["final_harness_gain_per_budget"]["delta"]
        < -thresholds.final_harness_gain_per_budget_non_degradation_tolerance
    ):
        reasons.append("final_harness_gain_per_budget_decreased")
    if macro_averages["regression_failure_rate"]["delta"] > thresholds.max_regression_failure_rate_increase:
        reasons.append("regression_failure_rate_increased")
    if macro_averages["infrastructure_failure_rate"]["delta"] > thresholds.max_infrastructure_failure_rate_increase:
        reasons.append("infrastructure_failure_rate_increased")
    return {
        "status": "inconclusive" if insufficient_evidence else ("eligible" if not reasons else "ineligible"),
        "scope": "full_improver",
        "reason_codes": reasons or ["promotion_thresholds_satisfied"],
    }


def _reason_codes(issues: Sequence[Mapping[str, Any]]) -> list[str]:
    return list(dict.fromkeys(str(issue["code"]) for issue in issues))


def _is_finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _rounded(value: float) -> float:
    return round(float(value), 12)
