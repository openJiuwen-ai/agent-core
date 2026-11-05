# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReAct retry budget for the PTL fallback."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine import ContextEngineConfig, PtlFallbackConfig
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _agent_with_budget(max_retries: int, enabled: bool = True) -> ReActAgent:
    agent = ReActAgent(card=AgentCard(name="ptl_retry_agent", description="ptl retry"))
    agent.context_engine = MagicMock()
    agent.context_engine.config = ContextEngineConfig(
        ptl_fallback_config=PtlFallbackConfig(
            enabled=enabled,
            max_retries=max_retries,
            phase_a_compress=False,
            archive_dropped_rounds=False,
        )
    )
    return agent


def _context_with_history():
    context = MagicMock()
    context.context_id.return_value = "context"
    context.get_messages.return_value = [UserMessage(content="current question")]
    return context


@pytest.mark.asyncio
async def test_disabled_fallback_keeps_a_single_retry():
    agent = _agent_with_budget(max_retries=3, enabled=False)
    agent.context_engine.recover_from_model_exception = AsyncMock(return_value=True)
    agent._railed_model_call = AsyncMock(
        side_effect=[
            RuntimeError("maximum context length is 1000 tokens"),
            AssistantMessage(content="recovered"),
        ]
    )
    ctx = AgentCallbackContext(agent=agent, session=MagicMock())

    result = await agent._call_model(ctx, _context_with_history(), tools=None)

    assert result.content == "recovered"
    assert agent._railed_model_call.await_count == 2
    assert agent.context_engine.recover_from_model_exception.await_count == 1


@pytest.mark.asyncio
async def test_enabled_fallback_uses_the_configured_budget_then_raises():
    agent = _agent_with_budget(max_retries=3)
    agent.context_engine.recover_from_model_exception = AsyncMock(return_value=True)
    agent._railed_model_call = AsyncMock(
        side_effect=[RuntimeError("maximum context length is 1000 tokens")] * 4
    )
    ctx = AgentCallbackContext(agent=agent, session=MagicMock())

    with pytest.raises(RuntimeError):
        await agent._call_model(ctx, _context_with_history(), tools=None)

    # Initial call plus one per recovery attempt.
    assert agent._railed_model_call.await_count == 4
    assert agent.context_engine.recover_from_model_exception.await_count == 3
    assert ctx.extra["_model_exception_recovery_attempts"] == 3


@pytest.mark.asyncio
async def test_recovery_receives_payload_and_attempt_trace():
    agent = _agent_with_budget(max_retries=2)
    agent.context_engine.recover_from_model_exception = AsyncMock(return_value=False)
    agent._railed_model_call = AsyncMock(
        side_effect=RuntimeError("maximum context length is 1000 tokens")
    )
    ctx = AgentCallbackContext(agent=agent, session=MagicMock())

    with pytest.raises(RuntimeError):
        await agent._call_model(ctx, _context_with_history(), tools=None)

    call_kwargs = agent.context_engine.recover_from_model_exception.await_args.kwargs
    assert call_kwargs["attempt"] == 1
    assert call_kwargs["attempted_recoveries"] == []
    assert call_kwargs["messages"]
    assert call_kwargs["tools"] is None


@pytest.mark.asyncio
async def test_non_context_error_is_not_retried():
    agent = _agent_with_budget(max_retries=3)
    agent.context_engine.recover_from_model_exception = AsyncMock(return_value=False)
    agent._railed_model_call = AsyncMock(side_effect=RuntimeError("auth failed"))
    ctx = AgentCallbackContext(agent=agent, session=MagicMock())

    with pytest.raises(RuntimeError, match="auth failed"):
        await agent._call_model(ctx, _context_with_history(), tools=None)

    assert agent._railed_model_call.await_count == 1
