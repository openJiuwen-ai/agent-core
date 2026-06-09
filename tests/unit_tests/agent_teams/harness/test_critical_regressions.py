# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for the critical bugs found in the NativeHarness review.

Each test exercises a fault that the prior implementations got wrong, now run
against the real task-loop kernel with a fake inner react_agent.
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.runner import Runner
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import (
    aborted_markers,
    answer_outputs,
    drain_outputs,
    make_spec,
    mock_chunks,
    start_harness,
    wait_for_state,
    wait_invoke_running,
)


@pytest.mark.asyncio
async def test_multi_round_second_round_receives_chunks() -> None:
    """Fatal #1: reusing one session must not close the stream between rounds."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="r1")

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("q1")
            assert await wait_for_state(harness, HarnessState.IDLE)  # round 1 done
            fake.answer_output = "r2"
            await harness.send("q2")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        # Pre-fix: round 2 streamed into a closed emitter -> only "r1" arrives.
        assert answer_outputs(collected) == ["r1", "r2"]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_immediate_abort_actually_stops_invoke() -> None:
    """Fatal #2: cancelling the round must stop the invoke work, not orphan it."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=5.0)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("go")
            await wait_invoke_running(fake)
            await harness.abort(immediate=True)
            # Give any orphaned work time to (wrongly) proceed.
            await asyncio.sleep(0.1)
            # invoke was cancelled mid-sleep: it never produced its real answer
            # ("echo:go") nor its mock chunk. (A cancel-path placeholder answer
            # from the controller's cancelled-future result may appear, but the
            # invoke's own work was truly stopped.)
            assert fake.cancelled_count == 1
            assert "echo:go" not in answer_outputs(collected)
            assert mock_chunks(collected) == []
            assert aborted_markers(collected) == [{"round_id": 1, "kind": "abort"}]
            assert harness.state is HarnessState.IDLE
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_rollback_preserves_history_without_duplication() -> None:
    """Fatal #3: rollback must not duplicate the persisted history segment."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)  # round 1 fast

        ctx = fake.context_engine.get_context(session_id=harness.session_id)
        ctx.seed_history([UserMessage(content="H1"), UserMessage(content="H2")])

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("round1")
            assert await wait_for_state(harness, HarnessState.IDLE)

            fake.sleep_seconds = 5.0
            await harness.send("round2")
            await wait_invoke_running(fake)
            await harness.abort(immediate=True)
            assert await wait_for_state(harness, HarnessState.IDLE)

            all_msgs = ctx.get_messages(with_history=True)
            history_count = sum(
                1 for m in all_msgs if getattr(m, "content", "") in ("H1", "H2")
            )
            # Pre-fix (snapshot with_history=True): history duplicated -> 4.
            assert history_count == 2
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_supervisor_handler_crash_does_not_hang_caller() -> None:
    """Finding #4: a crashing handler must reject the caller's ack, not hang it."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        await start_harness(harness)

        def boom(session):  # noqa: ANN001, ANN202
            raise RuntimeError("snapshot boom")

        # _start_round -> capture_snapshot -> load_state; make it explode.
        harness.load_state = boom

        # send triggers _on_send -> _start_round -> crash; ack must be rejected.
        with pytest.raises(Exception):  # noqa: B017
            await asyncio.wait_for(harness.send("trigger"), timeout=2.0)
        assert harness.state is HarnessState.TERMINATED
    finally:
        await harness.stop()
        await Runner.stop()


@pytest.mark.asyncio
async def test_graceful_abort_then_send_does_not_restart() -> None:
    """Finding #7: a send during graceful drain must not start a new round."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=0.1)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("q1")
            await wait_invoke_running(fake)
            await harness.abort(immediate=False)
            await harness.send("q2", immediate=False)  # arrives during drain
            assert await wait_for_state(harness, HarnessState.IDLE)
            # q2 must NOT have started a new round.
            assert [inv["query"] for inv in fake.invocations] == ["q1"]
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_immediate_steer_reaches_invoke_steering_queue() -> None:
    """immediate send during RUNNING injects into the active round's steering queue.

    The executor passes the shared steering queue as ``effective["_steering_queue"]``.
    A long-running round lets a second immediate send push a steer; a follow-up
    round then drains it (the first round was cancelled-free, so the steer that
    arrives after invoke has begun is observed by the *next* round's invoke,
    which reads the same shared queue). The assertion is simply that the steered
    content reaches an invoke's steering queue.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        # First round long enough to receive a steer; it will be cancelled so a
        # second send can re-run and drain the queue deterministically.
        fake = await start_harness(harness, sleep_seconds=5.0)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("base")
            await wait_invoke_running(fake)
            # Steer the active round: pushed onto the shared steering queue.
            await harness.send("STEER-ME", immediate=True)
            # Let the supervisor process the steer push.
            await asyncio.sleep(0.05)
            # The shared steering queue is drained by invoke; abort the current
            # round and run a fresh one so a fully-controlled invoke drains it.
            await harness.abort(immediate=True)
            assert await wait_for_state(harness, HarnessState.IDLE)

            fake.sleep_seconds = 0.0
            await harness.send("next")
            assert await wait_for_state(harness, HarnessState.IDLE)
            assert "STEER-ME" in fake.seen_steers
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()
