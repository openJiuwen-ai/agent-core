# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ACH evaluation trajectory storage."""

from __future__ import annotations

import json

from openjiuwen.agent_evolving.trajectory.types import (
    LegacyTrajectory,
    LLMCallDetail,
    ToolCallDetail,
    TrajectoryStep,
    trajectory_from_legacy,
)
from openjiuwen.rsi.evaluator.case_backend import (
    _is_runtime_workspace_metadata,
    _skip_trace_snapshot_path,
)
from openjiuwen.rsi.evaluator.case_runner import (
    _messages_from_role_trajectory,
    _skip_harvest_path,
)
from openjiuwen.rsi.evaluator.trajectory_paths import (
    RoleFileTrajectoryStore,
)


def test_role_file_trajectory_store_writes_bounded_latest_snapshot(tmp_path) -> None:
    store = RoleFileTrajectoryStore(tmp_path, "ui-designer")
    large_text = "x" * 20_000
    large_tool_schema = {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": large_text,
            "parameters": {"type": "object", "properties": {"content": {"description": large_text}}},
        },
    }
    first = trajectory_from_legacy(
        LegacyTrajectory(
            execution_id="first",
            steps=[
                TrajectoryStep(
                    kind="llm",
                    detail=LLMCallDetail(
                        model="deepseek-v4-flash",
                        messages=[
                            {"role": "system", "content": large_text},
                            {"role": "user", "content": "task"},
                            {"role": "assistant", "content": large_text},
                            {"role": "tool", "content": large_text},
                            {"role": "user", "content": "latest"},
                        ],
                        response={"role": "assistant", "content": large_text},
                        tools=[large_tool_schema],
                    ),
                ),
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="write_file",
                        call_args={"content": large_text},
                        call_result={"ok": True, "content": large_text},
                    ),
                ),
            ],
        )
    )
    second = trajectory_from_legacy(LegacyTrajectory(execution_id="second", steps=[]))

    store.save(first)
    store.save(second)

    path = tmp_path / "ui-designer.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["execution_id"] == "second"

    store.save(first)
    saved = json.loads(path.read_text(encoding="utf-8"))
    llm_detail = saved["steps"][0]["detail"]
    tool_detail = saved["steps"][1]["detail"]

    assert len(json.dumps(saved, ensure_ascii=False)) < 30_000
    assert llm_detail["tools"] == [{"name": "write_file", "type": "function"}]
    assert "description" not in llm_detail["tools"][0]
    assert llm_detail["meta"]["omitted_message_count"] == 1
    assert all(len(message.get("content", "")) <= 1600 for message in llm_detail["messages"])
    assert len(tool_detail["call_args"]["content"]) <= 1600
    assert len(tool_detail["call_result"]["content"]) <= 1600


def test_role_trajectory_reader_uses_tool_call_args_and_results() -> None:
    messages = _messages_from_role_trajectory(
        {
            "steps": [
                {
                    "kind": "tool",
                    "detail": {
                        "tool_name": "write_file",
                        "call_args": {"file_path": "artifacts/index.html"},
                        "call_result": {"success": True},
                    },
                }
            ]
        }
    )

    tool_call = messages[0]["tool_calls"][0]
    assert tool_call["name"] == "write_file"
    assert "artifacts/index.html" in tool_call["input"]
    assert "true" in tool_call["output"].lower()


def test_role_trajectory_reader_preserves_middle_decisions_without_cumulative_duplicates() -> None:
    steps = []
    cumulative_messages = [
        {"role": "system", "content": "system policy"},
        {"role": "user", "content": "fix the task"},
    ]
    for index in range(20):
        if index == 10:
            cumulative_messages.append({"role": "user", "content": "recovery after an empty turn"})
        steps.append(
            {
                "kind": "llm",
                "detail": {
                    "messages": [*cumulative_messages],
                    "response": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": f"causal decision {index}",
                    },
                },
            }
        )
        cumulative_messages.append({"role": "assistant", "content": f"causal decision {index}"})

    messages = _messages_from_role_trajectory({"steps": steps})
    serialized = json.dumps(messages, ensure_ascii=False)

    assert len(messages) == 23
    assert serialized.count("system policy") == 1
    assert serialized.count("fix the task") == 1
    assert "causal decision 10" in serialized
    assert "trajectory_step_11:response" in serialized
    assert "recovery after an empty turn" in serialized


def test_runtime_workspace_metadata_is_not_solver_evidence() -> None:
    runtime_paths = [
        "AGENT.md",
        "HEARTBEAT.md",
        "context/session.json",
        "memory/notes.md",
        "messages/turn.json",
        "skills/.workspace",
        "agents/solver/.workspace",
    ]
    for path in runtime_paths:
        assert _is_runtime_workspace_metadata(path)
        assert _skip_trace_snapshot_path(path)
        assert _skip_harvest_path(path)

    assert not _is_runtime_workspace_metadata("src/agent.py")
    assert not _skip_trace_snapshot_path("src/agent.py")
    assert not _skip_harvest_path("src/agent.py")
