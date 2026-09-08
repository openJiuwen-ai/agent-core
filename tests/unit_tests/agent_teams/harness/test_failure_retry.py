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
from typing import Any, Callable

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.agents.react_agent import (
    InterruptionState,
    WorkflowInterruptEntry,
)
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import (
    answer_outputs,
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
)


def _workflow_state() -> InterruptionState:
    workflow_id = "workflow-1"
    return InterruptionState(
        ai_message=AssistantMessage(content="workflow input required"),
        iteration=0,
        interrupted_workflows={
            workflow_id: WorkflowInterruptEntry(
                tool_call=ToolCall(
                    id=workflow_id,
                    type="function",
                    name="workflow_tool",
                    arguments="{}",
                ),
                component_ids=["component-1"],
                workflow_execution_state={},
            )
        },
        pending_workflow_id=workflow_id,
        pending_component_id="component-1",
    )


def _crash_initial_submits(
    harness: NativeHarness,
    *,
    crash_count: int = 1,
    before_crash: Callable[[], None] | None = None,
) -> tuple[asyncio.Event, asyncio.Event, list[Any]]:
    original_submit = harness.loop_controller.submit_round
    entered = asyncio.Event()
    release = asyncio.Event()
    submit_count = 0
    submitted_queries: list[Any] = []

    async def submit(*args: Any, **kwargs: Any) -> Any:
        nonlocal submit_count
        submit_count += 1
        submitted_queries.append(args[1])
        if submit_count <= crash_count:
            entered.set()
            await release.wait()
            if before_crash is not None:
                before_crash()
            raise RuntimeError("round driver crashed")
        return await original_submit(*args, **kwargs)

    harness.loop_controller.submit_round = submit
    return entered, release, submitted_queries


async def _wait_for_invocations(fake: Any, count: int) -> None:
    deadline = asyncio.get_running_loop().time() + 3.0
    while len(fake.invocations) < count and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert len(fake.invocations) == count


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


@pytest.mark.asyncio
@pytest.mark.level1
async def test_runtime_crash_prioritizes_committed_interrupt_before_text() -> None:
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        session = harness.loop_session
        entered, release, _ = _crash_initial_submits(harness)
        approval = InteractiveInput(raw_inputs="")
        base_invoke = fake.invoke

        async def invoke(inputs: Any, invoke_session: Any, **kwargs: Any) -> dict:
            if isinstance(inputs["query"], InteractiveInput):
                invoke_session.update_state({INTERRUPTION_KEY: None})
            return await base_invoke(inputs, invoke_session, **kwargs)

        fake.invoke = invoke
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("query that committed an interrupt")
            await entered.wait()
            session.update_state({INTERRUPTION_KEY: _workflow_state()})
            await harness.send(approval)
            await harness.send("text waits behind approval")
            release.set()
            await _wait_for_invocations(fake, 2)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            release.set()
            await harness.stop()
            await consumer

        queries = [item["query"] for item in fake.invocations]
        assert isinstance(queries[0], InteractiveInput)
        assert queries[0].raw_inputs == ""
        assert queries[0].user_inputs == {}
        assert queries[1] == "text waits behind approval"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_runtime_crash_does_not_replay_or_drain_text_across_interrupt() -> None:
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        session = harness.loop_session
        entered, release, _ = _crash_initial_submits(
            harness,
            before_crash=lambda: session.update_state({INTERRUPTION_KEY: _workflow_state()}),
        )
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("query that committed an interrupt")
            await entered.wait()
            await harness.send("text cannot answer workflow input")
            release.set()
            assert await wait_for_state(harness, HarnessState.IDLE)
            await asyncio.sleep(0.05)
        finally:
            release.set()
            await harness.stop()
            await consumer

        assert fake.invocations == []
        assert harness.load_state(session).pending_follow_ups == [
            "text cannot answer workflow input"
        ]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_interactive_input_crash_settles_queued_text_without_replay() -> None:
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        entered, release, _ = _crash_initial_submits(harness)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(InteractiveInput(raw_inputs="answer"))
            await entered.wait()
            await harness.send("continue after failed resume")
            release.set()
            await _wait_for_invocations(fake, 1)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            release.set()
            await harness.stop()
            await consumer

        assert [item["query"] for item in fake.invocations] == [
            "continue after failed resume"
        ]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_runtime_crash_without_interrupt_replays_plain_query_once() -> None:
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        entered, release, _ = _crash_initial_submits(harness)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("retry this query")
            await entered.wait()
            release.set()
            await _wait_for_invocations(fake, 1)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            release.set()
            await harness.stop()
            await consumer

        assert [item["query"] for item in fake.invocations] == ["retry this query"]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_second_runtime_crash_settles_follow_up_without_third_replay() -> None:
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        entered, release, submitted_queries = _crash_initial_submits(
            harness,
            crash_count=2,
        )
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("replay this query once")
            await entered.wait()
            await harness.send("settle this after the retry crashes")
            release.set()
            await _wait_for_invocations(fake, 1)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            release.set()
            await harness.stop()
            await consumer

        assert submitted_queries == [
            "replay this query once",
            "replay this query once",
            ["settle this after the retry crashes"],
        ]
        assert [item["query"] for item in fake.invocations] == [
            "settle this after the retry crashes"
        ]
    finally:
        await Runner.stop()
