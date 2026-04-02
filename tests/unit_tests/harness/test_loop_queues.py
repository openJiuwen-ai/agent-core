# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for LoopQueues."""
from __future__ import annotations

import pytest

from openjiuwen.harness.task_loop.loop_queues import (
    LoopQueues,
)


def test_push_and_drain_steering() -> None:
    """Push messages to steering, drain returns all."""
    q = LoopQueues()
    q.push_steer("msg1")
    q.push_steer("msg2")

    msgs = q.drain_steering()
    assert msgs == ["msg1", "msg2"]

    # Second drain returns empty
    assert q.drain_steering() == []


def test_push_and_drain_follow_up() -> None:
    """Push messages to follow_up, drain returns all."""
    q = LoopQueues()
    q.push_follow_up("fu1")
    q.push_follow_up("fu2")
    q.push_follow_up("fu3")

    msgs = q.drain_follow_up()
    assert msgs == ["fu1", "fu2", "fu3"]

    # Second drain returns empty
    assert q.drain_follow_up() == []


def test_queues_are_independent() -> None:
    """Steering and follow_up queues are independent."""
    q = LoopQueues()
    q.push_steer("steer1")
    q.push_follow_up("follow1")

    assert q.drain_steering() == ["steer1"]
    assert q.drain_follow_up() == ["follow1"]

    # Both empty now
    assert q.drain_steering() == []
    assert q.drain_follow_up() == []


def test_drain_empty_queue() -> None:
    """Draining an empty queue returns empty list."""
    q = LoopQueues()
    assert q.drain_steering() == []
    assert q.drain_follow_up() == []


def test_multiple_drain_cycles() -> None:
    """Multiple push/drain cycles work correctly."""
    q = LoopQueues()

    q.push_steer("a")
    assert q.drain_steering() == ["a"]

    q.push_steer("b")
    q.push_steer("c")
    assert q.drain_steering() == ["b", "c"]


def test_has_follow_up_does_not_consume() -> None:
    """has_follow_up checks pending state without draining."""
    q = LoopQueues()
    q.push_follow_up("fu1")

    assert q.has_follow_up() is True
    assert q.has_follow_up() is True
    assert q.drain_follow_up() == ["fu1"]
    assert q.has_follow_up() is False
