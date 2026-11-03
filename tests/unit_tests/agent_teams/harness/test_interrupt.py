# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness safe interrupt: stop at a safe boundary, keep context, replan."""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from openjiuwen.agent_teams.harness.protocol import INTERRUPT_APPLIED, INTERRUPT_UNAVAILABLE
from openjiuwen.core.runner import Runner
from openjiuwen.harness.schema.task import TaskPlan, TodoItem
from tests.unit_tests.agent_teams.harness.fixtures import (
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
    wait_tool_running,
)


@pytest.mark.level0
@pytest.mark.asyncio
async def test_tool_phase_interrupt_waits_for_tool_and_replans() -> None:
    """The running tool finishes, the plan is dropped, the content is steered in."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, iterations=2, emit_tools=True, tool_sleep_seconds=0.3)
        state = harness.load_state(harness._session)
        state.task_plan = TaskPlan(goal="Kafka ingestion", tasks=[TodoItem(id="kafka", content="deploy Kafka")])
        harness.save_state(harness._session, state)
        assert await harness.interrupt("ignored") == INTERRUPT_UNAVAILABLE  # IDLE: nothing to interrupt

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("design Kafka ingestion")
            await wait_tool_running(fake)
            interrupt = asyncio.create_task(harness.interrupt("REPLAN: use Redis Streams"))
            assert await wait_for_state(harness, HarnessState.PAUSING)
            await harness.send("late steer", immediate=True)  # must not be dropped while PAUSING

            assert await interrupt == INTERRUPT_APPLIED
            assert fake.completed_tools == 1 and fake.cancelled_count == 0
            assert harness.load_state(harness._session).task_plan is None
            assert await wait_for_state(harness, HarnessState.IDLE)
            assert "REPLAN: use Redis Streams" in fake.seen_steers
            assert "late steer" in fake.seen_steers
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()
