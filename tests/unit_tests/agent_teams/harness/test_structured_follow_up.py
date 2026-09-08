# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Structured interrupt inputs queued while NativeHarness is running."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from openjiuwen.agent_teams.harness.interrupt_resume import (
    matches_pending_interrupt,
    tool_resume_scope_ids,
)
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.core.single_agent.agents.react_agent import (
    InterruptionState,
    WorkflowInterruptEntry,
)
from tests.unit_tests.agent_teams.harness.fixtures import (
    drain_outputs,
    make_spec,
    start_harness,
    wait_invoke_running,
    wait_for_state,
)


def _tool_state(*request_ids: str) -> ToolInterruptionState:
    return ToolInterruptionState(
        ai_message=AssistantMessage(content="approval required"),
        iteration=1,
        interrupted_tools={
            request_id: ToolInterruptEntry(
                tool_call=ToolCall(
                    id=request_id,
                    type="function",
                    name=f"tool_{request_id}",
                    arguments="{}",
                ),
                interrupt_requests={
                    request_id: InterruptRequest(message="approve?")
                },
            )
            for request_id in request_ids
        },
    )


def _approval(*request_ids: str) -> InteractiveInput:
    value = InteractiveInput()
    for request_id in request_ids:
        value.update(request_id, {"approved": True})
    return value


def _workflow_state(
    component_id: str = "component-1",
    workflow_id: str = "workflow-1",
) -> InterruptionState:
    return InterruptionState(
        ai_message=AssistantMessage(content="workflow input required"),
        iteration=1,
        interrupted_workflows={
            workflow_id: WorkflowInterruptEntry(
                tool_call=ToolCall(
                    id=workflow_id,
                    type="function",
                    name="workflow_tool",
                    arguments="{}",
                ),
                component_ids=[component_id],
                workflow_execution_state={},
            )
        },
        pending_workflow_id=workflow_id,
        pending_component_id=component_id,
    )


@pytest.mark.level1
@pytest.mark.parametrize("raw_value", ["", [], {"approved": False}])
def test_workflow_raw_input_matches_pending_component(raw_value: Any) -> None:
    assert matches_pending_interrupt(
        InteractiveInput(raw_inputs=raw_value),
        _workflow_state(),
    )


@pytest.mark.level1
def test_workflow_keyed_input_matches_only_pending_component() -> None:
    matching = InteractiveInput()
    matching.update("component-1", "answer")
    mismatched = InteractiveInput()
    mismatched.update("component-2", "answer")

    state = _workflow_state()
    assert matches_pending_interrupt(matching, state)
    assert not matches_pending_interrupt(mismatched, state)


@pytest.mark.level1
def test_workflow_match_does_not_create_tool_admission_scope() -> None:
    candidate = InteractiveInput()
    candidate.update("shared-id", "answer")
    session = type(
        "WorkflowSession",
        (),
        {"get_state": lambda self, key: _workflow_state("shared-id")},
    )()

    assert matches_pending_interrupt(candidate, session.get_state(INTERRUPTION_KEY))
    assert tool_resume_scope_ids(candidate, session) == frozenset()


async def _wait_for_invocations(fake: Any, count: int) -> None:
    deadline = asyncio.get_running_loop().time() + 3.0
    while len(fake.invocations) < count and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert len(fake.invocations) == count


@pytest.mark.asyncio
@pytest.mark.level1
async def test_graceful_abort_settlement_discards_structured_follow_ups() -> None:
    """A cycle-local resume cannot survive the round it was queued behind."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=0.15)
        collected: list[Any] = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("running")
            await wait_invoke_running(fake)
            queued = InteractiveInput(raw_inputs="queued resume")
            await harness.send(queued)
            assert [message.content for message in harness._st.pending_queue] == [queued]

            await harness.abort(immediate=False)

            assert await wait_for_state(harness, HarnessState.IDLE)
            assert list(harness._st.pending_queue) == []
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stop_discards_structured_follow_ups() -> None:
    """Terminal teardown drops transient structured resumes."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=0.15)
        collected: list[Any] = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("running")
            await wait_invoke_running(fake)
            queued = InteractiveInput(raw_inputs="queued resume")
            await harness.send(queued)
            assert [message.content for message in harness._st.pending_queue] == [queued]

            await harness.stop()

            assert list(harness._st.pending_queue) == []
        finally:
            if harness.state is not HarnessState.TERMINATED:
                await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
@pytest.mark.parametrize("follow_up_order", [("text", "approval"), ("approval", "text")])
async def test_structured_resume_precedes_text_across_reinterrupt(
    follow_up_order: tuple[str, str],
) -> None:
    """The matching structured reply starts before text while an interrupt remains."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        session = harness.loop_session
        session.update_state({INTERRUPTION_KEY: _tool_state("call-1", "call-2")})

        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        base_invoke = fake.invoke

        async def invoke(inputs: Any, invoke_session: Any, **kwargs: Any) -> dict:
            index = len(fake.invocations)
            query = inputs["query"]
            if index == 0:
                first_entered.set()
                await release_first.wait()
                await base_invoke(inputs, invoke_session, **kwargs)
                invoke_session.update_state({INTERRUPTION_KEY: _tool_state("call-2")})
                return {"output": "", "result_type": "interrupt"}
            if index == 1:
                assert query == second
                invoke_session.update_state({INTERRUPTION_KEY: None})
            else:
                assert query == text
            return await base_invoke(inputs, invoke_session, **kwargs)

        fake.invoke = invoke
        first = _approval("call-1")
        second = _approval("call-2")
        text = "ordinary text waits for the interrupt"
        collected: list[Any] = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(first)
            await asyncio.wait_for(first_entered.wait(), timeout=3.0)
            for item in follow_up_order:
                await harness.send(second if item == "approval" else text)
            release_first.set()
            await _wait_for_invocations(fake, 3)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            release_first.set()
            await harness.stop()
            await consumer

        assert [invocation["query"] for invocation in fake.invocations] == [
            first,
            second,
            text,
        ]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_immediate_structured_duplicate_is_queued_then_discarded() -> None:
    """A RUNNING structured input is never steering or a text batch item."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        session = harness.loop_session
        session.update_state({INTERRUPTION_KEY: _tool_state("call-1")})
        first = _approval("call-1")
        duplicate = _approval("call-1")
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        base_invoke = fake.invoke

        async def invoke(inputs: Any, invoke_session: Any, **kwargs: Any) -> dict:
            first_entered.set()
            await release_first.wait()
            result = await base_invoke(inputs, invoke_session, **kwargs)
            invoke_session.update_state({INTERRUPTION_KEY: None})
            return result

        fake.invoke = invoke
        collected: list[Any] = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(first)
            await asyncio.wait_for(first_entered.wait(), timeout=3.0)
            await harness.send(duplicate, immediate=True)
            assert [message.content for message in harness._st.pending_queue] == [duplicate]
            assert harness.loop_controller.drain_follow_up() == []
            release_first.set()
            assert await wait_for_state(harness, HarnessState.IDLE)
            await asyncio.sleep(0.05)
        finally:
            release_first.set()
            await harness.stop()
            await consumer

        assert [invocation["query"] for invocation in fake.invocations] == [first]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_structured_duplicate_retries_while_slot_is_still_pending() -> None:
    """Admission provenance must not discard a reply that was never consumed."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        session = harness.loop_session
        session.update_state({INTERRUPTION_KEY: _tool_state("call-1")})
        first = _approval("call-1")
        retry = _approval("call-1")
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        base_invoke = fake.invoke

        async def invoke(inputs: Any, invoke_session: Any, **kwargs: Any) -> dict:
            index = len(fake.invocations)
            if index == 0:
                first_entered.set()
                await release_first.wait()
            else:
                invoke_session.update_state({INTERRUPTION_KEY: None})
            return await base_invoke(inputs, invoke_session, **kwargs)

        fake.invoke = invoke
        collected: list[Any] = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(first)
            await asyncio.wait_for(first_entered.wait(), timeout=3.0)
            await harness.send(retry)
            release_first.set()
            await _wait_for_invocations(fake, 2)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            release_first.set()
            await harness.stop()
            await consumer

        assert [invocation["query"] for invocation in fake.invocations] == [first, retry]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_auto_confirmed_sibling_duplicate_is_not_replayed() -> None:
    """The active round's full tool scope proves an indirectly consumed sibling."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        session = harness.loop_session
        session.update_state({INTERRUPTION_KEY: _tool_state("call-1", "call-2")})
        first = _approval("call-1")
        sibling = _approval("call-2")
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        base_invoke = fake.invoke

        async def invoke(inputs: Any, invoke_session: Any, **kwargs: Any) -> dict:
            first_entered.set()
            await release_first.wait()
            result = await base_invoke(inputs, invoke_session, **kwargs)
            # Mirrors auto-confirm: handling call-1 consumes call-2 as part of
            # the same pending tool scope, so neither request remains pending.
            invoke_session.update_state({INTERRUPTION_KEY: None})
            return result

        fake.invoke = invoke
        collected: list[Any] = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(first)
            await asyncio.wait_for(first_entered.wait(), timeout=3.0)
            await harness.send(sibling)
            release_first.set()
            assert await wait_for_state(harness, HarnessState.IDLE)
            await asyncio.sleep(0.05)
        finally:
            release_first.set()
            await harness.stop()
            await consumer

        assert [invocation["query"] for invocation in fake.invocations] == [first]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_partially_consumed_multi_id_approval_keeps_pending_fields() -> None:
    """Duplicate filtering removes consumed fields, not the whole approval."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        session = harness.loop_session
        session.update_state({INTERRUPTION_KEY: _tool_state("call-1", "call-2")})
        first = _approval("call-1")
        combined = _approval("call-1", "call-2")
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        base_invoke = fake.invoke

        async def invoke(inputs: Any, invoke_session: Any, **kwargs: Any) -> dict:
            if not fake.invocations:
                first_entered.set()
                await release_first.wait()
                result = await base_invoke(inputs, invoke_session, **kwargs)
                invoke_session.update_state({INTERRUPTION_KEY: _tool_state("call-2")})
                return result
            invoke_session.update_state({INTERRUPTION_KEY: None})
            return await base_invoke(inputs, invoke_session, **kwargs)

        fake.invoke = invoke
        collected: list[Any] = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(first)
            await asyncio.wait_for(first_entered.wait(), timeout=3.0)
            await harness.send(combined)
            release_first.set()
            await _wait_for_invocations(fake, 2)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            release_first.set()
            await harness.stop()
            await consumer

        second_query = fake.invocations[1]["query"]
        assert isinstance(second_query, InteractiveInput)
        assert second_query.user_inputs == {"call-2": {"approved": True}}
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
@pytest.mark.parametrize(
    ("next_workflow_id", "next_component_id"),
    [
        ("workflow-1", "component-2"),
        ("workflow-2", "component-1"),
    ],
)
async def test_queued_raw_workflow_reply_stays_bound_to_admission_slot(
    next_workflow_id: str,
    next_component_id: str,
) -> None:
    """A duplicate raw reply must not answer a different workflow slot."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        session = harness.loop_session
        session.update_state({INTERRUPTION_KEY: _workflow_state("component-1")})
        first = InteractiveInput(raw_inputs="first answer")
        duplicate = InteractiveInput(raw_inputs="duplicate first answer")
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        base_invoke = fake.invoke

        async def invoke(inputs: Any, invoke_session: Any, **kwargs: Any) -> dict:
            first_entered.set()
            await release_first.wait()
            result = await base_invoke(inputs, invoke_session, **kwargs)
            invoke_session.update_state(
                {
                    INTERRUPTION_KEY: _workflow_state(
                        next_component_id,
                        next_workflow_id,
                    )
                }
            )
            return result

        fake.invoke = invoke
        collected: list[Any] = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(first)
            await asyncio.wait_for(first_entered.wait(), timeout=3.0)
            await harness.send(duplicate)
            release_first.set()
            assert await wait_for_state(harness, HarnessState.IDLE)
            await asyncio.sleep(0.05)
            assert [item["query"] for item in fake.invocations] == [first]
            assert len(harness._st.pending_queue) == 1
            queued = harness._st.pending_queue[0]
            assert queued.content == duplicate
            assert queued.admitted_workflow_slot == (
                "workflow-1",
                "component-1",
            )
        finally:
            release_first.set()
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
@pytest.mark.parametrize(
    ("next_workflow_id", "next_component_id"),
    [
        ("workflow-1", "component-2"),
        ("workflow-2", "component-1"),
    ],
)
async def test_queued_keyed_workflow_reply_stays_bound_to_admission_slot(
    next_workflow_id: str,
    next_component_id: str,
) -> None:
    """A queued keyed reply cannot be retargeted to another workflow slot."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        session = harness.loop_session
        session.update_state({INTERRUPTION_KEY: _workflow_state("component-1")})
        first = _approval("component-1")
        queued_reply = _approval("component-1")
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        base_invoke = fake.invoke

        async def invoke(inputs: Any, invoke_session: Any, **kwargs: Any) -> dict:
            first_entered.set()
            await release_first.wait()
            result = await base_invoke(inputs, invoke_session, **kwargs)
            invoke_session.update_state(
                {
                    INTERRUPTION_KEY: _workflow_state(
                        next_component_id,
                        next_workflow_id,
                    )
                }
            )
            return result

        fake.invoke = invoke
        collected: list[Any] = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(first)
            await asyncio.wait_for(first_entered.wait(), timeout=3.0)
            await harness.send(queued_reply)
            release_first.set()
            assert await wait_for_state(harness, HarnessState.IDLE)
            await asyncio.sleep(0.05)
            assert [item["query"] for item in fake.invocations] == [first]
            assert [message.content for message in harness._st.pending_queue] == [queued_reply]
            if next_workflow_id == "workflow-2":
                assert harness._st.pending_queue[0].admitted_workflow_slot == (
                    "workflow-1",
                    "component-1",
                )
        finally:
            release_first.set()
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_queued_keyed_workflow_retry_starts_for_same_admission_slot() -> None:
    """A keyed reply retries as its original structured object for the same slot."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        session = harness.loop_session
        session.update_state({INTERRUPTION_KEY: _workflow_state("component-1")})
        first = _approval("component-1")
        retry = _approval("component-1")
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        base_invoke = fake.invoke

        async def invoke(inputs: Any, invoke_session: Any, **kwargs: Any) -> dict:
            if not fake.invocations:
                first_entered.set()
                await release_first.wait()
            return await base_invoke(inputs, invoke_session, **kwargs)

        fake.invoke = invoke
        collected: list[Any] = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(first)
            await asyncio.wait_for(first_entered.wait(), timeout=3.0)
            await harness.send(retry)
            release_first.set()
            await _wait_for_invocations(fake, 2)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            release_first.set()
            await harness.stop()
            await consumer

        second_query = fake.invocations[1]["query"]
        assert isinstance(second_query, InteractiveInput)
        assert second_query == retry
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_keyed_workflow_admission_does_not_match_tool_with_same_id() -> None:
    """Workflow provenance prevents a keyed reply from becoming a tool reply."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        session = harness.loop_session
        session.update_state({INTERRUPTION_KEY: _workflow_state("shared-id")})
        first = _approval("shared-id")
        queued_reply = _approval("shared-id")
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        base_invoke = fake.invoke

        async def invoke(inputs: Any, invoke_session: Any, **kwargs: Any) -> dict:
            first_entered.set()
            await release_first.wait()
            result = await base_invoke(inputs, invoke_session, **kwargs)
            invoke_session.update_state({INTERRUPTION_KEY: _tool_state("shared-id")})
            return result

        fake.invoke = invoke
        collected: list[Any] = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(first)
            await asyncio.wait_for(first_entered.wait(), timeout=3.0)
            await harness.send(queued_reply)
            release_first.set()
            assert await wait_for_state(harness, HarnessState.IDLE)
            await asyncio.sleep(0.05)
            assert [item["query"] for item in fake.invocations] == [first]
            assert [message.content for message in harness._st.pending_queue] == [queued_reply]
        finally:
            release_first.set()
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()
