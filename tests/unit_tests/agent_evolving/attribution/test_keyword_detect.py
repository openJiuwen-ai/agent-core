# coding: utf-8
"""Tests for process-signal diagnosis (rule and Jev modes) and signal models."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openjiuwen.agent_evolving.attribution.keyword_detect import (
    detect_rule_signals,
    run_keyword_diagnosis,
)
from openjiuwen.agent_evolving.attribution.model import (
    DiagnosticSignal,
    SignalSource,
    jev_tier,
    make_signal_id,
    reward_tier,
)
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode
from openjiuwen.agent_evolving.attribution.snapshot import SlicedNodes

pytestmark = pytest.mark.asyncio


def _node(
    index: int,
    role: str,
    content: str,
    name: str | None = None,
    subagent: str = "main",
    error: bool = False,
    kind: str = "main_agent",
) -> TrajectoryNode:
    return TrajectoryNode(
        node_id=f"{subagent}:{index}",
        subagent_key=subagent,
        display_name="M",
        kind=kind,
        role=role,
        content=content,
        name=name,
        start_ts_ns=index * 100,
        end_ts_ns=index * 100 + 50,
        error=error,
    )


def _user(index: int, text: str, subagent: str = "main", kind: str = "main_agent") -> TrajectoryNode:
    envelope = json.dumps({"type": "user input", "content": text}, ensure_ascii=False)
    return _node(index, "user", f"你收到一条消息：\n{envelope}", subagent=subagent, kind=kind)


def test_rule_signals_take_failed_tool_calls_only() -> None:
    nodes = {
        "main": [
            _node(0, "tool", "---\nname: planner\n万一某个环节出问题怎么办", name="skill_tool"),  # a document
            _node(1, "tool", "PASS 53 项断言全部通过，没有错误", name="bash"),  # test output
            _node(2, "tool", "Error: File has not been fully read yet.", name="write_file", error=True),
            _node(3, "tool", "Error: File has not been fully read yet.", name="write_file", error=True),  # repeat
        ]
    }
    signals = detect_rule_signals(nodes)
    assert [signal.node_ids[0] for signal in signals] == ["main:2"]
    assert signals[0].source == SignalSource.KEYWORD_TOOL
    assert signals[0].text.startswith("write_file: Error: File has not been fully read yet.")
    assert signals[0].tier == "uncertain"


def test_rule_signals_cap_failed_tool_calls() -> None:
    nodes = {"main": [_node(i, "tool", f"Error {i}", name="bash", error=True) for i in range(15)]}
    assert len(detect_rule_signals(nodes)) == 10


def test_rule_signals_take_the_users_follow_ups_not_the_first_request() -> None:
    nodes = {
        "main": [
            _user(0, "帮我安排周六"),
            _node(1, "user", "<system-reminder>有错误</system-reminder>"),  # not the user's words
            _user(2, "没有路径我怎么知道去哪里看文件"),  # no error word, still a follow-up
        ],
        "member": [
            _node(3, "user", "请把定稿写入 schedule.md，出错就重新做", subagent="member", kind="team_member"),
        ],
        "leader": [_user(4, "没有路径我怎么知道去哪里看文件", subagent="leader", kind="team_leader")],  # same text
    }
    signals = detect_rule_signals(nodes)
    assert [(signal.source, signal.node_ids[0]) for signal in signals] == [(SignalSource.KEYWORD_USER, "main:2")]
    assert signals[0].text == "没有路径我怎么知道去哪里看文件"


def test_rule_signals_know_the_first_request_from_outside_the_selection() -> None:
    sliced = SlicedNodes({"main": [_user(5, "用 agent team 重新做一份")]})
    sliced.user_requests = (_user(0, "帮我安排周六"), _user(5, "用 agent team 重新做一份"))
    signals = detect_rule_signals(sliced)
    assert [signal.node_ids[0] for signal in signals] == ["main:5"]


class _FakeJevClient:
    def __init__(self, probabilities: list[float], fail: bool = False) -> None:
        self._probabilities = probabilities
        self._fail = fail
        self.calls = 0

    async def system_one(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        del state, questions
        self.calls += 1
        if self._fail:
            raise ConnectionError("jev down")
        probability = self._probabilities[min(self.calls - 1, len(self._probabilities) - 1)]
        return {"is_diagnostic": {"noul": probability}}


async def test_jev_signals_threshold_and_tier() -> None:
    nodes = {"main": [_node(0, "tool", "boom", name="bash"), _node(1, "user", "meh")]}
    client = _FakeJevClient([0.95, 0.1])
    signals, meta = await run_keyword_diagnosis(nodes, "jev", jev_client=client)
    assert meta["mode"] == "jev"
    assert len(signals) == 1
    assert signals[0].tier == "weak"
    assert signals[0].confidence == 0.95


async def test_jev_unavailable_degrades_to_rule() -> None:
    nodes = {"main": [_node(0, "tool", "error happened", name="bash", error=True)]}
    client = _FakeJevClient([0.95], fail=True)
    signals, meta = await run_keyword_diagnosis(nodes, "jev", jev_client=client)
    assert meta["mode"] == "rule"
    assert meta["degraded"] == "ConnectionError"
    assert len(signals) == 1


async def test_jev_missing_client_degrades() -> None:
    nodes = {"main": [_node(0, "tool", "AssertionError", name="bash")]}
    signals, meta = await run_keyword_diagnosis(nodes, "jev", jev_client=None)
    assert meta["mode"] == "rule"
    assert meta["degraded"] == "jev_client_missing"


def test_tier_mappings() -> None:
    assert jev_tier(95.0) == "weak"
    assert jev_tier(90.0) == "weak"
    assert jev_tier(80.0) == "uncertain"
    assert jev_tier(70.0) == "uncertain"
    assert jev_tier(50.0) == "fair"
    assert reward_tier(100.0) == "perfect"
    assert reward_tier(90.0) == "perfect"
    assert reward_tier(89.0) == "fair"
    assert reward_tier(75.0) == "fair"
    assert reward_tier(74.0) == "weak"


def test_signal_id_stable_and_roundtrip() -> None:
    signal = DiagnosticSignal.create(
        "same text",
        source=SignalSource.KEYWORD_TOOL,
        node_ids=["main:1"],
        ts_ns=123,
        tier="uncertain",
    )
    again = DiagnosticSignal.create("same text", source=SignalSource.KEYWORD_TOOL, ts_ns=123)
    assert signal.signal_id == again.signal_id == make_signal_id("same text", SignalSource.KEYWORD_TOOL, 123)
    assert signal.signal_id.startswith("diag-")
    assert len(signal.signal_id) == len("diag-") + 24
    restored = DiagnosticSignal.from_dict(signal.to_dict())
    assert restored.to_dict() == signal.to_dict()


@pytest.mark.parametrize("value,expected", [(100.0, "perfect"), (89.9, "fair"), (0.0, "weak")])
def test_reward_tier_bands(value: float, expected: str) -> None:
    assert reward_tier(value) == expected
