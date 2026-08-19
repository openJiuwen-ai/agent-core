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


def test_reasoning_is_split_per_phase_across_a_tool_call() -> None:
    projector = _projector()
    projector.begin_turn("task-1", "query")
    projector.project(
        {"type": "llm_reasoning", "payload": {"content": "I should read the file"}},
        task_id="task-1",
    )
    tool_message = projector.project(
        {"type": "tool_call", "payload": {"tool_call": {"tool_name": "read_file"}}},
        task_id="task-1",
    )
    projector.project(
        {"type": "llm_reasoning", "payload": {"content": "the file says X"}},
        task_id="task-1",
    )
    aggregator = TurnOutputAggregator()
    aggregator.consume({"type": "llm_reasoning", "payload": {"content": "I should read the file"}})
    aggregator.consume({"type": "llm_reasoning", "payload": {"content": "the file says X"}})
    aggregator.consume({"type": "llm_output", "payload": {"content": "done"}})
    final_message = projector.end_turn("task-1", aggregator)

    assert tool_message is not None
    assert tool_message.reasoning_content == "I should read the file"
    assert tool_message.phase_id == 1
    # The post-tool reasoning is a new phase and must not repeat the first one.
    assert final_message.reasoning_content == "the file says X"
    assert final_message.phase_id == 2
