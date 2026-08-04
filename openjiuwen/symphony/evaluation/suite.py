# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evaluator registration, async orchestration, and window aggregation."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from pydantic import ValidationError as PydanticValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, raise_error
from openjiuwen.symphony.evaluation.base import (
    EvaluationContext,
    Evaluator,
    redacted_evidence_reference,
    sanitize_metric_result,
)
from openjiuwen.symphony.evaluation.evaluators import BUILTIN_EVALUATORS, latency_statistics
from openjiuwen.symphony.interfaces import SymphonyLLM
from openjiuwen.symphony.models import (
    CapabilityFingerprint,
    EvaluationCase,
    EvidenceRef,
    FailureReason,
    FailureSeverity,
    ImprovementSuggestion,
    MetricResult,
    MetricStatus,
    QualityConfidence,
    QualityResult,
    SuggestionPriority,
)

_EVALUATION_CACHE_PROTOCOL = "symphony-evaluation-suite-v3"
_BUILTIN_EVALUATOR_MODULE = "openjiuwen.symphony.evaluation"


def _is_positive_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, int):
        return False
    return value > 0


def _validated_evaluator_metric_id(evaluator: Any) -> str | None:
    metric_id = getattr(evaluator, "metric_id", None)
    if not isinstance(metric_id, str):
        return None
    if not metric_id.strip() or metric_id != metric_id.strip():
        return None
    if getattr(evaluator, "scope", None) not in {"static", "trace"}:
        return None
    if not callable(getattr(evaluator, "evaluate", None)):
        return None
    return metric_id


@dataclass(frozen=True)
class EvaluationWindow:
    """Optional caller-defined event-time window used for trace aggregation."""

    start: datetime | None = None
    end: datetime | None = None
    label: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat() if self.start is not None else None,
            "end": self.end.isoformat() if self.end is not None else None,
            "label": self.label,
        }


class EvaluationSuite:
    """Run registered metrics over fingerprints and caller-supplied traces.

    The suite never executes a capability, computes an admission decision, or
    invents an overall composite score. Model-heavy metrics are opt-in.
    """

    def __init__(
        self,
        evaluators: Iterable[Evaluator] | None = None,
        *,
        llm: SymphonyLLM | Any | None = None,
        enable_llm: bool = False,
        evidence_text_limit: int = 512,
    ) -> None:
        if not _is_positive_integer(evidence_text_limit):
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR,
                reason="evidence_text_limit must be a positive integer",
            )
        self.llm = llm
        self.enable_llm = enable_llm
        self.evidence_text_limit = evidence_text_limit
        self._evaluators: dict[str, Evaluator] = {}
        selected: Iterable[Evaluator]
        if evaluators is None:
            selected = (cast(Evaluator, evaluator_type()) for evaluator_type in BUILTIN_EVALUATORS)
        else:
            selected = evaluators
        for evaluator in selected:
            self.register(evaluator)

    @classmethod
    def default(
        cls,
        *,
        llm: SymphonyLLM | Any | None = None,
        enable_llm: bool = False,
        evidence_text_limit: int = 512,
    ) -> EvaluationSuite:
        """Create the standard static and trace metric suite."""

        return cls(
            llm=llm,
            enable_llm=enable_llm,
            evidence_text_limit=evidence_text_limit,
        )

    @property
    def evaluators(self) -> tuple[Evaluator, ...]:
        return tuple(self._evaluators.values())

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(self._evaluators)

    def cache_signature(self) -> str | None:
        """Return a safe cache key, or disable quality reuse for opaque extensions.

        Caller-provided evaluators and LLM adapters must expose a non-secret
        ``cache_signature`` string (or zero-argument method) before their
        results can be reused. Built-in deterministic evaluators are versioned
        by this module's protocol and class identity.
        """

        evaluator_signatures: list[str] = []
        for evaluator in self.evaluators:
            signature = _extension_cache_signature(evaluator)
            if signature is None:
                evaluator_type = type(evaluator)
                if not evaluator_type.__module__.startswith(_BUILTIN_EVALUATOR_MODULE):
                    return None
                signature = f"{evaluator_type.__module__}:{evaluator_type.__qualname__}"
            evaluator_signatures.append(f"{evaluator.metric_id}:{evaluator.scope}:{signature}")

        llm_signature = "disabled"
        if self.enable_llm:
            if self.llm is None:
                return None
            llm_signature = _extension_cache_signature(self.llm) or ""
            if not llm_signature:
                return None

        canonical = json.dumps(
            {
                "protocol": _EVALUATION_CACHE_PROTOCOL,
                "evaluators": evaluator_signatures,
                "llm": llm_signature,
                "evidence_text_limit": self.evidence_text_limit,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def register(self, evaluator: Evaluator, *, replace: bool = False) -> EvaluationSuite:
        """Register a structural evaluator implementation by its stable metric ID."""

        metric_id = _validated_evaluator_metric_id(evaluator)
        if metric_id is None:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR,
                reason="an evaluator requires metric_id, scope, and an evaluate method",
            )
        if metric_id in self._evaluators and not replace:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR,
                reason=f"evaluator metric is already registered: {metric_id}",
            )
        self._evaluators[metric_id] = evaluator
        return self

    def unregister(self, metric_id: str) -> Evaluator | None:
        """Remove and return a registered evaluator when present."""

        return self._evaluators.pop(metric_id, None)

    async def evaluate(
        self,
        fingerprint: CapabilityFingerprint,
        cases: Sequence[EvaluationCase] = (),
        *,
        window: EvaluationWindow | Mapping[str, Any] | None = None,
        metric_ids: Sequence[str] | None = None,
    ) -> QualityResult:
        """Evaluate one fingerprint and aggregate the selected trace window."""

        fingerprint = self._validate_fingerprint(fingerprint)
        cases = self._validate_cases(cases)
        selected_window = self._coerce_window(window)
        selected_evaluators = self._select_evaluators(metric_ids)
        selected_cases = self._select_cases(fingerprint, cases, selected_window)
        results: list[MetricResult] = []
        for evaluator in selected_evaluators:
            if evaluator.scope == "static":
                results.append(
                    await self._run(
                        evaluator,
                        EvaluationContext(
                            fingerprint=fingerprint,
                            llm=self.llm,
                            llm_enabled=self.enable_llm,
                            evidence_text_limit=self.evidence_text_limit,
                        ),
                    )
                )
                continue
            if not selected_cases:
                results.append(
                    await self._run(
                        evaluator,
                        EvaluationContext(
                            fingerprint=fingerprint,
                            llm=self.llm,
                            llm_enabled=self.enable_llm,
                            evidence_text_limit=self.evidence_text_limit,
                        ),
                    )
                )
                continue
            for case in selected_cases:
                result = await self._run(
                    evaluator,
                    EvaluationContext(
                        fingerprint=fingerprint,
                        case=case,
                        llm=self.llm,
                        llm_enabled=self.enable_llm,
                        evidence_text_limit=self.evidence_text_limit,
                    ),
                )
                details = dict(result.details)
                details.setdefault("case_reference", redacted_evidence_reference("case", case.case_id))
                results.append(result.model_copy(update={"details": details}))
        return self.aggregate(
            results,
            window=selected_window,
            sample_count=len(selected_cases),
            capability_id=fingerprint.capability_id,
            capability_type=fingerprint.capability_type,
        )

    async def evaluate_static(
        self,
        fingerprint: CapabilityFingerprint,
        *,
        metric_ids: Sequence[str] | None = None,
    ) -> tuple[MetricResult, ...]:
        """Evaluate only registered static metrics."""

        fingerprint = self._validate_fingerprint(fingerprint)
        results = []
        for evaluator in self._select_evaluators(metric_ids):
            if evaluator.scope != "static":
                continue
            results.append(
                await self._run(
                    evaluator,
                    EvaluationContext(
                        fingerprint=fingerprint,
                        llm=self.llm,
                        llm_enabled=self.enable_llm,
                        evidence_text_limit=self.evidence_text_limit,
                    ),
                )
            )
        return tuple(results)

    async def evaluate_case(
        self,
        fingerprint: CapabilityFingerprint,
        case: EvaluationCase,
        *,
        metric_ids: Sequence[str] | None = None,
    ) -> tuple[MetricResult, ...]:
        """Evaluate only registered trace metrics for one supplied case."""

        fingerprint = self._validate_fingerprint(fingerprint)
        case = self._validate_cases((case,))[0]
        self._validate_case_identity(fingerprint, case)
        results = []
        for evaluator in self._select_evaluators(metric_ids):
            if evaluator.scope != "trace":
                continue
            results.append(
                await self._run(
                    evaluator,
                    EvaluationContext(
                        fingerprint=fingerprint,
                        case=case,
                        llm=self.llm,
                        llm_enabled=self.enable_llm,
                        evidence_text_limit=self.evidence_text_limit,
                    ),
                )
            )
        return tuple(results)

    def aggregate(
        self,
        results: Sequence[MetricResult],
        *,
        window: EvaluationWindow | Mapping[str, Any] | None = None,
        sample_count: int | None = None,
        capability_id: str | None = None,
        capability_type: str | None = None,
    ) -> QualityResult:
        """Aggregate metric facts without producing a composite score or gate."""

        try:
            results = tuple(
                sanitize_metric_result(MetricResult.model_validate(result), self.evidence_text_limit)
                for result in results
            )
        except (PydanticValidationError, TypeError) as exc:
            raise build_error(
                StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
                reason="metric results are invalid",
                cause=exc,
            ) from exc
        selected_window = self._coerce_window(window)
        self._validate_window(selected_window)
        identities = {(result.capability_id, result.capability_type) for result in results}
        if capability_id is not None and capability_type is not None:
            identities.add((capability_id, capability_type))
        if len(identities) != 1:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
                reason="metric results must share exactly one capability identity",
            )
        identity = next(iter(identities))
        grouped: dict[str, list[MetricResult]] = defaultdict(list)
        order: list[str] = []
        for result in results:
            if result.metric_id not in grouped:
                order.append(result.metric_id)
            grouped[result.metric_id].append(result)
        aggregated = tuple(self._aggregate_metric(metric_id, grouped[metric_id]) for metric_id in order)
        inferred_count = max((len(items) for items in grouped.values()), default=0)
        count = inferred_count if sample_count is None else sample_count
        if count < 0:
            raise_error(StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID, reason="sample_count must be non-negative")
        details: dict[str, Any] = {
            "aggregation": "per_metric",
            "composite_score": "not_computed",
        }
        if selected_window is not None:
            details["window"] = selected_window.payload()
        return QualityResult(
            capability_id=identity[0],
            capability_type=identity[1],
            score=None,
            metrics=aggregated,
            confidence=self._confidence(count),
            sample_count=count,
            details=details,
        )

    async def _run(self, evaluator: Evaluator, context: EvaluationContext) -> MetricResult:
        try:
            raw_result: Any = evaluator.evaluate(context)
            if inspect.isawaitable(raw_result):
                raw_result = await raw_result
            if not isinstance(raw_result, MetricResult):
                raise TypeError("evaluator did not return MetricResult")
            result = MetricResult.model_validate(raw_result)
            result = sanitize_metric_result(result, self.evidence_text_limit)
            if result.metric_id != evaluator.metric_id:
                raise ValueError("evaluator result metric_id does not match its registration")
            if (result.capability_id, result.capability_type) != (
                context.capability_id,
                context.capability_type,
            ):
                raise ValueError("evaluator result identity does not match its context")
            return result
        except Exception as exc:  # noqa: BLE001 - registered evaluators are an extension boundary.
            return self._error_result(evaluator.metric_id, context, type(exc).__name__)

    @staticmethod
    def _error_result(metric_id: str, context: EvaluationContext, error_type: str) -> MetricResult:
        reason = f"Evaluator failed ({error_type})."
        evidence = EvidenceRef(
            evidence_type="evaluation_error",
            reference=f"evaluator:{metric_id}",
            description="The evaluator failed; input and exception text were not retained.",
        )
        return MetricResult(
            metric_id=metric_id,
            capability_id=context.capability_id,
            capability_type=context.capability_type,
            score=None,
            status=MetricStatus.ERROR,
            reason=reason,
            evidence=(evidence,),
            failures=(
                FailureReason(
                    code="evaluator_failed",
                    message=reason,
                    severity=FailureSeverity.ERROR,
                    evidence=(evidence,),
                    details={"error_type": error_type},
                ),
            ),
            suggestions=(
                ImprovementSuggestion(
                    code="inspect_evaluator",
                    message="Inspect the registered evaluator and retry this metric.",
                    priority=SuggestionPriority.MEDIUM,
                    related_failures=("evaluator_failed",),
                ),
            ),
        )

    def _select_evaluators(self, metric_ids: Sequence[str] | None) -> tuple[Evaluator, ...]:
        if metric_ids is None:
            return self.evaluators
        unknown = [metric_id for metric_id in metric_ids if metric_id not in self._evaluators]
        if unknown:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR,
                reason=f"unknown evaluation metrics: {', '.join(unknown)}",
            )
        return tuple(self._evaluators[metric_id] for metric_id in metric_ids)

    def _select_cases(
        self,
        fingerprint: CapabilityFingerprint,
        cases: Sequence[EvaluationCase],
        window: EvaluationWindow | None,
    ) -> tuple[EvaluationCase, ...]:
        selected: list[EvaluationCase] = []
        self._validate_window(window)
        for case in cases:
            self._validate_case_identity(fingerprint, case)
            if window is not None and (window.start is not None or window.end is not None):
                if case.event_time is None:
                    continue
                try:
                    if window.start is not None and case.event_time < window.start:
                        continue
                    if window.end is not None and case.event_time >= window.end:
                        continue
                except TypeError as exc:
                    raise_error(
                        StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
                        reason="window and event_time timezone awareness must match",
                        cause=exc,
                    )
            selected.append(case)
        return tuple(selected)

    @staticmethod
    def _validate_case_identity(fingerprint: CapabilityFingerprint, case: EvaluationCase) -> None:
        direct_match = (case.capability_id, case.capability_type) == (
            fingerprint.capability_id,
            fingerprint.capability_type,
        )
        call_match = any(
            (call.capability_id, call.capability_type) == (fingerprint.capability_id, fingerprint.capability_type)
            for call in case.calls
        )
        if not direct_match and not call_match:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
                reason="evaluation case identity does not match fingerprint",
            )

    @staticmethod
    def _validate_fingerprint(fingerprint: CapabilityFingerprint) -> CapabilityFingerprint:
        try:
            return CapabilityFingerprint.model_validate(fingerprint)
        except (PydanticValidationError, TypeError) as exc:
            raise build_error(
                StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
                reason="capability fingerprint is invalid",
                cause=exc,
            ) from exc

    @staticmethod
    def _validate_cases(cases: Sequence[EvaluationCase]) -> tuple[EvaluationCase, ...]:
        try:
            return tuple(EvaluationCase.model_validate(case) for case in cases)
        except (PydanticValidationError, TypeError) as exc:
            raise build_error(
                StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
                reason="evaluation cases are invalid",
                cause=exc,
            ) from exc

    @staticmethod
    def _coerce_window(window: EvaluationWindow | Mapping[str, Any] | None) -> EvaluationWindow | None:
        if window is None or isinstance(window, EvaluationWindow):
            return window
        if isinstance(window, Mapping):
            try:
                return EvaluationWindow(
                    start=EvaluationSuite._coerce_datetime(window.get("start")),
                    end=EvaluationSuite._coerce_datetime(window.get("end")),
                    label=window.get("label"),
                )
            except (TypeError, ValueError) as exc:
                raise_error(
                    StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
                    reason="invalid evaluation window",
                    cause=exc,
                )
        raise build_error(StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID, reason="invalid evaluation window")

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        raise TypeError("window boundary must be a datetime or ISO-8601 string")

    @staticmethod
    def _validate_window(window: EvaluationWindow | None) -> None:
        if window is None:
            return
        if not isinstance(window.start, (datetime, type(None))) or not isinstance(window.end, (datetime, type(None))):
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
                reason="window boundaries must be datetime values",
            )
        if window.label is not None and not isinstance(window.label, str):
            raise_error(StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID, reason="window label must be text")
        if window.start is not None and window.end is not None:
            invalid = False
            try:
                invalid = window.end < window.start
            except TypeError as exc:
                raise_error(
                    StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
                    reason="window start and end timezone awareness must match",
                    cause=exc,
                )
            if invalid:
                raise_error(StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID, reason="window end precedes start")

    @staticmethod
    def _confidence(sample_count: int) -> QualityConfidence:
        if sample_count == 0:
            return QualityConfidence.NONE
        if sample_count == 1:
            return QualityConfidence.LOW
        if sample_count < 10:
            return QualityConfidence.NORMAL
        return QualityConfidence.HIGH

    def _aggregate_metric(self, metric_id: str, results: Sequence[MetricResult]) -> MetricResult:
        template = results[0]
        counts: dict[str, Any] = {
            "window_result_count": len(results),
            "pass_count": sum(result.status == MetricStatus.PASS for result in results),
            "fail_count": sum(result.status == MetricStatus.FAIL for result in results),
            "not_applicable_count": sum(result.status == MetricStatus.NOT_APPLICABLE for result in results),
            "error_count": sum(result.status == MetricStatus.ERROR for result in results),
            "observed_count": sum(result.status == MetricStatus.OBSERVED for result in results),
        }
        if len(results) == 1:
            details: dict[str, Any] = dict(template.details)
            if metric_id == "latency":
                details.pop("samples_ms", None)
            details.update(counts)
            return template.model_copy(update={"details": details})
        details = counts
        case_references = sorted(
            {
                str(result.details["case_reference"])
                for result in results
                if isinstance(result.details.get("case_reference"), str)
            }
        )
        not_applicable_codes = sorted(
            {
                str(result.details["not_applicable_code"])
                for result in results
                if isinstance(result.details.get("not_applicable_code"), str)
            }
        )
        if case_references:
            details["case_references"] = case_references
        if not_applicable_codes:
            details["not_applicable_codes"] = not_applicable_codes
        if metric_id == "latency":
            samples: list[float] = []
            for result in results:
                raw_samples = result.details.get("samples_ms", [])
                if isinstance(raw_samples, list):
                    samples.extend(
                        float(value)
                        for value in raw_samples
                        if isinstance(value, (int, float)) and not isinstance(value, bool)
                    )
            details.update(latency_statistics(samples))
            details.pop("samples_ms", None)
            details["observation_bases"] = sorted(
                {
                    str(result.details["observation_basis"])
                    for result in results
                    if result.details.get("observation_basis")
                }
            )
            details["target_bases"] = sorted(
                {str(result.details["target_basis"]) for result in results if result.details.get("target_basis")}
            )
        evidence = self._deduplicate(item for result in results for item in result.evidence)
        failures = self._deduplicate(item for result in results for item in result.failures)
        suggestions = self._deduplicate(item for result in results for item in result.suggestions)
        if any(result.status == MetricStatus.ERROR for result in results):
            return MetricResult(
                metric_id=metric_id,
                capability_id=template.capability_id,
                capability_type=template.capability_type,
                score=None,
                status=MetricStatus.ERROR,
                reason="At least one metric evaluation failed in the aggregation window.",
                details=details,
                evidence=evidence,
                failures=failures,
                suggestions=suggestions,
            )
        scored = [result for result in results if result.score is not None]
        if scored:
            score = sum(float(result.score) for result in scored) / len(scored)
            status = (
                MetricStatus.PASS if all(result.status == MetricStatus.PASS for result in scored) else MetricStatus.FAIL
            )
            return MetricResult(
                metric_id=metric_id,
                capability_id=template.capability_id,
                capability_type=template.capability_type,
                score=score,
                status=status,
                reason="Metric results were aggregated across the supplied window.",
                details=details,
                evidence=evidence,
                failures=failures,
                suggestions=suggestions,
            )
        if any(result.status == MetricStatus.OBSERVED for result in results):
            return MetricResult(
                metric_id=metric_id,
                capability_id=template.capability_id,
                capability_type=template.capability_type,
                score=None,
                status=MetricStatus.OBSERVED,
                reason="Raw observations were aggregated without a target or admission threshold.",
                details=details,
                evidence=evidence,
                failures=failures,
                suggestions=suggestions,
            )
        return MetricResult(
            metric_id=metric_id,
            capability_id=template.capability_id,
            capability_type=template.capability_type,
            score=None,
            status=MetricStatus.NOT_APPLICABLE,
            reason="No applicable metric result was available in the aggregation window.",
            details=details,
            evidence=evidence,
            failures=failures,
            suggestions=suggestions,
        )

    @staticmethod
    def _deduplicate(items: Iterable[Any]) -> tuple[Any, ...]:
        unique: dict[str, Any] = {}
        for item in items:
            key = item.model_dump_json() if hasattr(item, "model_dump_json") else repr(item)
            unique.setdefault(key, item)
        return tuple(unique.values())


def _extension_cache_signature(extension: Any) -> str | None:
    try:
        signature = getattr(extension, "cache_signature", None)
        value = signature() if callable(signature) else signature
    except Exception:  # noqa: BLE001 -- an optional extension key must only disable reuse.
        return None
    if not isinstance(value, str):
        return None
    return value.strip() or None


__all__ = ["EvaluationSuite", "EvaluationWindow"]
