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
from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryStore
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
)
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail
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
async def test_invoke_callbacks_fire_per_round_in_multi_round_mode() -> None:
    """BEFORE/AFTER_INVOKE fire once per outer round in start+send mode.

    The supervisor drives rounds directly (bypassing ``DeepAgent.invoke``), so
    this pins the per-round invoke lifecycle the harness installs in
    ``_run_round``. AFTER_TASK_ITERATION (fired by the shared executor) is
    counted alongside to show both lifecycles co-fire without interfering.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        await start_harness(harness)

        counts: dict[str, int] = {
            "before_invoke": 0,
            "after_invoke": 0,
            "after_task_iteration": 0,
        }
        after_invoke_results: list = []

        def _before_invoke(ctx: AgentCallbackContext) -> None:
            counts["before_invoke"] += 1

        def _after_invoke(ctx: AgentCallbackContext) -> None:
            counts["after_invoke"] += 1
            after_invoke_results.append(ctx.inputs.result)

        def _after_task_iteration(ctx: AgentCallbackContext) -> None:
            counts["after_task_iteration"] += 1

        await harness.agent_callback_manager.register_callback(
            AgentCallbackEvent.BEFORE_INVOKE, _before_invoke
        )
        await harness.agent_callback_manager.register_callback(
            AgentCallbackEvent.AFTER_INVOKE, _after_invoke
        )
        await harness.agent_callback_manager.register_callback(
            AgentCallbackEvent.AFTER_TASK_ITERATION, _after_task_iteration
        )

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("round one")
            assert await wait_for_state(harness, HarnessState.IDLE)
            await harness.send("round two")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        # One invoke lifecycle per outer round — not once for the whole session.
        assert counts["before_invoke"] == 2
        assert counts["after_invoke"] == 2
        # The shared executor still fires its own task-iteration lifecycle.
        assert counts["after_task_iteration"] == 2
        # AFTER_INVOKE sees the genuine round result (the echoed query output).
        assert [r["output"] for r in after_invoke_results] == [
            "echo:round one",
            "echo:round two",
        ]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_after_task_iteration_fires_when_round_fails() -> None:
    """AFTER_TASK_ITERATION fires on the executor error path, not only success.

    Per-round cleanup rails (snapshot, otel span close, ...) must run even when
    the inner round raises. The fake's ``invoke`` raises, driving the real
    TaskLoopEventExecutor through its except branch.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)

        fired: list = []

        def _after_task_iteration(ctx: AgentCallbackContext) -> None:
            fired.append(getattr(ctx.inputs, "result", None))

        await harness.agent_callback_manager.register_callback(
            AgentCallbackEvent.AFTER_TASK_ITERATION, _after_task_iteration
        )

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            fake.raise_exc = ValueError("boom")
            await harness.send("will fail")
            # Wait on the cleanup hook rather than a specific harness state —
            # failure-mode state transitions are out of scope for this test.
            deadline = asyncio.get_running_loop().time() + 3.0
            while not fired and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
        finally:
            await harness.stop()
            await consumer

        # Fired exactly once, carrying the error-shaped result the except
        # branch installs (not a leftover success result).
        assert len(fired) == 1
        assert fired[0] == {"result_type": "error", "error": "boom"}
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_evolution_rail_triggers_per_round_in_multi_round_mode() -> None:
    """An AFTER_INVOKE-triggered EvolutionRail fires once per outer round.

    Regression guard for the original bug: in start+send multi-round mode the
    invoke lifecycle never fired, so SkillEvolutionRail / TeamSkillEvolutionRail
    (default trigger AFTER_INVOKE) never built a trajectory nor ran evolution.
    This mounts a minimal EvolutionRail and proves the whole chain now fires:
    ``before_invoke`` creates the trajectory builder and ``after_invoke``
    triggers ``run_evolution`` — per round.
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        await start_harness(harness)

        evolved: list = []

        class _CountingEvolutionRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                evolved.append(trajectory)

        # Default trigger is AFTER_INVOKE; sync evolution so the count is
        # settled by the time after_invoke returns.
        rail = _CountingEvolutionRail(
            trajectory_store=InMemoryTrajectoryStore(),
            async_evolution=False,
        )
        await harness.agent_callback_manager.register_rail(rail, harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("round one")
            assert await wait_for_state(harness, HarnessState.IDLE)
            await harness.send("round two")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        # Evolution ran once per outer round — the bug would leave this empty.
        assert len(evolved) == 2
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
