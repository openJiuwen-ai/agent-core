# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness pause + resume behavior over the real task-loop kernel."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import (
    drain_outputs,
    make_provider,
    start_harness,
    wait_for_state,
    wait_invoke_running,
)


@pytest.mark.asyncio
async def test_pause_caches_query_and_resume_merges() -> None:
    """pause caches the query; the next send restarts with the merged query."""
    await Runner.start()
    try:
        harness = NativeHarness(make_provider())
        fake = await start_harness(harness, sleep_seconds=5.0)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("first")
            await wait_invoke_running(fake)
            await harness.pause()
            assert harness.state is HarnessState.PAUSED
            # The paused round's invoke was cancelled.
            assert fake.cancelled_count == 1

            # Resume: the next send concatenates and restarts the round.
            fake.sleep_seconds = 0.0
            await harness.send("addendum")
            assert harness.state is HarnessState.RUNNING
            assert await wait_for_state(harness, HarnessState.IDLE)

            assert fake.invocations[-1]["query"] == "first\naddendum"
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_paused_send_immediate_behaves_same_as_next_round() -> None:
    """In PAUSED the immediate flag is ignored — both concatenate + restart."""
    await Runner.start()
    try:
        harness = NativeHarness(make_provider())
        fake = await start_harness(harness, sleep_seconds=5.0)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("first")
            await wait_invoke_running(fake)
            await harness.pause()
            assert harness.state is HarnessState.PAUSED

            fake.sleep_seconds = 0.0
            await harness.send("more", immediate=True)
            assert await wait_for_state(harness, HarnessState.IDLE)
            assert fake.invocations[-1]["query"] == "first\nmore"
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_pause_rolls_back_to_pre_round_no_duplicate_query() -> None:
    """pause rolls back to the pre-round baseline, so resume does not duplicate.

    The paused round already appended its user message to the context; pause
    discards the whole round (it restarts with a merged query), so it must roll
    back to the pre-round baseline. Otherwise the original query's message would
    survive and the restarted round would duplicate it.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_provider())
        fake = await start_harness(harness, sleep_seconds=5.0)

        ctx = fake.context_engine.get_context(session_id=harness.session_id)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("first")
            await wait_invoke_running(fake)
            # The in-flight round appended "first" to the current segment.
            assert any(
                getattr(m, "content", "") == "first"
                for m in ctx.get_messages(with_history=False)
            )
            await harness.pause()
            # Rolled back to the pre-round baseline: no stray "first" survives.
            contents = [getattr(m, "content", "") for m in ctx.get_messages(with_history=True)]
            assert contents.count("first") == 0

            fake.sleep_seconds = 0.0
            await harness.send("addendum")
            assert await wait_for_state(harness, HarnessState.IDLE)
            # The restarted round sent the merged query exactly once.
            merged_queries = [
                inv["query"] for inv in fake.invocations if inv["query"] == "first\naddendum"
            ]
            assert merged_queries == ["first\naddendum"]
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()
