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


def _inbound(text: str, *, sender: str = "m1") -> str:
    """用生产侧渲染器构造 <team-inbound> 消息（外部检视修订：与真实注入
    格式保持同步，不手写字面量——格式漂移时测试跟随生产渲染器变化）。"""
    from openjiuwen.agent_teams.inbound_render import render_inbound

    return render_inbound(
        content=text,
        sender=sender,
        message_id="msg-1",
        msg_type="direct",
        time_info="2026-11-14 12:00:00",
    )


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
    """IDLE+挂起中断：系统注入输入不开轮、入 follow-up 队列、中断态保留。

    后裸 str 视为用户直接文本放行（回填恢复），系统注入以
    <team-inbound> 包装 str 为代表（成员消息形态）。
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        _arm_pending_interrupt(harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(_inbound("board nudge"))
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
    """作答直开 resume 轮（闸放行）；轮末 settle 的既有 drain 消费排队系统输入。"""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        _arm_pending_interrupt(harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(_inbound("board nudge"))
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


@pytest.mark.asyncio
@pytest.mark.level1
async def test_idle_gate_user_text_admitted_as_interrupt_resume() -> None:
    """IDLE+挂起中断，用户直接文本（裸 str）放行开轮（回填恢复），不 defer。"""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        _arm_pending_interrupt(harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send("不用问了，按默认方案直接做")
            assert await _wait_invocations(fake, 1)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert len(fake.invocations) == 1, fake.invocations
        assert fake.invocations[0]["query"] == "不用问了，按默认方案直接做"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_pause_settles_paused_at_idle_with_pending_interrupt() -> None:
    """IDLE+挂起中断 pause 落定 PAUSED（paused_query 留空）；
    warm resume 不起空轮（守卫留 PAUSED）；用户文本到达直接开新轮。"""
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        _arm_pending_interrupt(harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.pause()
            assert harness.state is HarnessState.PAUSED
            assert (harness._st.paused_query or "") == ""

            # warm resume：空 paused_query 守卫——不起空轮，留在 PAUSED
            await harness.resume()
            await asyncio.sleep(0.2)
            assert harness.state is HarnessState.PAUSED
            assert len(fake.invocations) == 0

            # 用户文本到达：PAUSED 分支空 query 直接开新轮（非续轮+steer）
            await harness.send("按你最熟悉的默认方案直接开始")
            assert await _wait_invocations(fake, 1)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert fake.invocations[0]["query"] == "按你最熟悉的默认方案直接开始"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_cold_resume_empty_query_guard() -> None:
    """kernel 冷恢复标记携带空串 query 时不起空轮（清标记等用户输入）。

    harness.resume(query="") 的公共 API 语义不变（显式空 query 是合法
    续轮，见 test_pause.py::test_cold_resume_without_query_cannot_drive_the_task_plan）——
    守卫在 kernel 的标记消费点。
    """
    from types import SimpleNamespace

    from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
    from openjiuwen.agent_teams.schema.team import TeamRole

    resume_calls: list[str] = []
    cleared: list[bool] = []

    async def _resume_agent(*, query: str) -> None:
        resume_calls.append(query)

    host = SimpleNamespace(
        role=TeamRole.LEADER,
        member_name="leader",
        resources=SimpleNamespace(
            harness=SimpleNamespace(state=HarnessState.IDLE),
        ),
        stream_controller=SimpleNamespace(resume_agent=_resume_agent),
    )
    kernel = CoordinationKernel(host)
    kernel._read_pending_resume = lambda: {"query": "", "paused_at_idle": True}
    kernel._clear_pending_resume = lambda: cleared.append(True)

    await kernel.resume_paused_round()

    assert resume_calls == []
    assert cleared == [True]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_cold_resume_empty_query_without_paused_at_idle_continues() -> None:
    """空 query 但非 paused_at_idle（II 起轮的暂停回合）：照常 resume 续跑。

    外部检视修订：paused_query 为空不止「paused at IDLE」一种——
    InteractiveInput 起轮的回合正常 pause 后 query 同样为空，但应续跑
    （续轮不追加 user turn，空 query 仅作 original_query，无害）。
    """
    from types import SimpleNamespace

    from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
    from openjiuwen.agent_teams.schema.team import TeamRole

    resume_calls: list[str] = []
    cleared: list[bool] = []

    async def _resume_agent(*, query: str) -> None:
        resume_calls.append(query)

    host = SimpleNamespace(
        role=TeamRole.LEADER,
        member_name="leader",
        resources=SimpleNamespace(
            harness=SimpleNamespace(state=HarnessState.IDLE),
        ),
        stream_controller=SimpleNamespace(resume_agent=_resume_agent),
    )
    kernel = CoordinationKernel(host)
    kernel._read_pending_resume = lambda: {"query": "", "paused_at_idle": False}
    kernel._clear_pending_resume = lambda: cleared.append(True)

    await kernel.resume_paused_round()

    assert resume_calls == [""]
    assert cleared == [True]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_idle_gate_system_text_deferred() -> None:
    """SystemText（异步工具完成/重试/spawn 摘要等系统注入）挂起中断时 defer。

    外部检视修订：裸 str 注入与用户文本同形——显式标记后闸按类型推迟，
    不再误当 resume 答复回填。
    """
    from openjiuwen.agent_teams.harness.state import SystemText

    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        _arm_pending_interrupt(harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(SystemText("后台任务已完成：构建产物就绪"))
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
async def test_idle_gate_user_inbound_from_user_admitted() -> None:
    """from="user" 的 <team-inbound>（用户经邮箱直发，F_72 形态）放行回填。

    外部检视修订：inbound 信封按发件人分化——from="user" 是用户直接输入，
    其余 sender 才是系统注入。
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness)
        _arm_pending_interrupt(harness)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(_inbound("用 Vue", sender="user"))
            assert await _wait_invocations(fake, 1)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert len(fake.invocations) == 1, fake.invocations
        assert "用 Vue" in str(fake.invocations[0]["query"])
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_paused_at_idle_gate_defers_system_inputs() -> None:
    """PAUSED-at-IDLE（pause 落定在无回合 IDLE）：系统输入同样 defer。

    外部检视修订：PAUSED 分支原先无准入闸——DeferredInput 渲染结果、
    <team-inbound> 成员消息会直开新轮被 invoke 当 resume 答复吃掉中断。
    修复后 IDLE/PAUSED 两个分支共用同一准入判定。
    """
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
            await harness.pause()
            assert harness.state is HarnessState.PAUSED

            nudge = DeferredInput(
                kind="task-board", renderer=_renderer, collapse_key="task-board"
            )
            await harness.send(nudge)
            await harness.send(_inbound("member report"))
            await asyncio.sleep(0.2)
            assert render_calls == 0  # DeferredInput 保持未渲染入队
            assert len(fake.invocations) == 0

            # 用户文本到达：开新轮回填，轮末 drain 消费排队系统输入
            await harness.send("继续按默认方案做")
            assert await _wait_invocations(fake, 2)
            assert await wait_for_state(harness, HarnessState.IDLE)
        finally:
            await harness.stop()
            await consumer

        assert render_calls == 1
        assert fake.invocations[0]["query"] == "继续按默认方案做"
        drained = str(fake.invocations[1]["query"])
        assert "board text" in drained or "member report" in drained
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_ii_started_round_pause_resume_continues() -> None:
    """II 起轮的回合 pause 后 resume 照常续跑（外部检视修订锁定）。

    InteractiveInput 起轮 → 跑到中途 pause（paused_query 为空但非
    paused_at_idle）→ warm resume 必须起 resume_continuation 续轮，
    不得被「空 query」误杀停摆。
    """
    await Runner.start()
    try:
        harness = NativeHarness(make_spec())
        fake = await start_harness(harness, iterations=1, sleep_seconds=1.0)

        collected: list = []
        consumer = asyncio.create_task(drain_outputs(harness, collected))
        try:
            await harness.send(_interactive())
            assert await _wait_invocations(fake, 1)

            # 单迭代 1s 模型调用窗口内 pause（RUNNING 中正常暂停）
            await harness.pause()
            assert harness.state is HarnessState.PAUSED
            assert (harness._st.paused_query or "") == ""
            assert harness._st.paused_at_idle is False  # II 起轮正常 pause，非 IDLE 落定

            await harness.resume()
            assert await _wait_invocations(fake, 2)
            assert await wait_for_state(harness, HarnessState.IDLE, timeout=6.0)
        finally:
            await harness.stop()
            await consumer

        assert len(fake.invocations) == 2, fake.invocations
    finally:
        await Runner.stop()
