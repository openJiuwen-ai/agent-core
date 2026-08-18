# -*- coding: utf-8 -*-
"""Tests for canonical trajectory_to_rollouts normalization."""

import json

from openjiuwen.agent_evolving.agent_rl.schemas import trajectory_to_rollouts
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.extensions.observability import semconv


def test_trajectory_to_rollouts_converts_assistant_message_response():
    traj = _trajectory(
        "e1",
        {
            f"{semconv.GEN_AI_REQUEST_MODEL}": "test-model",
            f"{semconv.GEN_AI_PROMPT}.0.role": "user",
            f"{semconv.GEN_AI_PROMPT}.0.content": "hi",
            f"{semconv.GEN_AI_COMPLETION}.0.role": "assistant",
            f"{semconv.GEN_AI_COMPLETION}.0.content": "hello",
        },
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
    traj = _trajectory(
        "e2",
        {
            f"{semconv.GEN_AI_REQUEST_MODEL}": "m",
            f"{semconv.GEN_AI_COMPLETION}.0.role": "assistant",
            f"{semconv.GEN_AI_COMPLETION}.0.content": "ok",
        },
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
    traj = _trajectory(
        "e3",
        {
            f"{semconv.GEN_AI_REQUEST_MODEL}": "test-model",
            f"{semconv.GEN_AI_PROMPT}.0.role": "system",
            f"{semconv.GEN_AI_PROMPT}.0.content": "be concise",
            f"{semconv.GEN_AI_PROMPT}.1.role": "user",
            f"{semconv.GEN_AI_PROMPT}.1.content": "hi",
            f"{semconv.GEN_AI_COMPLETION}.0.role": "assistant",
            f"{semconv.GEN_AI_COMPLETION}.0.content": "calling lookup",
            semconv.GEN_AI_TOOL_CALLS: json.dumps(response["tool_calls"]),
            semconv.GEN_AI_TOOL_DEFINITIONS: json.dumps(tools),
            "evolution.rl.prompt_token_ids": [101, 102, 103],
            "evolution.rl.completion_token_ids": [201, 202],
            "llm_config": {"temperature": 0.2},
        },
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


def _trajectory(execution_id: str, span_attributes: dict) -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map(
                            {
                                "openjiuwen.trajectory_id": execution_id,
                                semconv.AT_SESSION_ID: "session-1",
                                "openjiuwen.trajectory.source": "rl_offline",
                            }
                        )
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "trace-1",
                                    "spanId": "llm-1",
                                    "name": "llm.call",
                                    "attributes": attributes_from_map(span_attributes),
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    )
