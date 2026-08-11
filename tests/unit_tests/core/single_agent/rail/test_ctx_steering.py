# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025.
# All rights reserved.
"""Unit tests for AgentCallbackContext steering methods.

``drain_steering`` returns ``SteeringInput`` envelopes rather than bare strings: a
steer carries the id of the request that produced it, so ``STEER_APPLIED`` can
name which one a rail dropped. Producers may still push plain text -- a rail
steering on its own has nothing to correlate -- and the drain coerces, which is
what ``_texts`` unwraps below.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
)


def _make_ctx() -> AgentCallbackContext:
    """Create a minimal AgentCallbackContext."""
    mock_agent = MagicMock()
    return AgentCallbackContext(agent=mock_agent)


def _texts(drained: list[Any]) -> list[str]:
    """The text of each drained steer, for assertions about content and order."""
    return [item.text for item in drained]


class TestCtxSteeringQueue:
    """Tests for bind_steering_queue / push / drain."""

    @staticmethod
    def test_drain_returns_empty_by_default() -> None:
        """No queue bound -> drain returns []."""
        ctx = _make_ctx()
        assert ctx.drain_steering() == []

    @staticmethod
    def test_push_without_bind_is_noop() -> None:
        """Push without bind does not crash."""
        ctx = _make_ctx()
        ctx.push_steering("ignored")
        assert ctx.drain_steering() == []

    @staticmethod
    def test_bind_push_and_drain() -> None:
        """Bound queue -> drain returns pushed msgs."""
        q: asyncio.Queue = asyncio.Queue()
        ctx = _make_ctx()
        ctx.bind_steering_queue(q)

        ctx.push_steering("msg1")
        ctx.push_steering("msg2")

        result = ctx.drain_steering()
        assert _texts(result) == ["msg1", "msg2"]
        # Pushed as plain text, so there is no id to carry.
        assert [item.id for item in result] == [None, None]

    @staticmethod
    def test_drain_clears_queue() -> None:
        """Second drain returns empty after first."""
        q: asyncio.Queue = asyncio.Queue()
        ctx = _make_ctx()
        ctx.bind_steering_queue(q)

        ctx.push_steering("once")
        assert _texts(ctx.drain_steering()) == ["once"]
        assert ctx.drain_steering() == []

    @staticmethod
    def test_shared_queue_with_external_writer() -> None:
        """External writer -> ctx drain reads it."""
        q: asyncio.Queue = asyncio.Queue()
        ctx = _make_ctx()
        ctx.bind_steering_queue(q)

        # Simulate EventHandler writing directly. It writes a bare string, which
        # is why the drain coerces rather than assuming envelopes.
        q.put_nowait("external_msg")

        result = ctx.drain_steering()
        assert _texts(result) == ["external_msg"]

    @staticmethod
    def test_multiple_drain_cycles() -> None:
        """Multiple push/drain cycles work."""
        q: asyncio.Queue = asyncio.Queue()
        ctx = _make_ctx()
        ctx.bind_steering_queue(q)

        ctx.push_steering("a")
        assert _texts(ctx.drain_steering()) == ["a"]

        ctx.push_steering("b")
        ctx.push_steering("c")
        assert _texts(ctx.drain_steering()) == ["b", "c"]


def _ctx_with_queue(*messages: str) -> AgentCallbackContext:
    """Create a context whose bound queue already holds ``messages``."""
    ctx = _make_ctx()
    ctx.bind_steering_queue(asyncio.Queue())
    for message in messages:
        ctx.push_steering(message)
    return ctx


def test_limit_takes_the_oldest_and_leaves_the_rest_queued() -> None:
    """The surplus stays in the queue, in order, for the next drain."""
    ctx = _ctx_with_queue("m1", "m2", "m3", "m4", "m5")

    assert _texts(ctx.drain_steering(2)) == ["m1", "m2"]
    assert ctx.has_pending_steering()
    assert _texts(ctx.drain_steering(2)) == ["m3", "m4"]
    assert _texts(ctx.drain_steering(2)) == ["m5"]
    assert not ctx.has_pending_steering()


def test_limit_above_the_queue_takes_what_there_is() -> None:
    ctx = _ctx_with_queue("only")
    assert _texts(ctx.drain_steering(5)) == ["only"]


def test_no_limit_still_takes_everything() -> None:
    """The default is the behaviour of a loop with no policy on the matter."""
    ctx = _ctx_with_queue("a", "b", "c")
    assert _texts(ctx.drain_steering()) == ["a", "b", "c"]
    assert ctx.drain_steering(None) == []


def test_a_non_empty_queue_always_yields_one() -> None:
    """Consumption has to advance, or the loop would spin on an untouched queue."""
    ctx = _ctx_with_queue("a", "b")
    assert _texts(ctx.drain_steering(0)) == ["a"]
    assert _texts(ctx.drain_steering(-3)) == ["b"]


def test_limit_on_an_empty_queue_returns_empty() -> None:
    ctx = _ctx_with_queue()
    assert ctx.drain_steering(2) == []
