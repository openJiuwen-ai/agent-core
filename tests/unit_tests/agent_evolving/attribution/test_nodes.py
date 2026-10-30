# coding: utf-8
"""Tests for attribution trajectory nodes: language, truncation, tiers, search."""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.agent_evolving.attribution.nodes import (
    TrajectoryNode,
    detect_language,
    is_error_node,
    render_node_detail,
    render_node_field,
    render_overview,
    render_summary,
    search_keyword,
)


def _node(index: int, **kwargs: Any) -> TrajectoryNode:
    defaults = {
        "node_id": f"main:{index}",
        "subagent_key": "main",
        "display_name": "Main Agent",
        "kind": "main_agent",
        "role": "user",
        "start_ts_ns": 1000 + index,
        "end_ts_ns": 1100 + index,
    }
    defaults.update(kwargs)
    return TrajectoryNode(**defaults)


def test_detect_language_cjk_threshold() -> None:
    assert detect_language("帮我实现一个文件分析工具，看看哪里出了问题") == "zh"
    assert detect_language("implement a file analysis tool please") == "en"
    assert detect_language("") == "en"
    assert detect_language("x" * 100 + "中") == "en"
    assert detect_language("x" * 20 + "中文") == "zh"


def test_render_summary_zh_truncates_50_and_exempts_file_args() -> None:
    nodes = [
        _node(
            0,
            role="assistant",
            tool_calls=[
                {
                    "name": "write_file",
                    "arguments": json.dumps({"file_path": "/workspace/a/b/c/report.md", "content": "长" * 200}),
                }
            ],
        )
    ]
    summary = render_summary(nodes, "zh")
    row = summary["nodes"][0]
    arguments = row["tool_calls"][0]["arguments"]
    assert arguments["file_path"] == "/workspace/a/b/c/report.md"
    assert len(arguments["content"]) == 50
    assert summary["level"] == "summary"
    assert summary["node_count"] == 1


def test_render_summary_en_truncates_100() -> None:
    nodes = [_node(0, role="user", content="a" * 300)]
    summary = render_summary(nodes, "en")
    assert len(summary["nodes"][0]["content"]) == 100


def test_render_node_detail_caps_fields_at_1000() -> None:
    nodes = [_node(0, role="user", content="b" * 5000, message_id="m1", trace_id="t1", span_id="s1")]
    detail = render_node_detail(nodes[0])
    assert detail["content"].endswith("[truncated]")
    assert len(detail["content"]) == 1000 + len("[truncated]")
    assert detail["message_id"] == "m1"
    assert detail["start_ts_ns"] == 1000
    assert detail["subagent"]["id"] == "main"


def test_render_node_detail_exposes_field_lengths() -> None:
    nodes = [
        _node(
            0,
            role="user",
            content="b" * 5000,
            tool_calls=[{"name": "write_file", "arguments": json.dumps({"content": "x" * 3000})}],
        )
    ]
    detail = render_node_detail(nodes[0])
    assert detail["field_lengths"]["content"] == 5000
    assert detail["field_lengths"]["tool_calls"] == len(json.dumps(nodes[0].tool_calls, ensure_ascii=False))
    short = render_node_detail(_node(1, role="user", content="hi"))
    assert short["field_lengths"] == {"content": 2}


def test_render_node_field_content_cursor_windows() -> None:
    node = _node(0, role="assistant", content="abcdefghij")
    first = render_node_field(node, "content", 0, 4)
    assert first == {"text": "abcd", "start": 0, "end": 4, "total_chars": 10, "has_more": True}
    second = render_node_field(node, "content", first["end"], 4)
    assert second["text"] == "efgh"
    assert second["has_more"] is True
    last = render_node_field(node, "content", second["end"], 100)
    assert last == {"text": "ij", "start": 8, "end": 10, "total_chars": 10, "has_more": False}


def test_render_node_field_tool_calls_window() -> None:
    tool_calls = [{"name": "write_file", "arguments": json.dumps({"content": "x" * 5000})}]
    node = _node(0, role="assistant", tool_calls=tool_calls)
    raw = json.dumps(tool_calls, ensure_ascii=False)
    window = render_node_field(node, "tool_calls", 10, 20)
    assert window["text"] == raw[10:30]
    assert window["total_chars"] == len(raw)
    assert window["has_more"] is True


def test_render_node_field_graceful_boundary() -> None:
    node = _node(0, role="assistant", content="abc")
    assert render_node_field(node, "content", 3, 10) == {
        "text": "",
        "start": 3,
        "end": 3,
        "total_chars": 3,
        "has_more": False,
    }
    assert render_node_field(node, "content", 100, 10)["has_more"] is False


def test_render_node_field_empty_source() -> None:
    node = _node(0, role="assistant")
    window = render_node_field(node, "content", 0, 10)
    assert window == {"text": "", "start": 0, "end": 0, "total_chars": 0, "has_more": False}
    assert render_node_field(node, "tool_calls", 0, 10)["total_chars"] == 0


def test_is_error_node_rules() -> None:
    assert is_error_node(_node(0, role="tool", content="Traceback (most recent call last)"))
    assert is_error_node(_node(1, role="tool", content="AssertionError: bad"))
    assert is_error_node(_node(2, role="tool", content="发生了错误"))
    assert is_error_node(_node(3, role="tool", content="ok", error=True))
    assert not is_error_node(_node(4, role="tool", content="all good"))
    assert not is_error_node(_node(5, role="user", content="Traceback happened?"))


def test_render_overview_anchors_and_chunks() -> None:
    nodes = [
        _node(index, role=("user" if index % 50 == 0 else "tool"), content="", name="bash") for index in range(120)
    ]
    nodes[10] = _node(10, role="tool", content="AssertionError", name="python")
    nodes[110] = _node(110, role="tool", content="boom 错误", name="python")
    overview = render_overview(nodes)
    assert overview["level"] == "overview"
    assert overview["node_count"] == 120
    assert overview["user_node_ids"] == ["main:0", "main:50", "main:100"]
    assert "main:10" in overview["error_node_ids"]
    assert "main:110" in overview["error_node_ids"]
    assert len(overview["chunks"]) == 3
    assert overview["chunks"][0]["node_range"] == ["main:0", "main:49"]
    kinds = [marker["kind"] for marker in overview["chunks"][0]["markers"]]
    assert "error" in kinds
    assert overview["landmarks"]["first_nodes"][0]["node_id"] == "main:0"
    assert overview["landmarks"]["last_nodes"][-1]["node_id"] == "main:119"
    assert "drill_down" in overview


def test_render_overview_error_cap_note() -> None:
    nodes = [_node(index, role="tool", content="AssertionError", name="python") for index in range(25)]
    overview = render_overview(nodes)
    assert len(overview["error_node_ids"]) == 20
    assert "25" in overview["note"]


def test_search_keyword_details_and_counts() -> None:
    nodes = {
        "main": [_node(0, content="the apple pie is bad"), _node(1, content="banana split")],
        "sub": [_node(0, subagent_key="sub", content="an apple a day")],
    }
    result = search_keyword(nodes, "apple")
    assert result["total"] == 2
    assert set(result["results"]) == {"main", "sub"}
    assert result["results"]["main"][0]["node_id"] == "main:0"
    assert "apple" in result["results"]["main"][0]["context"]


def test_search_keyword_over_threshold_counts_only() -> None:
    nodes = {"main": [_node(index, content=f"error number {index}") for index in range(40)]}
    result = search_keyword(nodes, "error")
    assert result["counts"][0]["count"] == 40
    assert "note" in result
    assert "results" not in result


def test_search_keyword_invalid_pattern() -> None:
    nodes = {"main": [_node(0, content="hi")]}
    assert "error" in search_keyword(nodes, "([bad")
