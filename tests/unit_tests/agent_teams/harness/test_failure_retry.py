# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Failure handling for structured task failures and runtime crashes.

A round's inbound query is marked read at deliver time, so a round that
crashes before reporting an outcome would silently lose its
message — no poll ever re-delivers it. ``_on_round_done`` therefore retries
the query once on a fresh round. Structured task failures instead rely on
their queued retry follow-up and never trigger that independent replay.
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import (
    answer_outputs,
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_failure_runs_queued_retry_follow_up() -> None:
    """A structured task failure runs its retry follow-up instead of replaying the query."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="recovered")
        fake.raise_exc_once = RuntimeError("inner round blew up")
        harness.loop_controller.enqueue_follow_up("retry the failed round")

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("please do the thing")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        queries = [inv["query"] for inv in fake.invocations]
        assert queries == ["please do the thing", "retry the failed round"]
        assert answer_outputs(collected) == ["recovered"]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_failure_without_follow_up_goes_idle_without_replay() -> None:
    """A structured task failure without a retry follow-up is not replayed."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        fake.raise_exc = RuntimeError("deterministic failure")

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("doomed query")
            assert await wait_for_state(harness, HarnessState.IDLE)
            # Give any unintended replay a chance to surface before counting.
            await asyncio.sleep(0.1)
        finally:
            await harness.stop()
            await consumer

        assert len(fake.invocations) == 1
        assert answer_outputs(collected) == []
        assert harness.state is HarnessState.TERMINATED
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_failure_emits_failed_event_not_finished() -> None:
    """A structured task failure surfaces as harness.round kind=failed."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        fake.raise_exc = RuntimeError("boom")

        round_events: list[tuple[str, int]] = []

        async def on_round(kind: str, round_id: int, result: dict | None = None) -> None:
            _ = result
            round_events.append((kind, round_id))

        await harness.subscribe(on_round=on_round)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("boom query")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        kinds = [kind for kind, _ in round_events]
        assert kinds.count("failed") == 1, kinds
        assert "finished" not in kinds
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_graceful_abort_is_not_retried() -> None:
    """A graceful abort finishes without triggering the failure retry."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=0.2, answer_output="done")

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("long job")
            await fake.invoke_running.wait()
            await harness.abort(immediate=False)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert len(fake.invocations) == 1
    finally:
        await Runner.stop()
