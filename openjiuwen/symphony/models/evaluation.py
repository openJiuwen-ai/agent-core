# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public data contracts for capability evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import AliasChoices, Field, JsonValue, field_validator, model_validator

from openjiuwen.symphony.models._base import JsonObject, NonEmptyString, SymphonyModel


def _is_openai_content(value: JsonValue) -> bool:
    return isinstance(value, str) or (
        isinstance(value, list) and bool(value) and all(isinstance(part, dict) for part in value)
    )


class MetricStatus(str, Enum):
    """Outcome of a metric evaluation."""

    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"
    OBSERVED = "observed"


class QualityConfidence(str, Enum):
    """Confidence assigned to an aggregated quality result."""

    NONE = "none"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class FailureSeverity(str, Enum):
    """Operational impact of an evaluation failure."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class SuggestionPriority(str, Enum):
    """Relative priority of an improvement suggestion."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceRef(SymphonyModel):
    """Reference to reproducible evidence without embedding sensitive source data."""

    evidence_type: NonEmptyString = Field(
        validation_alias=AliasChoices("evidence_type", "type", "kind"),
    )
    reference: NonEmptyString = Field(
        validation_alias=AliasChoices("reference", "ref", "uri"),
    )
    description: str = ""
    metadata: JsonObject = Field(default_factory=dict)


class FailureReason(SymphonyModel):
    """Structured explanation of a quality or execution failure."""

    code: NonEmptyString
    message: NonEmptyString
    severity: FailureSeverity = FailureSeverity.ERROR
    evidence: tuple[EvidenceRef, ...] = ()
    details: JsonObject = Field(default_factory=dict)


class ImprovementSuggestion(SymphonyModel):
    """Actionable follow-up linked to one or more failure codes."""

    code: NonEmptyString
    message: NonEmptyString
    priority: SuggestionPriority = SuggestionPriority.MEDIUM
    related_failures: tuple[NonEmptyString, ...] = ()
    metadata: JsonObject = Field(default_factory=dict)


class CapabilityCall(SymphonyModel):
    """One capability invocation observed in an evaluation trace."""

    call_id: NonEmptyString = Field(default_factory=lambda: uuid4().hex)
    capability_id: NonEmptyString
    capability_type: NonEmptyString
    inputs: JsonValue = None
    output: JsonValue = None
    success: bool | None = None
    error: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    failures: tuple[FailureReason, ...] = ()
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_timing(self) -> CapabilityCall:
        if self.started_at is not None and self.ended_at is not None:
            started_aware = self.started_at.tzinfo is not None and self.started_at.utcoffset() is not None
            ended_aware = self.ended_at.tzinfo is not None and self.ended_at.utcoffset() is not None
            if started_aware != ended_aware:
                raise ValueError("started_at and ended_at timezone awareness must match")
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must not be earlier than started_at")
        return self


class Latency(SymphonyModel):
    """Observed response latency in milliseconds."""

    ttft: float | None = Field(default=None, ge=0)
    e2e: float | None = Field(default=None, ge=0)


class EvaluationCase(SymphonyModel):
    """Caller-supplied static or runtime evidence for one capability."""

    case_id: NonEmptyString = Field(default_factory=lambda: uuid4().hex)
    capability_id: NonEmptyString
    capability_type: NonEmptyString
    query: str = ""
    inputs: JsonValue = None
    expected_output: JsonValue = None
    message: tuple[JsonObject, ...] = ()
    output: JsonValue = None
    success: bool | None = None
    latency: Latency | None = None
    event_time: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_trace_fields(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        legacy_fields = tuple(field for field in ("actual_output", "calls", "metadata") if field in value)
        if legacy_fields:
            joined = ", ".join(legacy_fields)
            raise ValueError(f"legacy EvaluationCase fields are not supported: {joined}")
        return value

    @field_validator("message")
    @classmethod
    def _validate_openai_message(cls, value: tuple[JsonObject, ...]) -> tuple[JsonObject, ...]:
        valid_roles = {"system", "developer", "user", "assistant", "tool"}
        known_tool_call_ids: set[str] = set()
        resolved_tool_call_ids: set[str] = set()

        for index, item in enumerate(value):
            role = item.get("role")
            if not isinstance(role, str) or role not in valid_roles:
                raise ValueError(f"message[{index}].role must be a standard OpenAI role")

            content = item.get("content")
            if role in {"system", "developer", "user", "tool"} and not _is_openai_content(content):
                raise ValueError(
                    f"message[{index}].content for role {role} must be a string "
                    "or a non-empty list of content-part objects"
                )

            if role == "assistant":
                if content is not None and not _is_openai_content(content):
                    raise ValueError(
                        f"message[{index}].content for role assistant must be null, a string, "
                        "or a non-empty list of content-part objects"
                    )
                raw_tool_calls = item.get("tool_calls")
                has_content = content is not None
                if raw_tool_calls is None:
                    if not has_content:
                        raise ValueError(f"message[{index}] assistant must include content or tool_calls")
                    continue
                if not isinstance(raw_tool_calls, list):
                    raise ValueError(f"message[{index}].tool_calls must be a list")
                if not raw_tool_calls:
                    if not has_content:
                        raise ValueError(f"message[{index}] assistant must include content or tool_calls")
                    continue
                for call_index, raw_call in enumerate(raw_tool_calls):
                    path = f"message[{index}].tool_calls[{call_index}]"
                    if not isinstance(raw_call, dict):
                        raise ValueError(f"{path} must be an object")
                    tool_call_id = raw_call.get("id")
                    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                        raise ValueError(f"{path}.id must be a non-empty string")
                    if tool_call_id in known_tool_call_ids:
                        raise ValueError(f"duplicate tool call id: {tool_call_id}")
                    if raw_call.get("type") != "function":
                        raise ValueError(f"{path}.type must be 'function'")
                    function = raw_call.get("function")
                    if not isinstance(function, dict):
                        raise ValueError(f"{path}.function must be an object")
                    name = function.get("name")
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError(f"{path}.function.name must be a non-empty string")
                    if not isinstance(function.get("arguments"), str):
                        raise ValueError(f"{path}.function.arguments must be a string")
                    known_tool_call_ids.add(tool_call_id)
                continue

            if role == "tool":
                tool_call_id = item.get("tool_call_id")
                if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                    raise ValueError(f"message[{index}].tool_call_id must be a non-empty string")
                if tool_call_id not in known_tool_call_ids:
                    raise ValueError(f"message[{index}].tool_call_id must reference a preceding assistant tool call")
                if tool_call_id in resolved_tool_call_ids:
                    raise ValueError(f"duplicate tool response for tool call id: {tool_call_id}")
                resolved_tool_call_ids.add(tool_call_id)

        return value


class MetricResult(SymphonyModel):
    """Result from one independently interpretable quality metric."""

    metric_id: NonEmptyString = Field(validation_alias=AliasChoices("metric_id", "metric"))
    capability_id: NonEmptyString
    capability_type: NonEmptyString
    score: float | None = Field(default=None, ge=0, le=1)
    status: MetricStatus = MetricStatus.NOT_APPLICABLE
    reason: str = ""
    details: JsonObject = Field(default_factory=dict)
    evidence: tuple[EvidenceRef, ...] = ()
    failures: tuple[FailureReason, ...] = ()
    suggestions: tuple[ImprovementSuggestion, ...] = ()
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _validate_score_state(self) -> MetricResult:
        scoreless_statuses = {
            MetricStatus.NOT_APPLICABLE,
            MetricStatus.ERROR,
            MetricStatus.OBSERVED,
        }
        if self.status in scoreless_statuses and self.score is not None:
            raise ValueError("not_applicable, error, and observed metric results must not include a score")
        return self

    @property
    def metric(self) -> str:
        """Compatibility accessor for callers that use the shorter metric name."""

        return self.metric_id


class QualityResult(SymphonyModel):
    """Aggregated quality result for one capability and evidence window."""

    capability_id: NonEmptyString
    capability_type: NonEmptyString
    score: float | None = Field(default=None, ge=0, le=1)
    metrics: tuple[MetricResult, ...] = ()
    confidence: QualityConfidence = QualityConfidence.NONE
    sample_count: int = Field(default=0, ge=0)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_metric_identity(self) -> QualityResult:
        mismatched = [
            result.metric_id
            for result in self.metrics
            if (result.capability_id, result.capability_type) != (self.capability_id, self.capability_type)
        ]
        if mismatched:
            joined = ", ".join(mismatched)
            raise ValueError(f"metric identity does not match quality result: {joined}")
        return self
