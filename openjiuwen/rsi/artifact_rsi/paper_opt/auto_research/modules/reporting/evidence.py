"""Evidence normalization and claim-status classification — see
docs/reporting_iteration_design.md §"Schemas"/"Host vs. model split for
Claim status". Phase 1 of that design: a host-only, no-model-call
foundation that a run without a prior paper never touches (see
``ReportingAgent._run_async``'s ``previous_context is None`` fallback).

``Evidence`` is the flat, uniform shape both a prior paper's reported
numbers and this run's own execution results get normalized into, so
downstream comparison never has to special-case "part JSON, part text, part
live experiment result" (docs/reporting_iteration_design.md §"Evidence").
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import (
    ExperimentResult,
    VariantResult,
)

EvidenceSource = Literal["prior_paper", "current_run"]


class Evidence(BaseModel):
    evidence_id: str
    type: Literal["experiment", "survey_finding", "reflection_note"] = "experiment"
    source: EvidenceSource
    dataset: str | None = None
    method: str | None = None
    metric: str | None = None
    value: float | str | None = None
    # Confidence-interval bounds, when the source reported them (e.g. a
    # VariantResult cell's ci95_lower/ci95_upper) -- used by
    # classify_numeric_status instead of a bare point-estimate delta.
    ci_lower: float | None = None
    ci_upper: float | None = None
    provenance: dict[str, str] = Field(default_factory=dict)


# Threshold for the no-CI fallback in classify_numeric_status: an absolute
# change smaller than this is treated as "no_significant_change" even
# without interval data, rather than flagging every float-precision wobble
# as a claim change.
_NO_CI_DELTA_THRESHOLD = 0.02


# Fields that describe *another* metric rather than being a metric
# themselves -- must never become their own Evidence row. `_std` is a
# per-metric companion (`<metric>_std`, consumed by _ci_from_std); the bare
# `ci95_*` pair is ambiguous about which metric it belongs to (see
# _ci_from_std's docstring) and is never metric-attributable, so it's
# excluded from metric extraction entirely rather than risking a wrong guess.
_AUXILIARY_KEYS = {"ci95_lower", "ci95_upper"}


def _is_reportable_numeric(name: str, value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    if name in _AUXILIARY_KEYS:
        return False
    return not name.endswith("_std")


def _cell_numeric_items(cell: dict) -> dict[str, float]:
    return {name: value for name, value in cell.items() if _is_reportable_numeric(name, value)}


def normalize_current_run_evidence(result: ExperimentResult) -> list[Evidence]:
    """Flatten ``ExperimentResult.variants`` into ``Evidence`` rows with
    ``source="current_run"``. Handles both a flat single-condition variant
    (metrics as top-level scalars) and a multi-condition variant (metrics
    nested under a ``metrics["metrics"]`` list-of-cells or dict-of-cells,
    e.g. a factorial or ablation study reported as one ``VariantResult``
    covering several cells) -- the real numbers for the latter live one
    level down, same distinction ``agent.py``'s table-building logic has to
    make for the same reason.
    """
    evidence: list[Evidence] = []
    for variant in result.variants:
        evidence.extend(_variant_evidence(variant))
    return evidence


def _variant_evidence(variant: VariantResult) -> list[Evidence]:
    flat_numeric = _cell_numeric_items(variant.metrics)
    if flat_numeric:
        return [
            Evidence(
                evidence_id=f"{variant.name}:{metric}",
                source="current_run",
                method=variant.name,
                metric=metric,
                value=value,
                ci_lower=_ci_from_std(variant.metrics, metric, value)[0],
                ci_upper=_ci_from_std(variant.metrics, metric, value)[1],
                provenance={"variant": variant.name},
            )
            for metric, value in flat_numeric.items()
        ]

    nested = variant.metrics.get("metrics")
    cells: list[tuple[str, dict]] = []
    if isinstance(nested, list):
        for index, cell in enumerate(nested):
            if isinstance(cell, dict):
                label = cell.get("cell") or cell.get("condition") or str(index)
                cells.append((label, cell))
    elif isinstance(nested, dict):
        cells = [(label, cell) for label, cell in nested.items() if isinstance(cell, dict)]

    result: list[Evidence] = []
    for cell_label, cell in cells:
        for metric, value in _cell_numeric_items(cell).items():
            ci_lower, ci_upper = _ci_from_std(cell, metric, value)
            result.append(
                Evidence(
                    evidence_id=f"{variant.name}/{cell_label}:{metric}",
                    source="current_run",
                    method=variant.name,
                    metric=metric,
                    value=value,
                    ci_lower=ci_lower,
                    ci_upper=ci_upper,
                    provenance={"variant": variant.name, "cell": cell_label},
                )
            )
    return result


def _ci_from_std(cell: dict, metric: str, value: float) -> tuple[float | None, float | None]:
    """Approximate a 95% interval from a per-metric standard-deviation
    field (``<metric>_std``) -- the convention this pipeline's per-cell
    metrics already use (e.g. ``tool_selection_accuracy_std`` alongside
    ``tool_selection_accuracy``). Deliberately does *not* use a generic
    ``ci95_lower``/``ci95_upper`` pair some cells also carry: that pair has
    no metric name attached, so attaching it uniformly to every metric in
    the cell would misattribute one metric's interval to another. A
    ``_std`` suffix is unambiguous about which metric it belongs to; a bare
    ``ci95_*`` pair is not."""
    std = cell.get(f"{metric}_std")
    if not isinstance(std, (int, float)) or isinstance(std, bool):
        return None, None
    margin = 1.96 * std
    return value - margin, value + margin


ClaimStatus = Literal["strengthened", "weakened", "no_significant_change", "tentative"]


def classify_numeric_status(old: Evidence, new: Evidence) -> ClaimStatus:
    """Pure, host-only classification of how ``new`` compares to ``old`` for
    the same metric -- no model call. See
    docs/reporting_iteration_design.md's "Host vs. model split for Claim
    status": a numeric claim's status is a deterministic function of its
    paired evidence, never a judgment call.

    If both sides carry a confidence interval, use non-overlap as the bar
    for "strengthened"/"weakened" -- this pipeline routinely runs on tiny
    samples (n=10, n=50), where a bare point-estimate delta overstates
    noise as a real change. Without interval data on either side, fall back
    to an absolute-delta threshold and label the result "tentative" so
    callers know it's a heuristic, not a statistically grounded call.
    """
    old_value, new_value = old.value, new.value
    if not isinstance(old_value, (int, float)) or not isinstance(new_value, (int, float)):
        raise ValueError("classify_numeric_status requires numeric Evidence.value on both sides")

    has_ci = (
        old.ci_lower is not None
        and old.ci_upper is not None
        and new.ci_lower is not None
        and new.ci_upper is not None
    )
    if has_ci:
        if old.ci_upper is None or new.ci_lower is None or new.ci_upper is None or old.ci_lower is None:
            raise ValueError("has_ci is True but a confidence-interval bound is unexpectedly None")
        if new.ci_lower > old.ci_upper:
            return "strengthened"
        if new.ci_upper < old.ci_lower:
            return "weakened"
        return "no_significant_change"

    delta = new_value - old_value
    if abs(delta) < _NO_CI_DELTA_THRESHOLD:
        return "no_significant_change"
    return "tentative"
