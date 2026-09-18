# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permission HITL must not treat plain text as a resume or reemit ASK."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.resume_guard import (
    is_confirm_payload_interrupt,
    is_matching_tool_resume,
    is_pure_permission_confirm_interrupt,
    should_abandon_unmatched_confirm_resume,
)
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserPayload
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


def _ask_user_state() -> ToolInterruptionState:
    return ToolInterruptionState(
        ai_message=AssistantMessage(content="question"),
        iteration=1,
        interrupted_tools={
            "call-ask": ToolInterruptEntry(
                tool_call=ToolCall(
                    id="call-ask",
                    type="function",
                    name="ask_user",
                    arguments="{}",
                ),
                interrupt_requests={
                    "call-ask": InterruptRequest(
                        message="what is the name?",
                        payload_schema=AskUserPayload.to_schema(),
                    )
                },
            )
        },
    )


def _skill_turbo_state() -> ToolInterruptionState:
    return ToolInterruptionState(
        ai_message=AssistantMessage(content=""),
        iteration=1,
        interrupted_tools={
            "call-st": ToolInterruptEntry(
                tool_call=ToolCall(
                    id="call-st",
                    type="function",
                    name="skill_acceleration_exec",
                    arguments="{}",
                ),
                interrupt_requests={
                    "call-st": InterruptRequest(
                        message="continue?",
                        payload_schema=ConfirmPayload.to_schema(),
                    )
                },
            )
        },
    )


def test_confirm_payload_interrupt_is_detected() -> None:
    assert is_confirm_payload_interrupt(_confirm_state()) is True
    assert is_confirm_payload_interrupt(_ask_user_state()) is False
    assert is_pure_permission_confirm_interrupt(_confirm_state()) is True
    assert is_pure_permission_confirm_interrupt(_ask_user_state()) is False
    assert is_pure_permission_confirm_interrupt(_skill_turbo_state()) is False


def test_plain_str_is_not_matching_tool_resume() -> None:
    state = _confirm_state()
    assert is_matching_tool_resume("继续", state) is False
    approval = InteractiveInput()
    approval.update("call-1", {"approved": True})
    assert is_matching_tool_resume(approval, state) is True
    mismatched = InteractiveInput()
    mismatched.update("call-other", {"approved": True})
    assert is_matching_tool_resume(mismatched, state) is False
    assert should_abandon_unmatched_confirm_resume("继续", state) is True
    assert should_abandon_unmatched_confirm_resume(mismatched, state) is False
    assert should_abandon_unmatched_confirm_resume(InteractiveInput(), state) is False
    assert should_abandon_unmatched_confirm_resume(approval, state) is False


def _agent_and_session():
    card = AgentCard(name="permission-hitl", description="permission-hitl")
    agent = ReActAgent(card)
    return agent, create_agent_session(session_id="permission-hitl", card=card)


def _resume_without_react_loop(*_args, invoke_inputs=None, **_kwargs):
    if invoke_inputs is not None:
        invoke_inputs.result = {"output": "resumed", "result_type": "answer"}
    return None


def _stub_invoke_prep(agent: ReActAgent, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
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
    return context


@pytest.mark.asyncio
@pytest.mark.level1
async def test_abandon_unmatched_confirm_writes_cancelled_result() -> None:
    agent, session = _agent_and_session()
    state = _confirm_state()
    session.update_state({INTERRUPTION_KEY: state})
    added: list = []

    async def capture_add(messages, **_kwargs):
        to_add = messages if isinstance(messages, list) else [messages]
        added.extend(to_add)
        return to_add

    context = SimpleNamespace(add_messages=capture_add, get_messages=lambda: [])
    await agent._hitl_handler.abandon_unmatched_confirm_interrupt(state, session, context)

    assert session.get_state(INTERRUPTION_KEY) is None
    assert len(added) == 1
    assert isinstance(added[0], ToolMessage)
    assert added[0].tool_call_id == "call-1"
    assert "bash" in added[0].content


@pytest.mark.asyncio
@pytest.mark.level1
async def test_confirm_interrupt_plain_str_abandons_and_skips_handle_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, session = _agent_and_session()
    state = _confirm_state()
    session.update_state({INTERRUPTION_KEY: state})
    handle_resume = AsyncMock()
    monkeypatch.setattr(agent, "_handle_resume", handle_resume)
    _stub_invoke_prep(agent, monkeypatch)

    result = await agent.invoke({"query": "继续"}, session=session)

    handle_resume.assert_not_awaited()
    assert session.get_state(INTERRUPTION_KEY) is None
    assert result.get("result_type") != "interrupt"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_confirm_interrupt_interactive_input_still_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, session = _agent_and_session()
    state = _confirm_state()
    session.update_state({INTERRUPTION_KEY: state})
    handle_resume = AsyncMock(side_effect=_resume_without_react_loop)
    monkeypatch.setattr(agent, "_handle_resume", handle_resume)
    _stub_invoke_prep(agent, monkeypatch)

    approval = InteractiveInput()
    approval.update("call-1", {"approved": True, "auto_confirm": False, "feedback": ""})
    await agent.invoke({"query": approval}, session=session)

    handle_resume.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_confirm_interrupt_mismatched_interactive_input_still_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, session = _agent_and_session()
    state = _confirm_state()
    session.update_state({INTERRUPTION_KEY: state})
    handle_resume = AsyncMock(side_effect=_resume_without_react_loop)
    monkeypatch.setattr(agent, "_handle_resume", handle_resume)
    _stub_invoke_prep(agent, monkeypatch)

    mismatched = InteractiveInput()
    mismatched.update("call-other", {"approved": True})
    await agent.invoke({"query": mismatched}, session=session)

    handle_resume.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_ask_user_plain_str_still_handle_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, session = _agent_and_session()
    state = _ask_user_state()
    session.update_state({INTERRUPTION_KEY: state})
    handle_resume = AsyncMock(side_effect=_resume_without_react_loop)
    monkeypatch.setattr(agent, "_handle_resume", handle_resume)
    _stub_invoke_prep(agent, monkeypatch)

    await agent.invoke({"query": "张三"}, session=session)

    handle_resume.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_skill_turbo_plain_str_still_handle_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, session = _agent_and_session()
    state = _skill_turbo_state()
    session.update_state({INTERRUPTION_KEY: state})
    handle_resume = AsyncMock(side_effect=_resume_without_react_loop)
    monkeypatch.setattr(agent, "_handle_resume", handle_resume)
    _stub_invoke_prep(agent, monkeypatch)

    await agent.invoke({"query": "继续"}, session=session)

    handle_resume.assert_awaited()
