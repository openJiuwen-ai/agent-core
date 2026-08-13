# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness steering: reaches a running round, refuses everything else.

``steer`` exists because ``send(content, immediate=True)`` does the wrong thing
when nothing is running -- it starts a round. For a new message that is correct;
for a correction aimed at a round that has since finished it invents a turn the
user never asked for.

The phase can only be decided by the supervisor. A caller that reads
``active_round`` and then calls ``send`` has at least one await between them, and
the round can finish inside it. Here the phase is read in the same step that
queues the text, so the decision cannot go stale.
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from openjiuwen.harness.task_loop.loop_queues import SteeringInput
from tests.unit_tests.agent_teams.harness.fixtures import (
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
    wait_invoke_running,
)


@pytest.mark.asyncio
async def test_steering_a_running_round_reaches_the_inner_loop() -> None:
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, iterations=3, sleep_seconds=5.0)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("first")
            await wait_invoke_running(fake)

            assert await harness.steer("prefer the async client") is True

            # Asserted on the queue rather than on what the agent saw. The fake
            # drains steering once at the top of ``invoke``, so a mid-round steer
            # can never show up in ``seen_steers`` -- the real react_agent drains
            # before every model call, and that side is covered by
            # tests/unit_tests/harness/test_steering_applied.py. What _on_steer
            # owns is exactly this: the text is in the running round's queue.
            queues = harness.event_handler.interaction_queues
            assert [
                SteeringInput.coerce(queues.steering.get_nowait()).text
                for _ in range(queues.steering.qsize())
            ] == ["prefer the async client"]
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_steering_an_idle_harness_refuses_and_starts_nothing() -> None:
    """The whole point: no round appears, and the caller is told so."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            assert harness.state is HarnessState.IDLE

            assert await harness.steer("too late") is False

            # Nothing ran and nothing was queued for a future round either.
            assert harness.state is HarnessState.IDLE
            assert harness.active_round is None
            assert fake.seen_steers == []
            assert fake.completed_iterations == 0
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_send_immediate_on_an_idle_harness_does_start_a_round() -> None:
    """Control, and the reason ``steer`` had to be added.

    If this ever stops starting a round then ``send(immediate=True)`` became
    safe for stale steering and the separate entry point is redundant. Until
    then, the two behaviours differ and the difference is the bug being avoided.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            assert harness.state is HarnessState.IDLE

            await harness.send("too late", immediate=True)

            await wait_for_state(harness, HarnessState.IDLE)
            # A whole round ran, from text that meant to steer an old one.
            assert fake.completed_iterations >= 1
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_steering_a_paused_harness_refuses_and_does_not_resume_it() -> None:
    """``send`` would resume the paused round and inject; steering must not.

    Waking a harness to hand it a correction is the same silent-promotion
    problem as starting a fresh round: the user asked to adjust something that
    is running, not to restart something that stopped.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, iterations=3, sleep_seconds=5.0)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("first")
            await wait_invoke_running(fake)
            await harness.pause()
            assert harness.state is HarnessState.PAUSED

            assert await harness.steer("while paused") is False

            assert harness.state is HarnessState.PAUSED
            assert "while paused" not in fake.seen_steers
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()
