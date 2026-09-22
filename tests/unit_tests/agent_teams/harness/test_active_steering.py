# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Strict team admission is serialized with supervisor stop/pause/round events."""

import asyncio

import pytest

from openjiuwen.agent_teams.harness import HarnessState, NativeHarness, TeamHarness
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.runner import Runner
from tests.unit_tests.agent_teams.harness.fixtures import (
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
    wait_invoke_running,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_active_team_dedup_stale_handle_and_stop_preserve_single_output(wrapped):
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=30)
        control = (TeamHarness(None, None, harness, role=TeamRole.LEADER, member_name="leader")
                   if wrapped else harness)
        sink = []
        consumer = asyncio.create_task(drain_outputs(harness, sink))
        try:
            assert (await control.steer_active(active_request_id="none", input_id="1", content="A"))[
                "reason"
            ] == "not_active"
            await harness.send("original")
            await wait_invoke_running(fake)
            handle = control.get_active_steering_request_id()
            assert (await control.get_steering_capability(active_request_id=handle))["supported"]
            result = await control.steer_active(active_request_id=handle, input_id="1", content="@expert literal")
            assert result["status"] == "accepted"
            assert (
                await control.steer_active(active_request_id=handle, input_id="1", content="@expert literal") == result
            )
            assert (await control.steer_active(active_request_id="old", input_id="2", content="B"))[
                "reason"
            ] == "not_active"
            assert harness._steering_inbox.queue.qsize() == 1
            assert len(fake.invocations) == 1
            assert fake.cancelled_count == 0
            await harness.abort(immediate=True)
            assert await wait_for_state(harness, HarnessState.IDLE)
            assert (await control.get_steering_status(active_request_id=handle, input_id="1"))[
                "status"
            ] == "not_applied"
            assert harness._steering_inbox.queue.empty()
            assert (await control.steer_active(active_request_id=handle, input_id="2", content="B"))[
                "reason"
            ] == "not_active"
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_paused_team_never_resumes_through_strict_steering(wrapped):
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=30)
        control = (TeamHarness(None, None, harness, role=TeamRole.LEADER, member_name="leader")
                   if wrapped else harness)
        consumer = asyncio.create_task(drain_outputs(harness, []))
        try:
            await harness.send("original")
            await wait_invoke_running(fake)
            handle = control.get_active_steering_request_id()
            await harness.pause()
            assert await wait_for_state(harness, HarnessState.PAUSED)
            assert (await control.steer_active(active_request_id=handle, input_id="1", content="continue"))[
                "reason"
            ] == "waiting_input"
            assert harness.state is HarnessState.PAUSED
            assert len(fake.invocations) == 1
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_team_wrapper_without_native_never_creates_or_starts_a_runtime(monkeypatch):
    from openjiuwen.agent_teams.harness import team_harness

    def forbidden(*args, **kwargs):
        raise AssertionError("Strict steering must not create a native runtime")

    monkeypatch.setattr(team_harness, "NativeHarness", forbidden)
    harness = TeamHarness(None, None, None, role=TeamRole.LEADER, member_name="leader")
    assert harness.get_active_steering_request_id() is None
    assert await harness.get_steering_capability(active_request_id="old") == {
        "supported": False, "reason": "not_active",
    }
    assert await harness.steer_active(active_request_id="old", input_id="one", content="direction") == {
        "active_request_id": "old", "input_id": "one", "status": "not_applied", "reason": "not_active",
    }
    assert await harness.get_steering_status(active_request_id="old", input_id="one") == {
        "active_request_id": "old", "input_id": "one", "status": "unknown", "reason": "not_active",
    }
    assert harness._native is None
