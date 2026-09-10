# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Canonical OTLP trajectory rendering for Metis reflection."""

from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis.trajectory_text import render_trajectory_text
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.extensions.observability import semconv


def _trajectory() -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map({TRAJECTORY_ID: "traj-1"}),
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "1" * 32,
                                    "spanId": "2" * 16,
                                    "name": "llm.call",
                                    "startTimeUnixNano": "1",
                                    "endTimeUnixNano": "2",
                                    "attributes": attributes_from_map(
                                        {
                                            semconv.GEN_AI_OUTPUT_MESSAGES: [
                                                {
                                                    "role": "assistant",
                                                    "parts": [
                                                        {"type": "text", "content": "Use the lookup tool."}
                                                    ],
                                                }
                                            ],
                                            semconv.GEN_AI_TOOL_CALLS: [
                                                {"id": "call-1", "name": "lookup", "arguments": {"q": "x"}}
                                            ],
                                        }
                                    ),
                                    "status": {"code": "STATUS_CODE_OK"},
                                },
                                {
                                    "traceId": "1" * 32,
                                    "spanId": "3" * 16,
                                    "name": "tool.lookup",
                                    "startTimeUnixNano": "3",
                                    "endTimeUnixNano": "4",
                                    "attributes": attributes_from_map(
                                        {
                                            semconv.GEN_AI_TOOL_NAME: "lookup",
                                            semconv.GEN_AI_TOOL_INPUT: {"q": "x"},
                                            semconv.GEN_AI_TOOL_OUTPUT: {"answer": 1},
                                        }
                                    ),
                                    "status": {"code": "STATUS_CODE_ERROR", "message": "temporary failure"},
                                },
                            ]
                        }
                    ],
                }
            ]
        }
    )


def test_render_trajectory_text_uses_canonical_span_accessors():
    text = render_trajectory_text(_trajectory())

    assert "[step 1 | assistant]" in text
    assert "Use the lookup tool." in text
    assert "calls: lookup" in text
    assert "[step 2 | tool]" in text
    assert 'args: {"q": "x"}' in text
    assert 'result: {"answer": 1}' in text
    assert "temporary failure" in text
