# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the BEFORE_STEERING_DRAIN rail hook.

The hook decides how much of the steering backlog one model call absorbs, and
it fires *before* the queue is touched: a rail that let too much through could
otherwise only push the surplus back, behind whatever arrived meanwhile. What
is not taken stays queued in order, and the loop keeps running while steering
is pending, so it arrives at the model calls that follow.
"""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.rail.base import (
    EVENT_METHOD_MAP,
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
    SteeringDrainInputs,
)


class _StubCallbackManager:
    """Runs the wired rails for one event, the way the real manager does.

    The real one registers into the process-global callback framework, which
    would carry rails from one test into the next.
    """

    def __init__(self, rails: list[AgentRail]) -> None:
        self._rails = rails

    async def execute(self, event: AgentCallbackEvent, ctx: AgentCallbackContext) -> None:
        """Dispatch the event to every rail that opted into it."""
        for rail in self._rails:
            callback = rail.get_callbacks().get(event)
            if callback is not None:
                await callback(ctx)


class _StubAgent:
    """The one attribute ``ctx.fire`` reaches for."""

    def __init__(self, rails: list[AgentRail]) -> None:
        self.agent_callback_manager = _StubCallbackManager(rails)


class _CappingRail(AgentRail):
    """Rail that caps every drain, recording the queue depth it was shown."""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self._limit = limit
        self.seen_pending: list[int] = []

    async def before_steering_drain(self, ctx: AgentCallbackContext) -> None:
        """Cap this drain and record what the backlog looked like."""
        self.seen_pending.append(ctx.inputs.pending)
        ctx.inputs.limit = self._limit


class _InertRail(AgentRail):
    """Rail that overrides nothing, to prove the hook stays opt-in."""


def _agent() -> ReActAgent:
    """Create an unconfigured agent: the drain reads ``ctx``, nothing else."""
    return ReActAgent.__new__(ReActAgent)


def _ctx(*messages: str, rails: list[AgentRail] | None = None) -> AgentCallbackContext:
    """Create a context with a loaded steering queue and the given rails wired."""
    ctx = AgentCallbackContext(agent=_StubAgent(list(rails or [])))
    ctx.bind_steering_queue(asyncio.Queue())
    for message in messages:
        ctx.push_steering(message)
    return ctx


@pytest.mark.level0
def test_event_is_mapped_to_its_method() -> None:
    assert EVENT_METHOD_MAP[AgentCallbackEvent.BEFORE_STEERING_DRAIN] == "before_steering_drain"


@pytest.mark.level0
def test_overriding_rail_registers_the_callback() -> None:
    callbacks = _CappingRail(2).get_callbacks()
    assert AgentCallbackEvent.BEFORE_STEERING_DRAIN in callbacks


@pytest.mark.level0
def test_inert_rail_does_not_register_the_callback() -> None:
    callbacks = _InertRail().get_callbacks()
    assert AgentCallbackEvent.BEFORE_STEERING_DRAIN not in callbacks


@pytest.mark.level0
def test_inputs_default_to_taking_everything() -> None:
    """A run nobody has an opinion about keeps draining the whole backlog."""
    inputs = SteeringDrainInputs()
    assert inputs.pending == 0
    assert inputs.limit is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_rail_caps_the_batch_and_leaves_the_rest_queued() -> None:
    rail = _CappingRail(2)
    ctx = _ctx("m1", "m2", "m3", "m4", "m5", rails=[rail])

    batch = await _agent()._drain_steering_batch(ctx)

    assert batch == ["m1", "m2"]
    assert ctx.has_pending_steering()
    assert ctx.steering_queue.qsize() == 3


@pytest.mark.asyncio
@pytest.mark.level0
async def test_successive_calls_walk_the_backlog_in_order() -> None:
    """The following model calls pick up exactly where the last one stopped."""
    rail = _CappingRail(2)
    ctx = _ctx("m1", "m2", "m3", "m4", "m5", rails=[rail])
    agent = _agent()

    batches = [await agent._drain_steering_batch(ctx) for _ in range(3)]

    assert batches == [["m1", "m2"], ["m3", "m4"], ["m5"]]
    assert not ctx.has_pending_steering()
    assert await agent._drain_steering_batch(ctx) == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_rail_is_shown_the_current_queue_depth() -> None:
    """``pending`` is what a rail capping only large bursts reads."""
    rail = _CappingRail(2)
    ctx = _ctx("m1", "m2", "m3", rails=[rail])
    agent = _agent()

    await agent._drain_steering_batch(ctx)
    await agent._drain_steering_batch(ctx)

    assert rail.seen_pending == [3, 1]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_no_rail_opinion_takes_the_whole_backlog() -> None:
    ctx = _ctx("m1", "m2", "m3", rails=[_InertRail()])

    assert await _agent()._drain_steering_batch(ctx) == ["m1", "m2", "m3"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_an_empty_queue_does_not_fire_the_hook() -> None:
    """Nothing to decide about, so no rail is woken for it."""
    rail = _CappingRail(2)
    ctx = _ctx(rails=[rail])

    assert await _agent()._drain_steering_batch(ctx) == []
    assert rail.seen_pending == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_no_bound_queue_returns_empty() -> None:
    ctx = AgentCallbackContext(agent=_StubAgent([]))

    assert await _agent()._drain_steering_batch(ctx) == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_inputs_are_restored_after_the_hook() -> None:
    """The drain borrows ``ctx.inputs``; the enclosing invoke keeps its own."""
    invoke_inputs = object()
    ctx = _ctx("m1", rails=[_CappingRail(1)])
    ctx.inputs = invoke_inputs

    await _agent()._drain_steering_batch(ctx)

    assert ctx.inputs is invoke_inputs
