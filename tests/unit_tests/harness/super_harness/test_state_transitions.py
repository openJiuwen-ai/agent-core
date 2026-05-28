# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SuperHarness state-transition tests.

Each test exercises one edge of the state-transition table from the plan.
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.harness.super_harness import HarnessState, SuperHarness
from tests.test_logger import logger
from tests.unit_tests.harness.super_harness.fixtures import (
    IterationStep,
    MockDeepAgent,
)


@pytest.mark.asyncio
async def test_idle_to_running_to_idle_one_round() -> None:
    """send() in IDLE starts a round; round completion returns to IDLE."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[{"type": "answer", "value": "hi"}]),
    ]

    harness = SuperHarness(lambda: agent)
    await harness.start()
    try:
        assert harness.state is HarnessState.IDLE
        seq = await harness.send("hello")
        assert seq == 1

        # Drain output until end-of-round.
        collected = []
        async for chunk in harness.outputs():
            collected.append(chunk)
            if len(collected) >= 1:
                break

        # Wait for supervisor to process round_finished.
        await asyncio.sleep(0.05)
        assert harness.state is HarnessState.IDLE
        assert collected == [{"type": "answer", "value": "hi"}]
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_running_queues_followup_then_runs_it_after_first_round() -> None:
    """send(immediate=False) in RUNNING buffers; next round picks it up."""
    await Runner.start()
    agent = MockDeepAgent()
    # First round = 1 chunk; second round = 1 chunk so we can verify.
    agent.react_agent.iteration_script = [
        IterationStep(
            chunks=[{"type": "answer", "value": "round1"}],
            sleep_before=0.02,
        ),
    ]

    harness = SuperHarness(lambda: agent)
    await harness.start()
    try:
        await harness.send("q1")
        # While round 1 is in flight, queue a follow-up.
        await harness.send("q2", immediate=False)

        chunks = []
        async for chunk in harness.outputs():
            chunks.append(chunk)
            if len(chunks) >= 2:
                break

        # Two MockReActAgent.stream invocations should have happened, in order.
        invocations = agent.react_agent.invocations
        assert len(invocations) == 2
        assert invocations[0]["inputs"] == {"query": "q1"}
        assert invocations[1]["inputs"] == {"query": "q2"}
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_stop_terminates_iterator_with_sentinel() -> None:
    """stop() pushes _END so outputs() iterator finishes cleanly."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = []  # empty script → empty stream

    harness = SuperHarness(lambda: agent)
    await harness.start()
    await harness.stop()

    # Iterator must exit naturally — no items pending after _END.
    collected = []
    async for chunk in harness.outputs():
        collected.append(chunk)
    assert collected == []
    assert harness.state is HarnessState.TERMINATED


@pytest.mark.asyncio
async def test_send_after_stop_raises() -> None:
    """send() on a terminated harness raises."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = []

    harness = SuperHarness(lambda: agent)
    await harness.start()
    await harness.stop()

    with pytest.raises(Exception):  # noqa: B017 - generic check; concrete = BaseError
        await harness.send("after-stop")

    logger.info("test_send_after_stop_raises: terminated state rejects sends")
