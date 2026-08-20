# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness state-machine transitions over the real task-loop kernel."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import (
    FakeReactAgent,
    answer_outputs,
    drain_outputs,
    make_spec,
    mock_chunks,
    start_harness,
    wait_for_state,
)


@pytest.mark.asyncio
async def test_idle_to_running_to_idle_single_round() -> None:
    """send() in IDLE runs one real round; completion returns to IDLE."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="hi")

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            assert harness.state is HarnessState.IDLE
            seq = await harness.send("hello")
            assert seq == 1
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert mock_chunks(collected) == [{"query": "hello"}]
        assert answer_outputs(collected) == ["hi"]
        assert [inv["query"] for inv in fake.invocations] == ["hello"]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_completion_timeout_only_logs_slow_round(caplog: pytest.LogCaptureFixture) -> None:
    """NativeHarness completion_timeout logs slow rounds without cancelling them."""
    await Runner.start()
    try:
        caplog.set_level("WARNING")
        harness = NativeHarness(make_spec(completion_timeout=0.01))
        fake = await start_harness(harness, sleep_seconds=0.05, answer_output="done")

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("slow")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert fake.cancelled_count == 0
        assert answer_outputs(collected) == ["done"]
        assert "slow round" in caplog.text
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_none_completion_timeout_disables_slow_round_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """None keeps NativeHarness rounds unlimited without spawning a warning timer."""
    await Runner.start()
    try:
        caplog.set_level("WARNING")
        harness = NativeHarness(make_spec(completion_timeout=None))
        fake = await start_harness(harness, sleep_seconds=0.02, answer_output="done")

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("unlimited")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert fake.cancelled_count == 0
        assert answer_outputs(collected) == ["done"]
        assert "slow round" not in caplog.text
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_followup_runs_in_fifo_order_after_first_round() -> None:
    """send(immediate=False) while RUNNING buffers; the next round picks it up."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        # Slow first round so q2 enqueues as a follow-up while q1 is RUNNING.
        fake = await start_harness(harness, sleep_seconds=0.05)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("q1")
            assert await wait_for_state(harness, HarnessState.RUNNING)
            await harness.send("q2", immediate=False)
            # First round finishes, then the follow-up round runs; back to IDLE.
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert [inv["query"] for inv in fake.invocations] == ["q1", "q2"]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_stop_terminates_output_iterator() -> None:
    """stop() closes the stream; the outputs() iterator ends cleanly."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        await start_harness(harness, answer_output="x")

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        await harness.stop()
        await consumer  # ends because _END was emitted

        assert harness.state is HarnessState.TERMINATED
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_send_after_stop_raises() -> None:
    """send() on a terminated harness raises rather than hanging."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        await start_harness(harness)
        await harness.stop()

        with pytest.raises(Exception):  # noqa: B017 - BaseError subclass
            await harness.send("after-stop")
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_concurrent_start_initializes_once() -> None:
    """Concurrent start() calls do not double-initialize the supervisor."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())

        await asyncio.gather(harness.start(), harness.start(), harness.start())
        harness.set_react_agent(FakeReactAgent(harness.card), initialized=True)
        try:
            assert harness.state in (HarnessState.IDLE, HarnessState.RUNNING)
            assert harness._st.supervisor_task is not None
        finally:
            await harness.stop()
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_follow_ups_queued_together_drive_one_round() -> None:
    """Everything queued while a round runs is consumed by the next round.

    One round each would make the member act on the oldest entry while newer
    ones wait behind it -- and for an input that supersedes its predecessor
    (a task board is a full survey) that means acting on a stale one.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=0.05)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("q1")
            assert await wait_for_state(harness, HarnessState.RUNNING)
            await harness.send("q2", immediate=False)
            await harness.send("q3", immediate=False)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert [inv["query"] for inv in fake.invocations] == ["q1", "q2\nq3"]
        # The batch also reaches the inner loop unjoined, so ON_USER_MESSAGE
        # rails can drop a whole superseded entry rather than parse a body.
        assert fake.invocations[1]["_input_parts"] == ["q2", "q3"]
        assert "_input_parts" not in fake.invocations[0]
    finally:
        await Runner.stop()
