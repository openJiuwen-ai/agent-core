# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SuperHarness graceful abort: iteration-granular finish without rollback."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.harness.super_harness import HarnessState, SuperHarness
from tests.unit_tests.harness.super_harness.fixtures import (
    IterationStep,
    MockDeepAgent,
)


@pytest.mark.asyncio
async def test_graceful_abort_lets_current_iteration_finish() -> None:
    """abort(immediate=False) lets the current iteration finish then breaks
    the inner ReAct loop at the next iteration top."""
    await Runner.start()
    agent = MockDeepAgent()
    # Two iterations: graceful abort during iter 1 should allow iter 1 to
    # finish (chunk emitted + AFTER_REACT_ITERATION fired) but skip iter 2.
    agent.react_agent.iteration_script = [
        IterationStep(
            chunks=[{"type": "step", "value": "iter1"}],
            sleep_after=0.05,
        ),
        IterationStep(chunks=[{"type": "step", "value": "iter2_never"}]),
    ]

    harness = SuperHarness(lambda: agent)
    await harness.start()
    try:
        await harness.send("go")
        # Let iter 1 start.
        await asyncio.sleep(0.02)
        await harness.abort(immediate=False)

        # Wait until the harness settles back to IDLE (round finished naturally
        # via the graceful path). Use a wall-clock guard so the test fails
        # rather than hangs if the implementation regresses.
        for _ in range(50):
            if harness.state is HarnessState.IDLE:
                break
            await asyncio.sleep(0.02)
        assert harness.state is HarnessState.IDLE

        # Drain whatever chunks have been queued. Iterator will only block
        # forever if more chunks are expected; since the round is over and
        # we have not called stop(), we add a timeout to terminate the read.
        chunks: list = []
        async def _read_until_done() -> None:
            async for chunk in harness.outputs():
                chunks.append(chunk)

        try:
            await asyncio.wait_for(_read_until_done(), timeout=0.1)
        except asyncio.TimeoutError:
            pass  # expected — stop() not yet called, no sentinel yet

        # Exactly one chunk (iter 1) should have been emitted; iter 2 skipped.
        assert len(chunks) == 1
        assert chunks[0]["value"] == "iter1"
    finally:
        await harness.stop()
