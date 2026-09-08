# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Interruption-state consumption boundaries in ReActAgent."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _tool_state() -> ToolInterruptionState:
    return ToolInterruptionState(
        ai_message=AssistantMessage(content="approval required"),
        iteration=1,
        interrupted_tools={
            "call-1": ToolInterruptEntry(
                tool_call=ToolCall(
                    id="call-1",
                    type="function",
                    name="tool_call_1",
                    arguments="{}",
                ),
                interrupt_requests={
                    "call-1": InterruptRequest(message="approve?")
                },
            )
        },
    )


def _agent_and_session() -> tuple[ReActAgent, Any]:
    card = AgentCard(name="resume-boundary", description="resume-boundary")
    agent = ReActAgent(card)
    return agent, create_agent_session(session_id="resume-boundary", card=card)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_pre_handler_failure_keeps_interruption_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preparation failure before _handle_resume leaves the approval retryable."""
    agent, session = _agent_and_session()
    state = _tool_state()
    session.update_state({INTERRUPTION_KEY: state})

    async def fail_context(_session: Any) -> Any:
        raise RuntimeError("context setup failed")

    monkeypatch.setattr(agent, "_init_context", fail_context)
    approval = InteractiveInput()
    approval.update("call-1", {"approved": True})

    with pytest.raises(RuntimeError, match="context setup failed"):
        await agent.invoke({"query": approval}, session=session)

    assert session.get_state(INTERRUPTION_KEY) == state


@pytest.mark.asyncio
@pytest.mark.level1
async def test_resume_handler_entry_consumes_interruption_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the handler starts, a failure must not replay possible tool effects."""
    agent, session = _agent_and_session()
    state = _tool_state()
    session.update_state({INTERRUPTION_KEY: state})
    handle_resume = AsyncMock(side_effect=RuntimeError("handler failed"))
    monkeypatch.setattr(agent._hitl_handler, "handle_resume", handle_resume)

    with pytest.raises(RuntimeError, match="handler failed"):
        await agent._handle_resume(
            state,
            InteractiveInput(raw_inputs="approve"),
            SimpleNamespace(),
            SimpleNamespace(),
            session,
            invoke_inputs=SimpleNamespace(),
        )

    assert session.get_state(INTERRUPTION_KEY) is None
