"""Pydantic contracts for the Experiment Design Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.ids import new_run_id

DesignStatus = Literal["draft", "continue", "accepted", "stopped"]
FeedbackVerdict = Literal["continue", "accept", "stop"]
RecoveryMode = Literal["live", "checkpoint", "artifacts"]


def design_session_id(run_id: str, epoch: int = 0) -> str:
    """OpenJiuwen conversation ID for one experiment-design lineage.

    ``epoch=0`` is the stable lineage id stored in design frontmatter.
    A later epoch is a fresh conversation after the operator interrupted an
    in-flight design round (so the next call does not continue that turn).
    """
    if int(epoch) > 0:
        return f"experiment-design:{run_id}:e{int(epoch)}"
    return f"experiment-design:{run_id}"


def design_tool_owner_id(run_id: str) -> str:
    """Per-run tool owner so concurrent runs do not collide."""
    return f"experiment-design-tools:{run_id}"


def _reject_unsafe_relative_path(path: str) -> str:
    value = path.strip()
    if not value:
        raise ValueError("path must be non-empty")
    if value.startswith("/") or (len(value) >= 2 and value[1] == ":"):
        raise ValueError(f"absolute paths are not allowed: {path!r}")
    parts = value.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise ValueError(f"path traversal is not allowed: {path!r}")
    return value.replace("\\", "/")


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("evaluated_at must be timezone-aware UTC datetime")
    return value.astimezone(UTC)


class ResearchBrief(BaseModel):
    """Create-mode research input: files and/or directories under the project root.

    Directories are expanded by the host into concrete files before the agent
    runs. There is no fixed folder layout — any project-relative tree works.
    """

    resource_paths: list[str] = Field(min_length=1)

    @field_validator("resource_paths")
    @classmethod
    def _validate_resource_paths(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("resource_paths must be non-empty")
        return [_reject_unsafe_relative_path(path) for path in value]


class ExperimentDesignInput(BaseModel):
    research: ResearchBrief
    run_id: str = Field(default_factory=new_run_id)
    branch: str | None = None
    session_epoch: int = Field(default=0, ge=0)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("run_id must be non-empty")
        return cleaned

    @field_validator("branch")
    @classmethod
    def _validate_branch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class EvaluationFeedback(BaseModel):
    verdict: FeedbackVerdict
    summary: str = Field(min_length=1)
    observed_metrics: dict[str, float | int | str] = Field(default_factory=dict)
    result_paths: list[str] = Field(min_length=1)
    branch: str = Field(min_length=1)
    code_session_id: str = Field(min_length=1)
    evaluator_session_id: str = Field(min_length=1)
    evaluated_at: datetime

    @field_validator("summary", "branch", "code_session_id", "evaluator_session_id")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("field must be non-empty")
        return cleaned

    @field_validator("result_paths")
    @classmethod
    def _validate_result_paths(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("result_paths must be non-empty")
        return [_reject_unsafe_relative_path(path) for path in value]

    @field_validator("evaluated_at")
    @classmethod
    def _validate_evaluated_at(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class ExperimentDesignFeedbackInput(BaseModel):
    run_id: str = Field(min_length=1)
    feedback: EvaluationFeedback
    allow_after_terminal: bool = False
    session_epoch: int = Field(default=0, ge=0)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("run_id must be non-empty")
        return cleaned


class ExperimentDesignResearchRevisionInput(BaseModel):
    """Incorporate supplemental survey evidence into an existing design lineage."""

    run_id: str = Field(min_length=1)
    additional_research_paths: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    session_epoch: int = Field(default=0, ge=0)

    @field_validator("run_id", "reason")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("field must be non-empty")
        return cleaned

    @field_validator("additional_research_paths")
    @classmethod
    def _validate_paths(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("additional_research_paths must be non-empty")
        return [_reject_unsafe_relative_path(path) for path in value]


class MetricDefinition(BaseModel):
    """One falsifiable decision metric.

    ``spec`` is a single sentence covering measurement, direction, and threshold.
    """

    name: str = Field(min_length=1)
    spec: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        # Mirrors metric_claim_slot's own check exactly. Rejecting here means
        # a bad metric name fails inside SubmitExperimentDesignTool.invoke()
        # (the model's draft never validates), instead of passing the tool
        # call — which commits the checkpoint session on return — and only
        # crashing later in write_initial_design_artifacts, which leaves a
        # stale committed session with no design file ever written (see
        # docs/manager_agent_design.md incident: a run_id gets permanently
        # stuck — create() refuses on stale prior context, update() has no
        # file to update from).
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("metric name must be non-empty")
        if any(ch.isspace() for ch in cleaned):
            raise ValueError(f"metric name must not contain whitespace: {value!r}")
        return cleaned

    @field_validator("spec")
    @classmethod
    def _strip_spec(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("field must be non-empty")
        return cleaned


class CodeAgentInstruction(BaseModel):
    goal: str = Field(min_length=1)
    read_first: list[str] = Field(min_length=1)
    scope: list[str] = Field(min_length=1)
    required_work: list[str] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    validation: list[str] = Field(min_length=1)
    expected_outputs: list[str] = Field(min_length=1)
    completion_report: list[str] = Field(min_length=1)


ClaimOutcome = Literal["good", "mixed", "bad"]
CORE_CLAIM_SLOTS = frozenset({"objective", "hypothesis"})
SECTION_UPDATE_FIELDS = (
    "grounding",
    "experiment",
)


def metric_claim_slot(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("metric name must be non-empty")
    if any(ch.isspace() for ch in cleaned):
        raise ValueError(f"metric name must not contain whitespace: {name!r}")
    return f"metric:{cleaned}"


def normalize_claim_slot(slot: str) -> str:
    cleaned = slot.strip()
    if cleaned in CORE_CLAIM_SLOTS:
        return cleaned
    if cleaned.startswith("metric:"):
        return metric_claim_slot(cleaned.split(":", 1)[1])
    raise ValueError(
        f"unknown claim slot {slot!r}; expected one of "
        f"{sorted(CORE_CLAIM_SLOTS)} or 'metric:<name>'"
    )


def is_metric_slot(slot: str) -> bool:
    return normalize_claim_slot(slot).startswith("metric:")


def metric_name_from_slot(slot: str) -> str:
    normalized = normalize_claim_slot(slot)
    if not normalized.startswith("metric:"):
        raise ValueError(f"not a metric slot: {slot!r}")
    return normalized.split(":", 1)[1]


def format_metric_claim_text(metric: MetricDefinition) -> str:
    return metric.spec.strip()


class ClosedClaim(BaseModel):
    """Close a living-summary claim with an experiment outcome."""

    slot: str
    index: int | None = None
    outcome: ClaimOutcome | None = None
    note: str = ""

    @field_validator("slot")
    @classmethod
    def _normalize_slot(cls, value: str) -> str:
        return normalize_claim_slot(value)


class NewClaim(BaseModel):
    """Append a new current claim for a slot that changed."""

    slot: str
    text: str = Field(min_length=1)

    @field_validator("slot")
    @classmethod
    def _normalize_slot(cls, value: str) -> str:
        return normalize_claim_slot(value)

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text must be non-empty")
        return cleaned


class SectionUpdates(BaseModel):
    """Partial section patches. Omitted / null fields leave markdown unchanged.

    Metrics are versioned claims under ``## Decision Metrics``, not section patches.
    Grounding is append-only (new bullets are merged in).
    """

    grounding: list[str] | None = None
    experiment: str | None = None


class ExperimentDesignDraft(BaseModel):
    """Structured payload submitted by the model; host owns provenance.

    Create mode uses the flat fields as the initial living summary.
    Update mode may also send ``closed_claims`` / ``new_claims`` /
    ``section_updates``; omitted section updates mean leave markdown as-is,
    and the host skips identical section bodies even when flat fields are resent.
    """

    objective: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    metrics: list[MetricDefinition] = Field(min_length=1)
    experiment: str = Field(min_length=1)
    grounding: list[str] = Field(min_length=1)
    code_agent_instruction: CodeAgentInstruction
    closed_claims: list[ClosedClaim] = Field(default_factory=list)
    new_claims: list[NewClaim] = Field(default_factory=list)
    section_updates: SectionUpdates | None = None

    @model_validator(mode="after")
    def _reject_empty_lists(self) -> ExperimentDesignDraft:
        for field_name in ("grounding", "metrics"):
            value = getattr(self, field_name)
            if not value:
                raise ValueError(f"{field_name} must be non-empty")
        if not self.experiment.strip():
            raise ValueError("experiment must be non-empty")
        return self


def outcome_from_verdict(verdict: FeedbackVerdict) -> ClaimOutcome:
    if verdict == "accept":
        return "good"
    if verdict == "stop":
        return "bad"
    return "mixed"


class ExperimentPlan(BaseModel):
    """Artifact reference threaded through execution/reporting."""

    run_id: str
    revision: int = 0
    status: DesignStatus = "draft"
    design_session_id: str
    design_path: str
    code_agent_instruction_path: str
    branch: str | None = None
    created_at: datetime
    updated_at: datetime
    recovery_mode: RecoveryMode = "checkpoint"

    # Legacy-compatible summary fields used by placeholders / downstream stubs.
    # Prefer reading design_path / code_agent_instruction_path on disk.
    setup: str = ""
    variables: list[str] = Field(default_factory=list)
    baselines: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    expected_outcomes: str = ""

    @field_validator("created_at", "updated_at")
    @classmethod
    def _require_aware(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class ExperimentDesignOutput(BaseModel):
    plan: ExperimentPlan


def status_from_verdict(verdict: FeedbackVerdict) -> DesignStatus:
    mapping: dict[FeedbackVerdict, DesignStatus] = {
        "continue": "continue",
        "accept": "accepted",
        "stop": "stopped",
    }
    return mapping[verdict]


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_utc_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _require_aware_utc(value)
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
