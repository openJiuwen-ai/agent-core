from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.schemas import (
    CodeImplementationManifest,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import ExperimentPlan


class ExperimentExecutionInput(BaseModel):
    plan: ExperimentPlan
    # code_implementation's manifest — implementation.variants[i].invocation is
    # the full CLI command for each variant; execution shouldn't need to know
    # anything about how the generated code is structured beyond that.
    implementation: CodeImplementationManifest


class VariantResult(BaseModel):
    name: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    exit_code: int
    log_path: str
    failure_kind: str = ""
    metrics_state: str = ""
    duration_ms: int | None = None
    process_status: Literal["completed", "failed"] = "failed"
    # >1 means a transient failure (launch_error/timeout) was retried — see
    # experiment_execution/agent.py's _TRANSIENT_FAILURE_KINDS.
    attempts: int = 1
    # Attempt-scoped sanitized sidecar copied from a workspace-contained
    # harness diagnostics_path, if any.
    diagnostics_path: str = ""


class ExperimentResult(BaseModel):
    run_id: str
    # experiments/<run_id>/ — contains generated_code/, logs/, results/ per
    # experiments/README.md. Downstream consumers (reporting) read from here
    # rather than the schema carrying full log/code content inline.
    workspace_dir: str
    # One entry per implemented variant (baselines + "proposed") — a flat
    # metrics dict can't represent the comparison the experiment exists to
    # make, so results are kept per-variant all the way through to reporting.
    variants: list[VariantResult] = Field(default_factory=list)
    status: Literal["completed", "failed"] = "failed"
    notes: str = ""


class ExperimentExecutionOutput(BaseModel):
    result: ExperimentResult
