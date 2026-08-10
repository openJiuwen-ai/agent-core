# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Built-in static and trace evaluators for capability fingerprints."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from math import isfinite
from typing import Any

from openjiuwen.symphony.evaluation.base import (
    BaseEvaluator,
    EvaluationContext,
    LLMJudgeEvaluator,
    redacted_evidence_reference,
)
from openjiuwen.symphony.models import (
    EvidenceRef,
    FailureReason,
    FailureSeverity,
    ImprovementSuggestion,
    MetricResult,
    SuggestionPriority,
)


def _fingerprint_evidence(context: EvaluationContext, description: str) -> EvidenceRef:
    return EvidenceRef(
        evidence_type="capability_fingerprint",
        reference=f"capability:{context.fingerprint.capability_id}",
        description=description,
    )


def _trace_evidence(context: EvaluationContext, description: str) -> EvidenceRef:
    case = context.case
    reference = (
        redacted_evidence_reference("case", case.case_id) if case is not None else f"capability:{context.capability_id}"
    )
    return EvidenceRef(evidence_type="evaluation_case", reference=reference, description=description)


def _matches_capability(call: Any, context: EvaluationContext) -> bool:
    if not isinstance(call, dict):
        return False
    identity = (call.get("capability_id"), call.get("capability_type"))
    return identity == (context.capability_id, context.capability_type)


def _non_negative_finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric_value = float(value)
    if not isfinite(numeric_value) or numeric_value < 0:
        return None
    return numeric_value


def _output_evaluation_payload(context: EvaluationContext) -> dict[str, Any]:
    """Exclude parent outputs when a trace attributes evidence to a child capability."""

    payload = context.payload()
    case = context.case
    if case is None or (case.capability_id, case.capability_type) == (
        context.capability_id,
        context.capability_type,
    ):
        return payload
    raw_case = payload.get("case")
    if not isinstance(raw_case, dict):
        return payload
    case_payload = dict(raw_case)
    case_payload["expected_output"] = None
    case_payload["actual_output"] = None
    case_payload["success"] = None
    raw_calls = case_payload.get("calls")
    if isinstance(raw_calls, list):
        case_payload["calls"] = [call for call in raw_calls if _matches_capability(call, context)]
    payload["case"] = case_payload
    return payload


class StructureConformanceEvaluator(BaseEvaluator):
    """Check the normalized fingerprint contract without reading source assets."""

    metric_id = "structure_conformance"
    scope = "static"

    def evaluate(self, context: EvaluationContext) -> MetricResult:
        fingerprint = context.fingerprint
        invalid: list[tuple[str, str]] = []
        for field_name in ("capability_id", "capability_type", "name", "content_hash"):
            value = getattr(fingerprint, field_name, None)
            if not isinstance(value, str) or not value.strip():
                invalid.append((field_name, f"{field_name} must be a non-empty string"))
        for field_name in ("inputs", "outputs", "tags"):
            value = getattr(fingerprint, field_name, None)
            if not isinstance(value, (tuple, list)):
                invalid.append((field_name, f"{field_name} must be a sequence"))
        for direction in ("inputs", "outputs"):
            values = getattr(fingerprint, direction, ())
            names = [getattr(value, "name", None) for value in values]
            if any(not isinstance(name, str) or not name.strip() for name in names):
                invalid.append((direction, f"{direction} entries must have non-empty names"))
            elif len(names) != len(set(names)):
                invalid.append((direction, f"{direction} names must be unique"))

        evidence = _fingerprint_evidence(context, "Normalized fingerprint structure was inspected.")
        if not invalid:
            return self.result(
                context,
                score=1.0,
                status="pass",
                reason="The fingerprint conforms to the public structure contract.",
                details={"checked_fields": 7},
                evidence=(evidence,),
            )
        failures = tuple(
            FailureReason(
                code=f"invalid_{field_name}",
                message=message,
                severity=FailureSeverity.WARNING,
                evidence=(evidence,),
            )
            for field_name, message in invalid
        )
        suggestions = tuple(
            ImprovementSuggestion(
                code=f"fix_{field_name}",
                message=f"Normalize the fingerprint {field_name} field before evaluation.",
                priority=SuggestionPriority.HIGH,
                related_failures=(f"invalid_{field_name}",),
            )
            for field_name, _ in invalid
        )
        return self.result(
            context,
            score=0.0,
            status="fail",
            reason=f"The fingerprint has {len(invalid)} structural issue(s).",
            details={"invalid_fields": [field_name for field_name, _ in invalid]},
            evidence=(evidence,),
            failures=failures,
            suggestions=suggestions,
        )


class DescriptionQualityEvaluator(LLMJudgeEvaluator):
    """Judge whether a description is specific enough for discovery and use."""

    metric_id = "description_quality"
    scope = "static"
    rubric = (
        "Judge whether the capability description clearly states what the capability does, "
        "when it should be used, and material limits without unsupported claims."
    )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        if context.fingerprint.description.strip():
            return None
        evidence = _fingerprint_evidence(context, "The normalized description field is empty.")
        return self.result(
            context,
            score=0.0,
            status="fail",
            reason="The capability description is empty.",
            evidence=(evidence,),
            failures=(
                FailureReason(
                    code="missing_description",
                    message="The capability description is empty.",
                    severity=FailureSeverity.WARNING,
                    evidence=(evidence,),
                ),
            ),
            suggestions=(
                ImprovementSuggestion(
                    code="add_capability_description",
                    message="Describe the capability purpose, intended use, and important limits.",
                    priority=SuggestionPriority.HIGH,
                    related_failures=("missing_description",),
                ),
            ),
        )


class ClassificationConsistencyEvaluator(LLMJudgeEvaluator):
    """Judge consistency among description, semantic profile, classification, and tags."""

    metric_id = "classification_consistency"
    scope = "static"
    rubric = (
        "Judge whether classification and tags are semantically consistent with the capability "
        "description, profile, inputs, and outputs."
    )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        fingerprint = context.fingerprint
        if fingerprint.classification.strip() or fingerprint.tags:
            return None
        evidence = _fingerprint_evidence(context, "Classification and tag fields are both empty.")
        return self.result(
            context,
            score=0.0,
            status="fail",
            reason="The fingerprint has neither a classification nor tags.",
            evidence=(evidence,),
            failures=(
                FailureReason(
                    code="missing_classification",
                    message="The fingerprint has neither a classification nor tags.",
                    severity=FailureSeverity.WARNING,
                    evidence=(evidence,),
                ),
            ),
            suggestions=(
                ImprovementSuggestion(
                    code="add_classification",
                    message="Add a classification or tags derived from the capability semantics.",
                    priority=SuggestionPriority.MEDIUM,
                    related_failures=("missing_classification",),
                ),
            ),
        )


class TraceEvaluator(BaseEvaluator):
    """Base class for metrics that require a caller-supplied trace case."""

    scope = "trace"

    def require_case(self, context: EvaluationContext) -> MetricResult | None:
        if context.case is not None:
            return None
        return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")


class LLMTraceEvaluator(LLMJudgeEvaluator, TraceEvaluator):
    """Base class for opt-in semantic trace metrics."""

    scope = "trace"

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        return self.require_case(context)


class SuccessRateEvaluator(TraceEvaluator):
    """Report whether a supplied execution case succeeded."""

    metric_id = "success_rate"

    def evaluate(self, context: EvaluationContext) -> MetricResult:
        missing = self.require_case(context)
        if missing is not None:
            return missing
        case = context.case
        if case is None:
            return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")
        directly_evaluated = (case.capability_id, case.capability_type) == (
            context.fingerprint.capability_id,
            context.fingerprint.capability_type,
        )
        success = case.success if directly_evaluated else None
        calls = context.matching_calls
        if directly_evaluated and not calls:
            calls = case.calls
        if success is None and calls:
            call_outcomes = [
                call.success if call.success is not None else False if call.error else None for call in calls
            ]
            if all(outcome is not None for outcome in call_outcomes):
                success = all(bool(outcome) for outcome in call_outcomes)
        if success is None:
            return self.not_applicable(
                context,
                "The trace does not contain an execution outcome.",
                code="missing_success_outcome",
            )
        evidence = _trace_evidence(context, "The caller-supplied execution outcome was evaluated.")
        if success:
            return self.result(
                context,
                score=1.0,
                status="pass",
                reason="The execution succeeded.",
                evidence=(evidence,),
            )
        return self.result(
            context,
            score=0.0,
            status="fail",
            reason="The execution did not succeed.",
            evidence=(evidence,),
            failures=(
                FailureReason(
                    code="execution_unsuccessful",
                    message="The caller-supplied trace reports an unsuccessful execution.",
                    severity=FailureSeverity.WARNING,
                    evidence=(evidence,),
                ),
            ),
            suggestions=(
                ImprovementSuggestion(
                    code="inspect_execution_failure",
                    message="Inspect the referenced trace and address its reported failure.",
                    priority=SuggestionPriority.HIGH,
                    related_failures=("execution_unsuccessful",),
                ),
            ),
        )


def _duration_ms(started_at: datetime | None, ended_at: datetime | None) -> float | None:
    if started_at is None or ended_at is None:
        return None
    return max(0.0, (ended_at - started_at).total_seconds() * 1_000.0)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def latency_statistics(values: Iterable[float]) -> dict[str, Any]:
    """Return raw latency distribution statistics without applying business thresholds."""

    samples = [float(value) for value in values if isfinite(float(value)) and float(value) >= 0]
    if not samples:
        return {}
    return {
        "avg_ms": sum(samples) / len(samples),
        "p50_ms": _percentile(samples, 0.50),
        "p95_ms": _percentile(samples, 0.95),
        "max_ms": max(samples),
        "count": len(samples),
        "samples_ms": samples,
    }


class LatencyEvaluator(TraceEvaluator):
    """Observe supplied call latency and only score an explicit caller target."""

    metric_id = "latency"

    def evaluate(self, context: EvaluationContext) -> MetricResult:
        missing = self.require_case(context)
        if missing is not None:
            return missing
        case = context.case
        if case is None:
            return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")
        samples: list[float] = []
        directly_evaluated = (case.capability_id, case.capability_type) == (
            context.fingerprint.capability_id,
            context.fingerprint.capability_type,
        )
        case_duration: float | None = None
        if directly_evaluated:
            for key in ("duration_ms", "latency_ms"):
                case_duration = _non_negative_finite_float(case.metadata.get(key))
                if case_duration is not None:
                    break
        calls = context.matching_calls
        if directly_evaluated and not calls:
            calls = case.calls
        for call in calls:
            value = call.latency_ms
            if value is None:
                value = _duration_ms(call.started_at, call.ended_at)
            if value is not None:
                samples.append(float(value))
        if case_duration is not None:
            samples = [case_duration]
        details = latency_statistics(samples)
        if not details:
            return self.not_applicable(
                context,
                "The trace does not contain latency observations.",
                code="missing_latency_observation",
            )
        details["observation_basis"] = "case_end_to_end" if case_duration is not None else "capability_calls"
        evidence = _trace_evidence(context, "Caller-supplied latency observations were aggregated.")
        target: float | None = None
        target_sources = (
            (case.metadata, context.fingerprint.metadata)
            if directly_evaluated
            else (*[call.metadata for call in calls], context.fingerprint.metadata)
        )
        for metadata in target_sources:
            for key in ("latency_target_ms", "target_latency_ms", "expected_latency_ms"):
                target = _non_negative_finite_float(metadata.get(key))
                if target is not None:
                    break
            if target is not None:
                break
        if target is None:
            return self.result(
                context,
                score=None,
                status="observed",
                reason="Latency was observed without applying a target or admission threshold.",
                details=details,
                evidence=(evidence,),
            )
        if case_duration is not None:
            observed = case_duration
            target_basis = "case_end_to_end"
        elif len(samples) == 1:
            observed = samples[0]
            target_basis = "single_call_latency"
        else:
            observed = float(details["avg_ms"])
            target_basis = "average_call_latency"
        passed = observed <= target
        details.update({"observed_ms": observed, "target_ms": target, "target_basis": target_basis})
        return self.result(
            context,
            score=1.0 if passed else 0.0,
            status="pass" if passed else "fail",
            reason=(
                "Observed latency met the supplied target."
                if passed
                else "Observed latency exceeded the supplied target."
            ),
            details=details,
            evidence=(evidence,),
        )


class AccuracyEvaluator(LLMTraceEvaluator):
    """Judge factual or expected-output correctness of a supplied result."""

    metric_id = "accuracy"
    rubric = (
        "Judge whether the actual output is correct for the query and supplied evidence. "
        "Treat expected output and capability-call results as authoritative when present."
    )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        missing = self.require_case(context)
        if missing is not None:
            return missing
        case = context.case
        if case is None:
            return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")
        directly_evaluated = (case.capability_id, case.capability_type) == (
            context.fingerprint.capability_id,
            context.fingerprint.capability_type,
        )
        matching_outputs = [call.output for call in context.matching_calls if call.output is not None]
        has_actual_output = case.actual_output is not None if directly_evaluated else bool(matching_outputs)
        if not has_actual_output:
            return self.not_applicable(context, "The trace has no actual output.", code="missing_actual_output")
        if directly_evaluated and case.expected_output is not None and case.actual_output == case.expected_output:
            evidence = _trace_evidence(context, "Actual output exactly matches the supplied expected output.")
            return self.result(
                context,
                score=1.0,
                status="pass",
                reason="Actual output exactly matches the supplied expected output.",
                details={"evaluation_method": "exact_match"},
                evidence=(evidence,),
            )
        return None

    def evaluation_payload(self, context: EvaluationContext) -> dict[str, Any]:
        return _output_evaluation_payload(context)


class CompletenessEvaluator(LLMTraceEvaluator):
    """Judge whether a result completes the requested work."""

    metric_id = "completeness"
    rubric = (
        "Judge whether the actual output and capability calls complete every material part "
        "of the caller-supplied query."
    )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        missing = self.require_case(context)
        if missing is not None:
            return missing
        case = context.case
        if case is None:
            return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")
        directly_evaluated = (case.capability_id, case.capability_type) == (
            context.fingerprint.capability_id,
            context.fingerprint.capability_type,
        )
        matching_outputs = [call.output for call in context.matching_calls if call.output not in (None, "")]
        has_usable_output = case.actual_output not in (None, "") if directly_evaluated else bool(matching_outputs)
        if has_usable_output:
            return None
        evidence = _trace_evidence(context, "The trace contains no usable actual output.")
        return self.result(
            context,
            score=0.0,
            status="fail",
            reason="The trace contains no usable actual output.",
            evidence=(evidence,),
            failures=(
                FailureReason(
                    code="missing_actual_output",
                    message="The trace contains no usable actual output.",
                    severity=FailureSeverity.WARNING,
                    evidence=(evidence,),
                ),
            ),
            suggestions=(
                ImprovementSuggestion(
                    code="produce_complete_output",
                    message="Return an output that addresses the requested task.",
                    priority=SuggestionPriority.HIGH,
                    related_failures=("missing_actual_output",),
                ),
            ),
        )

    def evaluation_payload(self, context: EvaluationContext) -> dict[str, Any]:
        return _output_evaluation_payload(context)


class CapabilitySelectionEvaluator(LLMTraceEvaluator):
    """Judge whether the observed calls selected suitable capabilities."""

    metric_id = "capability_selection"
    rubric = (
        "Judge whether the capabilities selected in the trace are relevant and sufficient "
        "for the query, without relying on capability execution outside the supplied trace."
    )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        missing = self.require_case(context)
        if missing is not None:
            return missing
        case = context.case
        if case is None:
            return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")
        if not case.calls:
            return self.not_applicable(
                context,
                "The trace contains no capability selections.",
                code="missing_capability_calls",
            )
        return None


class CompositionEffectivenessEvaluator(LLMTraceEvaluator):
    """Judge the ordering and hand-off of a multi-capability trace."""

    metric_id = "composition_effectiveness"
    rubric = (
        "Judge whether the order, inputs, outputs, and hand-offs among the selected capabilities "
        "form an effective composition for the query."
    )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        missing = self.require_case(context)
        if missing is not None:
            return missing
        case = context.case
        if case is None:
            return self.not_applicable(context, "No evaluation trace case was supplied.", code="missing_trace_case")
        if len(case.calls) < 2:
            return self.not_applicable(
                context,
                "Composition evaluation requires at least two observed capability calls.",
                code="insufficient_composition_calls",
            )
        return None


BUILTIN_EVALUATORS = (
    StructureConformanceEvaluator,
    DescriptionQualityEvaluator,
    ClassificationConsistencyEvaluator,
    SuccessRateEvaluator,
    LatencyEvaluator,
    AccuracyEvaluator,
    CompletenessEvaluator,
    CapabilitySelectionEvaluator,
    CompositionEffectivenessEvaluator,
)


__all__ = [
    "BUILTIN_EVALUATORS",
    "AccuracyEvaluator",
    "CapabilitySelectionEvaluator",
    "ClassificationConsistencyEvaluator",
    "CompletenessEvaluator",
    "CompositionEffectivenessEvaluator",
    "DescriptionQualityEvaluator",
    "LatencyEvaluator",
    "StructureConformanceEvaluator",
    "SuccessRateEvaluator",
    "latency_statistics",
]
