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
from openjiuwen.agent_evolving.attribution.skill_inventory import SkillUsage
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
    tools = build_attribution_tools(runtime, _signals())
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


def _reward_signal() -> DiagnosticSignal:
    return DiagnosticSignal.create("dimension\ncomment", source=SignalSource.REWARD, node_ids=["main:1"])


def _marked_runtime(*, inventory: list[tuple[str, str]] | None = None) -> NodeBackedRuntime:
    runtime = NodeBackedRuntime(_nodes())
    runtime.skill_inventory = [SkillUsage(key, kind, "main:1") for key, kind in (inventory or [])]
    return runtime


def _marked_tool_map(runtime: NodeBackedRuntime, signals: list[DiagnosticSignal] | None = None) -> dict[str, Any]:
    active = signals or _signals()
    tools = build_attribution_tools(runtime, active)
    return {tool.tool_name: tool for tool in tools}


async def test_submit_reward_failure_atomic_and_overwrite() -> None:
    signal = _reward_signal()
    runtime = _marked_runtime()
    tools = _marked_tool_map(runtime, [signal])
    result = await _invoke(
        tools["submit_reward_failure"],
        diag_signal_id=signal.signal_id,
        results=[{"text_md": "cause", "start_node_id": "main:1", "suggestion": "fix it"}],
    )
    assert result["status"] == "recorded"
    result = await _invoke(
        tools["submit_reward_failure"],
        diag_signal_id=signal.signal_id,
        results=[{"text_md": "better cause", "start_node_id": "main:0", "suggestion": "fix it better"}],
    )
    assert result["status"] == "updated"
    assert runtime.reward_attributions[signal.signal_id][0]["text_md"] == "better cause"
    assert runtime.reward_attributions[signal.signal_id][0]["suggestion"] == "fix it better"


async def test_cause_entries_carry_attr_ids() -> None:
    signal = _reward_signal()
    runtime = _marked_runtime()
    tools = _marked_tool_map(runtime, [signal])
    await _invoke(
        tools["submit_reward_failure"],
        diag_signal_id=signal.signal_id,
        results=[
            {"text_md": "cause", "start_node_id": "main:1", "suggestion": "fix one"},
            {"text_md": "span", "start_node_id": "main:0", "end_node_id": "main:1", "suggestion": "fix two"},
        ],
    )
    entries = runtime.reward_attributions[signal.signal_id]
    attr_ids = {entry["attr_id"] for entry in entries}
    assert len(attr_ids) == 2
    for attr_id in attr_ids:
        assert re.fullmatch(r"attr-[0-9a-f]{8}", attr_id)
    assert entries[1]["end_node_id"] == "main:1"


async def test_cause_whole_error_on_bad_entry_and_cross_subagent() -> None:
    signal = _reward_signal()
    runtime = _marked_runtime()
    tools = _marked_tool_map(runtime, [signal])
    result = await _invoke(
        tools["submit_reward_failure"],
        diag_signal_id=signal.signal_id,
        results=[
            {"text_md": "good", "start_node_id": "main:1", "suggestion": "fix it"},
            {"text_md": "bad", "start_node_id": "main:999", "suggestion": "fix it"},
        ],
    )
    assert result["status"] == "error"
    assert signal.signal_id not in runtime.reward_attributions
    result = await _invoke(
        tools["submit_reward_failure"],
        diag_signal_id=signal.signal_id,
        results=[{"text_md": "x", "start_node_id": "main:0", "end_node_id": "sub:0", "suggestion": "fix it"}],
    )
    assert result["status"] == "error"
    assert "不同 subagent" in result["error"]


async def test_reward_failure_requires_an_entry_suggestion() -> None:
    signal = _reward_signal()
    runtime = _marked_runtime()
    tools = _marked_tool_map(runtime, [signal])
    result = await _invoke(
        tools["submit_reward_failure"],
        diag_signal_id=signal.signal_id,
        results=[{"text_md": "cause", "start_node_id": "main:1", "suggestion": "  "}],
    )
    assert result["status"] == "error"
    assert "suggestion" in result["error"]
    assert signal.signal_id not in runtime.reward_attributions


async def test_reward_failure_rejects_other_sources() -> None:
    runtime = _marked_runtime()
    tools = _marked_tool_map(runtime)
    result = await _invoke(
        tools["submit_reward_failure"],
        diag_signal_id=_signals()[0].signal_id,
        results=[{"text_md": "x", "start_node_id": "main:0", "suggestion": "fix it"}],
    )
    assert result["status"] == "error"
    assert "reward-source" in result["error"]


async def test_skill_cause_requires_mark_two_step() -> None:
    signal = _signals()[0]
    runtime = _marked_runtime(inventory=[("s1", "tool")])
    tools = _marked_tool_map(runtime)
    unmarked = await _invoke(
        tools["submit_skill_evolution_cause"],
        diag_signal_id=signal.signal_id,
        results=[{"text_md": "x", "start_node_id": "main:1", "suggestion": "fix it"}],
    )
    assert unmarked["status"] == "error"
    marked = await _invoke(
        tools["mark_signal_topic"], diag_signal_id=signal.signal_id, category="skill", skill_key="s1"
    )
    assert marked["status"] == "recorded"
    result = await _invoke(
        tools["submit_skill_evolution_cause"],
        diag_signal_id=signal.signal_id,
        results=[{"text_md": "x", "start_node_id": "main:1", "suggestion": "fix it"}],
    )
    assert result["status"] == "recorded"
    assert signal.signal_id in runtime.skill_attributions


async def test_mark_signal_topic_validations_and_dual_marks() -> None:
    signal = _signals()[0]
    runtime = _marked_runtime(inventory=[("s1", "tool")])
    tools = _marked_tool_map(runtime)
    missing_key = await _invoke(tools["mark_signal_topic"], diag_signal_id=signal.signal_id, category="skill")
    assert missing_key["status"] == "error"
    outside = await _invoke(
        tools["mark_signal_topic"], diag_signal_id=signal.signal_id, category="skill", skill_key="nope"
    )
    assert outside["status"] == "error"
    assert (await _invoke(
        tools["mark_signal_topic"], diag_signal_id=signal.signal_id, category="skill", skill_key="s1"
    ))["status"] == "recorded"
    assert (await _invoke(
        tools["mark_signal_topic"], diag_signal_id=signal.signal_id, category="experience"
    ))["status"] == "recorded"
    assert set(runtime.marks[signal.signal_id]) == {"skill", "experience"}


async def test_mark_signal_topic_switch_gating() -> None:
    signal = _signals()[0]
    runtime = _marked_runtime(inventory=[("s1", "tool")])
    runtime.skill_evolution_enabled = False
    runtime.ttse_advice = False
    tools = _marked_tool_map(runtime)
    assert (await _invoke(
        tools["mark_signal_topic"], diag_signal_id=signal.signal_id, category="skill", skill_key="s1"
    ))["status"] == "error"
    assert (await _invoke(
        tools["mark_signal_topic"], diag_signal_id=signal.signal_id, category="experience"
    ))["status"] == "error"


async def test_mark_signal_invalid_and_invalid_rejects_cause() -> None:
    signal = _reward_signal()
    runtime = _marked_runtime()
    tools = _marked_tool_map(runtime, [signal])
    result = await _invoke(tools["mark_signal_invalid"], diag_signal_id=signal.signal_id)
    assert result["status"] == "marked"
    assert signal.signal_id in runtime.invalid_signals
    rejected = await _invoke(
        tools["submit_reward_failure"],
        diag_signal_id=signal.signal_id,
        results=[{"text_md": "x", "start_node_id": "main:1", "suggestion": "fix it"}],
    )
    assert rejected["status"] == "error"


async def test_skill_evolution_requires_inventory_and_mark() -> None:
    signal = _signals()[0]
    runtime = _marked_runtime(inventory=[("s1", "tool")])
    tools = _marked_tool_map(runtime)
    outside = await _invoke(tools["submit_skill_evolution"], skill_name_or_filename="nope", md_string="x")
    assert "error" in outside
    unmarked = await _invoke(tools["submit_skill_evolution"], skill_name_or_filename="s1", md_string="x")
    assert "error" in unmarked
    await _invoke(tools["mark_signal_topic"], diag_signal_id=signal.signal_id, category="skill", skill_key="s1")
    recorded = await _invoke(
        tools["submit_skill_evolution"], skill_name_or_filename="s1", md_string="advice", confidence=0.8
    )
    assert recorded["status"] == "recorded"
    assert runtime.skill_evolutions["s1"] == {"md_string": "advice", "confidence": 0.8}


async def test_experience_evolution_requires_mark_and_overwrites() -> None:
    signal = _signals()[0]
    runtime = _marked_runtime()
    tools = _marked_tool_map(runtime)
    assert "error" in await _invoke(tools["submit_experience_evolution"], outcome="partial")
    await _invoke(tools["mark_signal_topic"], diag_signal_id=signal.signal_id, category="experience")
    assert (await _invoke(
        tools["submit_experience_evolution"], outcome="partial", suggestions=["tip"]
    ))["status"] == "recorded"
    assert runtime.experience_evolution == {"outcome": "partial", "suggestions": ["tip"]}


async def test_trajectory_summary_overwrites() -> None:
    runtime = _marked_runtime()
    tools = _marked_tool_map(runtime)
    assert (await _invoke(tools["submit_trajectory_summary"], summary="v1"))["status"] == "recorded"
    assert (await _invoke(tools["submit_trajectory_summary"], summary="v2"))["status"] == "updated"
    assert runtime.trajectory_summary == "v2"


async def test_check_complete_pending_and_all_done() -> None:
    signal = _reward_signal()
    runtime = _marked_runtime()
    tools = _marked_tool_map(runtime, [signal])
    result = await _invoke(tools["check_attribution_complete"])
    assert result["all_done"] is False
    assert result["pending_reward_signal_ids"] == [signal.signal_id]
    assert result["missing_trajectory_summary"] is True
    await _invoke(
        tools["submit_reward_failure"],
        diag_signal_id=signal.signal_id,
        results=[{"text_md": "x", "start_node_id": "main:1", "suggestion": "fix it"}],
    )
    result = await _invoke(tools["check_attribution_complete"])
    assert result["all_done"] is False and result["missing_trajectory_summary"] is True
    await _invoke(tools["submit_trajectory_summary"], summary="done")
    result = await _invoke(tools["check_attribution_complete"])
    assert result["all_done"] is True


async def test_check_complete_skill_and_experience_obligations() -> None:
    signal = _signals()[0]
    runtime = _marked_runtime(inventory=[("s1", "tool")])
    tools = _marked_tool_map(runtime)
    await _invoke(tools["mark_signal_topic"], diag_signal_id=signal.signal_id, category="skill", skill_key="s1")
    await _invoke(tools["mark_signal_topic"], diag_signal_id=signal.signal_id, category="experience")
    await _invoke(tools["submit_trajectory_summary"], summary="done")
    state = await _invoke(tools["check_attribution_complete"])
    assert state["pending_skill_cause_signal_ids"] == [signal.signal_id]
    assert state["pending_experience_cause_signal_ids"] == [signal.signal_id]
    assert state["missing_skill_globals"] == ["s1"]
    assert state["missing_experience_evolution"] is True
    await _invoke(
        tools["submit_skill_evolution_cause"],
        diag_signal_id=signal.signal_id,
        results=[{"text_md": "x", "start_node_id": "main:1", "suggestion": "fix it"}],
    )
    await _invoke(
        tools["submit_experience_cause"],
        diag_signal_id=signal.signal_id,
        results=[{"text_md": "x", "start_node_id": "main:1", "suggestion": "fix it"}],
    )
    await _invoke(tools["submit_skill_evolution"], skill_name_or_filename="s1", md_string="advice")
    await _invoke(tools["submit_experience_evolution"], outcome="fail")
    assert (await _invoke(tools["check_attribution_complete"]))["all_done"] is True


async def test_check_complete_all_invalid_counts_as_done() -> None:
    runtime = _marked_runtime()
    tools = _marked_tool_map(runtime)
    await _invoke(tools["mark_signal_invalid"], diag_signal_id=_signals()[0].signal_id)
    await _invoke(tools["submit_trajectory_summary"], summary="done")
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
