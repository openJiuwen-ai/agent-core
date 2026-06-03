# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for reliability sliding-window counter and stable call hashing."""

from openjiuwen.agent_teams.reliability.window import SlidingWindowCounter, stable_call_hash


def test_sliding_window_counts_within_window():
    counter = SlidingWindowCounter(window_seconds=10.0)
    assert counter.add(0.0) == 1
    assert counter.add(1.0) == 2
    assert counter.add(2.0) == 3


def test_sliding_window_evicts_old_events():
    counter = SlidingWindowCounter(window_seconds=10.0)
    counter.add(0.0)
    counter.add(1.0)
    # At t=15 the cutoff is 5.0, so the events at 0.0 and 1.0 fall out.
    assert counter.add(15.0) == 1


def test_sliding_window_count_does_not_record():
    counter = SlidingWindowCounter(window_seconds=10.0)
    counter.add(1.0)
    assert counter.count(2.0) == 1
    assert counter.count(2.0) == 1


def test_sliding_window_reset_clears():
    counter = SlidingWindowCounter(window_seconds=10.0)
    counter.add(1.0)
    counter.add(2.0)
    counter.reset()
    assert counter.count(3.0) == 0


def test_stable_call_hash_is_argument_order_independent():
    h1 = stable_call_hash("edit", {"path": "a.py", "content": "x"})
    h2 = stable_call_hash("edit", {"content": "x", "path": "a.py"})
    assert h1 == h2


def test_stable_call_hash_nested_order_independent():
    h1 = stable_call_hash("run", {"opts": {"a": 1, "b": 2}, "cmd": "ls"})
    h2 = stable_call_hash("run", {"cmd": "ls", "opts": {"b": 2, "a": 1}})
    assert h1 == h2


def test_stable_call_hash_distinguishes_different_calls():
    h1 = stable_call_hash("read", {"path": "a.py"})
    h2 = stable_call_hash("read", {"path": "b.py"})
    assert h1 != h2


def test_stable_call_hash_treats_none_and_empty_args_alike():
    assert stable_call_hash("noop", None) == stable_call_hash("noop", {})


def test_stable_call_hash_tolerates_non_serializable_args():
    digest = stable_call_hash("call", {"obj": object()})
    assert isinstance(digest, str)
    assert len(digest) == 64
