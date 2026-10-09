"""Pydantic contracts for the Reflection module."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.schemas import (
    CodeImplementationManifest,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import ExperimentPlan
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import ExperimentResult

HypothesisVerdict = Literal["supported", "partially_supported", "contradicted", "inconclusive"]
ValidityStatus = Literal["valid", "suspect", "invalid_run"]
ObjectiveProgress = Literal["advanced", "neutral", "regressed", "unclear"]
ConfidenceLevel = Literal["high", "medium", "low"]
Recommendation = Literal[
    "iterate_design",
    "repair_code",
    "rerun_execution",
    "gather_more_evidence",
    "accept_and_report",
]


class EvidenceItem(BaseModel):
    """One cited metric comparison. Values are variant name -> number."""

    metric: str = Field(min_length=1)
    values: dict[str, float] = Field(min_length=1)
    comparison: str = Field(min_length=1)
    what_it_shows: str = Field(min_length=1)


class ReflectionJudgment(BaseModel):
    """Structured judgment submitted by the reflection agent. Acyclic by design."""

    validity: ValidityStatus
    validity_notes: str = ""
    hypothesis_verdict: HypothesisVerdict
    objective_progress: ObjectiveProgress
    evidence: list[EvidenceItem] = Field(min_length=1)
    reinterpreted: bool = False
    reinterpretation_reason: str = ""
    confidence: ConfidenceLevel
    confidence_reason: str = Field(min_length=1)
    execution_notes: str = ""
    caveats: list[str] = Field(default_factory=list)
    recommendation: Recommendation
    recommendation_reason: str = Field(min_length=1)
    summary: str = Field(min_length=1)

    @field_validator("evidence", mode="before")
    @classmethod
    def _decode_evidence(cls, value: Any) -> Any:
        # Some tool-calling backends don't dereference the `$ref` this nested
        # model gets in the JSON schema (see `model_json_schema()`) and emit
        # the argument as a JSON string instead of a list -- decode it here
        # rather than failing validation outright.
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value
        return value

    @model_validator(mode="after")
    def _reinterpretation_requires_reason(self) -> ReflectionJudgment:
        if self.reinterpreted and not self.reinterpretation_reason.strip():
            raise ValueError("reinterpreted=True requires a non-empty reinterpretation_reason")
        return self


def judgment_cites_primary(judgment: ReflectionJudgment, primary_metric: str) -> None:
    """Require at least one evidence item to cite the pre-committed primary metric."""
    cleaned = str(primary_metric or "").strip()
    if not cleaned:
        return
    names = {item.metric.strip() for item in judgment.evidence}
    if cleaned not in names:
        raise ValueError(
            f"at least one evidence item must cite the primary metric {cleaned!r}"
        )


def render_reflection_markdown(judgment: ReflectionJudgment, *, primary_metric: str = "") -> str:
    """Host-owned markdown artifact rendered from a submitted judgment."""
    evidence_blocks: list[str] = []
    for item in judgment.evidence:
        values = ", ".join(f"{name}={value}" for name, value in item.values.items())
        evidence_blocks.append(
            f"- **{item.metric}** ({values})\n"
            f"  - comparison: {item.comparison}\n"
            f"  - shows: {item.what_it_shows}"
        )
    caveats = "\n".join(f"- {item}" for item in judgment.caveats) or "- (none)"
    primary_line = f"`{primary_metric}`" if primary_metric else "(unspecified)"
    reinterpreted = (
        f"yes — {judgment.reinterpretation_reason.strip()}"
        if judgment.reinterpreted
        else "no"
    )
    return (
        "# Reflection\n\n"
        f"**Validity:** {judgment.validity}\n\n"
        f"{judgment.validity_notes.strip() or '(no additional validity notes)'}\n\n"
        f"**Hypothesis verdict:** {judgment.hypothesis_verdict}\n\n"
        f"**Objective progress:** {judgment.objective_progress}\n\n"
        f"**Primary metric:** {primary_line}\n\n"
        f"**Reinterpreted:** {reinterpreted}\n\n"
        f"**Confidence:** {judgment.confidence} — {judgment.confidence_reason.strip()}\n\n"
        "## Evidence\n\n"
        + "\n".join(evidence_blocks)
        + "\n\n"
        "## Execution notes\n\n"
        f"{judgment.execution_notes.strip() or '(none)'}\n\n"
        "## Caveats\n\n"
        f"{caveats}\n\n"
        "## Recommendation\n\n"
        f"**{judgment.recommendation}** — {judgment.recommendation_reason.strip()}\n\n"
        "This recommendation is a hint for the manager. The manager decides the next "
        "move and may diverge from it.\n\n"
        "## Summary\n\n"
        f"{judgment.summary.strip()}\n"
    )


class ReflectionInput(BaseModel):
    plan: ExperimentPlan
    result: ExperimentResult
    implementation: CodeImplementationManifest | None = None
    extra_host_instructions: str = ""


class Reflection(BaseModel):
    """Host-finalized artifact. The model submits a structured judgment; the host
    stamps provenance, writes markdown, and stores both.
    """

    run_id: str
    revision: int
    reflection_path: str
    content: str = Field(min_length=1)
    created_at: datetime
    judgment: ReflectionJudgment


class ReflectionOutput(BaseModel):
    reflection: Reflection
