# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the session-scoped DeepAgent interaction entrypoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.interaction import (
    InputDispatchMode,
    RoundWorkItem,
    SendInputRequest,
)


@pytest.mark.asyncio
async def test_send_input_queues_interactive_input_as_interrupt_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())
    interactive_input = InteractiveInput()
    interactive_input.update("call_123", {"action": "allow_once"})

    await agent.send_input(
        SendInputRequest(
            request_id="resume-1",
            inputs={"query": interactive_input},
        )
    )

    work = agent._event_manager.next_work()
    assert work is not None
    assert isinstance(work.query, InteractiveInput)
    assert work.query.user_inputs == {
        "call_123": {"action": "allow_once"},
    }
    assert work.reset_loop is False


@pytest.mark.asyncio
async def test_send_input_preserves_text_validation_and_queueing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())

    with pytest.raises(ValueError, match="non-empty string or InteractiveInput"):
        await agent.send_input(SendInputRequest(request_id="empty-1", inputs={"query": ""}))

    await agent.send_input(SendInputRequest(request_id="text-1", inputs={"query": "continue"}))

    work = agent._event_manager.next_work()
    assert work is not None
    assert work.query == "continue"
    assert work.reset_loop is True


@pytest.mark.parametrize("mode", list(InputDispatchMode))
@pytest.mark.asyncio
async def test_send_input_ignores_dispatch_mode_for_interrupt_resume(
    mode: InputDispatchMode,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    interactive_input = InteractiveInput()
    interactive_input.update("call_123", {"action": "allow_once"})

    await agent.send_input(
        SendInputRequest(
            request_id="resume-1",
            inputs={"query": interactive_input},
            mode=mode,
        )
    )

    work = agent._event_manager.next_work()
    assert work is not None
    assert isinstance(work.query, InteractiveInput)
    assert work.query.user_inputs == interactive_input.user_inputs
    assert work.reset_loop is False


@pytest.mark.asyncio
async def test_run_one_round_uses_single_round_path_for_interrupt_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    interactive_input = InteractiveInput()
    interactive_input.update("call_123", {"action": "allow_once"})
    work = RoundWorkItem.user(
        request_id="resume-1",
        inputs={"query": interactive_input},
        reset_loop=False,
    )
    session = MagicMock()
    coordinator = MagicMock()
    controller = MagicMock()
    controller.submit_round = AsyncMock()
    result = {"result_type": "answer", "output": "resumed"}
    react_agent = MagicMock()
    react_agent.invoke = AsyncMock(return_value=result)
    agent._react_agent = react_agent
    write_result = AsyncMock()
    build_next_work = MagicMock(return_value=None)

    monkeypatch.setattr(
        agent,
        "prepare_interaction_task_loop",
        AsyncMock(return_value=(coordinator, controller)),
    )
    monkeypatch.setattr(agent, "_write_round_result_to_stream", write_result)
    monkeypatch.setattr(agent, "_build_interaction_next_work", build_next_work)
    monkeypatch.setattr(agent, "save_state", MagicMock())
    monkeypatch.setattr(agent, "clear_state", MagicMock())

    outcome = await agent.run_one_round(work, "task-1", session)

    effective_inputs = react_agent.invoke.await_args.args[0]
    assert isinstance(effective_inputs["query"], InteractiveInput)
    assert effective_inputs["query"].user_inputs == interactive_input.user_inputs
    coordinator.reset.assert_not_called()
    controller.submit_round.assert_not_awaited()
    build_next_work.assert_not_called()
    write_result.assert_awaited_once_with(result, session)
    assert outcome.error_code is None
