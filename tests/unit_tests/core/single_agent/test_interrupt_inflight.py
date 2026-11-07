# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the in-flight interrupt registration.

背景：ask_user/权限 rail 的 ToolInterruptException 要等整批工具完成后才由
ReAct 循环 commit 成 INTERRUPTION_KEY，而卡片在抛出瞬间已下发——窗口内
用户作答会被 manager 以 unsupported_interactive_input 拒收。修复：
ability_manager 在逐工具异常边界把中断登记为 INTERRUPTION_INFLIGHT_KEY，
批末 commit_interrupt 转正并清键，invoke 异常中止由 react_agent finally 清键。
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.handler import ToolInterruptHandler
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_INFLIGHT_KEY,
    INTERRUPTION_KEY,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent


class _FakeSession:
    """Minimal session stub: dict-backed get_state/update_state."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}

    def get_state(self, key: str) -> Any:
        return self.state.get(key)

    def update_state(self, updates: dict) -> None:
        self.state.update(updates)


def _tool_card(name: str) -> ToolCard:
    return ToolCard(id=name, name=name, description=f"{name} desc")


def _tool_call(name: str, *, call_id: str | None = None) -> ToolCall:
    return ToolCall(id=call_id or f"tc-{name}", type="function", name=name, arguments="{}")


def _interrupt_exc(tool_call: ToolCall | None = None) -> ToolInterruptException:
    return ToolInterruptException(InterruptRequest(message="请确认"), tool_call=tool_call)


def _make_slow_tool(name: str, release: asyncio.Event) -> LocalFunction:
    async def _func(**_):
        await release.wait()
        return f"{name}:ok"

    return LocalFunction(card=_tool_card(name), func=_func)


def _make_plain_tool(name: str) -> LocalFunction:
    async def _func(**_):
        return f"{name}:ok"

    return LocalFunction(card=_tool_card(name), func=_func)


def _make_ctx(
        session: _FakeSession,
        *,
        interrupt_tool_name: str | None = None,
) -> AgentCallbackContext:
    """Build a callback context; optionally simulate an interrupt rail.

    生产形态：中断由 interrupt/security rail 以 AbortError(cause=
    ToolInterruptException) 抛出，callback trigger() 边界把 cause 重抛——
    到达 railed 包装之外时已是裸 ToolInterruptException（工具体内裸抛的
    ToolInterruptException 会被 ability_manager 包成 AbilityExecutionError，
    生产不存在该形态）。这里用 agent_callback_manager.execute 的副作用
    在 BEFORE_TOOL_CALL 对指定工具抛裸 ToolInterruptException 复现同一形态。
    """
    agent = MagicMock(name="Agent")

    async def _fire(event, ctx):
        if (
                interrupt_tool_name is not None
                and event == AgentCallbackEvent.BEFORE_TOOL_CALL
                and getattr(ctx.inputs, "tool_name", "") == interrupt_tool_name
        ):
            raise _interrupt_exc(getattr(ctx.inputs, "tool_call", None))

    agent.agent_callback_manager.execute = AsyncMock(side_effect=_fire)
    return AgentCallbackContext(agent=agent, session=session)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# ability_manager：逐工具捕获边界登记在途标记
# ---------------------------------------------------------------------------


def test_inflight_registered_while_slow_sibling_still_running() -> None:
    """并行批 [慢工具, ask_user]：慢工具未完成时在途标记已登记。"""

    async def _scenario():
        await Runner.start()
        release = asyncio.Event()
        am = AbilityManager(owner_id="inflight-1")
        slow = _make_slow_tool("slow_mem", release)
        asking = _make_plain_tool("ask_user")
        am.add_ability(slow.card, slow)
        am.add_ability(asking.card, asking)

        session = _FakeSession()
        ctx = _make_ctx(session, interrupt_tool_name="ask_user")
        tc_slow = _tool_call("slow_mem")
        tc_ask = _tool_call("ask_user", call_id="call_ask_1")

        execute_task = asyncio.create_task(
            am.execute(
                ctx=ctx,
                tool_call=[tc_slow, tc_ask],
                session=session,
                parallel_tool_calls=True,
            )
        )
        try:
            # 轮询等待在途登记出现（慢工具仍被 release 卡住，批远未结束）
            deadline = asyncio.get_running_loop().time() + 5.0
            while not session.get_state(INTERRUPTION_INFLIGHT_KEY):
                assert asyncio.get_running_loop().time() < deadline, (
                    "in-flight interrupt was not registered before batch end"
                )
                assert not execute_task.done(), (
                    "execute finished before inflight was observed — test broken"
                )
                await asyncio.sleep(0.01)

            inflight = session.get_state(INTERRUPTION_INFLIGHT_KEY)
            assert set(inflight.keys()) == {"call_ask_1"}
            assert isinstance(inflight["call_ask_1"], InterruptRequest)
        finally:
            release.set()
        results = await execute_task

        # 批末：异常作为结果被收集；在途标记不归 ability_manager 清（commit 的职责）
        assert any(isinstance(r, ToolInterruptException) for r, _ in results)
        assert session.get_state(INTERRUPTION_INFLIGHT_KEY) is not None

    _run(_scenario())


def test_inflight_registered_in_sequential_mode() -> None:
    """非并行执行路径同样登记（顺序分支复用同一 tasks 包装）。"""

    async def _scenario():
        await Runner.start()
        am = AbilityManager(owner_id="inflight-2")
        asking = _make_plain_tool("ask_user_seq")
        am.add_ability(asking.card, asking)

        session = _FakeSession()
        ctx = _make_ctx(session, interrupt_tool_name="ask_user_seq")
        tc_ask = _tool_call("ask_user_seq", call_id="call_seq_1")

        results = await am.execute(
            ctx=ctx,
            tool_call=[tc_ask],
            session=session,
            parallel_tool_calls=False,
        )
        assert any(isinstance(r, ToolInterruptException) for r, _ in results)
        inflight = session.get_state(INTERRUPTION_INFLIGHT_KEY)
        assert set(inflight.keys()) == {"call_seq_1"}

    _run(_scenario())


def test_no_inflight_for_non_interrupt_results() -> None:
    """普通成功/失败工具不产生在途标记。"""

    async def _scenario():
        await Runner.start()
        am = AbilityManager(owner_id="inflight-3")

        async def _ok(**_):
            return "ok"

        am.add_ability(_tool_card("plain"), LocalFunction(card=_tool_card("plain"), func=_ok))
        session = _FakeSession()
        ctx = _make_ctx(session)
        results = await am.execute(
            ctx=ctx,
            tool_call=[_tool_call("plain")],
            session=session,
        )
        assert results
        assert session.get_state(INTERRUPTION_INFLIGHT_KEY) is None

    _run(_scenario())


# ---------------------------------------------------------------------------
# handler.commit_interrupt：转正并清在途键
# ---------------------------------------------------------------------------


def test_commit_interrupt_promotes_and_clears_inflight() -> None:
    async def _scenario():
        agent = MagicMock()
        agent.context_engine.save_contexts = AsyncMock()
        handler = ToolInterruptHandler(agent)

        session = _FakeSession()
        session.update_state({INTERRUPTION_INFLIGHT_KEY: {"call_ask_1": InterruptRequest(message="q")}})

        state = MagicMock(name="ToolInterruptionState")
        context = MagicMock(name="ModelContext")
        invoke_inputs = MagicMock(name="InvokeInputs")

        result = await handler.commit_interrupt(state, context, session, invoke_inputs)

        assert result.get("result_type") == "interrupt"
        # 正式态已写入、在途键已清
        assert session.get_state(INTERRUPTION_KEY) is state
        assert session.get_state(INTERRUPTION_INFLIGHT_KEY) is None

    _run(_scenario())


# ---------------------------------------------------------------------------
# react_agent：invoke finally 清残（异常中止路径）
# ---------------------------------------------------------------------------


def test_react_agent_finally_clears_leftover_inflight() -> None:
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgent

    session = _FakeSession()
    session.update_state({INTERRUPTION_INFLIGHT_KEY: {"call_x": None}})
    ReActAgent._clear_interrupt_inflight_state(session)
    assert session.get_state(INTERRUPTION_INFLIGHT_KEY) is None

    # 无残留时不动其它键
    session.update_state({INTERRUPTION_KEY: object()})
    ReActAgent._clear_interrupt_inflight_state(session)
    assert session.get_state(INTERRUPTION_INFLIGHT_KEY) is None
    assert session.get_state(INTERRUPTION_KEY) is not None

    # session 抛错也被吞掉（finally 不得放大原异常）
    bad = MagicMock()
    bad.get_state.side_effect = RuntimeError("boom")
    ReActAgent._clear_interrupt_inflight_state(bad)


# ---------------------------------------------------------------------------
# team_harness：准入查询读在途态
# ---------------------------------------------------------------------------


def test_harness_pending_interrupt_and_resume_valid_with_inflight_only() -> None:
    from openjiuwen.agent_teams.harness import TeamHarness

    session = _FakeSession()
    session.update_state({
        INTERRUPTION_INFLIGHT_KEY: {"call_ask_1": InterruptRequest(message="q")},
    })
    native = MagicMock(name="NativeHarness")
    native.loop_session = session
    harness = TeamHarness.__new__(TeamHarness)
    harness._native = native
    harness._active_agent_session = None

    assert harness.has_pending_interrupt() is True

    good = InteractiveInput()
    good.update("call_ask_1", {"answers": {"q": "A"}})
    assert harness.is_pending_interrupt_resume_valid(good) is True

    bad = InteractiveInput()
    bad.update("call_other", {"approved": True})
    assert harness.is_pending_interrupt_resume_valid(bad) is False


def test_harness_inflight_empty_dict_is_not_pending() -> None:
    from openjiuwen.agent_teams.harness import TeamHarness

    session = _FakeSession()
    session.update_state({INTERRUPTION_INFLIGHT_KEY: {}})
    native = MagicMock(name="NativeHarness")
    native.loop_session = session
    harness = TeamHarness.__new__(TeamHarness)
    harness._native = native
    harness._active_agent_session = None

    assert harness.has_pending_interrupt() is False


def test_harness_committed_state_still_dominates() -> None:
    """正式态在场时按正式态校验（在途键已清是 commit 的职责，双在场也不误判）。"""
    from openjiuwen.agent_teams.harness import TeamHarness
    from types import SimpleNamespace

    entry = SimpleNamespace(interrupt_requests={"call-1": object()})
    state = SimpleNamespace(interrupted_tools={"call-1": entry})
    session = _FakeSession()
    session.update_state({
        INTERRUPTION_KEY: state,
        INTERRUPTION_INFLIGHT_KEY: {"call-stale": None},
    })
    native = MagicMock(name="NativeHarness")
    native.loop_session = session
    harness = TeamHarness.__new__(TeamHarness)
    harness._native = native
    harness._active_agent_session = None

    assert harness.has_pending_interrupt() is True

    good = InteractiveInput()
    good.update("call-1", {"approved": True})
    assert harness.is_pending_interrupt_resume_valid(good) is True

    stale = InteractiveInput()
    stale.update("call-stale", {"approved": True})
    assert harness.is_pending_interrupt_resume_valid(stale) is False
