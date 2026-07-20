# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""One-shot failure retry for rounds that die abnormally.

A round's inbound query is marked read at deliver time, so a round that
crashes (or is killed by a completion timeout) would silently lose its
message — no poll ever re-delivers it. ``_on_round_done`` therefore retries
the query once on a fresh round, and gives up loudly on a second abnormal
death so a deterministic failure cannot loop forever.
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
async def test_crashed_round_retries_query_once_and_succeeds() -> None:
    """A round that crashes is retried once with the same query; the retry runs to completion."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="recovered")
        fake.raise_exc_once = RuntimeError("inner round blew up")

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("please do the thing")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        queries = [inv["query"] for inv in fake.invocations]
        assert queries == ["please do the thing", "please do the thing"]
        assert answer_outputs(collected) == ["recovered"]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_round_crashing_twice_gives_up_without_looping() -> None:
    """A second abnormal death gives up (IDLE) instead of retrying forever."""
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
            # Give any (buggy) further retry a chance to surface before counting.
            await asyncio.sleep(0.1)
        finally:
            await harness.stop()
            await consumer

        assert len(fake.invocations) == 2
        assert answer_outputs(collected) == []
        assert harness.state is HarnessState.TERMINATED
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_failed_round_emits_failed_event_not_finished() -> None:
    """An abnormally-dying round surfaces as harness.round kind=failed to subscribers."""
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
        assert kinds.count("failed") == 2, kinds
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
