# coding: utf-8
"""Tests for snapshot node building, range slicing and node-id mapping."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode
from openjiuwen.agent_evolving.attribution.snapshot import (
    assign_node_ids,
    build_nodes_from_snapshot,
    build_subject_nodes,
    detect_trajectory_language,
    node_native_key,
    slice_nodes_by_range,
)


def _attr(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _commit(
    span_id: str, ts: int, messages: list[dict[str, Any]], seq: int = 0, epoch: str = "e1", parent: str = "l1"
) -> dict[str, Any]:
    return {
        "traceId": "t1",
        "spanId": span_id,
        "parentSpanId": parent,
        "startTimeUnixNano": ts,
        "endTimeUnixNano": ts + 10,
        "name": "context.window.commit",
        "attributes": [
            _attr("openjiuwen.trajectory.event_kind", "context.window.commit"),
            _attr("openjiuwen.trajectory.event_id", f"evt-{span_id}"),
            _attr("openjiuwen.trajectory.subject_id", "main"),
            _attr("openjiuwen.trajectory.sequence_epoch", epoch),
            _attr("openjiuwen.trajectory.subject_sequence", str(seq)),
            _attr("openjiuwen.trajectory.recorded_at_unix_nano", str(ts)),
            _attr(
                "openjiuwen.trajectory.payload",
                json.dumps(
                    {
                        "window_id": f"w-{span_id}",
                        "base_window_id": None,
                        "complete": True,
                        "delta": [],
                        "transition_kind": "epoch_baseline",
                        "baseline_reason": "runtime_epoch_start",
                        "messages": messages,
                    }
                ),
            ),
        ],
    }


def _llm(span_id: str, ts: int, completions: list[dict[str, Any]], window_span: str = "c1") -> dict[str, Any]:
    return {
        "traceId": "t1",
        "spanId": span_id,
        "parentSpanId": window_span,
        "startTimeUnixNano": ts,
        "endTimeUnixNano": ts + 10,
        "name": "llm.call",
        "attributes": [
            _attr("gen_ai.input.messages", json.dumps([{"role": "user", "content": "task"}])),
            _attr("gen_ai.output.messages", json.dumps(completions)),
        ],
    }


def _turn_span(span_id: str, ts: int, input_text: str, output_text: str, wrapped: bool = False) -> dict[str, Any]:
    output_value = f"{{'output': '{output_text}'}}" if wrapped else output_text
    return {
        "traceId": "t1",
        "spanId": span_id,
        "parentSpanId": "",
        "startTimeUnixNano": ts,
        "endTimeUnixNano": ts + 20,
        "name": "agent.agent.work.normal.s1",
        "attributes": [
            _attr("openjiuwen.trajectory.record.kind", "turn"),
            _attr("openjiuwen.trajectory.schema_version", "2"),
            _attr("openjiuwen.span.input", input_text),
            _attr("openjiuwen.span.output", output_value),
            _attr("openjiuwen.execution.subject.id", "main"),
        ],
    }


def _tool(span_id: str, ts: int, call_id: str, name: str, error: bool = False) -> dict[str, Any]:
    attributes = [
        _attr("gen_ai.tool.name", name),
        _attr("gen_ai.tool.call.id", call_id),
        _attr("gen_ai.tool.result", "done"),
    ]
    span: dict[str, Any] = {
        "traceId": "t1",
        "spanId": span_id,
        "parentSpanId": "l1",
        "startTimeUnixNano": ts,
        "endTimeUnixNano": ts + 10,
        "name": f"tool.{name}",
        "attributes": attributes,
    }
    if error:
        span["status"] = {"code": "STATUS_CODE_ERROR"}
    return span


def _records() -> list[dict[str, Any]]:
    return [
        _commit(
            "c1", 100, [{"message_id": "m1", "role": "user", "content": "帮我写个脚本", "origin": "external_user"}]
        ),
        _llm(
            "l1",
            200,
            [
                {
                    "role": "assistant",
                    "content": "好的",
                    "tool_calls": [
                        {
                            "id": "call_x",
                            "type": "function",
                            "function": {"name": "write_file", "arguments": '{"file_path": "/w/a.py"}'},
                        }
                    ],
                }
            ],
        ),
        _tool("t1", 300, "call_x", "write_file"),
    ]


def test_build_subject_nodes_sources_and_order() -> None:
    nodes = build_subject_nodes(_records(), subject_id="main", display_name="Main", kind="main_agent")
    assert [node.role for node in nodes] == ["user", "assistant", "tool"]
    user, assistant, tool = nodes
    assert user.source_kind == "msg"
    assert user.message_id == "m1"
    assert user.span_id == "c1"
    assert user.start_ts_ns == 100
    assert assistant.source_kind == "llm"
    assert assistant.span_id == "l1"
    assert assistant.trace_id == "t1"
    assert tool.source_kind == "tool"
    assert tool.span_id == "t1"
    assert tool.call_id == "call_x"
    assert tool.name == "write_file"


def test_runtime_turn_follows_producing_call_not_next_context_commit() -> None:
    records = _records()
    records[0]["attributes"].extend([_attr("openjiuwen.turn.id", "turn-one"), _attr("openjiuwen.turn.number", 1)])
    reply = {"message_id": "a1", "role": "assistant", "content": "好的", "tool_calls": [
        {"id": "call_x", "type": "function", "function": {"name": "write_file", "arguments": '{}'}}]}
    later = _commit("c2", 1000, [reply], seq=2)
    later["attributes"].extend([_attr("openjiuwen.turn.id", "turn-two"), _attr("openjiuwen.turn.number", 2)])
    nodes = build_subject_nodes([*records, later], subject_id="main", display_name="Main", kind="main_agent")
    assistant = next(n for n in nodes if n.role == "assistant")
    assert assistant.turn_id == "turn-one"
    assert assistant.turn_number == 1
    assert assistant.with_node_id("main:10").turn_id == "turn-one"


def test_identical_turn_inputs_and_outputs_do_not_erase_distinct_runtime_turns() -> None:
    first = _turn_span("r1", 100, "continue", "done")
    second = _turn_span("r2", 300, "continue", "done")
    for number, span in enumerate([first, second], 1):
        span["attributes"].extend([_attr("openjiuwen.turn.id", f"t{number}"),
                                   _attr("openjiuwen.turn.number", number)])
    nodes = build_subject_nodes([first, second], subject_id="main", display_name="Main", kind="main_agent")
    assert [(n.turn_id, n.role) for n in nodes] == [
        ("t1", "user"), ("t1", "assistant"), ("t2", "user"), ("t2", "assistant")]


def test_build_subject_nodes_error_tool_flagged() -> None:
    records = [
        _commit("c1", 100, [{"message_id": "m1", "role": "user", "content": "do it", "origin": "external_user"}]),
        _llm(
            "l1",
            200,
            [
                {
                    "role": "assistant",
                    "content": "ok",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "bash", "arguments": '{"command":"ls"}'}}
                    ],
                }
            ],
        ),
        _tool("t1", 300, "c1", "bash", error=True),
    ]
    nodes = build_subject_nodes(records, subject_id="main", display_name="Main", kind="main_agent")
    assert nodes[-1].error is True


def test_build_nodes_from_snapshot() -> None:
    snapshot = {
        "schema_version": 1,
        "session_id": "s1",
        "subjects": [
            {"subject_id": "main", "display_name": "Main Agent", "kind": "main_agent", "records": _records()},
        ],
    }
    result = build_nodes_from_snapshot(snapshot)
    assert set(result) == {"main"}
    assert len(result["main"]) == 3


def test_slice_nodes_by_range_closed_interval_and_subject_drop() -> None:
    records = _records()
    main = build_subject_nodes(records, subject_id="main", display_name="M", kind="main_agent")
    sub = build_subject_nodes(records, subject_id="sub", display_name="S", kind="subagent")
    assigned, _ = assign_node_ids({"main": main, "sub": sub})
    sliced = slice_nodes_by_range(assigned, [("main", 100, 200)])
    assert set(sliced) == {"main"}
    ids = [node.node_id for node in sliced["main"]]
    # assistant 的起止为 (200, 210)，窗口 (100, 200) 未覆盖其结束 → 只留 user。
    assert ids == ["main:0"]


def test_slice_nodes_by_range_requires_full_coverage() -> None:
    records = _records()
    main = build_subject_nodes(records, subject_id="main", display_name="M", kind="main_agent")
    assigned, _ = assign_node_ids({"main": main})
    # assistant (200, 210)：end 到 209 不覆盖完整区间，到 210 才带上。
    short = slice_nodes_by_range(assigned, [("main", 100, 209)])
    assert [node.node_id for node in short["main"]] == ["main:0"]
    covering = slice_nodes_by_range(assigned, [("main", 100, 210)])
    assert [node.node_id for node in covering["main"]] == ["main:0", "main:1"]


def test_slice_nodes_by_range_zero_end_judged_by_start() -> None:
    node = TrajectoryNode(
        node_id="main:0",
        subagent_key="main",
        display_name="M",
        kind="main_agent",
        role="assistant",
        start_ts_ns=150,
        end_ts_ns=0,
    )
    included = slice_nodes_by_range({"main": [node]}, [("main", 100, 200)])
    assert [item.node_id for item in included["main"]] == ["main:0"]
    excluded = slice_nodes_by_range({"main": [node]}, [("main", 160, 200)])
    assert excluded["main"] == []


def test_assign_node_ids_reuse_across_refresh() -> None:
    records = _records()
    nodes = build_subject_nodes(records, subject_id="main", display_name="M", kind="main_agent")
    assigned, mapping = assign_node_ids({"main": nodes})
    first_ids = [node.node_id for node in assigned["main"]]
    assert first_ids == ["main:0", "main:1", "main:2"]
    extended = records + [
        _commit(
            "c2", 400, [{"message_id": "m2", "role": "user", "content": "继续", "origin": "external_user"}], parent="l2"
        ),
        _llm("l2", 500, [{"role": "assistant", "content": "ok"}], window_span="c2"),
    ]
    nodes2 = build_subject_nodes(extended, subject_id="main", display_name="M", kind="main_agent")
    assigned2, mapping2 = assign_node_ids({"main": nodes2}, mapping)
    second_ids = [node.node_id for node in assigned2["main"]]
    assert second_ids[:3] == first_ids
    assert second_ids[3] == "main:3"
    assert mapping2 != mapping


def test_replayed_tool_views_keep_content_and_have_unique_stable_ids() -> None:
    records = _records() + [
        _commit("c2", 400, [{"message_id": "tool-first", "role": "tool", "tool_call_id": "call_x",
                             "origin": "harness_internal", "name": "write_file", "content": "done"}],
                seq=2, parent="l2"),
        _llm("l2", 450, [], window_span="c2"),
        _commit("c3", 500, [{"message_id": "tool-rendered", "role": "tool", "tool_call_id": "call_x",
                             "origin": "harness_internal", "content": "Tool result: done"}],
                seq=3, parent="l3"),
        _llm("l3", 550, [], window_span="c3"),
    ]
    nodes = build_subject_nodes(records, subject_id="main", display_name="Main", kind="main_agent")
    tool_views = [n for n in nodes if n.tool_call_id == "call_x"]
    assert [n.content for n in tool_views] == ["done", "Tool result: done"]
    assert node_native_key(tool_views[0]) == node_native_key(tool_views[1])
    assigned, mapping = assign_node_ids({"main": nodes})
    assert len({n.node_id for n in assigned["main"]}) == len(nodes)
    repeated, _ = assign_node_ids({"main": nodes}, mapping)
    assert [n.node_id for n in repeated["main"]] == [n.node_id for n in assigned["main"]]


def test_view_identity_survives_reordering_and_insertions() -> None:
    first = TrajectoryNode("", "main", "M", "main_agent", "tool", content="first", source_kind="tool",
                           trace_id="trace", span_id="span", call_id="call")
    second = replace(first, content="second", name="bash")
    assigned, mapping = assign_node_ids({"main": [first, second]})
    expected = {n.content: n.node_id for n in assigned["main"]}
    third = replace(first, content="new view")
    refreshed, updated = assign_node_ids({"main": [third, second, first]}, mapping)
    actual = {n.content: n.node_id for n in refreshed["main"]}
    assert all(actual[text] == node_id for text, node_id in expected.items())
    assert actual["new view"] not in expected.values()
    assert mapping != updated


def test_new_view_inserted_before_existing_view_does_not_take_its_id() -> None:
    first = TrajectoryNode("", "main", "M", "main_agent", "tool", content="existing", source_kind="tool",
                           trace_id="trace", span_id="span", call_id="call")
    assigned, mapping = assign_node_ids({"main": [first]})
    inserted = replace(first, content="new context representation")
    refreshed, _ = assign_node_ids({"main": [inserted, first]}, mapping)
    assert refreshed["main"][1].node_id == assigned["main"][0].node_id
    assert refreshed["main"][0].node_id != refreshed["main"][1].node_id


def test_identical_repetitions_and_legacy_mapping_never_duplicate_node_ids() -> None:
    node = TrajectoryNode("", "main", "M", "main_agent", "tool", content="same", source_kind="tool",
                          trace_id="trace", span_id="span", call_id="call")
    previous = {"main": {node_native_key(node): 7}}
    assigned, mapping = assign_node_ids({"main": [node, node, node]}, previous)
    assert [n.node_id for n in assigned["main"]] == ["main:7", "main:8", "main:9"]
    refreshed, _ = assign_node_ids({"main": [node, node, node]}, mapping)
    assert [n.node_id for n in refreshed["main"]] == ["main:7", "main:8", "main:9"]
    assert previous == {"main": {node_native_key(node): 7}}


def test_changed_single_view_keeps_its_native_id_and_legacy_collisions_are_repaired() -> None:
    first = TrajectoryNode("", "main", "M", "main_agent", "tool", content="old", source_kind="tool",
                           trace_id="trace", span_id="one", call_id="call")
    _, mapping = assign_node_ids({"main": [first]})
    refreshed, _ = assign_node_ids({"main": [replace(first, content="updated")]}, mapping)
    assert refreshed["main"][0].node_id == "main:0"
    second = replace(first, span_id="two")
    collision = {"main": {node_native_key(first): 3, node_native_key(second): 3}}
    repaired, _ = assign_node_ids({"main": [first, second]}, collision)
    assert [n.node_id for n in repaired["main"]] == ["main:3", "main:4"]


def test_committed_assistant_message_prefers_llm_production_time() -> None:
    records = [
        _commit(
            "c1", 100, [{"message_id": "m1", "role": "user", "content": "问题", "origin": "external_user"}], parent="l1"
        ),
        # 答案由 l1 在 200ns 产出，但承载它的 commit 直到 1000ns（下一次用户消息到来）才落盘。
        _llm("l1", 200, [{"role": "assistant", "content": "回答一"}], window_span="c1"),
        _commit(
            "c2",
            1000,
            [{"message_id": "m2", "role": "assistant", "content": "回答一", "origin": "harness_internal"}],
            seq=1,
            parent="l2",
        ),
        _llm("l2", 1100, [{"role": "assistant", "content": "最终答案"}], window_span="c2"),
    ]
    nodes = build_subject_nodes(records, subject_id="main", display_name="M", kind="main_agent")
    assistant = nodes[1]
    assert assistant.role == "assistant"
    assert assistant.content == "回答一"
    assert assistant.source_kind == "msg"
    assert assistant.message_id == "m2"
    assert assistant.span_id == "c2"
    assert assistant.start_ts_ns == 200
    assert assistant.end_ts_ns == 210


def test_committed_assistant_message_keeps_commit_time_without_llm_match() -> None:
    records = [
        _commit(
            "c1", 100, [{"message_id": "m1", "role": "user", "content": "问题", "origin": "external_user"}], parent="l1"
        ),
        # 窗口 span 存在，但其产出与 commit 里的 assistant 内容不一致 → 无签名匹配，时间取 commit。
        _llm("l1", 200, [{"role": "assistant", "content": "别的回复"}], window_span="c1"),
        _commit(
            "c2",
            1000,
            [{"message_id": "m2", "role": "assistant", "content": "回答一", "origin": "harness_internal"}],
            seq=1,
            parent="l2",
        ),
        _llm("l2", 1100, [{"role": "assistant", "content": "最终答案"}], window_span="c2"),
    ]
    nodes = build_subject_nodes(records, subject_id="main", display_name="M", kind="main_agent")
    assistant = nodes[2]
    assert assistant.role == "assistant"
    assert assistant.content == "回答一"
    assert assistant.source_kind == "msg"
    assert assistant.start_ts_ns == 1000
    assert assistant.end_ts_ns == 1010


def test_node_native_key_forms() -> None:
    records = _records()
    nodes = build_subject_nodes(records, subject_id="main", display_name="M", kind="main_agent")
    keys = [node_native_key(node) for node in nodes]
    assert keys[0] == "msg:m1"
    assert keys[1] == "llm:t1:l1"
    assert keys[2] == "tool:t1:t1:call_x"


def test_detect_trajectory_language() -> None:
    records = _records()
    nodes = build_subject_nodes(records, subject_id="main", display_name="M", kind="main_agent")
    assert detect_trajectory_language({"main": nodes}) == "zh"


def test_turn_span_output_fallback_when_commit_missing() -> None:
    records = [
        _commit(
            "c1", 100, [{"message_id": "m1", "role": "user", "content": "问题", "origin": "external_user"}], parent="l1"
        ),
        _llm("l1", 200, [{"role": "assistant", "content": "回答一"}], window_span="c1"),
        # 最后一轮：只有 turn span，没有对应 commit。
        _turn_span("t2", 500, "追问", "你说得对，应该推荐快速排序"),
    ]
    nodes = build_subject_nodes(records, subject_id="main", display_name="M", kind="main_agent")
    assigned, mapping = assign_node_ids({"main": nodes})
    roles = [(node.role, node.source_kind, node.content) for node in assigned["main"]]
    assert roles == [
        ("user", "msg", "问题"),
        ("assistant", "llm", "回答一"),
        ("user", "turn", "追问"),
        ("assistant", "turn", "你说得对，应该推荐快速排序"),
    ]
    keys = sorted(mapping["main"])
    assert "turn:t1:t2:assistant" in keys
    assert "turn:t1:t2:user" in keys


def test_turn_fallback_dedups_content_already_in_commit() -> None:
    records = [
        _commit(
            "c1", 100, [{"message_id": "m1", "role": "user", "content": "问题", "origin": "external_user"}], parent="l1"
        ),
        _llm("l1", 200, [{"role": "assistant", "content": "回答一"}], window_span="c1"),
        # 输出与 commit 里已有的 assistant 内容一致 → 不重复投影。
        _turn_span("t2", 500, "问题", "回答一"),
    ]
    nodes = build_subject_nodes(records, subject_id="main", display_name="M", kind="main_agent")
    contents = [(node.role, node.content) for node in nodes]
    assert contents == [("user", "问题"), ("assistant", "回答一")]


def test_turn_fallback_unwraps_dict_output() -> None:
    records = [
        _commit(
            "c1", 100, [{"message_id": "m1", "role": "user", "content": "问题", "origin": "external_user"}], parent="l1"
        ),
        _turn_span("t2", 500, "问题", "最终回答", wrapped=True),
    ]
    nodes = build_subject_nodes(records, subject_id="main", display_name="M", kind="main_agent")
    assert nodes[-1].role == "assistant"
    assert nodes[-1].content == "最终回答"


def test_turn_fallback_sorts_after_same_ts_commit_nodes() -> None:
    records = [
        _commit(
            "c1", 100, [{"message_id": "m1", "role": "user", "content": "问题", "origin": "external_user"}], parent="l1"
        ),
        _turn_span("t2", 100, "问题", "补充回答"),
    ]
    nodes = build_subject_nodes(records, subject_id="main", display_name="M", kind="main_agent")
    assert nodes[-1].role == "assistant"
    assert nodes[-1].source_kind == "turn"
