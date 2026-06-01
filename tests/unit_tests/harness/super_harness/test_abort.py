# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SuperHarness abort semantics: graceful (iteration-granular) vs immediate."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.harness.super_harness import HarnessState, SuperHarness
from tests.unit_tests.harness.super_harness.fixtures import (
    IterationStep,
    MockDeepAgent,
    aborted_markers,
    drain_outputs,
)


@pytest.mark.asyncio
async def test_graceful_abort_finishes_current_iteration_then_stops() -> None:
    """graceful abort lets the current iteration finish, skips the next."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[{"v": "iter1"}], sleep_after=0.05),
        IterationStep(chunks=[{"v": "iter2_never"}]),
    ]
    harness = SuperHarness(lambda: agent)
    await harness.start()

    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    try:
        await harness.send("go")
        await asyncio.sleep(0.02)  # inside iteration 1
        await harness.abort(immediate=False)
        # Wait for the round to wind down to IDLE.
        for _ in range(50):
            if harness.state is HarnessState.IDLE:
                break
            await asyncio.sleep(0.02)
        assert harness.state is HarnessState.IDLE
        # iteration 1 ran; iteration 2 was skipped by the graceful break.
        assert agent.react_agent.steps_executed == 1
    finally:
        await harness.stop()
        await consumer


@pytest.mark.asyncio
async def test_immediate_abort_cancels_and_returns_to_idle() -> None:
    """immediate abort cancels the in-flight round and returns to IDLE."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[], sleep_before=5.0),  # long-running
    ]
    harness = SuperHarness(lambda: agent)
    await harness.start()

    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    try:
        await harness.send("longjob")
        await asyncio.sleep(0.05)
        await harness.abort(immediate=True)
        await asyncio.sleep(0.05)
        assert harness.state is HarnessState.IDLE
        # The round was cancelled mid-sleep: the step never completed.
        assert agent.react_agent.steps_executed == 0
        # An abort marker was emitted so consumers can void prior chunks.
        assert aborted_markers(collected) == [{"round_id": 1, "kind": "abort"}]
    finally:
        await harness.stop()
        await consumer


@pytest.mark.asyncio
async def test_immediate_abort_rolls_back_to_last_safe_snapshot() -> None:
    """immediate abort restores context to the last completed iteration."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[{"v": "t1"}]),          # completes -> snapshot
        IterationStep(chunks=[], sleep_before=5.0),    # cancelled here
    ]
    harness = SuperHarness(lambda: agent)
    await harness.start()

    ctx = agent.react_agent.context_engine.get_context(session_id=harness.session_id)
    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    try:
        await harness.send("go")
        await asyncio.sleep(0.1)  # iteration 1 done, iteration 2 sleeping
        msgs_before = len(ctx.get_messages(with_history=False))
        await harness.abort(immediate=True)
        await asyncio.sleep(0.05)
        # Context restored to the snapshot taken after iteration 1.
        assert len(ctx.get_messages(with_history=False)) == msgs_before
        assert harness.state is HarnessState.IDLE
    finally:
        await harness.stop()
        await consumer
