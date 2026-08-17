# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent activity projection."""

from __future__ import annotations

from openjiuwen.harness.subagent_runtime.activity import ActivityProjector
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig


def _projector(**overrides) -> ActivityProjector:
    config = SubagentRuntimeConfig(**overrides)
    return ActivityProjector(subagent_id="sid-1", config=config)


def test_tool_call_chunk_projects_activity() -> None:
    projector = _projector()
    activity = projector.project(
        {
            "type": "tool_call",
            "payload": {
                "tool_call": {
                    "tool_name": "read_file",
                    "tool_call_id": "call-1",
                    "arguments": {"path": "README.md"},
                }
            },
        },
        task_id="task-1",
    )
    assert activity is not None
    assert activity.kind == "tool_call"
    assert activity.tool_name == "read_file"
    assert activity.tool_call_id == "call-1"
    assert "read_file" in activity.summary


def test_tool_result_chunk_projects_activity() -> None:
    projector = _projector()
    activity = projector.project(
        {
            "type": "tool_result",
            "payload": {
                "tool_result": {
                    "tool_name": "read_file",
                    "tool_call_id": "call-1",
                    "success": True,
                    "summary": "ok",
                }
            },
        },
        task_id="task-1",
    )
    assert activity is not None
    assert activity.kind == "tool_result"
    assert activity.ok is True
    assert activity.summary == "ok"


def test_llm_output_chunks_are_ignored() -> None:
    projector = _projector()
    for _ in range(1000):
        assert (
            projector.project(
                {"type": "llm_output", "payload": {"content": "token"}},
                task_id="task-1",
            )
            is None
        )


def test_llm_reasoning_is_throttled() -> None:
    projector = _projector(activity_throttle_ms=60_000, activity_text_max_len=10)
    first = projector.project(
        {"type": "llm_reasoning", "payload": {"content": "think"}},
        task_id="task-1",
    )
    second = projector.project(
        {"type": "llm_reasoning", "payload": {"content": " more"}},
        task_id="task-1",
    )
    assert first is not None
    assert first.kind == "thinking"
    assert second is None


def test_unknown_chunk_type_is_ignored() -> None:
    projector = _projector()
    assert (
        projector.project(
            {"type": "message", "payload": {"content": "hello"}},
            task_id="task-1",
        )
        is None
    )


def test_turn_limit_emits_truncated_once() -> None:
    projector = _projector(activity_queue_size=2)
    first = projector.project(
        {"type": "tool_call", "payload": {"tool_call": {"tool_name": "a"}}},
        task_id="task-1",
    )
    second = projector.project(
        {"type": "tool_call", "payload": {"tool_call": {"tool_name": "b"}}},
        task_id="task-1",
    )
    third = projector.project(
        {"type": "tool_call", "payload": {"tool_call": {"tool_name": "c"}}},
        task_id="task-1",
    )
    fourth = projector.project(
        {"type": "tool_call", "payload": {"tool_call": {"tool_name": "d"}}},
        task_id="task-1",
    )
    assert first is not None and first.kind == "tool_call"
    assert second is not None and second.kind == "tool_call"
    assert third is not None and third.kind == "truncated"
    assert fourth is not None and fourth.kind == "tool_call"


def test_new_task_id_resets_turn_state() -> None:
    projector = _projector(activity_queue_size=1)
    projector.project(
        {"type": "tool_call", "payload": {"tool_call": {"tool_name": "a"}}},
        task_id="task-1",
    )
    truncated = projector.project(
        {"type": "tool_call", "payload": {"tool_call": {"tool_name": "b"}}},
        task_id="task-1",
    )
    assert truncated is not None and truncated.kind == "truncated"

    fresh = projector.project(
        {"type": "tool_call", "payload": {"tool_call": {"tool_name": "c"}}},
        task_id="task-2",
    )
    assert fresh is not None and fresh.kind == "tool_call"
