"""Pydantic contracts for the Reporting Agent. See docs/paper_writing_design.md
(the design doc keeps its original filename; the module itself replaced the
old lightweight-markdown reporting stage — see auto_research/modules/
reporting_legacy for that prior implementation, kept unreferenced)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import (
    ExperimentPlan,
    ResearchBrief,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import ExperimentResult
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reflection.schemas import Reflection


class ResearchContext(BaseModel):
    """Derived state from a previous paper — see
    docs/reporting_iteration_design.md. Always re-derived from a compiled
    paper by the (not yet implemented) Preprocessing step, never
    hand-updated by ReportingAgent itself: Paper is the source of truth,
    ResearchContext is derived state, and re-deriving it every time is what
    keeps the two from silently drifting apart. Minimal shape for Phase 1 of
    that design — just enough to type ReportingInput.previous_context
    correctly ahead of Preprocessing (Phase 3) and Claim Management
    (Phase 2/4) actually existing."""

    problem: str = ""
    method: str = ""
    claims: list[dict] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ReportingInput(BaseModel):
    survey: ResearchBrief
    plan: ExperimentPlan
    result: ExperimentResult
    # Optional: a failed/timed-out reflection must never block reaching this
    # module — same rule docs/reflection_design.md §9 established for reporting.
    reflection: Reflection | None = None
    # Previous paper's derived state, if this run is extending/updating a
    # prior paper rather than writing from scratch — see
    # docs/reporting_iteration_design.md. None (the default) means today's
    # exact write-from-scratch behavior; ReportingAgent must not do any
    # extra work for a None here (see its _run_async fallback gate).
    previous_context: ResearchContext | None = None
    # Manager-authored notes on why the previous reporting attempt failed
    # (SubtaskContract.repair_instruction) — strategic-level commentary
    # layered on top of the host-authored PREVIOUS_ATTEMPT_NOTES.md the
    # agent finds in its own workspace on a retry (see agent.py's
    # _run_async). Say so up front instead of hoping this attempt fares
    # differently by chance.
    repair_instruction: str = ""
    # 1 = first attempt for this run: workspace wiped fresh, as before.
    # >1 = retry: workspace is kept as-is (sections/figures/notes from the
    # interrupted previous attempt survive) instead of wiping and redoing a
    # full write+review+compile pass from zero every time. Host-computed
    # (ReportingAdapter passes the same attempt number it uses for the
    # report_id), not inferred from repair_instruction being non-empty —
    # a manager slip-up in populating that field must not silently turn a
    # retry into an accidental full wipe or vice versa.
    attempt: int = 1


class ReportingOutput(BaseModel):
    status: Literal["compiled", "failed"]
    paper_pdf_path: str | None = None
    sections_dir: str
    refs_bib_path: str
    figure_paths: list[str] = Field(default_factory=list)
    # Gate failures, compile errors — surfaced, not hidden. This module must
    # never silently ship a partial or non-compiling artifact without saying
    # so here.
    notes: str | None = None


class FigureNode(BaseModel):
    """One stage box in a method/architecture overview figure. ``label``
    must be one of the real ``\\subsection{}`` headings ts-write already
    committed to in method.tex (see ts-figure/scripts/extract_headings.py)
    — the figure is derived from the written Method section, not authored
    ahead of it, so there is nothing for its terminology to drift from."""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=60)
    subtitle: str = Field(default="", max_length=80)
    tone: Literal["neutral", "accent", "secondary", "warning", "success", "danger"] = "neutral"
    badge: str = Field(default="", max_length=20)


class FigureEdge(BaseModel):
    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    label: str = Field(default="", max_length=40)
    style: Literal["solid", "dashed"] = "solid"

    model_config = {"populate_by_name": True}


class MethodFigureSpec(BaseModel):
    """Structured contract for the method-figure renderer(s) — see
    ts-figure/scripts/render_method_figure.py (matplotlib fallback) and
    ts-figure/scripts/build_drawio_figure.py (Draw.io path, tried first
    when a ``drawio`` binary is available)."""

    claim: str = Field(min_length=1, max_length=240)
    five_second_takeaway: str = Field(min_length=1, max_length=90)
    nodes: list[FigureNode] = Field(min_length=2, max_length=7)
    edges: list[FigureEdge] = Field(min_length=1)
