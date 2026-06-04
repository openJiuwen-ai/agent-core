# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness protocol conformance + lifecycle cleanup (no task leaks)."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.agent_teams.harness import (
    HarnessProtocol,
    HarnessState,
    NativeHarness,
)
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.deep_agent import DeepAgent
from tests.unit_tests.agent_teams.harness.fixtures import (
    FakeReactAgent,
    drain_outputs,
    make_card,
    make_spec,
    start_harness,
    wait_for_state,
)


def test_native_harness_is_a_deep_agent_and_satisfies_protocol() -> None:
    """NativeHarness IS a DeepAgent and structurally implements HarnessProtocol."""
    harness = NativeHarness(make_spec())
    assert isinstance(harness, DeepAgent)
    assert isinstance(harness, HarnessProtocol)


@pytest.mark.asyncio
async def test_session_id_none_before_start_then_set() -> None:
    """session_id is None before start() and resolves to the owned session after."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        assert harness.session_id is None
        assert harness.state is HarnessState.IDLE

        await start_harness(harness)
        assert harness.session_id is not None
        await harness.stop()
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_injected_session_is_reused_and_not_owned() -> None:
    """A start(session=...) reuses the injected session across rounds."""
    await Runner.start()
    try:
        session = Session(card=make_card("injected_owner"), session_id="injected_sid")
        await session.pre_run()
        harness = NativeHarness(make_spec())
        await harness.start(session=session)
        fake = FakeReactAgent(harness.card)
        harness.set_react_agent(fake, initialized=True)

        assert harness.session_id == "injected_sid"

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("hi")
            assert await wait_for_state(harness, HarnessState.IDLE)
            assert [inv["query"] for inv in fake.invocations] == ["hi"]
        finally:
            await harness.stop()
            await consumer
        # The harness does not own the injected session: caller tears it down.
        await session.post_run()
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_stop_leaves_no_lingering_harness_tasks() -> None:
    """After stop() the supervisor, forwarder, and round tasks are all done."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, sleep_seconds=0.02)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))

        await harness.send("one")
        assert await wait_for_state(harness, HarnessState.IDLE)
        await harness.stop()
        await consumer

        assert harness.state is HarnessState.TERMINATED
        # Supervisor + forwarder fully wound down.
        assert harness._st.supervisor_task is not None
        assert harness._st.supervisor_task.done()
        assert harness._forwarder_task is not None
        assert harness._forwarder_task.done()
        # No active round left dangling.
        assert harness._st.active is None

        # No leftover harness-owned tasks in the loop.
        leftover = [
            t
            for t in asyncio.all_tasks()
            if not t.done()
            and (t.get_name() or "").startswith("native_harness_")
        ]
        assert leftover == []
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_stop_is_idempotent() -> None:
    """Calling stop() twice is safe and stays TERMINATED."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        await start_harness(harness)
        await harness.stop()
        await harness.stop()  # second stop is a no-op
        assert harness.state is HarnessState.TERMINATED
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_abort_while_idle_is_noop() -> None:
    """abort() while IDLE leaves the harness IDLE without error."""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        await start_harness(harness)
        try:
            await harness.abort(immediate=True)
            assert harness.state is HarnessState.IDLE
            await harness.abort(immediate=False)
            assert harness.state is HarnessState.IDLE
        finally:
            await harness.stop()
    finally:
        await Runner.stop()
