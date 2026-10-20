# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deliverables are reviewed only when the selection holds the leader in the round that last delivered them."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openjiuwen.agent_evolving.attribution.model import DiagnosticSignal, SignalSource, TaskParseResult
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode
from openjiuwen.agent_evolving.attribution.reward_stage.reward_adapter import (
    final_reply,
    review_task,
    to_reward_payload,
)
from openjiuwen.agent_evolving.attribution.run import NodeBackedRuntime, run_attribution, run_diagnosis
from openjiuwen.agent_evolving.attribution.snapshot import slice_nodes_by_range
from openjiuwen.agent_evolving.attribution.task_parse import delivery_round, last_writes, run_task_parse
from openjiuwen.agent_evolving.attribution.tools import build_attribution_tools

pytestmark = pytest.mark.asyncio

LEADER = "team-member:s:t:team-leader"
MEMBER = "team-member:s:t:writer"
LONG = "，时间表、购物清单和给外婆的消息都已经整理好，路上的时间也都算进去了，请查看文件"


def _node(
    key: str,
    seq: int,
    ts: int,
    role: str = "assistant",
    content: str = "",
    calls: list[dict[str, Any]] | None = None,
    kind: str = "team_leader",
) -> TrajectoryNode:
    return TrajectoryNode(
        node_id=f"{key}:{seq}",
        subagent_key=key,
        display_name=key,
        kind=kind,
        role=role,
        content=content or None,
        tool_calls=calls or [],
        start_ts_ns=ts,
        end_ts_ns=ts + 1,
    )


def _said(seq: int, ts: int, text: str) -> TrajectoryNode:
    envelope = json.dumps({"type": "user input", "content": text}, ensure_ascii=False)
    return _node(LEADER, seq, ts, "user", f"你收到一条消息：\n{envelope}")


def _write(path: str) -> list[dict[str, Any]]:
    return [{"name": "write_file", "arguments": json.dumps({"file_path": path, "content": "x"})}]


def _send(path: str) -> list[dict[str, Any]]:
    return [{"name": "send_file_to_user", "arguments": json.dumps({"abs_file_path_list": [path]})}]


def _session() -> dict[str, list[TrajectoryNode]]:
    """Four rounds: a first version, a question about the path, a team redo, and a thank-you."""
    return {
        LEADER: [
            _said(1, 10, "排周六，写 schedule.md"),
            _node(LEADER, 2, 20, calls=_write("/w/schedule.md")),
            _node(LEADER, 3, 25, content="schedule.md 写好了" + LONG),
            _said(4, 30, "没有路径我怎么知道去哪里看文件"),
            _node(LEADER, 5, 35, content="路径是 /w/schedule.md"),
            _said(6, 40, "用 agent team 重新做一份"),
            _node(LEADER, 7, 55, calls=_send("/w/schedule-v2.md")),
            _node(LEADER, 8, 60, content="重做好了：schedule-v2.md" + LONG),
            _said(9, 70, "谢谢"),
            _node(LEADER, 10, 72, content="不客气"),
        ],
        MEMBER: [
            _node(MEMBER, 1, 45, "user", "请重做时间表", kind="team_member"),
            _node(MEMBER, 2, 50, calls=_write("/w/schedule-v2.md"), kind="team_member"),
            _node(MEMBER, 3, 52, calls=_write("/w/test.js"), kind="team_member"),
            _node(MEMBER, 4, 53, content="写好了，交给 leader", kind="team_member"),
        ],
    }


class _Llm:
    def __init__(self, round_id: str | None = None, files: list[str] | None = None, **extra: Any) -> None:
        self._round = round_id
        self._files = files if files is not None else ["/w/schedule.md", "/w/schedule-v2.md"]
        self._extra = extra
        self.digest = ""

    async def invoke(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **kwargs: Any) -> Any:
        del tools, kwargs
        self.digest = messages[1]["content"]
        calls = [
            {"name": "report_task_description", "arguments": json.dumps({"task": "排周六"})},
            {"name": "report_message_artifacts", "arguments": json.dumps({"node_ids": [f"{LEADER}:3"]})},
            {"name": "report_file_artifacts", "arguments": json.dumps({"paths": self._files})},
        ]
        if self._round is not None:
            calls.append({"name": "report_delivery_round", "arguments": json.dumps({"node_id": self._round})})
        for name, arguments in self._extra.items():
            calls.append({"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)})
        return {"tool_calls": calls}


def test_last_writes_follow_time_across_subjects_and_skip_sending() -> None:
    assert last_writes(_session()) == {
        "/w/schedule.md": f"{LEADER}:2",
        "/w/schedule-v2.md": f"{MEMBER}:2",  # sending it at 55 is not a write
        "/w/test.js": f"{MEMBER}:3",
    }


def test_the_delivery_round_skips_a_closing_thank_you() -> None:
    whole = _session()
    assert delivery_round(whole).start.node_id == f"{LEADER}:6"  # "谢谢" wrote nothing and got "不客气"
    assert delivery_round(whole, named=f"{LEADER}:4").start.node_id == f"{LEADER}:6"  # a file sent decides
    assert delivery_round(whole, named=f"{LEADER}:9").start.node_id == f"{LEADER}:6"  # it delivered nothing
    assert delivery_round({LEADER: [_node(LEADER, 1, 10, content="hi")]}) is None  # no user message


async def test_the_leader_in_the_delivery_round_reviews_its_final_deliverables() -> None:
    sliced = slice_nodes_by_range(_session(), [(LEADER, 38, 62)])  # the redo up to its last reply
    parse = await run_task_parse(sliced, "zh", _Llm())
    assert parse.delivery_round == f"{LEADER}:6" and parse.delivery_round_selected is True
    assert parse.message_artifacts == [(f"{LEADER}:8", "重做好了：schedule-v2.md" + LONG)]  # the round's last reply
    assert parse.file_artifacts == ["/w/schedule-v2.md"]  # schedule.md is the first round's version
    assert "/w/schedule-v2.md" in [path for path, _, _ in parse.file_candidates]  # from the whole session


def test_a_named_round_wins_over_a_later_side_question() -> None:
    whole = _session()
    whole[LEADER][-2:] = [
        _said(9, 70, "顺便问下电影院在哪"),
        _node(LEADER, 10, 72, content="城东万达影城" + LONG + LONG),
    ]
    assert delivery_round(whole).start.node_id == f"{LEADER}:6"  # the redo still sent a file
    whole[LEADER] = [node for node in whole[LEADER] if node.node_id != f"{LEADER}:7"]
    whole[MEMBER] = whole[MEMBER][:1]  # now no round after the first wrote or sent a file
    assert delivery_round(whole).start.node_id == f"{LEADER}:1"
    whole[LEADER][1:2] = []  # nor did the first: the rule takes the last long reply, the side question
    assert delivery_round(whole).start.node_id == f"{LEADER}:9"
    assert delivery_round(whole, named=f"{LEADER}:6").start.node_id == f"{LEADER}:6"  # the model knows better


async def test_only_the_leader_at_work_in_the_round_counts() -> None:
    sliced = slice_nodes_by_range(_session(), [(LEADER, 36, 41)])  # the redo request and nothing more
    assert (await run_task_parse(sliced, "zh", _Llm())).delivery_round_selected is False  # only the user's message
    sliced = slice_nodes_by_range(_session(), [(LEADER, 30, 36)])  # the leader answering the path question
    assert (await run_task_parse(sliced, "zh", _Llm(round_id=f"{LEADER}:4"))).delivery_round_selected is False


async def test_the_leader_starting_the_round_is_not_the_delivery() -> None:
    nodes = _session()
    nodes[LEADER].append(_node(LEADER, 11, 42, calls=[{"name": "create_task", "arguments": "{}"}]))
    nodes[LEADER].sort(key=lambda node: node.start_ts_ns)
    sliced = slice_nodes_by_range(nodes, [(LEADER, 38, 54)])  # the leader dispatching, not handing over
    parse = await run_task_parse(sliced, "zh", _Llm())
    assert parse.delivery_round_selected is False and parse.file_artifacts == []


async def test_sending_the_file_before_the_last_reply_is_not_the_hand_over() -> None:
    sliced = slice_nodes_by_range(_session(), [(LEADER, 38, 58)])  # the file sent at 55, not the reply at 60
    parse = await run_task_parse(sliced, "zh", _Llm())
    assert parse.delivery_round_selected is False and parse.file_artifacts == []


async def test_a_single_agent_hands_over_with_its_last_step() -> None:
    main = "main"
    nodes = {
        main: [
            _node(main, 1, 10, "user", "写 report.md", kind="main_agent"),
            _node(main, 2, 20, calls=_write("/w/report.md"), kind="main_agent"),
            _node(main, 3, 30, content="report.md 写好了" + LONG, kind="main_agent"),
        ]
    }
    sliced = slice_nodes_by_range(nodes, [(main, 15, 25)])  # the write, not the reply after it
    parse = await run_task_parse(sliced, "zh", _Llm(files=["/w/report.md"]))
    assert parse.delivery_round_selected is False
    nodes[main] = nodes[main][:2]  # it wrote the file and stopped without a reply: the write is the last step
    sliced = slice_nodes_by_range(nodes, [(main, 15, 25)])
    parse = await run_task_parse(sliced, "zh", _Llm(files=["/w/report.md"]))
    assert parse.delivery_round_selected is True and parse.file_artifacts == ["/w/report.md"]


async def test_a_member_in_the_delivery_round_is_a_look_at_the_process() -> None:
    sliced = slice_nodes_by_range(_session(), [(MEMBER, 40, 60), (LEADER, 0, 36)])
    parse = await run_task_parse(sliced, "zh", _Llm())
    assert parse.delivery_round_selected is False
    assert parse.message_artifacts == [] and parse.file_artifacts == []


async def test_the_model_names_the_round() -> None:
    sliced = slice_nodes_by_range(_session(), [(LEADER, 0, 26)])  # only the first round
    parse = await run_task_parse(sliced, "zh", _Llm(round_id=f"{LEADER}:1"))  # a file sent later decides
    assert parse.run_meta["delivery_round_named"] == f"{LEADER}:1"
    assert parse.delivery_round_selected is False and parse.file_artifacts == []
    whole = slice_nodes_by_range(_session(), [(LEADER, 0, 100), (MEMBER, 0, 100)])
    parse = await run_task_parse(whole, "zh", _Llm(round_id=f"{LEADER}:6"))
    assert parse.delivery_round_selected is True and parse.file_artifacts == ["/w/schedule-v2.md"]


async def test_requirements_are_parsed() -> None:
    whole = slice_nodes_by_range(_session(), [(LEADER, 0, 100)])
    items = [{"text": "写 schedule.md", "corrected": False}, {"text": "回复里给出文件的绝对路径", "corrected": True}]
    parse = await run_task_parse(whole, "zh", _Llm(report_requirements={"items": items + ["写 schedule.md"]}))
    assert parse.requirements == items  # deduplicated; a bare string counts as an uncorrected item
    assert parse.run_meta["requirements"] == 2


def test_review_task_puts_the_note_first_and_corrections_at_the_top_of_the_list() -> None:
    parse = TaskParseResult(
        task_description="排周六",
        message_artifacts=[],
        file_artifacts=[],
        file_candidates=[],
        user_request="排周六，写 schedule.md\n\n没有路径我怎么知道去哪里看文件",
        requirements=[{"text": "写 schedule.md", "corrected": False}, {"text": "给出绝对路径", "corrected": True}],
    )
    task = review_task(parse, "外婆其实 5 点半才有空", "zh")
    sections = task.split("\n\n")
    assert sections[0].startswith("## 用户补充（最高优先级") and sections[0].endswith("外婆其实 5 点半才有空")
    assert sections[1].splitlines()[1:] == ["1. 给出绝对路径（用户纠正过）", "2. 写 schedule.md"]
    assert sections[2].startswith("## 用户原话")
    bare = TaskParseResult(task_description="排周六", message_artifacts=[], file_artifacts=[], file_candidates=[])
    assert review_task(bare, "", "zh") == "排周六"  # nothing to add: the task as before


def test_the_reviewers_note_comes_first_within_a_tier() -> None:
    item = {"category_id": "plan", "category_name": "日程", "type": "floor", "status": "unmet"}
    payload = to_reward_payload(
        {
            "total": {"score": 40, "summary": "-"},
            "sub_category_items": [
                {**item, "rubric": "购物顺路"},
                {**item, "rubric": "外婆 5 点半后到", "from_user_note": True},
            ],
        }
    )
    assert [d["requirement"] for d in payload["dimensions"]] == [
        "[要求] 外婆 5 点半后到（用户补充）",
        "[要求] 购物顺路",
    ]


def test_no_reply_is_made_up_once_the_round_is_decided() -> None:
    parse = TaskParseResult(
        task_description="排周六", message_artifacts=[], file_artifacts=[], file_candidates=[], delivery_round="x"
    )
    assert final_reply(parse, _session()) == ""


async def test_no_signal_is_left_to_the_host() -> None:
    parse = TaskParseResult(task_description="排周六", message_artifacts=[], file_artifacts=[], file_candidates=[])
    nodes = {LEADER: [_node(LEADER, 2, 20, content="团队已组建")]}
    result = await run_diagnosis(
        run_id="r", language="zh", nodes_by_subagent=nodes, user_attribution="", task_parse=parse
    )
    assert result.signals == []  # JiuwenSwarm ends such a run as "nothing to attribute", attribution skipped
    outcome = await run_attribution(model=object(), nodes_by_subagent=nodes, signals=[], task_description="排周六")
    assert outcome.status == "success" and outcome.completed


async def _invoke(tool: Any, **inputs: Any) -> dict[str, Any]:
    output = await tool.invoke(inputs)
    assert output.success
    return json.loads(output.data["content"])


async def test_no_artifact_improvement_when_no_deliverable_was_reviewed() -> None:
    nodes = {LEADER: [_node(LEADER, 2, 20, content="团队已组建")]}
    signal = DiagnosticSignal.create("write_file: Error", source=SignalSource.KEYWORD_TOOL, node_ids=[f"{LEADER}:2"])
    runtime = NodeBackedRuntime(nodes)
    runtime.artifact_improvement_required = False  # what run_attribution sets without reward signals
    tools = {tool.tool_name: tool for tool in build_attribution_tools(runtime, [signal.signal_id])}
    assert "error" in await _invoke(tools["submit_artifact_improvement"], md_string="改产物")
    assert runtime.artifact_improvement is None
    await _invoke(tools["mark_signal_invalid"], diag_signal_id=signal.signal_id)
    assert (await _invoke(tools["check_attribution_complete"]))["all_done"] is True


async def test_only_feedback_follow_ups_stay_process_signals() -> None:
    sliced = slice_nodes_by_range(_session(), [(LEADER, 0, 100)])
    parse = await run_task_parse(sliced, "zh", _Llm(report_user_feedback={"node_ids": [f"{LEADER}:4"]}))
    assert parse.user_feedback == ["没有路径我怎么知道去哪里看文件"]
    result = await run_diagnosis(
        run_id="r", language="zh", nodes_by_subagent=sliced, user_attribution="", task_parse=parse
    )
    user_signals = [signal.text for signal in result.signals if signal.source == SignalSource.KEYWORD_USER]
    assert user_signals == ["没有路径我怎么知道去哪里看文件"]  # the redo request and the thanks are not complaints
    assert result.run_meta["user_messages_not_feedback"] == 2


async def test_unjudged_follow_ups_all_stay() -> None:
    sliced = slice_nodes_by_range(_session(), [(LEADER, 0, 100)])
    parse = await run_task_parse(sliced, "zh", _Llm())
    assert parse.user_feedback is None
    result = await run_diagnosis(
        run_id="r", language="zh", nodes_by_subagent=sliced, user_attribution="", task_parse=parse
    )
    assert sum(signal.source == SignalSource.KEYWORD_USER for signal in result.signals) == 3
