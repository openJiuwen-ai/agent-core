# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Idempotency for a double-delivered interrupt approval.

``resume_interrupt`` releases ``_interrupt_lock`` before ``harness.send`` (to
break the hold-and-wait deadlock with ``_on_idle_settled``), so check-then-send
is no longer atomic under the lock. A double-delivered approval can therefore
reach the supervisor as a second ``_CmdSend`` that parks as a follow-up while
the first resume round runs. When that round consumes the interrupt and
settles, ``_on_round_done`` must drop the stale InteractiveInput follow-up
instead of starting a spurious round that re-resumes an already-cleared slot.

The keep branch is the counterpart contract: an InteractiveInput follow-up
whose slot is STILL pending (the prior round ended before consuming it) is a
legitimate retry and must be kept, not dropped.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.harness.schema.task import TaskPlan, TodoItem
from tests.unit_tests.agent_teams.harness.fixtures import (
    answer_outputs,
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
)


def script_first_round_interrupt(fake: Any, harness: Any, observed_phases: list) -> None:
    """Make exactly the first round settle with ``result_type="interrupt"``.

    ``FakeReactAgent.invoke`` hardcodes ``result_type="answer"``; this wrapper
    swaps the first round's result for an interrupt payload — the shape a real
    permission-gated round returns (``ToolInterruptHandler.build_interrupt_result``).
    After each round's inner work it records ``harness.state``, so assertions
    can pin the phase a follow-up round runs under: the settle-time drain must
    start it while still RUNNING — never after an IDLE bounce.
    """
    base_invoke = fake.invoke

    async def invoke(inputs: Any, session: Any, **kwargs: Any) -> dict:
        result = await base_invoke(inputs, session, **kwargs)
        observed_phases.append(harness.state)
        if len(fake.invocations) == 1:
            return {"output": "", "result_type": "interrupt"}
        return result

    fake.invoke = invoke


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stale_interactive_input_follow_up_dropped_after_slot_consumed() -> None:
    """A duplicate InteractiveInput follow-up whose interrupt slot is already
    cleared must not start a spurious round.

    The first round runs a normal query (no interrupt involved). The stale
    InteractiveInput was enqueued as a follow-up; when the first round settles,
    its interrupt slot is gone (never set), so the guard in ``_on_round_done``
    drops it. Only the first round's query is ever invoked.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="done")

        stale = InteractiveInput()
        stale.update("call-x", {"approved": True, "feedback": "", "auto_confirm": False})
        # No INTERRUPTION_KEY seeded on the session → the slot is cleared, so
        # this follow-up is stale and must be dropped, not run as a round.
        harness.loop_controller.enqueue_follow_up(stale)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("do the thing")
            assert await wait_for_state(harness, HarnessState.IDLE)
            # Let any unintended spurious follow-up round surface before counting.
            await asyncio.sleep(0.1)
        finally:
            await harness.stop()
            await consumer

        queries = [inv.get("query") for inv in fake.invocations]
        assert queries == ["do the thing"]  # stale InteractiveInput NOT run
        assert answer_outputs(collected) == ["done"]
        assert harness.state is HarnessState.TERMINATED
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_pending_interrupt_follow_up_kept_as_retry_when_slot_still_pending() -> None:
    """An InteractiveInput follow-up whose interrupt slot is still pending is
    kept and starts a retry round, not dropped.

    The keep branch of the settle-time filter: the prior round ended before
    consuming the slot, so ``INTERRUPTION_KEY`` still holds the tool's
    interrupt request. Dropping the follow-up here would strand the approval —
    this is the legitimate-retry half of the idempotency contract (the drop
    test above is the duplicate half).

    The kept InteractiveInput is started as a SINGLE-object round (bypassing
    the batch text pipeline, whose ``InputEvent.from_user_input(list)`` would
    str-ify it), so the retry round's inner query is the InteractiveInput
    object itself and the structured approval reaches the interrupt rail.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="done")

        # Seed a still-pending interrupt slot for call-2 — the exact shape the
        # real react_agent commits at the interrupt rail and both
        # TeamHarness.is_pending_interrupt_resume_valid and
        # NativeHarness._interrupt_resume_still_pending read back.
        session = harness._session
        session.update_state(
            {
                INTERRUPTION_KEY: ToolInterruptionState(
                    ai_message=AssistantMessage(content="requesting approval"),
                    iteration=1,
                    interrupted_tools={
                        "call-2": ToolInterruptEntry(
                            tool_call=ToolCall(
                                id="call-2",
                                type="function",
                                name="needs_approval",
                                arguments="{}",
                            ),
                            interrupt_requests={
                                "call-2": InterruptRequest(message="approve?")
                            },
                        ),
                    },
                )
            }
        )
        assert session.get_state(INTERRUPTION_KEY) is not None  # seed took

        resume = InteractiveInput()
        resume.update("call-2", {"approved": True, "feedback": "", "auto_confirm": False})
        # Slot still pending → the follow-up must survive the settle-time filter
        # and start its own retry round.
        harness.loop_controller.enqueue_follow_up(resume)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("do the thing")
            # First round settles keeping the follow-up, which starts a second
            # round. Wait for the second invocation directly — on the keep path
            # the harness never touches IDLE between the two rounds, so an
            # IDLE wait alone could hide a dropped follow-up.
            deadline = asyncio.get_running_loop().time() + 3.0
            while (
                asyncio.get_running_loop().time() < deadline
                and len(fake.invocations) < 2
            ):
                await asyncio.sleep(0.01)
            assert len(fake.invocations) == 2  # keep branch ran the retry round
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        queries = [inv.get("query") for inv in fake.invocations]
        assert queries[0] == "do the thing"
        # The retry round ran with the kept follow-up as a structured
        # InteractiveInput (single-object start, NOT the str-ifying batch path).
        assert isinstance(queries[1], InteractiveInput)
        assert set(queries[1].user_inputs.keys()) == {"call-2"}
        assert answer_outputs(collected) == ["done", "done"]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_mixed_batch_interactive_input_runs_single_then_texts_requeued() -> None:
    """In a mixed follow-up batch, the InteractiveInput runs alone first and
    the text follow-ups re-queue and run as their own round afterwards.

    Guards the settle-time split branch: the InteractiveInput must bypass the
    str-ifying batch pipeline (started single-object), while the text
    follow-ups keep their FIFO position — drained on the retry round's settle
    instead of being lost or mangled into it.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="done")

        # Same still-pending-slot seed as the keep test above.
        session = harness._session
        session.update_state(
            {
                INTERRUPTION_KEY: ToolInterruptionState(
                    ai_message=AssistantMessage(content="requesting approval"),
                    iteration=1,
                    interrupted_tools={
                        "call-2": ToolInterruptEntry(
                            tool_call=ToolCall(
                                id="call-2",
                                type="function",
                                name="needs_approval",
                                arguments="{}",
                            ),
                            interrupt_requests={
                                "call-2": InterruptRequest(message="approve?")
                            },
                        ),
                    },
                )
            }
        )

        resume = InteractiveInput()
        resume.update("call-2", {"approved": True, "feedback": "", "auto_confirm": False})
        # Worst ordering: the text follow-up queued BEFORE the InteractiveInput.
        harness.loop_controller.enqueue_follow_up("a plain text follow-up")
        harness.loop_controller.enqueue_follow_up(resume)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("do the thing")
            deadline = asyncio.get_running_loop().time() + 3.0
            while (
                asyncio.get_running_loop().time() < deadline
                and len(fake.invocations) < 3
            ):
                await asyncio.sleep(0.01)
            assert len(fake.invocations) == 3  # retry round + requeued text round
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        queries = [inv.get("query") for inv in fake.invocations]
        assert queries[0] == "do the thing"
        # Retry round: the InteractiveInput, structured (not str-ified).
        assert isinstance(queries[1], InteractiveInput)
        assert set(queries[1].user_inputs.keys()) == {"call-2"}
        # Requeued text follow-up ran afterwards as plain text (FIFO kept).
        assert queries[2] == "a plain text follow-up"
        assert answer_outputs(collected) == ["done", "done", "done"]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_start_round_splits_interactive_input_out_of_list_batch() -> None:
    """A list round query containing an InteractiveInput is normalized, not
    mangled: the InteractiveInput starts as its own single-object round and
    the text items re-queue for the next settle drain.

    Guards the transport invariant at the ``_start_round`` narrow waist:
    ``InputEvent.from_user_input(list)`` str-ifies list items, so an
    InteractiveInput riding a batch would reach the inner agent as its repr
    string. ``_settle_round_done`` splits such follow-ups out before
    ``_start_round``; this defensive split catches any producer that skips
    that step — no silent corruption, and no raise (a raise here would be
    terminal for the supervisor). Text-only lists never hit it.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="done")
        resume = InteractiveInput()
        resume.update("call-2", {"approved": True, "feedback": "", "auto_confirm": False})
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            # Direct list round start (bypassing the settle split) carrying
            # the InteractiveInput — the defensive split must normalize it.
            harness._start_round(["plain text", resume], is_follow_up=True)
            deadline = asyncio.get_running_loop().time() + 3.0
            while (
                asyncio.get_running_loop().time() < deadline
                and len(fake.invocations) < 2
            ):
                await asyncio.sleep(0.01)
            assert len(fake.invocations) == 2  # resume round + requeued text round
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        queries = [inv.get("query") for inv in fake.invocations]
        # The InteractiveInput ran as a structured single-object round.
        assert isinstance(queries[0], InteractiveInput)
        assert set(queries[0].user_inputs.keys()) == {"call-2"}
        # The re-queued text ran afterwards as plain text (FIFO kept).
        assert queries[1] == "plain text"
        assert answer_outputs(collected) == ["done", "done"]
    finally:
        await harness.stop()
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_interrupt_settle_drains_queued_approval_follow_up() -> None:
    """An interrupt-ended round must still drain the follow-up queue.

    The 2nd-of-N approval path: the resume round for tool 1 is RUNNING when
    the user approves tool 2. That approval is valid (its ask is already
    committed), so ``resume_interrupt`` delivers it straight to the harness,
    where RUNNING parks it as a follow-up. The resume round then legitimately
    re-interrupts on tool 2 — and THAT settle must consume the queued approval
    as a structured resume round, not strand it behind the interrupt stop.
    This is the gap the ``result_type == "interrupt"`` early return left open.

    Phase contract: the follow-up round starts while the harness is still
    RUNNING (follow-up chains never bounce through IDLE), and IDLE appears
    exactly once, at the end of the chain.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="done")

        # Tool 2's ask is committed — the exact shape the real interrupt rail
        # leaves behind, and what makes the queued approval a legitimate
        # retry for the settle-time filter.
        session = harness._session
        session.update_state(
            {
                INTERRUPTION_KEY: ToolInterruptionState(
                    ai_message=AssistantMessage(content="requesting approval"),
                    iteration=1,
                    interrupted_tools={
                        "call-2": ToolInterruptEntry(
                            tool_call=ToolCall(
                                id="call-2",
                                type="function",
                                name="needs_approval",
                                arguments="{}",
                            ),
                            interrupt_requests={
                                "call-2": InterruptRequest(message="approve?")
                            },
                        ),
                    },
                )
            }
        )

        # The approval arrived while the round was RUNNING and parked as a
        # follow-up (the ``_on_send(RUNNING)`` path).
        approval = InteractiveInput()
        approval.update("call-2", {"approved": True, "feedback": "", "auto_confirm": False})
        harness.loop_controller.enqueue_follow_up(approval)

        states: list = []

        async def record_state(new: HarnessState) -> None:
            states.append(new)

        await harness.subscribe(on_state=record_state)

        observed_phases: list = []
        script_first_round_interrupt(fake, harness, observed_phases)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("start work")
            deadline = asyncio.get_running_loop().time() + 3.0
            while (
                asyncio.get_running_loop().time() < deadline
                and len(fake.invocations) < 2
            ):
                await asyncio.sleep(0.01)
            assert len(fake.invocations) == 2  # the queued approval ran, not stranded
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        queries = [inv.get("query") for inv in fake.invocations]
        assert queries[0] == "start work"
        # The approval resumed as a structured InteractiveInput round.
        assert isinstance(queries[1], InteractiveInput)
        assert set(queries[1].user_inputs.keys()) == {"call-2"}
        # Phase contract: no IDLE bounce inside the chain; IDLE exactly once.
        # stop() appends TERMINATED after the lifecycle events; filter it so
        # the trace assertion reads the lifecycle transitions only.
        assert [s for s in states if s is not HarnessState.TERMINATED] == [
            HarnessState.RUNNING,
            HarnessState.IDLE,
        ]
        assert observed_phases == [HarnessState.RUNNING, HarnessState.RUNNING]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_interrupt_settle_drains_text_follow_ups_as_batch_round() -> None:
    """An interrupt-ended round must also drain queued TEXT follow-ups.

    Not just approvals: any input parked while the interrupted round ran
    (user text, rail follow-ups, async-tool completions) rides the same
    settle-time drain and starts the batch text round. Without the fix the
    interrupt stop strands those too.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="done")

        states: list = []

        async def record_state(new: HarnessState) -> None:
            states.append(new)

        await harness.subscribe(on_state=record_state)

        observed_phases: list = []
        script_first_round_interrupt(fake, harness, observed_phases)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            harness.loop_controller.enqueue_follow_up("a text follow-up during interrupt")
            await harness.send("start work")
            deadline = asyncio.get_running_loop().time() + 3.0
            while (
                asyncio.get_running_loop().time() < deadline
                and len(fake.invocations) < 2
            ):
                await asyncio.sleep(0.01)
            assert len(fake.invocations) == 2  # the queued text ran, not stranded
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        queries = [inv.get("query") for inv in fake.invocations]
        assert queries == ["start work", "a text follow-up during interrupt"]
        # Phase contract: no IDLE bounce inside the chain; IDLE exactly once.
        assert [s for s in states if s is not HarnessState.TERMINATED] == [
            HarnessState.RUNNING,
            HarnessState.IDLE,
        ]
        assert observed_phases == [HarnessState.RUNNING, HarnessState.RUNNING]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_interrupt_settle_without_follow_ups_still_settles_idle() -> None:
    """An interrupt with nothing queued still settles IDLE — and never
    auto-continues the task plan.

    Guards the merged terminal condition: the interrupt round reaches the
    drain (nothing to start), then must settle IDLE *before* the
    remaining-tasks branch, even though the seeded task plan still has a
    pending task. An interrupt awaits the external resume; continuing the
    plan would re-drive the round past an unanswered permission ask.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="done")

        # A pending task plan: if the interrupt term ever drops out of the
        # merged terminal condition, the settle falls into the remaining-tasks
        # continuation and this test catches it (second invocation).
        session = harness._session
        st = harness.load_state(session)
        st.task_plan = TaskPlan(
            goal="finish the work",
            tasks=[TodoItem(id="t1", content="remaining task")],  # status defaults to PENDING
        )
        harness.save_state(session, st)

        states: list = []

        async def record_state(new: HarnessState) -> None:
            states.append(new)

        await harness.subscribe(on_state=record_state)

        observed_phases: list = []
        script_first_round_interrupt(fake, harness, observed_phases)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("start work")
            assert await wait_for_state(harness, HarnessState.IDLE)
            # Let any unintended task-plan continuation round surface before
            # counting invocations.
            await asyncio.sleep(0.1)
        finally:
            await harness.stop()
            await consumer

        queries = [inv.get("query") for inv in fake.invocations]
        assert queries == ["start work"]  # no auto-continuation round
        # IDLE exactly once: RUNNING on send, IDLE at the interrupt settle.
        assert [s for s in states if s is not HarnessState.TERMINATED] == [
            HarnessState.RUNNING,
            HarnessState.IDLE,
        ]
        assert observed_phases == [HarnessState.RUNNING]
    finally:
        await Runner.stop()
