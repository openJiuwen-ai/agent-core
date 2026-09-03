"""Pydantic contracts for the Reflection module. See docs/reflection_design.md."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.schemas import (
    CodeImplementationManifest,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import ExperimentPlan
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import ExperimentResult


class ReflectionInput(BaseModel):
    plan: ExperimentPlan
    result: ExperimentResult
    # Optional: reveals where the actual code diverged from the design
    # (synthetic data used as a stand-in, a library substitution, a partial
    # smoke-test failure) — reflection must still work standalone if it's
    # absent (e.g. a hand-built plan/result with no real code_implementation
    # run behind it), just with a narrower "whole story" to reason from.
    implementation: CodeImplementationManifest | None = None
    # Prepended to the rendered task prompt -- lets a caller (e.g. the manager
    # pipeline's ReflectionAdapter) inject host-side context such as the
    # manager subtask contract, without reaching into the agent's protected
    # _build_task_prompt.
    extra_host_instructions: str = ""


class Reflection(BaseModel):
    """Host-finalized artifact reference. The model authors reflection_path's
    markdown content directly (see agent.py's ReflectionWriteRail) — there is
    no structured judgment schema for the host to validate. The host only
    stamps run_id/revision/created_at and reads the file back into `content`.
    """

    run_id: str
    revision: int
    reflection_path: str
    content: str = Field(min_length=1)
    created_at: datetime


class ReflectionOutput(BaseModel):
    reflection: Reflection
