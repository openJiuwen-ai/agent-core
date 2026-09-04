from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import ExperimentPlan


class CodeImplementationInput(BaseModel):
    plan: ExperimentPlan
    # Prepended to the rendered task prompt -- lets a caller (e.g. the manager
    # pipeline's CodeImplementationAdapter) inject host-side context such as a
    # prior attempt's failure summary, without reaching into the agent's
    # protected _build_task_prompt.
    extra_host_instructions: str = ""


class ImplementedVariant(BaseModel):
    """One runnable variant (a baseline, or "proposed") sharing the same entry
    point and metric computation as every other variant in the run.
    """

    name: str
    invocation: list[str]


class CodeImplementationManifest(BaseModel):
    # Mirrors ExperimentResult: a pointer into experiments/<run_id>/, not the
    # generated code itself (see docs/architecture.md). This is the object
    # experiment_execution consumes (ExperimentExecutionInput.implementation).
    run_id: str
    workspace_dir: str
    files: list[str] = Field(default_factory=list)
    variants: list[ImplementedVariant] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    smoke_test_passed: bool = False
    status: Literal["ready", "failed"] = "failed"
    readiness: Literal["smoke_ready", "failed"] = "failed"
    smoke_failures: dict[str, str] = Field(default_factory=dict)
    notes: str = ""


class CodeImplementationOutput(BaseModel):
    implementation: CodeImplementationManifest
