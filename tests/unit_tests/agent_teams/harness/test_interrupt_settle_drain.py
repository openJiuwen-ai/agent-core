# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Interrupt-settle follow-up drain.

修复让窗口内（中断已发卡、批末 commit 未完成）的 InteractiveInput
作答被受理并按 RUNNING 语义排进 follow-up 队列。但中断收尾分支
（``result_type == "interrupt"``）此前直接转 IDLE、跳过
``_drain_pending_follow_ups``——队列无人消费，作答永久悬挂
打包版实证：作答受理成功、批末 commit 成功，随后 harness 静默至用户暂停）。
修复：中断收尾前排空队列，含 InteractiveInput 则以其开 resume 轮
（INTERRUPTION_KEY 已 commit，新轮走 handle_resume），其余输入重新入队
待 resume 轮收尾时按既有 drain 顺序消费。
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.agent_teams.harness import HarnessState, NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import (
    FakeReactAgent,
    drain_outputs,
    make_spec,
    start_harness,
    wait_for_state,
    wait_invoke_running,
)


class _InterruptScriptedFake(FakeReactAgent):
    """Fake whose scripted invocations end with result_type="interrupt"."""

    def __init__(self, card):
        super().__init__(card)
        self.interrupt_on_invocations: set[int] = set()

    async def invoke(self, inputs, session, **kwargs):
        result = await super().invoke(inputs, session, **kwargs)
        if (len(self.invocations) - 1) in self.interrupt_on_invocations:
            result["result_type"] = "interrupt"
        return result


def _interactive(call_id: str = "call_ask_1") -> InteractiveInput:
    ii = InteractiveInput()
    ii.update(call_id, {"answers": {"q": "A"}})
    return ii


async def _start_with_interrupt_fake(harness, **kwargs) -> _InterruptScriptedFake:
    """start_harness 变体：注入带 interrupt 剧本的 fake。"""
    await harness.start()
    fake = _InterruptScriptedFake(harness.card)
    for key, value in kwargs.items():
        setattr(fake, key, value)
    harness.set_react_agent(fake, initialized=True)
    from tests.unit_tests.agent_teams.harness.fixtures import rebind_bridge_rails

    await rebind_bridge_rails(harness, fake)
    return fake


@pytest.mark.asyncio
@pytest.mark.level1
async def test_interrupt_settle_consumes_queued_interactive_input() -> None:
    """窗口内入队的作答在中断收尾时驱动 resume 轮（不再滞留悬挂）。"""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await _start_with_interrupt_fake(harness, sleep_seconds=0.05)
        fake.interrupt_on_invocations.add(0)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("first query")
            await wait_invoke_running(fake)
            # 中断已发卡、批末 commit 未完成的窗口内：作答按 RUNNING 语义入队
            ii = _interactive()
            await harness.send(ii)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert len(fake.invocations) == 2, fake.invocations
        assert fake.invocations[0]["query"] == "first query"
        # resume 轮：executor 从任务输入提取 InteractiveInput 作为 effective query
        # （经任务包装是重建副本，比内容不比身份）
        resumed = fake.invocations[1]["query"]
        assert isinstance(resumed, InteractiveInput)
        assert resumed.user_inputs == ii.user_inputs
        assert harness.state is HarnessState.TERMINATED
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_interrupt_settle_without_follow_ups_goes_idle() -> None:
    """无排队输入的中断收尾保持旧行为：直接 IDLE、不开新轮。"""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await _start_with_interrupt_fake(harness, sleep_seconds=0.0)
        fake.interrupt_on_invocations.add(0)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("solo query")
            assert await wait_for_state(harness, HarnessState.IDLE)
            await asyncio.sleep(0.1)
        finally:
            await harness.stop()
            await consumer

        assert len(fake.invocations) == 1
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_interrupt_settle_resume_first_then_residuals() -> None:
    """混合队列：InteractiveInput 先开 resume 轮，其余输入重新入队随后消费。"""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await _start_with_interrupt_fake(harness, sleep_seconds=0.05)
        fake.interrupt_on_invocations.add(0)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("first query")
            await wait_invoke_running(fake)
            ii = _interactive()
            await harness.send(ii)
            await harness.send("board note")
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert len(fake.invocations) == 3, fake.invocations
        resumed = fake.invocations[1]["query"]
        assert isinstance(resumed, InteractiveInput)
        assert resumed.user_inputs == ii.user_inputs
        third = fake.invocations[2]["query"]
        assert "board note" in (third if isinstance(third, str) else " ".join(map(str, third)))
    finally:
        await Runner.stop()
