# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evaluator protocols and shared fail-soft result construction."""

from __future__ import annotations

import hashlib
import inspect
import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable

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
    SuggestionPriority,
)
from openjiuwen.symphony.models._redaction import is_sensitive_name, redact_sensitive_text

EvaluationScope = Literal["static", "trace"]

_REFERENCE_ID_KEYS = frozenset({"call_id", "case_id"})
_DEFAULT_TEXT_LIMIT = 512


EvaluationLLM: TypeAlias = SymphonyLLM


@dataclass(frozen=True)
class EvaluationContext:
    """Inputs available to one evaluator invocation."""

    fingerprint: CapabilityFingerprint
    case: EvaluationCase | None = None
    llm: SymphonyLLM | Any | None = None
    llm_enabled: bool = False
    evidence_text_limit: int = _DEFAULT_TEXT_LIMIT

    @property
    def capability_id(self) -> str:
        return self.fingerprint.capability_id

    @property
    def capability_type(self) -> str:
        return self.fingerprint.capability_type

    @property
    def matching_calls(self) -> tuple[Any, ...]:
        """Return calls that explicitly reference the evaluated fingerprint."""

        if self.case is None:
            return ()
        identity = (self.fingerprint.capability_id, self.fingerprint.capability_type)
        return tuple(call for call in self.case.calls if (call.capability_id, call.capability_type) == identity)

    def payload(self) -> dict[str, Any]:
        """Build a redacted JSON-compatible payload for semantic evaluation."""

        payload: dict[str, Any] = {"fingerprint": _object_payload(self.fingerprint)}
        if self.case is not None:
            payload["case"] = _object_payload(self.case)
        return _sanitize_payload(payload, self.evidence_text_limit)


def redacted_evidence_reference(namespace: str, identifier: str) -> str:
    """Create a stable evidence handle without retaining a caller-owned ID."""

    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:24]
    return f"{namespace}:sha256:{digest}"


def sanitize_metric_result(result: MetricResult, max_length: int) -> MetricResult:
    """Apply the public evidence boundary to a built-in or extension result."""

    def sanitize_evidence(item: EvidenceRef) -> EvidenceRef:
        return item.model_copy(
            update={
                "reference": _sanitize_text(item.reference, max_length),
                "description": _sanitize_text(item.description, max_length),
                "metadata": _sanitize_payload(item.metadata, max_length),
            }
        )

    failures = tuple(
        item.model_copy(
            update={
                "message": _sanitize_text(item.message, max_length),
                "details": _sanitize_payload(item.details, max_length),
                "evidence": tuple(sanitize_evidence(evidence) for evidence in item.evidence),
            }
        )
        for item in result.failures
    )
    suggestions = tuple(
        item.model_copy(
            update={
                "message": _sanitize_text(item.message, max_length),
                "metadata": _sanitize_payload(item.metadata, max_length),
            }
        )
        for item in result.suggestions
    )
    return result.model_copy(
        update={
            "reason": _sanitize_text(result.reason, max_length),
            "details": _sanitize_payload(result.details, max_length),
            "evidence": tuple(sanitize_evidence(item) for item in result.evidence),
            "failures": failures,
            "suggestions": suggestions,
        }
    )


@runtime_checkable
class Evaluator(Protocol):
    """Structural protocol implemented by built-in and caller evaluators."""

    @property
    def metric_id(self) -> str:
        """Stable metric identifier used for registration and result validation."""

    @property
    def scope(self) -> EvaluationScope:
        """Whether the evaluator consumes static metadata or trace evidence."""

    @property
    def requires_llm(self) -> bool:
        """Whether the evaluator requires the explicitly enabled LLM path."""

    def evaluate(self, context: EvaluationContext) -> MetricResult | Awaitable[MetricResult]:
        """Evaluate a fingerprint or one supplied trace case."""


class BaseEvaluator(ABC):
    """Shared result helpers for deterministic and semantic metrics."""

    metric_id: str
    scope: EvaluationScope
    requires_llm = False

    @abstractmethod
    def evaluate(self, context: EvaluationContext) -> MetricResult | Awaitable[MetricResult]:
        """Evaluate one context without executing the underlying capability."""

    def result(
        self,
        context: EvaluationContext,
        *,
        score: float | None,
        status: MetricStatus | str,
        reason: str,
        details: Mapping[str, Any] | None = None,
        evidence: tuple[EvidenceRef, ...] = (),
        failures: tuple[FailureReason, ...] = (),
        suggestions: tuple[ImprovementSuggestion, ...] = (),
    ) -> MetricResult:
        return MetricResult(
            metric_id=self.metric_id,
            capability_id=context.capability_id,
            capability_type=context.capability_type,
            score=score,
            status=MetricStatus(status),
            reason=_sanitize_text(reason, context.evidence_text_limit),
            details=_sanitize_payload(dict(details or {}), context.evidence_text_limit),
            evidence=evidence,
            failures=failures,
            suggestions=suggestions,
        )

    def not_applicable(
        self,
        context: EvaluationContext,
        reason: str,
        *,
        code: str = "missing_evaluation_input",
    ) -> MetricResult:
        sanitized_reason = _sanitize_text(reason, context.evidence_text_limit)
        return self.result(
            context,
            score=None,
            status="not_applicable",
            reason=sanitized_reason,
            details={"not_applicable_code": code},
        )

    def error(
        self,
        context: EvaluationContext,
        reason: str,
        *,
        code: str = "evaluation_failed",
        evidence_reference: str = "evaluator",
    ) -> MetricResult:
        sanitized_reason = _sanitize_text(reason, context.evidence_text_limit)
        evidence = EvidenceRef(
            evidence_type="evaluation_error",
            reference=_sanitize_text(evidence_reference, context.evidence_text_limit),
            description="The evaluator failed without exposing its input payload.",
        )
        return self.result(
            context,
            score=None,
            status="error",
            reason=sanitized_reason,
            evidence=(evidence,),
            failures=(
                FailureReason(
                    code=code,
                    message=sanitized_reason,
                    severity=FailureSeverity.ERROR,
                    evidence=(evidence,),
                ),
            ),
            suggestions=(
                ImprovementSuggestion(
                    code="retry_evaluation",
                    message="Verify the evaluator dependency and retry this metric.",
                    priority=SuggestionPriority.MEDIUM,
                    related_failures=(code,),
                ),
            ),
        )


class LLMJudgeEvaluator(BaseEvaluator):
    """Base class for semantic metrics whose model calls are opt-in."""

    requires_llm = True
    rubric: str

    def evaluate(self, context: EvaluationContext) -> Awaitable[MetricResult]:
        """Return the asynchronous model judgment through the evaluator protocol."""

        return self._evaluate(context)

    async def _evaluate(self, context: EvaluationContext) -> MetricResult:
        unavailable = self.validate_context(context)  # pylint: disable=assignment-from-none
        if unavailable is not None:
            return unavailable
        if not context.llm_enabled or context.llm is None:
            return self.not_applicable(
                context,
                "Semantic evaluation is disabled or no evaluation LLM is configured.",
                code="evaluation_llm_unavailable",
            )
        try:
            response = await _invoke_llm(
                context.llm,
                self.metric_id,
                self.rubric,
                self.evaluation_payload(context),
            )
            payload = _parse_llm_response(response)
            score = payload.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise TypeError("the response score is not numeric")
            numeric_score = float(score)
            if numeric_score not in {0.0, 1.0}:
                raise ValueError("the response score is not binary")
            reason = payload.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise TypeError("the response reason is missing")
        except Exception as exc:  # noqa: BLE001 - injected model adapters are an external boundary.
            return self.error(
                context,
                f"Semantic evaluation failed ({type(exc).__name__}).",
                code="evaluation_llm_failed",
                evidence_reference=f"llm:{self.metric_id}",
            )
        return self.result(
            context,
            score=numeric_score,
            status="pass" if numeric_score == 1.0 else "fail",
            reason=reason,
            details={"evaluation_method": "llm"},
            evidence=(
                EvidenceRef(
                    evidence_type="llm_judgment",
                    reference=f"metric:{self.metric_id}",
                    description="Score produced from the supplied fingerprint and trace data.",
                ),
            ),
        )

    def validate_context(self, context: EvaluationContext) -> MetricResult | None:
        """Return a deterministic early result, or None when the model should judge."""

        return None

    def evaluation_payload(self, context: EvaluationContext) -> Mapping[str, Any]:
        """Return the redacted metric-specific payload supplied to the judge."""

        return context.payload()


def _object_payload(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return value


def _sanitize_text(value: Any, max_length: int = _DEFAULT_TEXT_LIMIT) -> str:
    text = redact_sensitive_text(str(value))
    if len(text) > max_length:
        return f"{text[:max_length]}..."
    return text


def _sanitize_payload(value: Any, max_length: int = _DEFAULT_TEXT_LIMIT) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if is_sensitive_name(text_key):
                sanitized[text_key] = "<redacted>"
            elif text_key in _REFERENCE_ID_KEYS and isinstance(item, str):
                sanitized[text_key] = redacted_evidence_reference(text_key.removesuffix("_id"), item)
            else:
                sanitized[text_key] = _sanitize_payload(item, max_length)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item, max_length) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value, max_length)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_text(value, max_length)


async def _invoke_llm(llm: Any, metric_id: str, rubric: str, payload: Mapping[str, Any]) -> Any:
    prompt = (
        f"{rubric}\n"
        "Return only JSON with score 0 or 1 and a concise reason.\n"
        f"Evaluation data:\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    )
    invoke = getattr(llm, "invoke", None)
    if callable(invoke):
        response = invoke([{"role": "user", "content": prompt}], temperature=0.0)
    else:
        evaluate = getattr(llm, "evaluate", None)
        if callable(evaluate):
            response = evaluate(metric_id, payload)
        else:
            chat = getattr(llm, "chat", None)
            generate = getattr(llm, "generate", None)
            if callable(chat):
                response = chat([{"role": "user", "content": prompt}])
            elif callable(generate):
                response = generate(prompt)
            elif callable(llm):
                response = llm(metric_id, payload)
            else:
                raise TypeError("evaluation LLM has no supported invocation method")
    if inspect.isawaitable(response):
        response = await response
    return response


def _parse_llm_response(response: Any) -> Mapping[str, Any]:
    parser_content = getattr(response, "parser_content", None)
    if isinstance(parser_content, Mapping):
        return parser_content
    parser_dump = getattr(parser_content, "model_dump", None)
    if callable(parser_dump):
        parsed = parser_dump(mode="json")
        if isinstance(parsed, Mapping):
            return parsed
    if parser_content is not None:
        response = parser_content
    elif not isinstance(response, (str, list, tuple, Mapping)):
        response = getattr(response, "content", response)
    if isinstance(response, (list, tuple)):
        if not response:
            raise ValueError("the model returned no response")
        if len(response) == 1 and isinstance(response[0], Mapping):
            response = response[0]
        else:
            parts = []
            for item in response:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(getattr(item, "text", None), str):
                    parts.append(item.text)
            response = "".join(parts)
    if isinstance(response, str):
        response = json.loads(response)
    if not isinstance(response, Mapping):
        raise TypeError("the model response is not a mapping")
    return response


__all__ = [
    "BaseEvaluator",
    "EvaluationContext",
    "EvaluationLLM",
    "EvaluationScope",
    "Evaluator",
    "LLMJudgeEvaluator",
    "redacted_evidence_reference",
]
