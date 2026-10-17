# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Offline integration of the real round handler, event, and output lease."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.interaction import InteractionPhase, RoundWorkItem


@pytest.mark.asyncio
@pytest.mark.level1
@pytest.mark.parametrize("failure_site", ["inner", "outer"])
async def test_round_cause_reaches_output_consumer(monkeypatch, failure_site):
    agent = DeepAgent(AgentCard(name="round-stream", description="offline integration"))
    agent._interaction_started = True
    agent._interaction_session = MagicMock()
    lease = await agent._interaction_output.attach()
    assert lease is not None
    message = "synthetic gateway 503: upstream temporarily unavailable"
    failure = AsyncMock(side_effect=RuntimeError(message))
    method = "prepare_interaction_task_loop" if failure_site == "inner" else "run_one_round"
    monkeypatch.setattr(agent, method, failure)
    monkeypatch.setattr(agent, "_emit_round_boundary", AsyncMock(return_value=False))
    work = RoundWorkItem.user(request_id="stream-error", inputs={"query": "synthetic"})

    try:
        await agent._execute_round(work)
        await asyncio.gather(*tuple(agent._interaction_emit_tasks))
        chunk = await asyncio.wait_for(agent._interaction_output.next_item(lease), timeout=1)

        assert chunk.type == "execution.error"
        assert chunk.payload == {"code": "round_execution_error", "message": message}
        assert agent.phase is InteractionPhase.IDLE
        assert agent.active_round is None
        assert agent._event_manager.active_work is None
        assert agent._invoke_active is False
        assert agent._interaction_round_forwarded.is_set()
    finally:
        await agent._interaction_output.shutdown()
