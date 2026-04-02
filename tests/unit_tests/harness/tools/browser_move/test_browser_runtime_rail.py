#!/usr/bin/env python
# coding: utf-8
"""Tests for BrowserRuntimeRail lifecycle hook."""
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail


def _run(coro):
    return asyncio.run(coro)


def _make_ctx() -> AgentCallbackContext:
    return AgentCallbackContext(agent=MagicMock())


def test_rail_is_agent_rail_subclass() -> None:
    assert issubclass(BrowserRuntimeRail, AgentRail)


def test_rail_holds_runtime_reference() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    rail = BrowserRuntimeRail(runtime)
    assert rail._runtime is runtime


def test_before_invoke_calls_ensure_started() -> None:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_started = AsyncMock()
    rail = BrowserRuntimeRail(runtime)
    _run(rail.before_invoke(_make_ctx()))
    runtime.ensure_started.assert_called_once_with()


def test_before_invoke_called_twice_delegates_twice() -> None:
    """Idempotency is BrowserAgentRuntime's responsibility; rail always delegates."""
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_started = AsyncMock()
    rail = BrowserRuntimeRail(runtime)
    _run(rail.before_invoke(_make_ctx()))
    _run(rail.before_invoke(_make_ctx()))
    assert runtime.ensure_started.call_count == 2


def test_rail_registered_for_before_invoke_event() -> None:
    """get_callbacks() must return before_invoke so the framework fires it."""
    from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent

    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_started = AsyncMock()
    rail = BrowserRuntimeRail(runtime)
    callbacks = rail.get_callbacks()
    assert AgentCallbackEvent.BEFORE_INVOKE in callbacks
