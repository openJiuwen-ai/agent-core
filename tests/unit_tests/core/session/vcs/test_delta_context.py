# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for vcs context diff/apply (message-level append vs reset)."""
from openjiuwen.core.session.vcs.delta import apply_context, diff_context


def _ctx(messages, offload=None):
    return {"messages": list(messages), "offload_messages": offload or {}}


def test_pure_append_emits_tail_only():
    old = {"c1": _ctx([{"role": "user", "content": "a"}])}
    new = {"c1": _ctx([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])}
    deltas = diff_context(old, new)
    assert len(deltas) == 1
    assert deltas[0].kind == "append"
    assert deltas[0].messages == [{"role": "assistant", "content": "b"}]
    assert apply_context(old, deltas) == new


def test_truncated_head_emits_reset():
    old = {"c1": _ctx([{"i": 1}, {"i": 2}, {"i": 3}])}
    new = {"c1": _ctx([{"i": 2}, {"i": 3}, {"i": 4}])}
    deltas = diff_context(old, new)
    assert len(deltas) == 1
    assert deltas[0].kind == "reset"
    assert apply_context(old, deltas) == new


def test_new_context_emits_reset():
    old = {}
    new = {"c1": _ctx([{"i": 1}])}
    deltas = diff_context(old, new)
    assert deltas[0].kind == "reset"
    assert apply_context(old, deltas) == new


def test_offload_change_emits_reset():
    old = {"c1": _ctx([{"i": 1}], offload={})}
    new = {"c1": _ctx([{"i": 1}], offload={"h": [{"i": 0}]})}
    deltas = diff_context(old, new)
    assert len(deltas) == 1
    assert deltas[0].kind == "reset"
    assert deltas[0].offload_messages == {"h": [{"i": 0}]}
    assert apply_context(old, deltas) == new


def test_no_change_emits_nothing():
    old = {"c1": _ctx([{"i": 1}])}
    new = {"c1": _ctx([{"i": 1}])}
    assert diff_context(old, new) == []
    assert apply_context(old, diff_context(old, new)) == old
