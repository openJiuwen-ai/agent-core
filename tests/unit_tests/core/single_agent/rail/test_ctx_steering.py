# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025.
# All rights reserved.
"""Unit tests for AgentCallbackContext steering methods."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
)


def _make_ctx() -> AgentCallbackContext:
    """Create a minimal AgentCallbackContext."""
    mock_agent = MagicMock()
    return AgentCallbackContext(agent=mock_agent)


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
        assert result == ["msg1", "msg2"]

    @staticmethod
    def test_drain_clears_queue() -> None:
        """Second drain returns empty after first."""
        q: asyncio.Queue = asyncio.Queue()
        ctx = _make_ctx()
        ctx.bind_steering_queue(q)

        ctx.push_steering("once")
        assert ctx.drain_steering() == ["once"]
        assert ctx.drain_steering() == []

    @staticmethod
    def test_shared_queue_with_external_writer() -> None:
        """External writer -> ctx drain reads it."""
        q: asyncio.Queue = asyncio.Queue()
        ctx = _make_ctx()
        ctx.bind_steering_queue(q)

        # Simulate EventHandler writing directly
        q.put_nowait("external_msg")

        result = ctx.drain_steering()
        assert result == ["external_msg"]

    @staticmethod
    def test_multiple_drain_cycles() -> None:
        """Multiple push/drain cycles work."""
        q: asyncio.Queue = asyncio.Queue()
        ctx = _make_ctx()
        ctx.bind_steering_queue(q)

        ctx.push_steering("a")
        assert ctx.drain_steering() == ["a"]

        ctx.push_steering("b")
        ctx.push_steering("c")
        assert ctx.drain_steering() == ["b", "c"]
