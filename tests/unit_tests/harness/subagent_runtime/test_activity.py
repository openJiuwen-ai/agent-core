# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for subagent activity projection."""

from __future__ import annotations

from openjiuwen.harness.subagent_runtime.activity import ActivityProjector
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig


def _projector(**overrides) -> ActivityProjector:
    config = SubagentRuntimeConfig(**overrides)
    return ActivityProjector(subagent_id="sid-1", config=config)


def _tool_call(name: str) -> dict:
    return {"type": "tool_call", "payload": {"tool_call": {"tool_name": name}}}


def _thinking(text: str) -> dict:
    return {"type": "llm_reasoning", "payload": {"content": text}}


def test_tool_call_chunk_projects_activity() -> None:
    projector = _projector()
    activities = projector.project(
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
    assert len(activities) == 1
    activity = activities[0]
    assert activity.kind == "tool_call"
    assert activity.tool_name == "read_file"
    assert activity.tool_call_id == "call-1"
    assert "read_file" in activity.summary


def test_tool_result_chunk_projects_activity() -> None:
    projector = _projector()
    activities = projector.project(
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
    assert len(activities) == 1
    assert activities[0].kind == "tool_result"
    assert activities[0].ok is True
    assert activities[0].summary == "ok"


def test_llm_output_chunks_are_ignored() -> None:
    projector = _projector()
    for _ in range(1000):
        assert (
            projector.project(
                {"type": "llm_output", "payload": {"content": "token"}},
                task_id="task-1",
            )
            == []
        )


def test_llm_reasoning_is_throttled() -> None:
    projector = _projector(activity_throttle_ms=60_000, activity_text_max_len=10)
    first = projector.project(_thinking("think"), task_id="task-1")
    second = projector.project(_thinking(" more"), task_id="task-1")
    assert len(first) == 1
    assert first[0].kind == "thinking"
    assert second == []


def test_unknown_chunk_type_is_ignored() -> None:
    projector = _projector()
    assert (
        projector.project(
            {"type": "message", "payload": {"content": "hello"}},
            task_id="task-1",
        )
        == []
    )


def test_turn_limit_emits_truncated_once() -> None:
    projector = _projector(activity_queue_size=2)
    first = projector.project(_tool_call("a"), task_id="task-1")
    second = projector.project(_tool_call("b"), task_id="task-1")
    third = projector.project(_tool_call("c"), task_id="task-1")
    fourth = projector.project(_tool_call("d"), task_id="task-1")
    assert [item.kind for item in first] == ["tool_call"]
    assert [item.kind for item in second] == ["tool_call"]
    assert [item.kind for item in third] == ["truncated"]
    assert [item.kind for item in fourth] == ["tool_call"]


def test_new_task_id_resets_turn_state() -> None:
    projector = _projector(activity_queue_size=1)
    projector.project(_tool_call("a"), task_id="task-1")
    truncated = projector.project(_tool_call("b"), task_id="task-1")
    assert [item.kind for item in truncated] == ["truncated"]

    fresh = projector.project(_tool_call("c"), task_id="task-2")
    assert [item.kind for item in fresh] == ["tool_call"]


def test_consecutive_thinking_shares_one_phase() -> None:
    projector = _projector(activity_throttle_ms=0)
    first = projector.project(_thinking("step one"), task_id="task-1")
    second = projector.project(_thinking("step two"), task_id="task-1")
    assert first[0].phase_id == 1
    assert second[0].phase_id == 1


def test_thinking_after_tool_starts_a_new_phase() -> None:
    projector = _projector(activity_throttle_ms=0)
    before = projector.project(_thinking("pick a tool"), task_id="task-1")
    tool = projector.project(_tool_call("read_file"), task_id="task-1")
    after = projector.project(_thinking("read the result"), task_id="task-1")

    assert before[0].phase_id == 1
    # The tool belongs to the phase that produced it.
    assert [item.kind for item in tool] == ["tool_call"]
    assert tool[0].phase_id == 1
    assert after[0].phase_id == 2


def test_buffered_thinking_is_flushed_at_the_tool_boundary() -> None:
    projector = _projector(activity_throttle_ms=60_000, activity_text_max_len=1000)
    # The very first fragment always flushes; the throttle only kicks in after it.
    assert len(projector.project(_thinking("warm up"), task_id="task-1")) == 1
    assert projector.project(_thinking("phase one text"), task_id="task-1") == []

    at_boundary = projector.project(_tool_call("read_file"), task_id="task-1")
    assert [item.kind for item in at_boundary] == ["thinking", "tool_call"]
    assert at_boundary[0].summary == "phase one text"
    assert at_boundary[0].phase_id == 1
    assert at_boundary[1].phase_id == 1

    # Phase two must not inherit any leftover text from phase one.
    assert projector.project(_thinking("phase two text"), task_id="task-1") == []
    tail = projector.flush_pending("task-1")
    assert [item.kind for item in tail] == ["thinking"]
    assert tail[0].summary == "phase two text"
    assert tail[0].phase_id == 2


def test_flush_pending_is_noop_without_buffered_thinking() -> None:
    projector = _projector(activity_throttle_ms=0)
    projector.project(_thinking("emitted right away"), task_id="task-1")
    assert projector.flush_pending("task-1") == []


def test_phase_ids_stay_unique_across_turns() -> None:
    projector = _projector(activity_throttle_ms=0)
    first_turn = projector.project(_thinking("turn one"), task_id="task-1")
    second_turn = projector.project(_thinking("turn two"), task_id="task-2")
    assert first_turn[0].phase_id == 1
    assert second_turn[0].phase_id == 2
