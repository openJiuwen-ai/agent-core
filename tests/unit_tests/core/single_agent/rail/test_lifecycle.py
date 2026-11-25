# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for AgentCallbackContext.lifecycle: the after event fires for every way the pair ends."""

import asyncio
import unittest
from unittest.mock import patch

import pytest

from openjiuwen.core.runner.callback.errors import AbortError
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
)
from tests.unit_tests.core.single_agent.rail.test_rail import _make_agent
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response


class RecordingRail(AgentRail):
    """Records before/after invoke; aborts before_invoke when asked."""

    def __init__(self, name, events, priority=50, abort=None):
        super().__init__()
        self.name = name
        self.events = events
        self.priority = priority
        self.abort = abort
        self.seen_exception = None

    async def before_invoke(self, ctx):
        self.events.append(f"{self.name}:before_invoke")
        if self.abort is not None:
            raise self.abort

    async def after_invoke(self, ctx):
        self.events.append(f"{self.name}:after_invoke")
        self.seen_exception = ctx.exception


class TestLifecycleAfterAlwaysFires(unittest.IsolatedAsyncioTestCase):
    """The after event of a lifecycle pair fires once its before event has started."""

    @pytest.mark.level1
    async def test_after_fires_when_before_aborts(self):
        """A rail aborting before_invoke still gets after_invoke; the abort reaches the caller."""
        agent, _ = _make_agent()
        events = []
        rail = RecordingRail("gate", events, abort=AbortError("refused"))
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("unused")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            with pytest.raises(AbortError):
                await agent.invoke({"query": "hello"})

        self.assertEqual(events, ["gate:before_invoke", "gate:after_invoke"])
        self.assertIsInstance(rail.seen_exception, AbortError)

    @pytest.mark.level1
    async def test_after_fires_for_rail_that_ran_before_another_aborts(self):
        """A higher-priority rail whose before_invoke ran sees after_invoke when a later rail aborts."""
        agent, _ = _make_agent()
        events = []
        keeper = RecordingRail("keeper", events, priority=90)
        gate = RecordingRail("gate", events, priority=10,
                             abort=AbortError("refused", cause=ValueError("bad input")))
        await agent.register_rail(keeper)
        await agent.register_rail(gate)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("unused")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            with pytest.raises(ValueError):
                await agent.invoke({"query": "hello"})

        self.assertEqual(events[:2], ["keeper:before_invoke", "gate:before_invoke"])
        self.assertIn("keeper:after_invoke", events)
        self.assertIsInstance(keeper.seen_exception, ValueError)

    @pytest.mark.level1
    async def test_body_does_not_run_when_before_aborts(self):
        """When before aborts, the block body is skipped and after fires once."""
        agent, _ = _make_agent()
        fired = []

        async def on_before(ctx):
            fired.append("before")
            raise AbortError("stop here")

        async def on_after(ctx):
            fired.append("after")

        await agent.register_callback(AgentCallbackEvent.BEFORE_INVOKE, on_before)
        await agent.register_callback(AgentCallbackEvent.AFTER_INVOKE, on_after)
        ctx = AgentCallbackContext(agent=agent)

        with pytest.raises(AbortError):
            async with ctx.lifecycle(AgentCallbackEvent.BEFORE_INVOKE, AgentCallbackEvent.AFTER_INVOKE):
                fired.append("body")

        self.assertEqual(fired, ["before", "after"])
        self.assertIsInstance(ctx.exception, AbortError)

    @pytest.mark.level1
    async def test_after_fires_when_before_is_cancelled(self):
        """A cancellation while before runs still fires after; the cancellation propagates."""
        agent, _ = _make_agent()
        fired = []
        started = asyncio.Event()

        async def on_before(ctx):
            fired.append("before")
            started.set()
            await asyncio.sleep(10)

        async def on_after(ctx):
            fired.append("after")

        await agent.register_callback(AgentCallbackEvent.BEFORE_INVOKE, on_before)
        await agent.register_callback(AgentCallbackEvent.AFTER_INVOKE, on_after)
        ctx = AgentCallbackContext(agent=agent)

        async def run():
            async with ctx.lifecycle(AgentCallbackEvent.BEFORE_INVOKE, AgentCallbackEvent.AFTER_INVOKE):
                fired.append("body")

        task = asyncio.create_task(run())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        self.assertEqual(fired, ["before", "after"])

    @pytest.mark.level0
    async def test_success_path_unchanged(self):
        """On success, before, body and after run once each, in order."""
        agent, _ = _make_agent()
        fired = []

        async def on_before(ctx):
            fired.append("before")

        async def on_after(ctx):
            fired.append("after")

        await agent.register_callback(AgentCallbackEvent.BEFORE_INVOKE, on_before)
        await agent.register_callback(AgentCallbackEvent.AFTER_INVOKE, on_after)
        ctx = AgentCallbackContext(agent=agent)

        async with ctx.lifecycle(AgentCallbackEvent.BEFORE_INVOKE, AgentCallbackEvent.AFTER_INVOKE):
            fired.append("body")

        self.assertEqual(fired, ["before", "body", "after"])
        self.assertIsNone(ctx.exception)
