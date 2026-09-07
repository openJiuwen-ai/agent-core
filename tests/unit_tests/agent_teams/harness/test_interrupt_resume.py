# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Approval delivery must leave the real Supervisor free to acknowledge it."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.harness.control import _CmdSend
from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from tests.unit_tests.agent_teams.harness.fixtures import make_spec, start_harness


async def _noop(*_args):
    pass


class _NativeRuntimeView(SimpleNamespace):
    @property
    def state(self):
        return self.native.state


def _controller(runtime, *, status_updater=_noop, completion_poll=_noop):
    return StreamController(
        blueprint_getter=lambda: SimpleNamespace(member_name="leader"),
        state=TeamAgentState(),
        resources=PrivateAgentResources(harness=runtime),
        status_updater=status_updater,
        execution_updater=_noop,
        request_completion_poll_callback=completion_poll,
    )


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("queued", [False, True], ids=["direct", "queued"])
async def test_approval_delivery_survives_supervisor_idle_callback(queued):
    """A matching approval during round settlement must receive its send ACK.

    The model and committed-approval predicate are controlled; the real
    Supervisor, command queue, state callbacks and controller lock are used.
    Events reproduce the same ordering for both direct and queued delivery.
    """
    await Runner.start()
    native = NativeHarness(make_spec())
    reached_idle = asyncio.Event()
    release_idle = asyncio.Event()
    idle_settled = asyncio.Event()
    send_queued = asyncio.Event()
    delivery = None
    sc = None
    try:
        fake = await start_harness(native)
        original_invoke = fake.invoke

        async def interrupt_once(*args, **kwargs):
            result = await original_invoke(*args, **kwargs)
            if len(fake.invocations) == 1:
                result["result_type"] = "interrupt"
            return result

        fake.invoke = interrupt_once
        original_put = native._control.put

        async def observe_send(command):
            await original_put(command)
            if isinstance(command, _CmdSend) and isinstance(command.msg.content, InteractiveInput):
                send_queued.set()

        native._control.put = observe_send

        async def update_status(status):
            if status is MemberStatus.READY and not reached_idle.is_set():
                reached_idle.set()
                await release_idle.wait()

        async def poll():
            idle_settled.set()

        runtime = _NativeRuntimeView(
            native=native,
            send=native.send,
            has_pending_interrupt=lambda: True,
            is_pending_interrupt_resume_valid=lambda value: isinstance(value, InteractiveInput),
        )
        sc = _controller(runtime, status_updater=update_status, completion_poll=poll)
        await native.subscribe(on_state=sc._map_state, on_round=sc._map_round)
        await native.send("produce an interrupt")
        await asyncio.wait_for(reached_idle.wait(), timeout=2)

        approval = InteractiveInput()
        approval.update("build-team-call", {"approved": True})
        if queued:
            sc._pending_interrupt_resumes.append(approval)
            delivery = asyncio.create_task(sc._drain_pending_interrupt_resumes())
            sc._drain_task = delivery
        else:
            delivery = asyncio.create_task(sc.resume_interrupt(approval))
        await asyncio.wait_for(send_queued.wait(), timeout=2)
        release_idle.set()

        result, _ = await asyncio.wait_for(
            asyncio.gather(delivery, idle_settled.wait()),
            timeout=1,
        )
        if not queued:
            assert result == "delivered"
        assert not sc._interrupt_lock.locked()
        assert sc._pending_interrupt_resumes == []
    finally:
        release_idle.set()
        if delivery is not None:
            delivery.cancel()
            await asyncio.gather(delivery, return_exceptions=True)
        if sc is not None:
            await asyncio.wait_for(sc.stop(), timeout=2)
        await asyncio.wait_for(native.stop(), timeout=2)
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_cancels_pending_delivery_before_waiting_for_its_lock():
    """Stopping must cancel the owned sender even if its ACK never arrives."""
    entered_send = asyncio.Event()

    async def send(_approval):
        entered_send.set()
        await asyncio.Event().wait()

    runtime = SimpleNamespace(
        send=send,
        state=HarnessState.IDLE,
        has_pending_interrupt=lambda: True,
        is_pending_interrupt_resume_valid=lambda _value: True,
    )
    sc = _controller(runtime)
    sc._pending_interrupt_resumes.append(InteractiveInput())
    delivery = asyncio.create_task(sc._drain_pending_interrupt_resumes())
    sc._drain_task = delivery
    try:
        await asyncio.wait_for(entered_send.wait(), timeout=1)
        await asyncio.wait_for(sc.stop(), timeout=1)
        assert delivery.cancelled()
        assert sc._pending_interrupt_resumes == []
        assert not sc._interrupt_lock.locked()
    finally:
        delivery.cancel()
        await asyncio.gather(delivery, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("phase", [HarnessState.RUNNING, HarnessState.PAUSED])
@pytest.mark.parametrize("pending", [False, True])
async def test_delayed_drain_preserves_answers_when_runtime_is_no_longer_idle(phase, pending):
    """An old IDLE notification must not inject an answer into a newer round."""
    sent = []

    async def send(approval):
        sent.append(approval)

    runtime = SimpleNamespace(
        state=phase,
        send=send,
        has_pending_interrupt=lambda: pending,
        is_pending_interrupt_resume_valid=lambda _value: True,
    )
    sc = _controller(runtime)
    approval = InteractiveInput()
    sc._pending_interrupt_resumes.append(approval)
    await sc._drain_pending_interrupt_resumes()
    assert sent == []
    assert sc._pending_interrupt_resumes == [approval]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_idle_during_delivery_does_not_lose_the_next_queued_answer():
    """The next IDLE can arrive before the previous send's caller resumes."""
    first, second = InteractiveInput(), InteractiveInput()
    sent = []

    async def send(approval):
        sent.append(approval)
        if approval is first:
            await sc._on_idle_settled()

    runtime = SimpleNamespace(
        state=HarnessState.IDLE,
        send=send,
        has_pending_interrupt=lambda: True,
        is_pending_interrupt_resume_valid=lambda _value: True,
    )
    sc = _controller(runtime)
    sc._pending_interrupt_resumes.extend([first, second])
    try:
        await sc._on_idle_settled()
        await asyncio.wait_for(sc._drain_task, timeout=1)
        assert sent == [first, second]
        assert sc._pending_interrupt_resumes == []
    finally:
        if sc._drain_task is not None:
            sc._drain_task.cancel()
            await asyncio.gather(sc._drain_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stopped_controller_drops_new_approvals():
    """A late approval must not restart execution after controller cleanup."""
    sent = []

    async def send(approval):
        sent.append(approval)

    runtime = SimpleNamespace(
        send=send,
        has_pending_interrupt=lambda: True,
        is_pending_interrupt_resume_valid=lambda _value: True,
    )
    sc = _controller(runtime)
    await sc.stop()
    assert await sc.resume_interrupt(InteractiveInput()) == "dropped"
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_idle_after_stop_does_not_restart_drain_until_next_start():
    """Late state callbacks cannot restart teardown; a new cycle can resume."""

    async def outputs():
        if False:
            yield

    runtime = SimpleNamespace(
        send=AsyncMock(),
        subscribe=AsyncMock(),
        outputs=outputs,
        state=HarnessState.IDLE,
        has_pending_interrupt=lambda: True,
        is_pending_interrupt_resume_valid=lambda _value: True,
    )
    sc = _controller(runtime)
    await sc.stop()
    approval = InteractiveInput()
    sc._pending_interrupt_resumes.append(approval)
    await sc._on_idle_settled()
    assert sc._drain_task is None
    runtime.send.assert_not_called()

    try:
        await sc.start()
        await sc._on_idle_settled()
        await asyncio.wait_for(sc._drain_task, timeout=1)
        runtime.send.assert_awaited_once_with(approval)
    finally:
        await sc.stop()
