# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness goal attempts over the real task-loop kernel."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from openjiuwen.core.runner import Runner
from openjiuwen.harness.goal.schema import GoalStatus, GoalStopConfig, GoalStopStrategy
from tests.unit_tests.agent_teams.harness.fixtures import (
    FakeReactAgent,
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
    wait_invoke_running,
)

_OBJECTIVE = "ship the parser"


def _stop_config() -> GoalStopConfig:
    """Trust the agent's own report so no assessor model is needed."""
    return GoalStopConfig(strategy=GoalStopStrategy.AGENT_REPORT)


def _queries(fake: FakeReactAgent) -> list[str]:
    """Return the query of every inner invocation, in order."""
    return [str(invocation["query"]) for invocation in fake.invocations]


async def _wait_goal_status(harness: NativeHarness, status: GoalStatus, timeout: float = 3.0) -> bool:
    """Poll until the stored goal reaches ``status`` or the timeout elapses."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        record = harness.goal_manager.peek()
        if record is not None and record.status is status:
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
@pytest.mark.level0
async def test_active_goal_keeps_the_harness_working_until_the_attempt_limit() -> None:
    """An unfinished goal starts attempt after attempt, then blocks at the limit."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        harness.enable_goal(_stop_config())
        fake = await start_harness(harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.goal_manager.set(_OBJECTIVE, max_attempts=2)
            assert await _wait_goal_status(harness, GoalStatus.BLOCKED)
            assert await wait_for_state(harness, HarnessState.IDLE)
            record = await harness.goal_manager.get()
        finally:
            await harness.stop()
            await consumer

        assert record.attempt_count == 2
        queries = _queries(fake)
        assert len(queries) == 2
        assert all(_OBJECTIVE in query for query in queries)
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_queued_input_runs_before_the_next_goal_attempt() -> None:
    """Input queued during a goal attempt is served before the goal continues."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        harness.enable_goal(_stop_config())
        fake = await start_harness(harness, sleep_seconds=0.2)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.goal_manager.set(_OBJECTIVE, max_attempts=2)
            await wait_invoke_running(fake)
            fake.sleep_seconds = 0.0
            await harness.send("status please")
            assert await _wait_goal_status(harness, GoalStatus.BLOCKED)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        queries = _queries(fake)
        assert len(queries) == 3
        assert _OBJECTIVE in queries[0]
        assert queries[1] == "status please"
        assert _OBJECTIVE in queries[2]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_goal_attempt_waits_for_the_host_precondition() -> None:
    """No attempt starts until the host says it may, then a nudge starts one."""
    await Runner.start()
    try:
        allowed = False

        async def _ready() -> bool:
            return allowed

        harness = NativeHarness(make_spec())
        harness.enable_goal(_stop_config(), ready=_ready)
        fake = await start_harness(harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.goal_manager.set(_OBJECTIVE, max_attempts=1)
            await asyncio.sleep(0.1)
            assert fake.invocations == []
            assert harness.state is HarnessState.IDLE

            allowed = True
            harness.nudge_goal()
            assert await _wait_goal_status(harness, GoalStatus.BLOCKED)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert len(fake.invocations) == 1
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_cleared_goal_stops_further_attempts() -> None:
    """Clearing the goal mid-attempt lets that attempt end without a successor."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        harness.enable_goal(_stop_config())
        fake = await start_harness(harness, sleep_seconds=0.2)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.goal_manager.set(_OBJECTIVE, max_attempts=5)
            await wait_invoke_running(fake)
            await harness.goal_manager.clear()
            assert await wait_for_state(harness, HarnessState.IDLE)
            assert await harness.goal_manager.get() is None
        finally:
            await harness.stop()
            await consumer

        assert len(fake.invocations) == 1
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_harness_without_goal_support_is_unchanged() -> None:
    """With goal support off there is no manager and a nudge does nothing."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, answer_output="hi")

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            assert harness.goal_manager is None
            harness.nudge_goal()
            await harness.send("hello")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert _queries(fake) == ["hello"]
    finally:
        await Runner.stop()
