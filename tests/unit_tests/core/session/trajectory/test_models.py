# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest
from pydantic import ValidationError

from openjiuwen.core.session.trajectory.models import (
    FinalMetrics,
    Observation,
    ObservationResult,
    StepError,
    StepMetrics,
    Trajectory,
    TrajectoryStep,
    TrajectoryToolCall,
)


def _agent_step_with_call(step_id: int, call_id: str) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=step_id,
        role="agent",
        message="calling a tool",
        tool_calls=[TrajectoryToolCall(tool_call_id=call_id, function_name="search", arguments='{"q": "x"}')],
        metrics=StepMetrics(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _observation_step(step_id: int, source_call_id: str) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=step_id,
        role="system",
        observation=Observation(results=[ObservationResult(source_call_id=source_call_id, content="42")]),
    )


class TestTrajectoryModels:
    def test_round_trip_preserves_all_fields(self):
        trajectory = Trajectory(
            session_id="s1",
            trajectory_id="t1",
            steps=[
                _agent_step_with_call(1, "call-1"),
                _observation_step(2, "call-1"),
                TrajectoryStep(step_id=3, role="agent", error=StepError(error_code=100000, message="boom")),
            ],
            final_metrics=FinalMetrics(total_tokens=15, llm_call_count=1, tool_call_count=1),
        )
        restored = Trajectory.model_validate(trajectory.model_dump())
        assert restored == trajectory

    def test_dangling_source_call_id_is_rejected(self):
        with pytest.raises(ValidationError, match="unknown tool_call_id 'ghost'"):
            Trajectory(steps=[_agent_step_with_call(1, "call-1"), _observation_step(2, "ghost")])

    def test_observation_without_source_call_id_is_valid(self):
        # A tool run outside an LLM turn has no declaring call to reference.
        trajectory = Trajectory(
            steps=[
                TrajectoryStep(
                    step_id=1,
                    role="system",
                    observation=Observation(results=[ObservationResult(content="standalone")]),
                )
            ]
        )
        assert trajectory.steps[0].observation.results[0].source_call_id is None
