# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public data contracts for capability evaluation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import AliasChoices, Field, JsonValue, model_validator

from openjiuwen.symphony.models._base import JsonObject, NonEmptyString, SymphonyModel


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


class EvaluationCase(SymphonyModel):
    """Caller-supplied static or runtime evidence for one capability."""

    case_id: NonEmptyString = Field(default_factory=lambda: uuid4().hex)
    capability_id: NonEmptyString
    capability_type: NonEmptyString
    query: str = ""
    inputs: JsonValue = None
    expected_output: JsonValue = None
    actual_output: JsonValue = None
    calls: tuple[CapabilityCall, ...] = ()
    success: bool | None = None
    event_time: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)


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
