# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for tool interrupt resume input routing."""

from __future__ import annotations

from typing import Any

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.handler import ResumeContext, ToolInterruptHandler
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import (
    RESUME_USER_INPUT_KEY,
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ToolCallInputs
from openjiuwen.harness.rails.evolution.evolution_interrupt_rail import EVOLUTION_RESUME_USER_INPUT_KEY


def _tool_call(call_id: str = "call_001", name: str = "demo_tool") -> ToolCall:
    return ToolCall(id=call_id, type="function", name=name, arguments="{}")


def _request(
    message: str = "Approve tool?",
    *,
    resume_user_input_key: str | None = None,
) -> InterruptRequest:
    metadata: dict[str, str] = {}
    if resume_user_input_key is not None:
        metadata = {"resume_user_input_key": resume_user_input_key}
    return InterruptRequest(message=message, metadata=metadata)


def _interrupted_tool(
    request: InterruptRequest,
    *,
    call_id: str = "call_001",
    name: str = "demo_tool",
) -> tuple[ToolCall, ToolInterruptEntry]:
    tool_call = _tool_call(call_id=call_id, name=name)
    return tool_call, ToolInterruptEntry(
        tool_call=tool_call,
        interrupt_requests={tool_call.id: request},
    )


def _state(*entries: tuple[ToolCall, ToolInterruptEntry]) -> ToolInterruptionState:
    tool_calls = [tool_call for tool_call, _ in entries]
    return ToolInterruptionState(
        ai_message=AssistantMessage(content="", tool_calls=tool_calls),
        iteration=0,
        interrupted_tools={tool_call.id: entry for tool_call, entry in entries},
    )


async def _handle_resume_and_capture_extra(
    state: ToolInterruptionState,
    user_input: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    handler = ToolInterruptHandler(agent=object())
    ctx = AgentCallbackContext(
        agent=object(),
        inputs=ToolCallInputs(),
    )
    seen_extra: dict[str, Any] = {}

    async def _execute_tool_call(ctx_arg, tools_to_execute, session, context):
        seen_extra.update(ctx_arg.extra)
        return []

    result = await handler.handle_resume(
        ResumeContext(
            state=state,
            user_input=user_input,
            ctx=ctx,
            context=None,
            session=None,
            invoke_inputs=InvokeInputs(query="resume"),
            execute_tool_call=_execute_tool_call,
        )
    )
    assert result is None
    return seen_extra, ctx.extra


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_input", "state", "expected_key", "unexpected_key"),
    [
        (
            {"action": "allow_always"},
            _state(
                _interrupted_tool(
                    _request(
                        "Approve evolution?",
                        resume_user_input_key=EVOLUTION_RESUME_USER_INPUT_KEY,
                    )
                )
            ),
            EVOLUTION_RESUME_USER_INPUT_KEY,
            RESUME_USER_INPUT_KEY,
        ),
        (
            {"approved": True, "auto_confirm": True},
            _state(_interrupted_tool(_request())),
            RESUME_USER_INPUT_KEY,
            EVOLUTION_RESUME_USER_INPUT_KEY,
        ),
    ],
)
async def test_single_resume_input_exposes_only_expected_key(
    user_input: dict[str, Any],
    state: ToolInterruptionState,
    expected_key: str,
    unexpected_key: str,
):
    seen_extra, final_extra = await _handle_resume_and_capture_extra(state, user_input)

    assert seen_extra[expected_key] == user_input
    assert unexpected_key not in seen_extra
    assert expected_key not in final_extra


@pytest.mark.asyncio
async def test_mixed_resume_input_exposes_generic_and_dedicated_keys():
    user_input = InteractiveInput()
    user_input.update("call_001", {"action": "allow_once"})
    user_input.update("call_002", {"approved": True})
    state = _state(
        _interrupted_tool(
            _request(
                "Approve evolution?",
                resume_user_input_key=EVOLUTION_RESUME_USER_INPUT_KEY,
            ),
            call_id="call_001",
            name="evolve_skill_experiences",
        ),
        _interrupted_tool(_request(), call_id="call_002"),
    )

    seen_extra, final_extra = await _handle_resume_and_capture_extra(state, user_input)

    assert seen_extra[RESUME_USER_INPUT_KEY] == user_input
    assert seen_extra[EVOLUTION_RESUME_USER_INPUT_KEY] == user_input
    assert RESUME_USER_INPUT_KEY not in final_extra
    assert EVOLUTION_RESUME_USER_INPUT_KEY not in final_extra
