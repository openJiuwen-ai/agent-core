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

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.agents.react_agent import (
    InterruptionState,
    WorkflowInterruptEntry,
)
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from openjiuwen.harness.schema.task import TaskPlan, TodoItem
from tests.unit_tests.agent_teams.harness.fixtures import (
    answer_outputs,
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
)


def workflow_interruption_state(component_id: str) -> InterruptionState:
    """Build the workflow state shape committed by ReActAgent."""
    return InterruptionState(
        ai_message=AssistantMessage(content="waiting for workflow input"),
        iteration=1,
        interrupted_workflows={
            "workflow-1": WorkflowInterruptEntry(
                tool_call=ToolCall(
                    id="workflow-call-1",
                    type="function",
                    name="workflow-1",
                    arguments="{}",
                ),
                component_ids=[component_id],
                workflow_execution_state=object(),
            ),
        },
        pending_workflow_id="workflow-1",
        pending_component_id=component_id,
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


@pytest.mark.asyncio
@pytest.mark.level1
async def test_workflow_resume_runtime_crash_runs_queued_next_resume() -> None:
    """A terminal resume crash must not strand the next workflow input."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="done")
        session = harness._session
        session.update_state({INTERRUPTION_KEY: workflow_interruption_state("component-1")})

        next_interrupt_committed = asyncio.Event()
        next_resume_queued = asyncio.Event()
        base_invoke = fake.invoke
        invocation_count = 0

        async def invoke(inputs, invoke_session, **kwargs):
            nonlocal invocation_count
            invocation_count += 1
            query = inputs.get("query") if isinstance(inputs, dict) else inputs
            if invocation_count == 1:
                assert isinstance(query, InteractiveInput)
                assert query.raw_inputs == "first workflow input"
                invoke_session.update_state(
                    {INTERRUPTION_KEY: workflow_interruption_state("component-2")}
                )
                next_interrupt_committed.set()
                await asyncio.wait_for(next_resume_queued.wait(), timeout=3.0)
            return await base_invoke(inputs, invoke_session, **kwargs)

        fake.invoke = invoke
        base_write = harness._write_round_result_to_stream
        write_count = 0

        async def write_result(result, write_session):
            nonlocal write_count
            write_count += 1
            if write_count == 1:
                raise RuntimeError("stream failed after workflow state commit")
            await base_write(result, write_session)

        harness._write_round_result_to_stream = write_result

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(InteractiveInput(raw_inputs="first workflow input"))
            await asyncio.wait_for(next_interrupt_committed.wait(), timeout=3.0)
            await harness.send(InteractiveInput(raw_inputs="second workflow input"))
            next_resume_queued.set()

            assert await wait_for_state(harness, HarnessState.IDLE)
            assert invocation_count == 2
            queries = [inv["query"] for inv in fake.invocations]
            assert [query.raw_inputs for query in queries] == [
                "first workflow input",
                "second workflow input",
            ]
            assert harness.loop_controller.drain_follow_up() == []
            assert harness.load_state(session).pending_follow_ups == []
        finally:
            next_resume_queued.set()
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_second_runtime_crash_runs_queued_text_after_one_shot_replay() -> None:
    """The one-shot replay keeps priority, then terminal crash drains text."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="recovered")

        retry_entered = asyncio.Event()
        follow_up_queued = asyncio.Event()
        base_invoke = fake.invoke
        invocation_count = 0

        async def invoke(inputs, invoke_session, **kwargs):
            nonlocal invocation_count
            invocation_count += 1
            if invocation_count == 2:
                retry_entered.set()
                await asyncio.wait_for(follow_up_queued.wait(), timeout=3.0)
            return await base_invoke(inputs, invoke_session, **kwargs)

        fake.invoke = invoke
        base_write = harness._write_round_result_to_stream
        write_count = 0

        async def write_result(result, write_session):
            nonlocal write_count
            write_count += 1
            if write_count <= 2:
                raise RuntimeError(f"stream failure {write_count}")
            await base_write(result, write_session)

        harness._write_round_result_to_stream = write_result

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("original query")
            await asyncio.wait_for(retry_entered.wait(), timeout=3.0)
            await harness.send("queued after retry")
            follow_up_queued.set()

            assert await wait_for_state(harness, HarnessState.IDLE)
            queries = [inv["query"] for inv in fake.invocations]
            assert queries == [
                "original query",
                "original query",
                "queued after retry",
            ]
        finally:
            follow_up_queued.set()
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_terminal_runtime_crash_without_follow_up_does_not_continue_task_plan() -> None:
    """Terminal crash remains terminal when no queued input can start a round."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="never written")
        session = harness._session
        state = harness.load_state(session)
        state.task_plan = TaskPlan(
            goal="finish the work",
            tasks=[TodoItem(id="t1", content="remaining task")],
        )
        harness.save_state(session, state)

        async def fail_write(_result, _session):
            raise RuntimeError("persistent stream failure")

        harness._write_round_result_to_stream = fail_write

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("doomed query")
            assert await wait_for_state(harness, HarnessState.IDLE)
            await asyncio.sleep(0.1)
            assert [inv["query"] for inv in fake.invocations] == [
                "doomed query",
                "doomed query",
            ]
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()
