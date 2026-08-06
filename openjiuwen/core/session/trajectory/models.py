# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Data models for recorded trajectories.

A trajectory consists of a header (``schema_version`` / ``session_id`` /
``trajectory_id`` / ``agent``), an ordered list of ``steps`` and an
aggregate ``final_metrics`` footer.
Observation results reference the tool call that produced them via
``source_call_id``, validated against declared ``tool_call_id`` values.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

TRAJECTORY_SCHEMA_VERSION = "1.0"


class TrajectoryToolCall(BaseModel):
    """A tool call declared by an agent step."""

    tool_call_id: Optional[str] = None
    function_name: str
    arguments: Optional[str] = None
    extra: Optional[dict] = None


class ObservationResult(BaseModel):
    """Result of one tool execution, linked to its declaring tool call."""

    source_call_id: Optional[str] = None
    content: Any = None
    extra: Optional[dict] = None


class Observation(BaseModel):
    """Container for tool execution results attached to a step."""

    results: list[ObservationResult] = Field(default_factory=list)


class StepError(BaseModel):
    """Error captured on a step, mirroring tracer span error payloads."""

    error_code: Optional[int] = None
    message: str = ""


class StepMetrics(BaseModel):
    """Per-step usage metrics taken from normalized ``UsageMetadata``."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_tokens: int = 0
    cost: float = 0.0
    latency: float = 0.0


class TrajectoryStep(BaseModel):
    """One event in a trajectory (LLM call, tool execution, error, ...)."""

    step_id: int
    timestamp: str = ""
    role: Literal["user", "agent", "system"] = "agent"
    message: Optional[str] = None
    model_name: Optional[str] = None
    reasoning_content: Optional[str] = None
    tool_calls: Optional[list[TrajectoryToolCall]] = None
    observation: Optional[Observation] = None
    error: Optional[StepError] = None
    metrics: Optional[StepMetrics] = None
    extra: Optional[dict] = None


class AgentInfo(BaseModel):
    """Identity of the agent that produced the trajectory."""

    name: str = ""
    version: Optional[str] = None
    model_name: Optional[str] = None
    extra: Optional[dict] = None


class ErrorRecord(BaseModel):
    """Aggregated error entry in the final metrics footer."""

    step_id: int
    error_code: Optional[int] = None
    message: str = ""


class FinalMetrics(BaseModel):
    """Aggregate metrics over a whole trajectory."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cache_tokens: int = 0
    total_cost: float = 0.0
    llm_call_count: int = 0
    tool_call_count: int = 0
    error_count: int = 0
    errors: list[ErrorRecord] = Field(default_factory=list)
    success: bool = True
    started_at: str = ""
    ended_at: str = ""


class Trajectory(BaseModel):
    """A complete recorded trajectory."""

    schema_version: str = TRAJECTORY_SCHEMA_VERSION
    session_id: str = ""
    trajectory_id: str = ""
    # Trajectory id of the run that spawned this one (e.g. via a subagent
    # tool call); None for top-level runs.
    parent_trajectory_id: Optional[str] = None
    # ISO start time of the run; set on the first recorded step, so the run
    # is identifiable by timestamp even before final_metrics is written.
    started_at: str = ""
    agent: Optional[AgentInfo] = None
    steps: list[TrajectoryStep] = Field(default_factory=list)
    # Mid-run snapshots carry the running totals with ``ended_at`` empty;
    # ``ended_at`` is stamped when the trace is finalized.
    final_metrics: Optional[FinalMetrics] = None
    extra: Optional[dict] = None

    @model_validator(mode="after")
    def _validate_observation_references(self) -> "Trajectory":
        """Ensure every observation references a previously declared tool call."""
        declared: set[str] = set()
        for step in self.steps:
            for call in step.tool_calls or []:
                if call.tool_call_id is not None:
                    declared.add(call.tool_call_id)
            if step.observation is None:
                continue
            for result in step.observation.results:
                if result.source_call_id is not None and result.source_call_id not in declared:
                    raise ValueError(
                        f"observation in step {step.step_id} references unknown tool_call_id '{result.source_call_id}'"
                    )
        return self
