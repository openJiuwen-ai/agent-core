# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The task comes from the user's own messages, whatever part of the trajectory is selected."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from openjiuwen.agent_evolving.attribution.model import TaskParseResult
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode, classify_input, user_request_nodes, user_request_text
from openjiuwen.agent_evolving.attribution.snapshot import SlicedNodes, slice_nodes_by_range
from openjiuwen.agent_evolving.attribution.task_parse import build_task_parse_digest, run_task_parse, user_request_of

pytestmark = pytest.mark.asyncio

LEADER = "team-member:s:t:team-leader"
MEMBER = "team-member:s:t:writer"


def _envelope(text: str, prefix: str = '<team-clock now="12:00"/>\n你收到一条消息：\n') -> str:
    payload = {"source": "web", "content": text, "type": "user input", "origin_kind": "external_user_authored"}
    return prefix + json.dumps(payload, ensure_ascii=False)


def _node(key: str, seq: int, role: str, content: str, ts: int, kind: str = "team_leader") -> TrajectoryNode:
    return TrajectoryNode(
        node_id=f"{key}:{seq}",
        subagent_key=key,
        display_name=key,
        kind=kind,
        role=role,
        content=content,
        start_ts_ns=ts,
        end_ts_ns=ts + 1,
    )


def _session() -> dict[str, list[TrajectoryNode]]:
    return {
        LEADER: [
            _node(LEADER, 1, "user", "<system-reminder>\n动态上下文</system-reminder>", 10),
            _node(LEADER, 2, "user", _envelope("帮我安排周六：外婆 5 点以后有空"), 20),
            _node(LEADER, 3, "assistant", "好的，我来组建团队", 30),
            _node(LEADER, 4, "user", '<team-event kind="all-done">刚完成：…</team-event>', 40),
            _node(LEADER, 5, "user", _envelope("文件路径在哪里？"), 50),
            _node(LEADER, 6, "assistant", "已完成", 60),
            _node(LEADER, 7, "user", _envelope("再做一个别的"), 900),
        ],
        MEMBER: [
            _node(MEMBER, 1, "user", _envelope("把定稿写入 schedule.md 并回读校验"), 35, kind="team_member"),
            _node(MEMBER, 2, "assistant", "已写入 schedule.md", 45, kind="team_member"),
        ],
    }


def test_user_request_text_reads_only_what_the_user_wrote() -> None:
    nodes = _session()
    assert user_request_text(nodes[LEADER][1]) == "帮我安排周六：外婆 5 点以后有空"
    assert user_request_text(nodes[LEADER][0]) is None  # system reminder
    assert user_request_text(nodes[LEADER][3]) is None  # team event
    assert user_request_text(nodes[LEADER][2]) is None  # assistant row
    assert user_request_text(nodes[MEMBER][0]) is None  # a member's dispatch is never the user's
    image = _node("main", 9, "user", "[{'type': 'text', 'text': 'Image loaded'}]", 5, kind="main_agent")
    assert user_request_text(image) is None  # a tool result replayed as a user turn
    plain = _node("main", 1, "user", "做一个贪吃蛇小游戏", 5, kind="main_agent")
    assert user_request_text(plain) == "做一个贪吃蛇小游戏"
    other = _node(LEADER, 8, "user", '你收到一条消息：\n{"source": "team", "content": "x", "type": "notice"}', 5)
    assert user_request_text(other) is None  # an envelope not authored by the user


def test_user_request_nodes_are_ordered_deduplicated_and_cut_at_a_time() -> None:
    nodes = _session()
    nodes[LEADER][1] = replace(nodes[LEADER][1], turn_id="t1")
    nodes[LEADER].append(replace(_node(LEADER, 9, "user", _envelope("帮我安排周六：外婆 5 点以后有空"), 70), turn_id="t1"))
    texts = [user_request_text(n) for n in user_request_nodes(nodes)]
    assert texts == ["帮我安排周六：外婆 5 点以后有空", "文件路径在哪里？", "再做一个别的"]
    assert [user_request_text(n) for n in user_request_nodes(nodes, until_ns=100)][-1] == "文件路径在哪里？"


@pytest.mark.parametrize("content,expected", [
    ("user_inputs={} raw_inputs=None interrupt_answers={'approval': False}", ("runtime", "approval_response")),
    ("raw_inputs=None interrupt_answers={'approval': True}", ("runtime", "approval_response")),
    ("<system-reminder>runtime status</system-reminder>", ("system", "system_context")),
    ('[STEERING] <team-clock now="12:00"/>\n<team-inbound from="leader">Please finish</team-inbound>', ("agent", "message")),
    ('<team-clock now="12:00"/><team-inbound from="worker">Teammate tool approval request</team-inbound>', ("agent", "approval_request")),
    ('<team-event kind="task-board">Task assigned</team-event>', ("system", "team_event")),
    ("[STEERING] Stop adding tests", ("system", "team_event")),
])
def test_runtime_and_team_inputs_are_not_user_requests(content, expected):
    for kind in ("main_agent", "team_leader", "team_member"):
        node = _node("A", 0, "user", content, 10, kind=kind)
        assert classify_input(node) == expected
        assert user_request_text(node) is None
        assert user_request_nodes({"A": [node]}) == []
        assert node.content == content and node.role == "user"


def test_explicit_origin_overrides_type_and_user_quoted_protocol_is_not_a_runtime_message():
    for origin in ("internal", [], {}):
        content = json.dumps({"type": "user input", "origin_kind": origin, "content": "Internal replay"})
        assert user_request_text(_node("main", 0, "user", content, 10, kind="main_agent")) is None
    quoted = _envelope('解释 <system-reminder> 和 user_inputs={} 的含义', prefix="你收到一条消息：\n")
    assert user_request_text(_node("main", 0, "user", quoted, 10, kind="main_agent")) == '解释 <system-reminder> 和 user_inputs={} 的含义'
    steered = _node(LEADER, 9, "user", "[STEERING] " + _envelope("A new user instruction"), 10)
    assert user_request_text(steered) == "A new user instruction"
    broken = _node("main", 1, "user", '你收到一条消息：\n{"content":"incomplete', 10, kind="main_agent")
    assert classify_input(broken) == ("unknown", "other")


def test_prefer_explicit_envelope_but_keep_repeated_request_in_another_turn():
    plain = replace(_node("main", 2, "user", "Make a game", 10, kind="main_agent"), turn_id="t1")
    wrapped = replace(_node("main", 4, "user", _envelope("Make a game"), 11, kind="main_agent"), turn_id="t1")
    repeated = replace(plain, node_id="main:12", turn_id="t2", start_ts_ns=30)
    groups = {"main": [plain, wrapped, repeated]}
    assert [n.node_id for n in user_request_nodes(groups)] == ["main:4", "main:12"]
    assert [n.node_id for n in user_request_nodes(groups, until_ns=10)] == ["main:2"]
    assert len(groups["main"]) == 3 and groups["main"][1].content == _envelope("Make a game")


def test_missing_turn_identity_does_not_merge_requests_at_different_times():
    one = _node("main", 1, "user", "Again", 10, kind="main_agent")
    two = replace(one, node_id="main:2", start_ts_ns=20)
    assert user_request_nodes({"main": [one, two]}) == [one, two]
    unknown = replace(one, start_ts_ns=0)
    assert len(user_request_nodes({"main": [unknown, replace(unknown, node_id="main:3")]})) == 2


def test_slicing_a_window_without_the_start_still_carries_the_users_request() -> None:
    sliced = slice_nodes_by_range(_session(), [(LEADER, 55, 65), (MEMBER, 30, 50)])
    assert isinstance(sliced, SlicedNodes) and isinstance(sliced, dict)
    assert [n.node_id for n in sliced[LEADER]] == [f"{LEADER}:6"]  # the window itself is unchanged
    assert [user_request_text(n) for n in sliced.user_requests] == [
        "帮我安排周六：外婆 5 点以后有空",
        "文件路径在哪里？",
        "再做一个别的",
    ]  # the whole session: later messages tell which round last delivered the result
    assert user_request_of(sliced)[:2] == [
        (f"{LEADER}:2", "帮我安排周六：外婆 5 点以后有空"),
        (f"{LEADER}:5", "文件路径在哪里？"),
    ]


def test_digest_does_not_import_user_text_the_tree_does_not_carry() -> None:
    sliced = slice_nodes_by_range(_session(), [(MEMBER, 30, 50)])  # only a member is selected
    digest = build_task_parse_digest(sliced, [], "zh", None)
    assert digest.startswith("## 分层轨迹")
    assert "外婆 5 点以后有空" not in digest
    assert "## 主体末尾原文" in digest
    plain = {LEADER: _session()[LEADER][:3]}  # a caller that does not slice
    assert user_request_of(plain) == [(f"{LEADER}:2", "帮我安排周六：外婆 5 点以后有空")]


class _Llm:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def invoke(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **kwargs: Any) -> Any:
        del tools, kwargs
        self.messages = messages
        return {
            "tool_calls": [
                {"name": "report_task_description", "arguments": json.dumps({"task": "安排周六"}, ensure_ascii=False)}
            ]
        }


async def test_task_parse_returns_the_users_words_beside_the_description() -> None:
    llm = _Llm()
    sliced = slice_nodes_by_range(_session(), [(MEMBER, 30, 50)])
    result = await run_task_parse(sliced, "zh", llm)
    assert isinstance(result, TaskParseResult)
    assert result.task_description == "安排周六"
    assert result.user_request == "再做一个别的"  # no source ids: only the message that starts the delivery round
    assert result.run_meta["user_requests"] == 3
    assert result.run_meta["task_sources"] == 1
    assert "## 分层轨迹" in llm.messages[1]["content"]
    assert "外婆 5 点以后有空" not in llm.messages[1]["content"]
