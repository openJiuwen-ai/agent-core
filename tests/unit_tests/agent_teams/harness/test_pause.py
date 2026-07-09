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
async def test_resume_without_query_is_a_noop_unless_paused() -> None:
    """A warm resume() only acts on a PAUSED harness; otherwise it is a no-op."""
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


@pytest.mark.asyncio
async def test_cold_resume_continues_over_restored_context() -> None:
    """resume(query=...) from IDLE drives a continuation round.

    Models ``pause -> stop -> start``: the rebuilt harness is IDLE and its context
    came back from the session checkpoint, so the coordination layer supplies the
    query the pause recorded. The round must continue that context — appending no
    user turn — rather than starting a fresh one.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)

        ctx = fake.context_engine.get_context(session_id=harness.session_id)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            assert harness.state is HarnessState.IDLE
            await harness.resume(query="original task")
            assert await wait_for_state(harness, HarnessState.IDLE)

            last = fake.invocations[-1]
            assert last.get("_resume_continuation") is True
            # The query rides along as original_query (task-plan continuation may
            # reuse it) but is never appended as a user turn.
            assert last["query"] == "original task"
            assert _contents(ctx).count("original task") == 0
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_cold_resume_query_drives_the_task_plan_continuation() -> None:
    """The persisted query drives the rounds *after* the continuation, not it.

    The continuation round never appends the query — its context was preserved.
    The query's only job is to be ``original_query``, so that once the
    continuation finishes, a task-plan continuation (an ordinary round) can be
    driven with it. ``_has_remaining_tasks`` is stubbed because the fake has no
    task-planning rail to build a real plan.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)

        # One remaining task-plan task after the continuation round, then none.
        remaining = iter([True, False])
        harness._has_remaining_tasks = lambda session: next(remaining, False)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.resume(query="original task")
            assert await wait_for_state(harness, HarnessState.IDLE)

            assert len(fake.invocations) == 2
            # Round 1: the continuation — no user turn appended.
            assert fake.invocations[0].get("_resume_continuation") is True
            # Round 2: the task-plan continuation — an ordinary round the query
            # drives (and which does append it, as any normal round would).
            assert fake.invocations[1].get("_resume_continuation") is not True
            assert fake.invocations[1]["query"] == "original task"
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_cold_resume_without_query_cannot_drive_the_task_plan() -> None:
    """The counterpart: no query, no task-plan continuation.

    This is why the query is worth persisting across a stop/start — without it a
    cold-resumed leader stops after one round and never works off its plan.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)

        remaining = iter([True, False])
        harness._has_remaining_tasks = lambda session: next(remaining, False)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.resume(query="")
            assert await wait_for_state(harness, HarnessState.IDLE)
            assert len(fake.invocations) == 1
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_paused_query_is_exposed_for_persistence() -> None:
    """The coordination layer reads paused_query to persist the resume marker."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, iterations=2, sleep_seconds=5.0)
        fake.sleep_from_iteration = 1

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            assert harness.paused_query is None
            await harness.send("first")
            assert await wait_completed_iterations(fake, 1)
            await wait_invoke_running(fake)
            await harness.pause()

            assert harness.state is HarnessState.PAUSED
            assert harness.paused_query == "first"
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()
