# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness state-machine transitions (invoke-based architecture)."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import (
    IterationStep,
    MockDeepAgent,
    drain_outputs,
    mock_chunks,
)


@pytest.mark.asyncio
async def test_idle_to_running_to_idle_single_round() -> None:
    """send() in IDLE runs a round; round completion returns to IDLE."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[{"value": "hi"}], is_answer=True, answer_output="hi"),
    ]
    harness = NativeHarness(lambda: agent)
    await harness.start()

    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    try:
        assert harness.state is HarnessState.IDLE
        seq = await harness.send("hello")
        assert seq == 1
        await asyncio.sleep(0.05)
        assert harness.state is HarnessState.IDLE  # round finished
    finally:
        await harness.stop()
        await consumer

    assert mock_chunks(collected) == [{"value": "hi"}]


@pytest.mark.asyncio
async def test_followup_runs_in_fifo_order_after_first_round() -> None:
    """send(immediate=False) while RUNNING buffers; next round picks it up in order."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(is_answer=True, answer_output="r", sleep_before=0.03),
    ]
    harness = NativeHarness(lambda: agent)
    await harness.start()

    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    try:
        await harness.send("q1")
        await harness.send("q2", immediate=False)  # queued while q1 runs
        await asyncio.sleep(0.15)
    finally:
        await harness.stop()
        await consumer

    queries = [inv["inputs"]["query"] for inv in agent.react_agent.invocations]
    assert queries == ["q1", "q2"]


@pytest.mark.asyncio
async def test_stop_terminates_output_iterator() -> None:
    """stop() closes the stream; outputs() iterator ends cleanly."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(is_answer=True, answer_output="x"),
    ]
    harness = NativeHarness(lambda: agent)
    await harness.start()

    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    await harness.stop()
    await consumer  # ends because _END was emitted

    assert harness.state is HarnessState.TERMINATED


@pytest.mark.asyncio
async def test_send_after_stop_raises() -> None:
    """send() on a terminated harness raises rather than hanging."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [IterationStep(is_answer=True)]
    harness = NativeHarness(lambda: agent)
    await harness.start()
    await harness.stop()

    with pytest.raises(Exception):  # noqa: B017 - BaseError subclass
        await harness.send("after-stop")


@pytest.mark.asyncio
async def test_concurrent_start_initializes_once() -> None:
    """Concurrent start() calls do not double-initialize the supervisor."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [IterationStep(is_answer=True)]
    harness = NativeHarness(lambda: agent)

    await asyncio.gather(harness.start(), harness.start(), harness.start())
    try:
        assert harness.state in (HarnessState.IDLE, HarnessState.RUNNING)
        assert harness._st.supervisor_task is not None
    finally:
        await harness.stop()
