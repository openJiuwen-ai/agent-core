# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""RESUME_SIGNAL protocol chunk regression (P2).

On resume of an interrupted session, ReActAgent.invoke must emit one explicit
marker chunk via session.write_stream (best-effort) so downstream host
adapters can clear HITL stream suppression precisely on this frame. The chunk
carries no user-visible payload; host-side drop/consumption lives in
jiuwenswarm (test_hitl_resume_signal_protocol.py).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.common.constants.constant import RESUME_SIGNAL
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner.callback.errors import AbortError
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmPayload


def _confirm_state() -> ToolInterruptionState:
    return ToolInterruptionState(
        ai_message=AssistantMessage(content="approval required"),
        iteration=1,
        interrupted_tools={
            "call-1": ToolInterruptEntry(
                tool_call=ToolCall(
                    id="call-1",
                    type="function",
                    name="bash",
                    arguments="{}",
                ),
                interrupt_requests={
                    "call-1": InterruptRequest(
                        message="approve bash?",
                        payload_schema=ConfirmPayload.to_schema(),
                    )
                },
            )
        },
    )


def _agent_and_session():
    card = AgentCard(name="resume-signal", description="resume-signal")
    agent = ReActAgent(card)
    return agent, create_agent_session(session_id="resume-signal", card=card)


def _stub_invoke_prep(agent: ReActAgent, monkeypatch: pytest.MonkeyPatch) -> None:
    context = SimpleNamespace(
        add_messages=AsyncMock(),
        get_messages=lambda: [],
    )
    monkeypatch.setattr(agent, "_init_context", AsyncMock(return_value=context))
    monkeypatch.setattr(agent, "_build_rendered_system_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr(agent, "add_prompt_builder_section", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent, "_update_skill_prompt_builder_section", AsyncMock())
    monkeypatch.setattr(agent.ability_manager, "list_tool_info", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent.context_engine, "save_contexts", AsyncMock())
    monkeypatch.setattr(
        agent,
        "_call_model",
        AsyncMock(return_value=AssistantMessage(content="continued")),
    )
    monkeypatch.setattr(agent, "_admit_user_message", AsyncMock())


def _spy_write_stream(session, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    real_write_stream = session.write_stream
    spy = AsyncMock(side_effect=real_write_stream)
    monkeypatch.setattr(session, "write_stream", spy)
    return spy


def _resume_signal_chunks(spy: AsyncMock) -> list:
    chunks = []
    for call in spy.call_args_list:
        data = call.args[0] if call.args else call.kwargs.get("data")
        if isinstance(data, OutputSchema) and data.type == RESUME_SIGNAL:
            chunks.append(data)
        elif isinstance(data, dict) and data.get("type") == RESUME_SIGNAL:
            chunks.append(data)
    return chunks


@pytest.mark.asyncio
@pytest.mark.level1
async def test_resume_emits_resume_signal_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resuming an interrupted session writes one RESUME_SIGNAL marker chunk
    (source=tool_interrupt) to the session stream before any resume work."""
    agent, session = _agent_and_session()
    session.update_state({INTERRUPTION_KEY: _confirm_state()})
    _stub_invoke_prep(agent, monkeypatch)
    spy = _spy_write_stream(session, monkeypatch)

    await agent.invoke({"query": "继续"}, session=session)

    chunks = _resume_signal_chunks(spy)
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].payload["source"] == "tool_interrupt"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_no_resume_signal_without_interruption(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain (non-resume) invoke must not emit the protocol marker."""
    agent, session = _agent_and_session()
    _stub_invoke_prep(agent, monkeypatch)
    spy = _spy_write_stream(session, monkeypatch)

    await agent.invoke({"query": "hello"}, session=session)

    assert _resume_signal_chunks(spy) == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_resume_signal_write_abort_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """AbortError raised at the write_stream callback boundary must propagate
    out of invoke. The best-effort guard may swallow ordinary failures, but
    AbortError is the callback framework's control-flow exception (the only
    one trigger() lets through) and must not be downgraded to a debug log."""
    agent, session = _agent_and_session()
    session.update_state({INTERRUPTION_KEY: _confirm_state()})
    _stub_invoke_prep(agent, monkeypatch)
    monkeypatch.setattr(
        session,
        "write_stream",
        AsyncMock(side_effect=AbortError("host aborted write_stream")),
    )

    with pytest.raises(AbortError):
        await agent.invoke({"query": "继续"}, session=session)
