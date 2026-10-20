# coding: utf-8
"""Tests for the ten attribution tools against NodeBackedRuntime."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from openjiuwen.agent_evolving.attribution.model import DiagnosticSignal, SignalSource
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode
from openjiuwen.agent_evolving.attribution.run import NodeBackedRuntime
from openjiuwen.agent_evolving.attribution.tools import (
    build_attribution_tools,
    render_partial_view,
    render_trajectory_view,
    same_subagent,
    split_node_id,
)

pytestmark = pytest.mark.asyncio


def _nodes() -> dict[str, list[TrajectoryNode]]:
    def node(subagent: str, index: int, role: str = "user", content: str = "") -> TrajectoryNode:
        return TrajectoryNode(
            node_id=f"{subagent}:{index}",
            subagent_key=subagent,
            display_name=subagent,
            kind="main_agent" if subagent == "main" else "subagent",
            role=role,
            content=content or None,
            start_ts_ns=index * 100,
            end_ts_ns=index * 100 + 50,
        )

    return {
        "main": [node("main", 0, "user", "task"), node("main", 1, "assistant", "ok")],
        "sub": [node("sub", 0, "user", "sub task")],
    }


def _signals() -> list[DiagnosticSignal]:
    return [DiagnosticSignal.create("bad", source=SignalSource.KEYWORD_TOOL, node_ids=["main:1"])]


def _tool_map(runtime: NodeBackedRuntime) -> dict[str, Any]:
    tools = build_attribution_tools(runtime, [signal.signal_id for signal in _signals()])
    return {tool.tool_name: tool for tool in tools}


async def _invoke(tool: Any, **inputs: Any) -> dict[str, Any]:
    output = await tool.invoke(inputs)
    assert output.success
    return json.loads(output.data["content"])


def test_split_node_id_and_same_subagent() -> None:
    assert split_node_id("main:3") == ("main", "3")
    assert split_node_id("subagent:abc:7") == ("subagent:abc", "7")
    assert split_node_id("nonsense") is None
    assert split_node_id("main:xx") is None
    assert same_subagent("main:1", "main:2")
    assert not same_subagent("main:1", "sub:2")
    assert not same_subagent("bad", "main:1")


async def test_keyword_reg_tool() -> None:
    runtime = NodeBackedRuntime(_nodes())
    result = await _invoke(_tool_map(runtime)["keyword_reg"], keyword="task")
    assert result["total"] == 2


async def test_view_trajectory_summary_and_partial() -> None:
    runtime = NodeBackedRuntime(_nodes())
    tools = _tool_map(runtime)
    summary = await _invoke(tools["view_trajectory"], subagent_id="main")
    assert summary["level"] == "summary"
    partial = await _invoke(tools["view_partial_trajectory"], node_id_from="main:1", node_id_end="main:0")
    assert [row["node_id"] for row in partial["nodes"]] == ["main:0", "main:1"]


async def test_view_partial_cross_subagent_error() -> None:
    runtime = NodeBackedRuntime(_nodes())
    result = await _invoke(_tool_map(runtime)["view_partial_trajectory"], node_id_from="main:0", node_id_end="sub:0")
    assert "error" in result


async def test_view_node_detail() -> None:
    runtime = NodeBackedRuntime(_nodes())
    detail = await _invoke(_tool_map(runtime)["view_node_detail"], node_id="main:0")
    assert detail["node_id"] == "main:0"
    assert detail["role"] == "user"


def _long_content_runtime() -> NodeBackedRuntime:
    nodes = {
        "main": [
            TrajectoryNode(
                node_id="main:0",
                subagent_key="main",
                display_name="main",
                kind="main_agent",
                role="assistant",
                content="0123456789" * 100,
                start_ts_ns=0,
                end_ts_ns=50,
            )
        ]
    }
    return NodeBackedRuntime(nodes)


async def test_view_node_field_content_and_cursor() -> None:
    runtime = _long_content_runtime()
    tool = _tool_map(runtime)["view_node_field"]
    first = await _invoke(tool, node_id="main:0", field="content", start=0, limit=12)
    assert first["text"] == "0123456789" * 1 + "01"
    assert first["start"] == 0
    assert first["end"] == 12
    assert first["total_chars"] == 1000
    assert first["has_more"] is True
    second = await _invoke(tool, node_id="main:0", field="content", start=first["end"], limit=8)
    assert second["text"] == "23456789"
    assert second["start"] == 12
    assert second["end"] == 20
    assert second["has_more"] is True


async def test_view_node_field_defaults_and_graceful_boundary() -> None:
    runtime = _long_content_runtime()
    tool = _tool_map(runtime)["view_node_field"]
    window = await _invoke(tool, node_id="main:0", field="content")
    assert window["start"] == 0
    assert window["end"] == 1000
    assert window["has_more"] is False
    beyond = await _invoke(tool, node_id="main:0", field="content", start=1000, limit=100)
    assert beyond == {"text": "", "start": 1000, "end": 1000, "total_chars": 1000, "has_more": False}


async def test_view_node_field_tool_calls_slice() -> None:
    nodes = {
        "main": [
            TrajectoryNode(
                node_id="main:0",
                subagent_key="main",
                display_name="main",
                kind="main_agent",
                role="assistant",
                tool_calls=[{"name": "write_file", "arguments": json.dumps({"content": "x" * 2000})}],
                start_ts_ns=0,
                end_ts_ns=50,
            )
        ]
    }
    tool = _tool_map(NodeBackedRuntime(nodes))["view_node_field"]
    window = await _invoke(tool, node_id="main:0", field="tool_calls", start=0, limit=10)
    raw = json.dumps(nodes["main"][0].tool_calls, ensure_ascii=False)
    assert window["text"] == raw[:10]
    assert window["total_chars"] == len(raw)
    assert window["has_more"] is True


async def test_view_node_field_invalid_params() -> None:
    runtime = _long_content_runtime()
    tool = _tool_map(runtime)["view_node_field"]
    for inputs in (
        {"node_id": "main:0", "field": "content", "start": -1},
        {"node_id": "main:0", "field": "content", "limit": 0},
        {"node_id": "main:0", "field": "content", "limit": 50_001},
        {"node_id": "main:0", "field": "content", "start": 1.5},
        {"node_id": "main:0", "field": "name"},
        {"node_id": "bad", "field": "content"},
    ):
        output = await tool.invoke(inputs)
        assert not output.success
    missing = await tool.invoke({"node_id": "main:9", "field": "content"})
    assert missing.success
    assert "error" in json.loads(missing.data["content"])


async def test_submit_attribution_atomic_and_overwrite() -> None:
    runtime = NodeBackedRuntime(_nodes())
    tools = _tool_map(runtime)
    signal_id = _signals()[0].signal_id
    result = await _invoke(
        tools["submit_attribution"],
        diag_signal_id=signal_id,
        results=[{"text_md": "cause", "start_node_id": "main:1"}],
    )
    assert result["status"] == "recorded"
    result = await _invoke(
        tools["submit_attribution"],
        diag_signal_id=signal_id,
        results=[{"text_md": "better cause", "start_node_id": "main:0"}],
    )
    assert result["status"] == "updated"
    assert runtime.attributions[signal_id][0]["text_md"] == "better cause"


async def test_submit_attribution_entries_carry_attr_ids() -> None:
    runtime = NodeBackedRuntime(_nodes())
    tools = _tool_map(runtime)
    signal_id = _signals()[0].signal_id
    await _invoke(
        tools["submit_attribution"],
        diag_signal_id=signal_id,
        results=[
            {"text_md": "cause", "start_node_id": "main:1"},
            {"text_md": "span", "start_node_id": "main:0", "end_node_id": "main:1"},
        ],
    )
    entries = runtime.attributions[signal_id]
    assert len(entries) == 2
    attr_ids = {entry["attr_id"] for entry in entries}
    assert len(attr_ids) == 2
    for attr_id in attr_ids:
        assert re.fullmatch(r"attr-[0-9a-f]{8}", attr_id)
    assert entries[0]["text_md"] == "cause"
    assert entries[1]["end_node_id"] == "main:1"


async def test_submit_attribution_whole_error_on_bad_entry() -> None:
    runtime = NodeBackedRuntime(_nodes())
    tools = _tool_map(runtime)
    signal_id = _signals()[0].signal_id
    result = await _invoke(
        tools["submit_attribution"],
        diag_signal_id=signal_id,
        results=[
            {"text_md": "good", "start_node_id": "main:1"},
            {"text_md": "bad", "start_node_id": "main:999"},
        ],
    )
    assert result["status"] == "error"
    assert signal_id not in runtime.attributions


async def test_submit_attribution_cross_subagent_error() -> None:
    runtime = NodeBackedRuntime(_nodes())
    tools = _tool_map(runtime)
    result = await _invoke(
        tools["submit_attribution"],
        diag_signal_id=_signals()[0].signal_id,
        results=[{"text_md": "x", "start_node_id": "main:0", "end_node_id": "sub:0"}],
    )
    assert result["status"] == "error"
    assert "不同 subagent" in result["error"]


async def test_submit_attribution_unknown_signal() -> None:
    runtime = NodeBackedRuntime(_nodes())
    result = await _invoke(
        _tool_map(runtime)["submit_attribution"],
        diag_signal_id="diag-nope",
        results=[{"text_md": "x", "start_node_id": "main:0"}],
    )
    assert result["status"] == "error"


async def test_mark_signal_invalid() -> None:
    runtime = NodeBackedRuntime(_nodes())
    tools = _tool_map(runtime)
    signal_id = _signals()[0].signal_id
    result = await _invoke(tools["mark_signal_invalid"], diag_signal_id=signal_id)
    assert result["status"] == "marked"
    assert signal_id in runtime.invalid_signals


async def test_improvement_tools_overwrite() -> None:
    runtime = NodeBackedRuntime(_nodes())
    tools = _tool_map(runtime)
    assert (await _invoke(tools["submit_artifact_improvement"], md_string="v1"))["status"] == "recorded"
    assert (await _invoke(tools["submit_artifact_improvement"], md_string="v2"))["status"] == "updated"
    assert runtime.artifact_improvement == "v2"
    assert (await _invoke(tools["submit_skill_improvement"], skill_name_or_filename="s.py", md_string="x"))[
        "status"
    ] == "recorded"
    assert runtime.skill_improvements["s.py"] == "x"


async def test_check_complete_pending_and_all_done() -> None:
    runtime = NodeBackedRuntime(_nodes())
    tools = _tool_map(runtime)
    signal_id = _signals()[0].signal_id
    result = await _invoke(tools["check_attribution_complete"])
    assert result["all_done"] is False
    assert result["pending_signal_ids"] == [signal_id]
    assert result["missing_artifact_improvement"] is True
    await _invoke(
        tools["submit_attribution"], diag_signal_id=signal_id, results=[{"text_md": "x", "start_node_id": "main:1"}]
    )
    await _invoke(tools["submit_artifact_improvement"], md_string="v1")
    result = await _invoke(tools["check_attribution_complete"])
    assert result["all_done"] is True


async def test_check_complete_all_invalid_counts_as_done() -> None:
    runtime = NodeBackedRuntime(_nodes())
    tools = _tool_map(runtime)
    await _invoke(tools["mark_signal_invalid"], diag_signal_id=_signals()[0].signal_id)
    result = await _invoke(tools["check_attribution_complete"])
    assert result["all_done"] is False
    assert result["pending_signal_ids"] == []
    await _invoke(tools["submit_artifact_improvement"], md_string="v1")
    result = await _invoke(tools["check_attribution_complete"])
    assert result["all_done"] is True


def test_render_trajectory_view_overview_for_large() -> None:
    nodes = [
        TrajectoryNode(
            node_id=f"main:{index}",
            subagent_key="main",
            display_name="M",
            kind="main_agent",
            role="tool",
            content="",
            name="bash",
            start_ts_ns=index,
            end_ts_ns=index,
        )
        for index in range(501)
    ]
    view = render_trajectory_view(nodes, "zh")
    assert view["level"] == "overview"


def test_render_partial_view_missing_node() -> None:
    nodes = list(_nodes()["main"])
    view = render_partial_view(nodes, "main:0", "main:9", "zh")
    assert "error" in view
