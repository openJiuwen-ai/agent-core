"""Pydantic contracts for the persistent-state manager agent."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.ids import new_run_id
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.schemas import (
    CodeImplementationManifest,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import (
    EvaluationFeedback,
    ExperimentPlan,
    _reject_unsafe_relative_path,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import ExperimentResult
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reflection.schemas import Reflection

ControlSignal = Literal["EXECUTE", "DONE", "BLOCKED"]
TaskMode = Literal["create_new_paper", "modify_paper"]
RecordStatus = Literal["pending", "completed", "blocked"]
RecordKind = Literal["requirement", "artifact", "fact"]
ModuleId = Literal[
    "topic_survey",
    "experiment_design",
    "code_implementation",
    "experiment_execution",
    "reflection",
    "reporting",
]
ModuleMode = Literal["run", "create", "update", "revise_research"]
ReportOutcome = Literal["succeeded", "failed", "skipped"]
TerminalStatus = Literal["complete", "blocked", "failed", "cancelled", "incomplete"]
RuntimeFailureKind = Literal[
    "none",
    "validation",
    "provider",
    "timeout",
    "cancelled",
    "exception",
    "budget",
    "missing_capability",
]


def utc_now() -> datetime:
    return datetime.now(UTC)


def _strip_nonempty(value: str, *, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    return cleaned


def _safe_paths(paths: list[str]) -> list[str]:
    return [_reject_unsafe_relative_path(path) for path in paths]


class OriginalTask(BaseModel):
    """Immutable research request owned by the manager for one lineage."""

    topic: str = Field(min_length=1)
    objective: str = ""
    constraints: list[str] = Field(default_factory=list)
    task_mode: TaskMode = "create_new_paper"
    initial_prompt: str = ""
    initial_research_paths: list[str] = Field(default_factory=list)
    run_id: str = Field(default_factory=new_run_id)

    @field_validator("topic")
    @classmethod
    def _strip_topic(cls, value: str) -> str:
        return _strip_nonempty(value, field_name="topic")

    @field_validator("objective")
    @classmethod
    def _strip_objective(cls, value: str) -> str:
        return value.strip()

    @field_validator("initial_prompt")
    @classmethod
    def _strip_initial_prompt(cls, value: str) -> str:
        return value.strip()

    @field_validator("run_id")
    @classmethod
    def _strip_run_id(cls, value: str) -> str:
        return _strip_nonempty(value, field_name="run_id")

    @field_validator("initial_research_paths")
    @classmethod
    def _validate_paths(cls, value: list[str]) -> list[str]:
        return _safe_paths(value)

    @model_validator(mode="after")
    def _default_objective(self) -> OriginalTask:
        if not self.objective:
            self.objective = (
                f"Design, implement, execute, and reflect on one experiment for: {self.topic}"
            )
        return self


class RequirementRecord(BaseModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: RecordStatus = "pending"
    supporting_report_ids: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("id", "description")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        return _strip_nonempty(value, field_name="field")


class ArtifactRecord(BaseModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    path: str = ""
    status: RecordStatus = "pending"
    produced_by_report_id: str | None = None
    notes: str = ""

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        cleaned = value.strip().replace("\\", "/")
        if not cleaned:
            return ""
        return _reject_unsafe_relative_path(cleaned)


class FactRecord(BaseModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    status: RecordStatus = "pending"
    supporting_report_ids: list[str] = Field(default_factory=list)


class BudgetCounters(BaseModel):
    rounds_used: int = Field(default=0, ge=0)
    survey_calls: int = Field(default=0, ge=0)
    code_attempts: int = Field(default=0, ge=0)
    design_revisions: int = Field(default=0, ge=0)
    execution_attempts: int = Field(default=0, ge=0)
    decision_retries: int = Field(default=0, ge=0)
    reporting_attempts: int = Field(default=0, ge=0)


class BudgetLimits(BaseModel):
    max_rounds: int = Field(default=30, ge=1)
    max_survey_calls: int = Field(default=2, ge=0)
    max_code_retries: int = Field(default=4, ge=0)
    max_design_revisions: int = Field(default=5, ge=0)
    max_execution_retries: int = Field(default=3, ge=0)
    max_decision_retries: int = Field(default=2, ge=0)
    # Extra attempts after the first — same "bound a doomed loop" shape as
    # max_code_retries/max_execution_retries. Unlike those two, reporting had
    # no cap at all until a real run's completion_timeout bug caused it to
    # retry from a cold, fresh workspace every ~10 minutes with nothing to
    # stop it short of max_rounds.
    max_reporting_retries: int = Field(default=3, ge=0)
    max_history_chars: int = Field(default=16_000, ge=500)
    max_report_chars: int = Field(default=8_000, ge=200)
    excerpt_chars: int = Field(default=4_000, ge=80)


class SubtaskContract(BaseModel):
    """Bounded work assignment for one existing module. Host maps this to typed inputs."""

    module: ModuleId
    mode: ModuleMode = "run"
    goal: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    related_report_ids: list[str] = Field(default_factory=list)
    repair_instruction: str = ""
    followup_query: str = ""

    @field_validator("goal")
    @classmethod
    def _strip_goal(cls, value: str) -> str:
        return _strip_nonempty(value, field_name="goal")

    @model_validator(mode="after")
    def _mode_matches_module(self) -> SubtaskContract:
        allowed: dict[ModuleId, frozenset[ModuleMode]] = {
            "topic_survey": frozenset({"run"}),
            "experiment_design": frozenset({"create", "update", "revise_research"}),
            "code_implementation": frozenset({"run"}),
            "experiment_execution": frozenset({"run"}),
            "reflection": frozenset({"run"}),
            "reporting": frozenset({"run"}),
        }
        if self.mode not in allowed[self.module]:
            raise ValueError(
                f"mode {self.mode!r} is not valid for module {self.module!r}"
            )
        return self


class StateChange(BaseModel):
    record_kind: RecordKind
    record_id: str = Field(min_length=1)
    status: RecordStatus | None = None
    notes: str | None = None
    supporting_report_ids: list[str] = Field(default_factory=list)
    text: str | None = None

    @field_validator("record_id")
    @classmethod
    def _strip_id(cls, value: str) -> str:
        return _strip_nonempty(value, field_name="record_id")


class ManagerDecision(BaseModel):
    signal: ControlSignal
    rationale: str = Field(min_length=1)
    state_changes: list[StateChange] = Field(default_factory=list)
    contract: SubtaskContract | None = None
    blocked_reason: str | None = None

    @field_validator("rationale")
    @classmethod
    def _strip_rationale(cls, value: str) -> str:
        return _strip_nonempty(value, field_name="rationale")

    @model_validator(mode="after")
    def _signal_payload(self) -> ManagerDecision:
        if self.signal == "EXECUTE":
            if self.contract is None:
                raise ValueError("EXECUTE requires a SubtaskContract")
        else:
            if self.contract is not None:
                raise ValueError(f"{self.signal} must not include a contract")
        if self.signal == "BLOCKED" and not (self.blocked_reason or "").strip():
            self.blocked_reason = self.rationale
        return self


class SurveyHandoff(BaseModel):
    kind: Literal["topic_survey"] = "topic_survey"
    short_summary: str = ""
    key_findings: list[str] = Field(default_factory=list)
    open_problems: list[str] = Field(default_factory=list)
    coverage_assessment: str = ""
    # Not read by any current consumer; kept for a potential 2nd-round-onwards use.
    evidence_gaps: list[str] = Field(default_factory=list)
    suggested_followup_queries: list[str] = Field(default_factory=list)
    source_count: int = 0
    research_summary_path: str = ""
    source_paths: list[str] = Field(default_factory=list)


class DesignHandoff(BaseModel):
    kind: Literal["experiment_design"] = "experiment_design"
    objective: str = ""
    hypothesis: str = ""
    metrics: list[str] = Field(default_factory=list)
    evidence_sufficiency: str = ""
    evidence_gaps: list[str] = Field(default_factory=list)
    revision: int = 0
    status: str = ""
    design_path: str = ""
    code_agent_instruction_path: str = ""


class VariantHandoff(BaseModel):
    name: str
    passed: bool = False
    exit_code: int | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    log_path: str = ""
    excerpt: str = ""
    failure_kind: str = ""
    metrics_state: str = ""
    duration_ms: int | None = None
    process_status: str = ""
    diagnostic: dict[str, Any] = Field(default_factory=dict)
    diagnostics_path: str = ""


class CodeHandoff(BaseModel):
    kind: Literal["code_implementation"] = "code_implementation"
    status: str = "failed"
    readiness: Literal["smoke_ready", "failed"] = "failed"
    smoke_test_passed: bool = False
    smoke_failures: dict[str, str] = Field(default_factory=dict)
    variants: list[VariantHandoff] = Field(default_factory=list)
    notes: str = ""
    workspace_dir: str = ""
    log_paths: list[str] = Field(default_factory=list)
    failure_excerpts: list[str] = Field(default_factory=list)


class ExecutionHandoff(BaseModel):
    kind: Literal["experiment_execution"] = "experiment_execution"
    status: str = "failed"
    process_status: Literal["completed", "failed"] = "failed"
    scientific_status: Literal["accepted", "below_threshold", "unknown"] = "unknown"
    failure_kind: str = ""
    variants: list[VariantHandoff] = Field(default_factory=list)
    notes: str = ""
    result_paths: list[str] = Field(default_factory=list)
    failure_excerpts: list[str] = Field(default_factory=list)
    diagnostic: dict[str, Any] = Field(default_factory=dict)
    failure_stage: str = ""
    failure_substage: str = ""
    failure_class: str = ""
    fingerprint: str = ""
    diagnostic_paths: list[str] = Field(default_factory=list)


class ReflectionHandoff(BaseModel):
    kind: Literal["reflection"] = "reflection"
    verdict: Literal["supported", "refuted", "mixed", "inconclusive"] = "inconclusive"
    summary: str = ""
    reflection_path: str = ""


class ReportHandoff(BaseModel):
    kind: Literal["reporting"] = "reporting"
    report_path: str = ""


Handoff = Annotated[
    SurveyHandoff
    | DesignHandoff
    | CodeHandoff
    | ExecutionHandoff
    | ReflectionHandoff
    | ReportHandoff,
    Field(discriminator="kind"),
]


class SubagentReport(BaseModel):
    report_id: str = Field(min_length=1)
    module: ModuleId
    mode: ModuleMode
    round_index: int = Field(ge=1)
    attempt: int = Field(default=1, ge=1)
    outcome: ReportOutcome
    retryable: bool = False
    summary: str = Field(min_length=1)
    artifact_paths: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    runtime_failure: RuntimeFailureKind = "none"
    related_report_ids: list[str] = Field(default_factory=list)
    handoff: Handoff | None = None

    @field_validator("artifact_paths")
    @classmethod
    def _validate_artifact_paths(cls, value: list[str]) -> list[str]:
        return _safe_paths(value)


class OperatorFollowup(BaseModel):
    """Host-injected operator instruction after a pause or resume."""

    text: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    source: Literal["cli", "file"] = "cli"

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return _strip_nonempty(value, field_name="text")


class TaskState(BaseModel):
    run_id: str
    phase: str = "start"
    requirements: list[RequirementRecord] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    facts: list[FactRecord] = Field(default_factory=list)
    research_paths: list[str] = Field(default_factory=list)
    counters: BudgetCounters = Field(default_factory=BudgetCounters)
    limits: BudgetLimits = Field(default_factory=BudgetLimits)
    last_contract: SubtaskContract | None = None
    pending_contract: SubtaskContract | None = None
    design_session_epoch: int = Field(default=0, ge=0)
    unresolved_issues: list[str] = Field(default_factory=list)
    latest_plan: ExperimentPlan | None = None
    latest_implementation_status: str | None = None
    latest_execution_status: str | None = None
    latest_evaluation: EvaluationFeedback | None = None
    pending_research_revision: bool = False
    enabled_modules: list[ModuleId] = Field(
        default_factory=lambda: [
            "topic_survey",
            "experiment_design",
            "code_implementation",
            "experiment_execution",
        ]
    )
    missing_capabilities: list[str] = Field(default_factory=list)

    @field_validator("research_paths")
    @classmethod
    def _validate_research_paths(cls, value: list[str]) -> list[str]:
        return _safe_paths(value)


class ManagedRound(BaseModel):
    round_index: int = Field(ge=1)
    decision: ManagerDecision
    contract: SubtaskContract | None = None
    related_report_ids: list[str] = Field(default_factory=list)
    report: SubagentReport | None = None
    validation_feedback: str = ""
    started_at: datetime
    finished_at: datetime | None = None


class TerminalReport(BaseModel):
    status: TerminalStatus
    run_id: str
    rounds_run: int = 0
    abort_reason: str = ""
    failure_reason: str = ""
    summary: str = ""
    completion_satisfied: bool = False


class PersistedManagerState(BaseModel):
    schema_version: int = 1
    original_task: OriginalTask
    task_state: TaskState
    reports: list[SubagentReport] = Field(default_factory=list)
    rounds: list[ManagedRound] = Field(default_factory=list)
    terminal: TerminalReport | None = None
    pending_decision_feedback: str = ""
    operator_followups: list[OperatorFollowup] = Field(default_factory=list)
    latest_implementation: CodeImplementationManifest | None = None
    latest_execution: ExperimentResult | None = None
    latest_reflection: Reflection | None = None


class RoutingHint(BaseModel):
    """Host-computed routing view placed first in the manager prompt JSON."""

    remaining_rounds: int = 0
    remaining_code_retries: int = 0
    remaining_execution_retries: int = 0
    remaining_survey_calls: int = 0
    remaining_design_revisions: int = 0
    remaining_reporting_retries: int = 0
    known_record_ids: list[str] = Field(default_factory=list)
    legal_actions: list[dict[str, str]] = Field(default_factory=list)
    can_complete: bool = False
    can_complete_reason: str = ""
    latest_metrics: dict[str, Any] = Field(default_factory=dict)
    latest_process_status: str = ""
    latest_scientific_status: str = ""
    latest_failure_kind: str = ""
    latest_failure_stage: str = ""
    latest_failure_substage: str = ""
    latest_failure_class: str = ""
    latest_failure_fingerprint: str = ""
    diagnostic_paths: list[str] = Field(default_factory=list)
    variant_metrics: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ManagerSnapshot(BaseModel):
    """Compact, reconstructed manager prompt payload. No raw trajectories."""

    original_task: OriginalTask
    task_state: TaskState
    reports: list[SubagentReport] = Field(default_factory=list)
    round_index: int = Field(ge=1)
    validation_feedback: str = ""
    routing: RoutingHint | None = None
    operator_followups: list[OperatorFollowup] = Field(default_factory=list)


def default_requirements(topic: str) -> list[RequirementRecord]:
    return [
        RequirementRecord(
            id="req-research",
            description=f"Obtain sufficient research evidence for: {topic}",
        ),
        RequirementRecord(
            id="req-design",
            description="Produce an executable experiment design grounded in the research",
        ),
        RequirementRecord(
            id="req-implement",
            description="Implement and smoke-test the designed experiment",
        ),
        RequirementRecord(
            id="req-execute",
            description="Run the experiment and collect per-variant metrics",
        ),
    ]


def report_requirement() -> RequirementRecord:
    """Separate from default_requirements(): this one is conditional on
    "reporting" actually being enabled (see build_initial_state) — unlike
    the requirements above, it would otherwise be permanently unsatisfiable
    whenever reporting is disabled, since default_requirements() has no way
    to know which modules are enabled.
    """
    return RequirementRecord(
        id="req-report",
        description="Produce the final report from the completed run",
    )


def limits_from_config(raw: dict[str, Any] | None) -> BudgetLimits:
    data = dict(raw or {})
    known = set(BudgetLimits.model_fields)
    return BudgetLimits(**{key: value for key, value in data.items() if key in known})
