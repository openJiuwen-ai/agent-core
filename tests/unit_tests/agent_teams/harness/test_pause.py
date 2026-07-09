# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness pause / resume: boundary-accurate, resumable in place.

``pause`` stops at the nearest inner ReAct iteration boundary and keeps the round
alive; ``resume`` continues it from the preserved context without a new user
turn. Two phases, two strategies:

- **model phase** (no tool_calls committed yet): interrupt the LLM and rewind to
  the previous boundary — the only window where a hard-cancel is safe.
- **tool phase** (irreversible side effects in flight): let the iteration run to
  completion and stop at the boundary that follows.
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import (
    drain_outputs,
    make_spec,
    start_harness,
    wait_completed_iterations,
    wait_for_state,
    wait_invoke_running,
    wait_tool_running,
)


def _contents(ctx) -> list:
    """Message contents of the context (history segment is empty in tests)."""
    return [getattr(m, "content", "") for m in ctx.get_messages(with_history=True)]


@pytest.mark.asyncio
async def test_pause_in_model_phase_interrupts_and_rewinds_to_boundary() -> None:
    """A pause parked in the model call interrupts it and rewinds one boundary.

    Iteration 0 completes (its boundary is snapshotted); iteration 1 parks in its
    model call. Pausing there hard-cancels the LLM — the only place a cancel can
    land without touching a running tool — and rewinds to iteration 0's boundary,
    so the half-done iteration leaves no trace.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, iterations=2, sleep_seconds=5.0)
        fake.sleep_from_iteration = 1  # iteration 0 is fast, iteration 1 parks

        ctx = fake.context_engine.get_context(session_id=harness.session_id)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("first")
            assert await wait_completed_iterations(fake, 1)
            await wait_invoke_running(fake)  # iteration 1 is inside its model call

            await harness.pause()
            assert harness.state is HarnessState.PAUSED
            assert fake.cancelled_count == 1  # the model call was interrupted

            # Rewound to iteration 0's boundary: the query and iteration 0's
            # assistant message survive; nothing of iteration 1 remains.
            assert _contents(ctx) == ["first", "assistant-0"]
            assert fake.completed_iterations == 1
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_pause_in_tool_phase_never_interrupts_the_tool() -> None:
    """A pause during tool execution lets the iteration finish first.

    Tool side effects are irreversible, so the cooperative stop waits for the
    iteration to complete and breaks at the next model-call boundary instead of
    hard-cancelling mid-tool.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(
            harness,
            iterations=2,
            emit_tools=True,
            tool_sleep_seconds=0.3,
        )

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("first")
            await wait_tool_running(fake)  # iteration 0's tool is executing

            # Deferred ack: returns only once PAUSED has actually been reached.
            await harness.pause()
            assert harness.state is HarnessState.PAUSED

            # The tool ran to completion and closed its iteration cleanly; the
            # next iteration stopped before its model call ever started.
            assert fake.cancelled_count == 0
            assert fake.completed_tools == 1
            assert fake.completed_iterations == 1
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_resume_continues_in_place_without_a_new_user_turn() -> None:
    """resume drives a continuation round over the preserved context."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, iterations=2, sleep_seconds=5.0)
        fake.sleep_from_iteration = 1

        ctx = fake.context_engine.get_context(session_id=harness.session_id)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("first")
            assert await wait_completed_iterations(fake, 1)
            await wait_invoke_running(fake)
            await harness.pause()
            assert harness.state is HarnessState.PAUSED

            # Let the continuation finish immediately.
            fake.sleep_seconds = 0.0
            fake.iterations = 1
            await harness.resume()
            assert await wait_for_state(harness, HarnessState.IDLE)

            # It carried the continuation flag and appended no duplicate turn.
            assert fake.invocations[-1].get("_resume_continuation") is True
            assert _contents(ctx).count("first") == 1
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_send_while_paused_resumes_and_injects_the_new_content() -> None:
    """A send in PAUSED resumes in place and steers the new content in.

    It must not merge onto the original query and restart the round — that would
    discard every iteration the paused round had already completed.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, iterations=2, sleep_seconds=5.0)
        fake.sleep_from_iteration = 1

        ctx = fake.context_engine.get_context(session_id=harness.session_id)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("first")
            assert await wait_completed_iterations(fake, 1)
            await wait_invoke_running(fake)
            await harness.pause()
            assert harness.state is HarnessState.PAUSED

            fake.sleep_seconds = 0.0
            fake.iterations = 1
            await harness.send("more")
            assert await wait_for_state(harness, HarnessState.IDLE)

            # Resumed as a continuation carrying the original query, with the new
            # content steered in — not concatenated into a restarted round.
            last = fake.invocations[-1]
            assert last.get("_resume_continuation") is True
            assert last["query"] == "first"
            assert "more" in fake.seen_steers
            assert _contents(ctx).count("first") == 1
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_resume_is_a_noop_unless_paused() -> None:
    """resume() only acts on a PAUSED harness; otherwise it is a no-op."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            assert harness.state is HarnessState.IDLE
            await harness.resume()
            assert harness.state is HarnessState.IDLE
            assert fake.invocations == []
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()
