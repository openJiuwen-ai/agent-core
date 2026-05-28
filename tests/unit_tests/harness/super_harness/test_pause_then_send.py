# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SuperHarness pause + send concatenation behavior."""
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
async def test_pause_caches_query_for_next_send() -> None:
    """After pause, the next send concatenates onto the cached query and
    starts a fresh round with the merged content."""
    await Runner.start()
    agent = MockDeepAgent()
    # First round = long iteration so we can pause during it.
    # Second round = short iteration producing a chunk.
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[], sleep_before=0.5),  # long pause
    ]

    harness = SuperHarness(lambda: agent)
    await harness.start()
    try:
        await harness.send("first")
        await asyncio.sleep(0.02)  # let round start
        await harness.pause()
        assert harness.state is HarnessState.PAUSED

        # Replace script for the resumed round.
        agent.react_agent.iteration_script = [
            IterationStep(chunks=[{"type": "answer", "value": "merged"}]),
        ]
        await harness.send("addendum")
        assert harness.state is HarnessState.RUNNING

        chunks = []
        async for chunk in harness.outputs():
            chunks.append(chunk)
            if chunks:
                break
        await asyncio.sleep(0.02)

        # Two invocations: the cancelled "first" and the resumed merged one.
        invocations = agent.react_agent.invocations
        assert len(invocations) >= 2
        assert invocations[-1]["inputs"] == {"query": "first\naddendum"}
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_pause_then_send_immediate_behaves_same_as_next_round() -> None:
    """PAUSED state ignores the immediate flag — both behaviors concatenate."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[], sleep_before=0.5),
    ]

    harness = SuperHarness(lambda: agent)
    await harness.start()
    try:
        await harness.send("first")
        await asyncio.sleep(0.02)
        await harness.pause()

        agent.react_agent.iteration_script = [
            IterationStep(chunks=[{"type": "answer", "value": "ok"}]),
        ]
        await harness.send("more", immediate=True)

        chunks = []
        async for chunk in harness.outputs():
            chunks.append(chunk)
            if chunks:
                break

        # immediate=True is ignored in PAUSED; same merged-query path runs.
        assert agent.react_agent.invocations[-1]["inputs"] == {"query": "first\nmore"}
    finally:
        await harness.stop()
