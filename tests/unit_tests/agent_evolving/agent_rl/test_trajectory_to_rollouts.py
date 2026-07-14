# -*- coding: utf-8 -*-
"""Tests for trajectory_to_rollouts normalization."""

from openjiuwen.agent_evolving.agent_rl.schemas import trajectory_to_rollouts
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    TrajectoryStep,
    trajectory_from_steps,
)
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage


def test_trajectory_to_rollouts_converts_assistant_message_response():
    traj = trajectory_from_steps(
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
    traj = trajectory_from_steps(
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


def test_trajectory_to_rollouts_projects_otlp_token_tools_and_meta_fields():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "parameters": {"type": "object"},
            },
        }
    ]
    response = {
        "role": "assistant",
        "content": "calling lookup",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q": "hi"}'},
            }
        ],
    }
    traj = trajectory_from_steps(
        execution_id="e3",
        steps=[
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="lookup", call_args={"q": "old"}),
            ),
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="test-model",
                    messages=[
                        {"role": "system", "content": "be concise"},
                        {"role": "user", "content": "hi"},
                    ],
                    response=response,
                    tools=tools,
                ),
                prompt_token_ids=[101, 102, 103],
                completion_token_ids=[201, 202],
                meta={"llm_config": {"temperature": 0.2}},
            ),
        ],
        source="rl_offline",
    )

    rollouts = trajectory_to_rollouts(traj)

    assert len(rollouts) == 1
    rollout = rollouts[0]
    assert rollout.turn_id == 0
    assert rollout.input_prompt == {
        "message": [
            {"role": "system", "content": "be concise"},
            {"role": "user", "content": "hi"},
        ],
        "tools": tools,
    }
    assert rollout.output_response == response
    assert rollout.input_prompt_ids == [101, 102, 103]
    assert rollout.output_response_ids == [201, 202]
    assert rollout.llm_config == {"temperature": 0.2}
