# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The task comes from the user's own messages, whatever part of the trajectory is selected."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openjiuwen.agent_evolving.attribution.model import TaskParseResult
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode, user_request_nodes, user_request_text
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
    nodes[LEADER].append(_node(LEADER, 9, "user", _envelope("帮我安排周六：外婆 5 点以后有空"), 70))
    texts = [user_request_text(n) for n in user_request_nodes(nodes)]
    assert texts == ["帮我安排周六：外婆 5 点以后有空", "文件路径在哪里？", "再做一个别的"]
    assert [user_request_text(n) for n in user_request_nodes(nodes, until_ns=100)][-1] == "文件路径在哪里？"


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


def test_digest_leads_with_the_users_words_and_a_plain_dict_still_works() -> None:
    sliced = slice_nodes_by_range(_session(), [(MEMBER, 30, 50)])  # only a member is selected
    digest = build_task_parse_digest(sliced, [], "zh", user_request_of(sliced))
    assert digest.startswith("## 用户本人发出的消息")
    assert digest.index("外婆 5 点以后有空") < digest.index("把定稿写入 schedule.md")
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
    assert result.user_request == "帮我安排周六：外婆 5 点以后有空\n\n文件路径在哪里？\n\n再做一个别的"
    assert result.run_meta["user_requests"] == 3
    assert "## 用户本人发出的消息" in llm.messages[1]["content"]
