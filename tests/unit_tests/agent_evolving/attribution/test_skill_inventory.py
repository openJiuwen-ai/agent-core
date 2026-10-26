# coding: utf-8
"""Tests for the rule-only Skill usage inventory and its prompt hints."""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.agent_evolving.attribution.model import DiagnosticSignal, SignalSource
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode
from openjiuwen.agent_evolving.attribution.skill_inventory import detect_skill_usage, rule_premarks


def _node(
    index: int,
    *,
    role: str = "assistant",
    tool_calls: list[dict[str, Any]] | None = None,
    start_ts_ns: int | None = None,
) -> TrajectoryNode:
    return TrajectoryNode(
        node_id=f"main:{index}",
        subagent_key="main",
        display_name="Main",
        kind="main_agent",
        role=role,
        tool_calls=tool_calls or [],
        start_ts_ns=index * 100 if start_ts_ns is None else start_ts_ns,
        end_ts_ns=index * 100 + 50,
    )


def _nodes() -> dict[str, list[TrajectoryNode]]:
    return {
        "main": [
            _node(0, role="user"),
            _node(1, tool_calls=[{"name": "skill_tool", "arguments": json.dumps({"skill_name": "report"})}]),
            _node(2, role="user"),
            _node(
                3,
                tool_calls=[
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"file_path": "/skills/writing/SKILL.md"}),
                        }
                    }
                ],
            ),
            _node(4, tool_calls=[{"name": "list_skills", "arguments": "{}"}]),
        ]
    }


def test_detect_skill_usage_tool_and_skill_md() -> None:
    usages = detect_skill_usage(_nodes())
    assert [(usage.skill_key, usage.kind, usage.first_node_id) for usage in usages] == [
        ("/skills/writing/SKILL.md", "file", "main:3"),
        ("report", "tool", "main:1"),
    ]


def test_detect_skill_usage_earliest_node_and_dedup() -> None:
    nodes = {
        "main": [
            _node(5, tool_calls=[{"name": "skill_tool", "arguments": {"skill_name": "report"}}], start_ts_ns=500),
            _node(1, tool_calls=[{"name": "skill_tool", "arguments": {"skill_name": "report"}}], start_ts_ns=100),
        ]
    }
    usages = detect_skill_usage(nodes)
    assert usages[0].first_node_id == "main:1"


def test_detect_skill_usage_ignores_listing_and_empty() -> None:
    assert detect_skill_usage({"main": [_node(0, tool_calls=[{"name": "list_skill", "arguments": "{}"}])]}) == []
    assert detect_skill_usage({}) == []


def test_rule_premarks_are_scoped_to_the_round() -> None:
    nodes = _nodes()
    signal = DiagnosticSignal.create("oops", source=SignalSource.KEYWORD_TOOL, node_ids=["main:3"])
    premarks = rule_premarks([signal], nodes)
    # main:2 starts the round holding main:3: only the skill.md read applies
    assert premarks == {signal.signal_id: ["/skills/writing/SKILL.md"]}


def test_rule_premarks_without_anchor_are_empty() -> None:
    signal = DiagnosticSignal.create("oops", source=SignalSource.KEYWORD_TOOL, node_ids=[])
    assert rule_premarks([signal], _nodes()) == {}
