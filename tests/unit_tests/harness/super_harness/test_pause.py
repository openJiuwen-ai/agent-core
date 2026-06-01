# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SuperHarness pause + resume behavior."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.harness.super_harness import HarnessState, SuperHarness
from tests.unit_tests.harness.super_harness.fixtures import (
    IterationStep,
    MockDeepAgent,
    drain_outputs,
)


@pytest.mark.asyncio
async def test_pause_caches_query_and_resume_merges() -> None:
    """pause caches the query; the next send restarts with the merged query."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[], sleep_before=5.0),
    ]
    harness = SuperHarness(lambda: agent)
    await harness.start()

    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    try:
        await harness.send("first")
        await asyncio.sleep(0.05)
        await harness.pause()
        assert harness.state is HarnessState.PAUSED

        agent.react_agent.iteration_script = [
            IterationStep(is_answer=True, answer_output="ok"),
        ]
        await harness.send("addendum")
        assert harness.state is HarnessState.RUNNING
        await asyncio.sleep(0.05)

        assert agent.react_agent.invocations[-1]["inputs"]["query"] == "first\naddendum"
    finally:
        await harness.stop()
        await consumer


@pytest.mark.asyncio
async def test_paused_send_immediate_behaves_same_as_next_round() -> None:
    """In PAUSED the immediate flag is ignored — both concatenate + restart."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[], sleep_before=5.0),
    ]
    harness = SuperHarness(lambda: agent)
    await harness.start()

    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    try:
        await harness.send("first")
        await asyncio.sleep(0.05)
        await harness.pause()

        agent.react_agent.iteration_script = [
            IterationStep(is_answer=True, answer_output="ok"),
        ]
        await harness.send("more", immediate=True)
        await asyncio.sleep(0.05)
        assert agent.react_agent.invocations[-1]["inputs"]["query"] == "first\nmore"
    finally:
        await harness.stop()
        await consumer
