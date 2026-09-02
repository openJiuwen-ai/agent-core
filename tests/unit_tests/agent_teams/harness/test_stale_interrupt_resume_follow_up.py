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
from tests.unit_tests.agent_teams.harness.fixtures import (
    answer_outputs,
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
)


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
