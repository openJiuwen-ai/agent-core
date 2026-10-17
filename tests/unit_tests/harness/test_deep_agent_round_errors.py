# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Round failures retain their cause without changing cancellation or cleanup."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.interaction import InteractionPhase, RoundWorkItem

pytestmark = [pytest.mark.asyncio, pytest.mark.level1]


@pytest.mark.parametrize("message", ["synthetic gateway 503: retry exhausted", ""])
async def test_run_one_round_preserves_error_and_resets_active_flag(monkeypatch, message):
    agent = DeepAgent(AgentCard(name="round-error", description="offline regression"))
    work = RoundWorkItem.user(request_id="inner", inputs={"query": "synthetic"})
    error = RuntimeError(message)
    prepare = AsyncMock(side_effect=error)
    monkeypatch.setattr(agent, "prepare_interaction_task_loop", prepare)
    logged = []
    log_exception = MagicMock(side_effect=lambda *_args: logged.append(sys.exc_info()))
    monkeypatch.setattr("openjiuwen.harness.deep_agent.logger.exception", log_exception)
    session = MagicMock()

    outcome = await agent.run_one_round(work, "task-1", session)

    assert outcome.error_code == "round_execution_error"
    assert outcome.error_message == (message or "interaction round execution failed")
    assert outcome.next_work is None
    assert agent._invoke_active is False
    prepare.assert_awaited_once_with(session)
    log_exception.assert_called_once_with("[DeepAgent] interaction round execution failed")
    assert logged[0][1] is error
    assert logged[0][2] is not None  # logger.exception still sees the original traceback.


@pytest.mark.parametrize("message", ["synthetic runtime failure after round dispatch", ""])
async def test_execute_round_preserves_escaped_error_and_finishes_work(monkeypatch, message):
    agent = DeepAgent(AgentCard(name="round-error", description="offline regression"))
    agent._interaction_started = True
    session = MagicMock()
    agent._interaction_session = session
    work = RoundWorkItem.user(request_id="outer", inputs={"query": "synthetic"})
    error = RuntimeError(message)
    monkeypatch.setattr(agent, "run_one_round", AsyncMock(side_effect=error))
    monkeypatch.setattr(agent._interaction_output, "has_consumer", lambda: True)
    boundary = AsyncMock(return_value=False)
    monkeypatch.setattr(agent, "_emit_round_boundary", boundary)
    events = []
    monkeypatch.setattr(agent, "_emit_interaction_event", events.append)
    logged = []
    log_exception = MagicMock(side_effect=lambda *_args: logged.append(sys.exc_info()))
    monkeypatch.setattr("openjiuwen.harness.deep_agent.logger.exception", log_exception)

    await agent._execute_round(work)

    assert len(events) == 1
    assert events[0].to_output_schema().type == "execution.error"
    assert events[0].payload == {
        "code": "round_execution_error",
        "message": message or "round execution failed",
    }
    assert agent.active_round is None
    assert agent._event_manager.active_work is None
    assert agent.phase is InteractionPhase.IDLE
    assert agent._interaction_round_forwarded.is_set()
    boundary.assert_awaited_once_with(session)
    log_exception.assert_called_once_with("[DeepAgent] round execution failed")
    assert logged[0][1] is error
    assert logged[0][2] is not None


async def test_run_one_round_propagates_cancellation_and_resets_active_flag(monkeypatch):
    agent = DeepAgent(AgentCard(name="round-cancel", description="offline regression"))
    work = RoundWorkItem.user(request_id="cancel", inputs={"query": "synthetic"})
    monkeypatch.setattr(agent, "prepare_interaction_task_loop", AsyncMock(side_effect=asyncio.CancelledError))
    log_exception = MagicMock()
    monkeypatch.setattr("openjiuwen.harness.deep_agent.logger.exception", log_exception)

    with pytest.raises(asyncio.CancelledError):
        await agent.run_one_round(work, "task-1", MagicMock())

    assert agent._invoke_active is False
    log_exception.assert_not_called()


async def test_execute_round_cancellation_emits_no_error_and_finishes_work(monkeypatch):
    agent = DeepAgent(AgentCard(name="round-cancel", description="offline regression"))
    agent._interaction_started = True
    agent._interaction_session = MagicMock()
    work = RoundWorkItem.user(request_id="cancel", inputs={"query": "synthetic"})
    monkeypatch.setattr(agent, "run_one_round", AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(agent._interaction_output, "has_consumer", lambda: True)
    monkeypatch.setattr(agent, "_emit_round_boundary", AsyncMock(return_value=False))
    emit = MagicMock()
    monkeypatch.setattr(agent, "_emit_interaction_event", emit)
    log_exception = MagicMock()
    monkeypatch.setattr("openjiuwen.harness.deep_agent.logger.exception", log_exception)

    await agent._execute_round(work)

    emit.assert_not_called()
    log_exception.assert_not_called()
    assert agent.active_round is None
    assert agent._event_manager.active_work is None
    assert agent.phase is InteractionPhase.IDLE
    assert agent._interaction_round_forwarded.is_set()
