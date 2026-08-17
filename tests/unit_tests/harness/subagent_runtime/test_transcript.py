# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent transcript projection."""

from __future__ import annotations

from openjiuwen.harness.subagent_runtime.stream_output import TurnOutputAggregator
from openjiuwen.harness.subagent_runtime.transcript import TranscriptProjector


def _projector() -> TranscriptProjector:
    return TranscriptProjector(
        subagent_id="parent_sub_explore_abcd1234",
        parent_session_id="parent-session",
    )


def test_begin_turn_emits_user_message() -> None:
    projector = _projector()
    message = projector.begin_turn("task-1", "find config files")
    assert message.role == "user"
    assert message.content == "find config files"
    assert message.task_id == "task-1"
    assert message.parent_session_id == "parent-session"


def test_tool_call_is_not_truncated() -> None:
    projector = _projector()
    long_args = "x" * 500
    message = projector.project(
        {
            "type": "tool_call",
            "payload": {
                "tool_call": {
                    "tool_name": "read_file",
                    "tool_call_id": "call-1",
                    "arguments": {"path": long_args},
                }
            },
        },
        task_id="task-1",
    )
    assert message is not None
    assert message.event_type == "chat.tool_call"
    assert long_args in message.content
    assert message.extra is not None
    assert message.extra["tool_call"]["arguments"]["path"] == long_args


def test_end_turn_attaches_reasoning_to_final() -> None:
    projector = _projector()
    projector.begin_turn("task-1", "query")
    projector.project(
        {"type": "llm_reasoning", "payload": {"content": "plan step 1"}},
        task_id="task-1",
    )
    aggregator = TurnOutputAggregator()
    aggregator.consume({"type": "llm_output", "payload": {"content": "done"}})
    final_message = projector.end_turn("task-1", aggregator)
    assert final_message.event_type == "chat.final"
    assert final_message.content == "done"
    assert final_message.reasoning_content == "plan step 1"
