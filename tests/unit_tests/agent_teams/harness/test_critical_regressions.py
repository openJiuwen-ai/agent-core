# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for the critical bugs found in the NativeHarness review.

Each test would FAIL against the pre-fix implementation and passes after the
invoke-based rewrite + targeted fixes.
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.runner import Runner
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import (
    IterationStep,
    MockDeepAgent,
    drain_outputs,
)


@pytest.mark.asyncio
async def test_multi_round_second_round_receives_chunks() -> None:
    """Fatal #1: reusing one session must not close the stream between rounds."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(is_answer=True, answer_output="r1"),
    ]
    harness = NativeHarness(lambda: agent)
    await harness.start()

    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    try:
        await harness.send("q1")
        await asyncio.sleep(0.05)  # round 1 done -> IDLE
        agent.react_agent.iteration_script = [
            IterationStep(is_answer=True, answer_output="r2"),
        ]
        await harness.send("q2")
        await asyncio.sleep(0.05)
    finally:
        await harness.stop()
        await consumer

    answers = [
        c.payload for c in collected if getattr(c, "type", None) == "answer"
    ]
    outputs = [a["output"] for a in answers]
    # Pre-fix: round 2 streamed into a closed emitter -> only "r1" arrives.
    assert outputs == ["r1", "r2"]


@pytest.mark.asyncio
async def test_immediate_abort_actually_stops_invoke() -> None:
    """Fatal #2: cancelling the round must stop the invoke work, not orphan it."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[], sleep_before=0.2),
        IterationStep(chunks=[{"v": "should_never_run"}]),
    ]
    harness = NativeHarness(lambda: agent)
    await harness.start()

    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    try:
        await harness.send("go")
        await asyncio.sleep(0.05)  # inside step 1's sleep_before
        await harness.abort(immediate=True)
        # Give any orphaned work time to (wrongly) proceed.
        await asyncio.sleep(0.3)
        # invoke was cancelled mid-sleep: no step completed, ever.
        assert agent.react_agent.steps_executed == 0
        assert harness.state is HarnessState.IDLE
    finally:
        await harness.stop()
        await consumer


@pytest.mark.asyncio
async def test_rollback_preserves_history_without_duplication() -> None:
    """Fatal #3: rollback must not duplicate the persisted history segment."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[{"v": "t1"}]),         # completes -> snapshot
        IterationStep(chunks=[], sleep_before=5.0),   # cancelled here
    ]
    harness = NativeHarness(lambda: agent)
    await harness.start()

    ctx = agent.react_agent.context_engine.get_context(session_id=harness.session_id)
    ctx.seed_history([UserMessage(content="H1"), UserMessage(content="H2")])

    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    try:
        await harness.send("go")
        await asyncio.sleep(0.1)  # iteration 1 done, iteration 2 sleeping
        await harness.abort(immediate=True)
        await asyncio.sleep(0.05)

        all_msgs = ctx.get_messages(with_history=True)
        history_count = sum(
            1 for m in all_msgs if getattr(m, "content", "") in ("H1", "H2")
        )
        # Pre-fix (snapshot with_history=True): history duplicated -> 4.
        assert history_count == 2
    finally:
        await harness.stop()
        await consumer


@pytest.mark.asyncio
async def test_supervisor_handler_crash_does_not_hang_caller() -> None:
    """Finding #4: a crashing handler must reject the caller's ack, not hang it."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [IterationStep(is_answer=True)]

    def boom(session):  # noqa: ANN001, ANN202
        raise RuntimeError("snapshot boom")

    # _start_round -> capture_snapshot -> load_state; make it explode.
    agent.load_state = boom

    harness = NativeHarness(lambda: agent)
    await harness.start()
    try:
        # send triggers _on_send -> _start_round -> crash; ack must be rejected.
        with pytest.raises(Exception):  # noqa: B017
            await asyncio.wait_for(harness.send("trigger"), timeout=2.0)
        assert harness.state is HarnessState.TERMINATED
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_graceful_abort_then_send_does_not_restart() -> None:
    """Finding #7: a send during graceful drain must not start a new round."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[{"v": "t1"}], sleep_after=0.1),
        IterationStep(is_answer=True, answer_output="done"),
    ]
    harness = NativeHarness(lambda: agent)
    await harness.start()

    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    try:
        await harness.send("q1")
        await asyncio.sleep(0.02)  # inside iteration 1
        await harness.abort(immediate=False)
        await harness.send("q2", immediate=False)  # arrives during drain window
        for _ in range(50):
            if harness.state is HarnessState.IDLE:
                break
            await asyncio.sleep(0.02)
        assert harness.state is HarnessState.IDLE
        # q2 must NOT have started a new round.
        assert len(agent.react_agent.invocations) == 1
    finally:
        await harness.stop()
        await consumer


@pytest.mark.asyncio
async def test_pause_resume_does_not_duplicate_query() -> None:
    """Finding #8: pause rolls back to pre-round, so resume doesn't duplicate query."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[{"v": "t1"}]),          # completes (context holds "first")
        IterationStep(chunks=[], sleep_before=5.0),    # paused here
    ]
    harness = NativeHarness(lambda: agent)
    await harness.start()

    ctx = agent.react_agent.context_engine.get_context(session_id=harness.session_id)
    collected: list = []
    consumer = asyncio.create_task(drain_outputs(harness, collected))
    try:
        await harness.send("first")
        await asyncio.sleep(0.1)  # iteration 1 done, iteration 2 sleeping
        await harness.pause()

        agent.react_agent.iteration_script = [
            IterationStep(is_answer=True, answer_output="ok"),
        ]
        await harness.send("addendum")
        await asyncio.sleep(0.05)

        contents = [getattr(m, "content", "") for m in ctx.get_messages(with_history=True)]
        # Pre-fix (rollback to last_safe_snapshot): a stray "first" survives.
        assert contents.count("first") == 0
        assert "first\naddendum" in contents
    finally:
        await harness.stop()
        await consumer
