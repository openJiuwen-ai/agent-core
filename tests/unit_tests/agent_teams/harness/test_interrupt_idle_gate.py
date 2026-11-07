# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Interrupt-pending idle gate.

中断 commit 后 harness IDLE 期间
``_on_send`` 准入闸——IDLE+挂起工具中断+非 InteractiveInput 挂
follow-up 队列等作答（DeferredInput 保持未渲染入队），作答开 resume
轮后由 settle 既有 drain 按序消费。
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.harness.task_loop.loop_queues import DeferredInput
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import (
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
)

_SENTINEL = {"tool_call_id": "call_ask_1"}


def _arm_pending_interrupt(harness) -> None:
    """模拟批末已 commit 的工具中断（正式态 INTERRUPTION_KEY）。

    与探针同一读路径（loop_session 优先），保证武装的一定被看见。
    """
    session = harness.loop_session or harness._session
    session.update_state({INTERRUPTION_KEY: dict(_SENTINEL)})


def _interactive(call_id: str = "call_ask_1") -> InteractiveInput:
    ii = InteractiveInput()
    ii.update(call_id, {"answers": {"q": "A"}})
    return ii


async def _wait_invocations(fake, count: int, timeout: float = 3.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if len(fake.invocations) >= count:
            return True
        await asyncio.sleep(0.02)
    return False


@pytest.mark.asyncio
@pytest.mark.level1
async def test_idle_gate_defers_non_answer_while_interrupt_pending() -> None:
    """IDLE+挂起中断：非作答输入不开轮、入 follow-up 队列、中断态保留。"""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        _arm_pending_interrupt(harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("board nudge")
            await asyncio.sleep(0.2)
            assert harness.state is HarnessState.IDLE
            assert len(fake.invocations) == 0
            session = harness.loop_session or harness._session
            assert session.get_state(INTERRUPTION_KEY) is not None
        finally:
            await harness.stop()
            await consumer
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_idle_gate_answer_resumes_then_queued_input_drains() -> None:
    """作答直开 resume 轮（闸放行）；轮末 settle 的既有 drain 消费排队 nudge。"""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        _arm_pending_interrupt(harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("board nudge")
            ii = _interactive()
            await harness.send(ii)
            assert await _wait_invocations(fake, 2)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert len(fake.invocations) == 2, fake.invocations
        resumed = fake.invocations[0]["query"]
        assert isinstance(resumed, InteractiveInput)
        assert resumed.user_inputs == ii.user_inputs
        second = fake.invocations[1]["query"]
        assert "board nudge" in (second if isinstance(second, str) else " ".join(map(str, second)))
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_idle_gate_inactive_without_pending_interrupt() -> None:
    """无挂起中断：IDLE send 直开轮（行为不变回归）。"""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("plain query")
            assert await _wait_invocations(fake, 1)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert fake.invocations[0]["query"] == "plain query"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_idle_gate_keeps_deferred_input_unrendered_until_drain() -> None:
    """DeferredInput 未渲染入队（renderer 零调用）；resume 轮末 drain 才渲染消费。"""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        _arm_pending_interrupt(harness)

        render_calls = 0

        async def _renderer():
            nonlocal render_calls
            render_calls += 1
            return "board text"

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            nudge = DeferredInput(
                kind="task-board", renderer=_renderer, collapse_key="task-board"
            )
            await harness.send(nudge)
            await asyncio.sleep(0.2)
            assert render_calls == 0
            assert len(fake.invocations) == 0

            await harness.send(_interactive())
            assert await _wait_invocations(fake, 2)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert render_calls == 1
        second = fake.invocations[1]["query"]
        assert "board text" in (second if isinstance(second, str) else " ".join(map(str, second)))
    finally:
        await Runner.stop()
