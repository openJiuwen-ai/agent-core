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
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
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
