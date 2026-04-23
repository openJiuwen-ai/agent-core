# -*- coding: utf-8 -*-
"""Tests for trajectory_to_rollouts normalization."""

from openjiuwen.agent_evolving.agent_rl.schemas import trajectory_to_rollouts
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    Trajectory,
    TrajectoryStep,
)
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage


def test_trajectory_to_rollouts_converts_assistant_message_response():
    traj = Trajectory(
        execution_id="e1",
        steps=[
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="test-model",
                    messages=[UserMessage(content="hi")],
                    response=AssistantMessage(content="hello"),
                ),
            )
        ],
    )
    rollouts = trajectory_to_rollouts(traj)
    assert len(rollouts) == 1
    assert rollouts[0].output_response is not None
    assert rollouts[0].output_response["role"] == "assistant"
    assert rollouts[0].output_response["content"] == "hello"
    assert isinstance(rollouts[0].input_prompt["message"], list)
    assert rollouts[0].input_prompt["message"][0]["role"] == "user"
    assert rollouts[0].input_prompt["message"][0]["content"] == "hi"


def test_trajectory_to_rollouts_keeps_dict_response():
    traj = Trajectory(
        execution_id="e2",
        steps=[
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="m",
                    messages=[],
                    response={"role": "assistant", "content": "ok"},
                ),
            )
        ],
    )
    rollouts = trajectory_to_rollouts(traj)
    assert rollouts[0].output_response == {"role": "assistant", "content": "ok"}
