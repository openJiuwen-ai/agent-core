# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.session.checkpointer.step_delta import (
    STEP_WINDOW_KEY,
    RangeDelta,
    apply_step_context,
    apply_step_state,
    build_step_snapshot,
    diff_step_context,
    diff_step_state,
    restore_full_state,
)


def m(t):
    return {"role": "user", "content": t}


def ctx(msgs, offload=None):
    return {"cid": {"messages": msgs, "offload_messages": offload or {}}}


def test_pure_growth_reuses_vcs_append():
    old, new = ctx([m("q")]), ctx([m("q"), m("a"), m("t")])
    deltas = diff_step_context(old, new)
    assert [(d.kind, len(d.messages)) for d in deltas] == [("append", 2)]
    assert apply_step_context(old, deltas) == new


def test_offload_truncation_writes_range_per_changed_index():
    old = ctx([m("q"), m("big tool output"), m("a1"), m("t1")])
    new = ctx([m("q"), m("[offloaded handle=h1]"), m("a1"), m("t1"), m("a2"), m("t2")])
    deltas = diff_step_context(old, new)
    assert [(d.kind, d.start, d.end) for d in deltas] == [("replace_range", 1, 2), ("replace_range", 4, 4)]
    assert all(d.offload_messages is None for d in deltas)  # 文件卸载不进增量
    assert apply_step_context(old, deltas) == new


def test_compression_writes_single_range_and_keeps_recall_marker():
    summary = m("summary [[COMPRESSION_RECALL: id=mem-1]]")
    old = ctx([m(f"m{i}") for i in range(8)])
    new = ctx([m("m0"), summary, m("m5"), m("m6"), m("m7")])
    deltas = diff_step_context(old, new)
    assert [(d.kind, d.start, d.end) for d in deltas] == [("replace_range", 1, 5)]
    assert deltas[0].messages == [summary]
    assert apply_step_context(old, deltas) == new


def test_memory_fallback_offload_table_rides_on_first_range():
    old = ctx([m("q"), m("big")])
    new = ctx([m("q"), m("[offloaded handle=h2]")], offload={"h2": [m("big")]})
    deltas = diff_step_context(old, new)
    assert deltas[0].kind == "replace_range" and deltas[0].offload_messages == {"h2": [m("big")]}
    assert apply_step_context(old, deltas) == new


def test_unalignable_change_falls_back_to_reset():
    old, new = ctx([m("a"), m("b")]), ctx([m("x"), m("y")])
    assert [d.kind for d in diff_step_context(old, new)] == ["reset"]


def test_first_context_is_reset():
    deltas = diff_step_context({}, ctx([m("q"), m("a")]))
    assert [d.kind for d in deltas] == ["reset"]


def test_dotted_or_non_str_state_keys_use_full_state():
    delta, full = diff_step_state({}, {"global_state": {"a.b": 1}})
    assert full == {"global_state": {"a.b": 1}} and delta.set == {}
    assert apply_step_state({"x": 1}, delta, full) == {"global_state": {"a.b": 1}}
    delta, full = diff_step_state({"global_state": {"k": 1}}, {"global_state": {"k": 2}})
    assert full is None and delta.set == {"global_state.k": 2}


def test_snapshot_roundtrip_keeps_window_and_offload():
    full_state = {
        "global_state": {
            "todo": {"todos": [1, 2]},
            "context": {
                "cid": {
                    "messages": [
                        UserMessage(content="q"),
                        AssistantMessage(content="a"),
                        ToolMessage(content="t", tool_call_id="call-1"),
                    ],
                    "offload_messages": {"h1": [ToolMessage(content="big", tool_call_id="call-0")]},
                    "last_context_window_access_at": 1234.5,
                }
            },
        },
        "agent_state": {"k": "v"},
    }

    snapshot = build_step_snapshot(full_state)
    assert "context" not in snapshot["state"]["global_state"]
    assert snapshot["state"][STEP_WINDOW_KEY] == {"cid": 1234.5}
    assert snapshot["state"]["global_state"]["todo"] == {"todos": [1, 2]}

    restored = restore_full_state(snapshot)
    assert STEP_WINDOW_KEY not in restored
    assert restored["agent_state"] == {"k": "v"}
    assert restored["global_state"]["todo"] == {"todos": [1, 2]}
    cstate = restored["global_state"]["context"]["cid"]
    assert cstate["last_context_window_access_at"] == 1234.5
    assert [type(x) for x in cstate["messages"]] == [UserMessage, AssistantMessage, ToolMessage]
    assert [x.content for x in cstate["messages"]] == ["q", "a", "t"]
    assert [x.content for x in cstate["offload_messages"]["h1"]] == ["big"]


def test_range_delta_is_the_replace_range_kind():
    delta = RangeDelta("cid", 1, 2, [m("x")])
    assert delta.kind == "replace_range"
