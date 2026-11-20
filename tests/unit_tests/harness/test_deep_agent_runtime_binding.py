# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for DeepAgent session bindings in AgentRuntime."""

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig


class _FakeReactAgent:
    async def invoke(self, inputs: dict[str, Any], session: Session | None = None) -> dict[str, Any]:
        return {"output": "done", "result_type": "answer"}

    async def stream(
        self,
        inputs: dict[str, Any],
        session: Session | None = None,
        stream_modes: list[Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        yield {
            "type": "answer",
            "payload": {"output": "done", "result_type": "answer"},
        }


def _resolve_bound_agent(session_id: str) -> BaseAgent:
    """Mirror CORE Handler lookup: session_id → AgentRuntime → BaseAgent."""
    agent = get_agent_runtime().get(session_id)
    assert agent is not None
    return agent


@pytest.mark.level0
@pytest.mark.asyncio
async def test_bound_agent_is_retrievable_from_runtime_and_callable() -> None:
    """After DeepAgent obtains a Session, CORE-style lookup returns the same instance."""
    card = AgentCard(name="runtime-callable-agent", description="test")
    agent = DeepAgent(card).configure(DeepAgentConfig(enable_task_loop=False))
    agent.set_react_agent(_FakeReactAgent(), initialized=True)
    session = Session(session_id="runtime-callable-agent-session")
    core_lookup_ok = False

    async def simulate_core_handler(_ctx: AgentCallbackContext) -> None:
        nonlocal core_lookup_ok
        resolved = _resolve_bound_agent(session.get_session_id())
        assert resolved is agent
        assert resolved.card.name == "runtime-callable-agent"
        assert resolved.ability_manager is agent.ability_manager
        core_lookup_ok = True

    await agent.register_callback(AgentCallbackEvent.BEFORE_INVOKE, simulate_core_handler)
    try:
        await agent.invoke("hello", session=session)

        assert core_lookup_ok
        post_invoke = _resolve_bound_agent(session.get_session_id())
        assert post_invoke.card.name == "runtime-callable-agent"
    finally:
        get_agent_runtime().unbind(session.get_session_id(), agent)
        await agent.agent_callback_manager.clear()


@pytest.mark.level0
@pytest.mark.parametrize("entrypoint", ["invoke", "stream"])
@pytest.mark.asyncio
async def test_single_round_entrypoint_binds_before_callback_and_keeps_binding(entrypoint: str) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test")).configure(DeepAgentConfig(enable_task_loop=False))
    agent.set_react_agent(_FakeReactAgent(), initialized=True)
    session = Session(session_id=f"runtime-{entrypoint}")
    runtime = get_agent_runtime()

    async def assert_bound(_ctx: AgentCallbackContext) -> None:
        assert runtime.get(session.get_session_id()) is agent

    await agent.register_callback(AgentCallbackEvent.BEFORE_INVOKE, assert_bound)
    try:
        if entrypoint == "invoke":
            await agent.invoke("hello", session=session)
        else:
            _ = [chunk async for chunk in agent.stream("hello", session=session)]

        assert runtime.get(session.get_session_id()) is agent
    finally:
        runtime.unbind(session.get_session_id(), agent)
        await agent.agent_callback_manager.clear()


@pytest.mark.level0
@pytest.mark.asyncio
async def test_start_binds_session_stop_unbinds_and_failure_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._task_completion_rail = object()  # type: ignore[assignment]
    session = MagicMock()
    session.get_session_id.return_value = "runtime-start"
    session.close_stream = AsyncMock()
    runtime = get_agent_runtime()

    monkeypatch.setattr(
        agent,
        "prepare_interaction_task_loop",
        AsyncMock(return_value=(MagicMock(), MagicMock())),
    )
    monkeypatch.setattr(agent, "_forward_session_stream", AsyncMock())
    monkeypatch.setattr(agent, "_ensure_supervisor_running", MagicMock())

    try:
        await agent.start(session=session)

        assert runtime.get("runtime-start") is agent
    finally:
        await agent.stop()
        assert runtime.get("runtime-start") is None
        runtime.unbind("runtime-start", agent)

    failing_agent = DeepAgent(AgentCard(name="failing-deep", description="test"))
    failing_session = MagicMock()
    failing_session.get_session_id.return_value = "runtime-start-failure"
    monkeypatch.setattr(
        failing_agent,
        "prepare_interaction_task_loop",
        AsyncMock(return_value=(MagicMock(), MagicMock())),
    )
    monkeypatch.setattr(
        failing_agent,
        "register_rail",
        AsyncMock(side_effect=RuntimeError("start failed")),
    )

    with pytest.raises(RuntimeError, match="start failed"):
        await failing_agent.start(session=failing_session)

    assert runtime.get("runtime-start-failure") is None


@pytest.mark.level0
@pytest.mark.asyncio
async def test_session_switch_unbinds_old_runtime() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    old_session = MagicMock()
    old_session.get_session_id.return_value = "runtime-old-session"
    old_controller = MagicMock()
    old_controller.unbind_session = AsyncMock()
    old_controller.stop = AsyncMock()
    agent._loop_controller = old_controller
    agent._loop_session = old_session
    agent._bound_session_id = "runtime-old-session"
    runtime = get_agent_runtime()
    runtime.bind("runtime-old-session", agent)

    new_session = Session(session_id="runtime-new-session")
    agent._bind_session_runtime(new_session)
    try:
        await agent._setup_task_loop(new_session)

        assert runtime.get("runtime-old-session") is None
        assert runtime.get("runtime-new-session") is agent
        assert agent._loop_session is new_session
    finally:
        runtime.unbind("runtime-old-session", agent)
        runtime.unbind("runtime-new-session", agent)
