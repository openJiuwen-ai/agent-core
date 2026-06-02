# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness abort semantics: graceful vs immediate (cancel + rollback)."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import (
    aborted_markers,
    drain_outputs,
    make_provider,
    start_harness,
    wait_for_state,
    wait_invoke_running,
)


@pytest.mark.asyncio
async def test_graceful_abort_finishes_current_round_then_stops() -> None:
    """graceful abort lets the in-flight round finish, then returns to IDLE.

    No follow-up is queued, so after the current round completes the supervisor
    goes IDLE without starting a continuation.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_provider())
        # Round runs long enough that abort lands while it is in-flight.
        fake = await start_harness(harness, sleep_seconds=0.1)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("go")
            await wait_invoke_running(fake)
            await harness.abort(immediate=False)
            assert await wait_for_state(harness, HarnessState.IDLE)
            # The round was NOT cancelled (graceful): invoke ran to completion.
            assert fake.cancelled_count == 0
            assert len(fake.invocations) == 1
            # Graceful abort emits no abort marker (the round completed normally).
            assert aborted_markers(collected) == []
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_immediate_abort_cancels_invoke_and_returns_to_idle() -> None:
    """immediate abort cancels the in-flight round and returns to IDLE.

    This is the cancel-chain assertion: cancelling the scheduler task must
    propagate a CancelledError into the running ``invoke`` (truly stopping the
    LLM/tool work), and an abort marker is emitted.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_provider())
        fake = await start_harness(harness, sleep_seconds=5.0)  # long-running

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("longjob")
            await wait_invoke_running(fake)  # invoke is now inside its sleep
            await harness.abort(immediate=True)
            assert await wait_for_state(harness, HarnessState.IDLE)
            # invoke observed the CancelledError mid-sleep (cancel chain works).
            assert fake.cancelled_count == 1
            # An abort marker was emitted so consumers can void prior chunks.
            assert aborted_markers(collected) == [{"round_id": 1, "kind": "abort"}]
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_immediate_abort_resets_coordinator_next_round_runs() -> None:
    """After an immediate abort the coordinator is reset so the next send runs.

    ``executor.cancel`` set ``is_aborted`` via ``coordinator.request_abort()``;
    the supervisor must reset it, otherwise the next round's continuation check
    would see a permanently-aborted coordinator and never run.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_provider())
        fake = await start_harness(harness, sleep_seconds=5.0)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("longjob")
            await wait_invoke_running(fake)
            coordinator = harness.loop_coordinator
            await harness.abort(immediate=True)
            assert await wait_for_state(harness, HarnessState.IDLE)
            assert coordinator.is_aborted is False

            # A fresh round must start AND complete.
            fake.sleep_seconds = 0.0
            await harness.send("after")
            assert await wait_for_state(harness, HarnessState.IDLE)
            assert [inv["query"] for inv in fake.invocations] == ["longjob", "after"]
            assert fake.cancelled_count == 1  # only the first round was cancelled
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_immediate_abort_rolls_back_to_last_safe_snapshot() -> None:
    """immediate abort restores context to the last completed round boundary.

    Round 1 completes (SnapshotRail captures last_safe_snapshot via the
    executor's AFTER_TASK_ITERATION). Round 2's invoke is cancelled mid-sleep;
    the immediate abort rolls the context back to the round-1 snapshot, so the
    current-round message segment matches what it was after round 1.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_provider())
        fake = await start_harness(harness)  # round 1 is fast

        ctx = fake.context_engine.get_context(session_id=harness.session_id)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("round1")
            assert await wait_for_state(harness, HarnessState.IDLE)
            msgs_after_round1 = len(ctx.get_messages(with_history=False))
            assert msgs_after_round1 == 1  # round1 appended its user message

            # Round 2: long-running, cancelled mid-flight.
            fake.sleep_seconds = 5.0
            await harness.send("round2")
            await wait_invoke_running(fake)
            # round2 appended its user message before sleeping.
            assert len(ctx.get_messages(with_history=False)) == 2
            await harness.abort(immediate=True)
            assert await wait_for_state(harness, HarnessState.IDLE)

            # Rolled back to the round-1 boundary snapshot.
            assert len(ctx.get_messages(with_history=False)) == msgs_after_round1
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_immediate_abort_no_completed_round_clears_to_baseline() -> None:
    """immediate abort with no completed round rolls back to the pre-round baseline.

    With only one (cancelled) round, there is no last_safe_snapshot; rollback
    falls back to the pre-round baseline, leaving the current-round segment
    empty.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_provider())
        fake = await start_harness(harness, sleep_seconds=5.0)

        ctx = fake.context_engine.get_context(session_id=harness.session_id)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("only")
            await wait_invoke_running(fake)
            assert len(ctx.get_messages(with_history=False)) == 1
            await harness.abort(immediate=True)
            assert await wait_for_state(harness, HarnessState.IDLE)
            # Pre-round baseline (index 0) had an empty current segment.
            assert ctx.get_messages(with_history=False) == []
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()
