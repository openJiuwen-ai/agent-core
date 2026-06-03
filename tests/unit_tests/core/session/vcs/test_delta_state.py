# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for vcs state diff/apply (nested set + removed paths, None-safe)."""
from openjiuwen.core.session.vcs.delta import apply_state, diff_state


def test_nested_set():
    old = {"a": {"b": 1}}
    new = {"a": {"b": 2}}
    delta = diff_state(old, new)
    assert delta.set == {"a.b": 2}
    assert apply_state(old, delta) == new


def test_add_key():
    old = {"a": 1}
    new = {"a": 1, "b": {"c": 3}}
    delta = diff_state(old, new)
    assert delta.set == {"b": {"c": 3}}
    assert apply_state(old, delta) == new


def test_remove_key():
    old = {"a": 1, "b": 2}
    new = {"a": 1}
    delta = diff_state(old, new)
    assert delta.removed == ["b"]
    assert apply_state(old, delta) == new


def test_none_is_a_value_not_deletion():
    old = {"a": 1}
    new = {"a": None}
    delta = diff_state(old, new)
    assert delta.set == {"a": None}
    result = apply_state(old, delta)
    assert result == {"a": None}
    assert "a" in result


def test_list_wholesale_replace():
    old = {"a": [1, 2]}
    new = {"a": [1, 2, 3]}
    delta = diff_state(old, new)
    assert delta.set == {"a": [1, 2, 3]}
    assert apply_state(old, delta) == new


def test_empty_delta():
    old = {"a": {"b": 1}}
    new = {"a": {"b": 1}}
    delta = diff_state(old, new)
    assert delta.set == {}
    assert delta.removed == []
    assert apply_state(old, delta) == old


def test_roundtrip_mixed():
    old = {"keep": 1, "change": {"x": 1, "drop": 9}, "removed_top": 5}
    new = {"keep": 1, "change": {"x": 2, "add": 7}, "new_top": {"k": "v"}}
    delta = diff_state(old, new)
    assert apply_state(old, delta) == new
