# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness lifecycle events (harness.state / harness.round) and resume.

These cover the stage-2 base layer: the harness fires phase and round events on
its private callback framework so a consumer (the team StreamController) can map
them onto MemberStatus / ExecutionStatus without polling, and a ``send`` of an
``InteractiveInput`` resumes an interrupted turn through the same ``submit_round``
path (the executor extracts the InteractiveInput and the inner ReAct agent
resumes).

Events fire inside the supervisor coroutine, so the recorded ordering matches
the real phase/round transitions exactly. Callbacks declare only the kwargs they
need (the framework narrows the payload), which these tests exercise directly.
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import (
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
    wait_invoke_running,
)


@pytest.mark.asyncio
async def test_state_and_round_events_single_round() -> None:
    """One round fires IDLE->RUNNING->IDLE and round started->finished, in order."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        await start_harness(harness, answer_output="hi")

        states: list = []
        rounds: list = []

        async def _on_state(old, new) -> None:
            states.append((old, new))

        async def _on_round(kind) -> None:
            rounds.append(kind)

        await harness.on_state_changed(_on_state)
        await harness.on_round(_on_round)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("hello")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        # Ignore the teardown transition to TERMINATED that stop() fires.
        non_terminal = [(o, n) for o, n in states if n is not HarnessState.TERMINATED]
        assert non_terminal == [
            (HarnessState.IDLE, HarnessState.RUNNING),
            (HarnessState.RUNNING, HarnessState.IDLE),
        ]
        assert rounds == ["started", "finished"]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_state_callback_receives_session_id_when_declared() -> None:
    """A callback declaring session_id receives it; the framework narrows kwargs."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        await start_harness(harness, answer_output="hi")

        seen: list = []

        async def _on_state(old, new, session_id) -> None:
            seen.append((new, session_id))

        await harness.on_state_changed(_on_state)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("hello")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        # Every payload carried the harness session id.
        assert seen, "no state events recorded"
        assert all(sid == harness.session_id for _, sid in seen)
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_followup_round_emits_second_started_without_returning_to_idle() -> None:
    """A follow-up keeps the harness RUNNING: round started fires twice, state stays."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=0.05)

        states: list = []
        rounds: list = []

        async def _on_state(old, new) -> None:
            states.append((old, new))

        async def _on_round(kind) -> None:
            rounds.append(kind)

        await harness.on_state_changed(_on_state)
        await harness.on_round(_on_round)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("q1")
            assert await wait_for_state(harness, HarnessState.RUNNING)
            await harness.send("q2", immediate=False)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert [inv["query"] for inv in fake.invocations] == ["q1", "q2"]
        # Two rounds: started/finished twice; the follow-up restart does not pass
        # through an extra RUNNING->IDLE->RUNNING in the phase machine.
        assert rounds == ["started", "finished", "started", "finished"]
        # Ignore the teardown transition to TERMINATED that stop() fires.
        non_terminal = [(o, n) for o, n in states if n is not HarnessState.TERMINATED]
        assert non_terminal == [
            (HarnessState.IDLE, HarnessState.RUNNING),
            (HarnessState.RUNNING, HarnessState.IDLE),
        ]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_immediate_abort_emits_aborted_event() -> None:
    """Immediate abort fires round 'aborted' and walks state RUNNING->IDLE."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=30.0)

        states: list = []
        rounds: list = []

        async def _on_state(old, new) -> None:
            states.append((old, new))

        async def _on_round(kind) -> None:
            rounds.append(kind)

        await harness.on_state_changed(_on_state)
        await harness.on_round(_on_round)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("slow")
            await wait_invoke_running(fake)
            await harness.abort(immediate=True)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert rounds == ["started", "aborted"]
        assert (HarnessState.RUNNING, HarnessState.IDLE) in states
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_pause_emits_paused_event() -> None:
    """Pause fires round 'paused' and walks state RUNNING->PAUSED."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=30.0)

        states: list = []
        rounds: list = []

        async def _on_state(old, new) -> None:
            states.append((old, new))

        async def _on_round(kind) -> None:
            rounds.append(kind)

        await harness.on_state_changed(_on_state)
        await harness.on_round(_on_round)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("slow")
            await wait_invoke_running(fake)
            await harness.pause()
            assert await wait_for_state(harness, HarnessState.PAUSED)
        finally:
            await harness.stop()
            await consumer

        assert rounds == ["started", "paused"]
        assert (HarnessState.RUNNING, HarnessState.PAUSED) in states
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_resume_round_via_send_interactive_input() -> None:
    """send(InteractiveInput) starts a resume round through submit_round.

    The task-loop executor extracts the InteractiveInput and hands it to the
    inner agent (so the fake observes an InteractiveInput query), and the resume
    round settles to IDLE without continuing the task plan.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="resumed")

        rounds: list = []

        async def _on_round(kind) -> None:
            rounds.append(kind)

        await harness.on_round(_on_round)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            resume = InteractiveInput("user-answer")
            seq = await harness.send(resume)
            assert seq == 1
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        # Exactly one inner invocation, and it carried the InteractiveInput (the
        # executor extracted it as the resume payload rather than a fresh query).
        assert len(fake.invocations) == 1
        assert isinstance(fake.invocations[0]["query"], InteractiveInput)
        assert rounds == ["started", "finished"]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_subscribers_cleared_on_stop() -> None:
    """stop() unregisters the harness-private namespace so no callbacks linger."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        await start_harness(harness)

        async def _on_state(old, new) -> None:
            return None

        await harness.on_state_changed(_on_state)
        assert harness._events.callbacks.get("harness.state")

        await harness.stop()
        assert not harness._events.callbacks.get("harness.state")
    finally:
        await Runner.stop()
