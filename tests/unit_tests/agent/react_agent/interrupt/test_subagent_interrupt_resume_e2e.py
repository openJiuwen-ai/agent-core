# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end style tests for subagent interrupt -> resume context hygiene."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.interrupt.handler import (
    ResumeContext,
    ToolInterruptHandler,
    _INTERRUPT_PENDING_TOOL_MESSAGE,
)
from openjiuwen.core.single_agent.interrupt.state import ToolInterruptEntry, ToolInterruptionState
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ToolCallInputs
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.subagent.task_tool import _build_success_tool_content


class _RecordingContext:
    def __init__(self):
        self._messages: list = []

    def get_messages(self, size=None, with_history=True):
        if size is None:
            return list(self._messages)
        return list(self._messages[-size:])

    def set_messages(self, messages, with_history=True):
        self._messages = list(messages)

    def add_message(self, message) -> None:
        self._messages.append(message)


@pytest.mark.asyncio
async def test_interrupt_then_resume_produces_single_clean_tool_message() -> None:
    """Simulate: task_tool interrupt -> pending msg in context -> resume success -> cleanup."""
    call_id = "call_e2e_task"
    context = _RecordingContext()

    manager = AbilityManager()
    parent_ctx = AgentCallbackContext(agent=MagicMock())
    tool_call = ToolCall(
        id=call_id,
        type="function",
        name="task_tool",
        arguments=json.dumps({"subagent_type": "code", "task_description": "read file"}),
    )
    interrupt_result = {
        "result_type": "interrupt",
        "interrupt_ids": ["perm_inner"],
        "state": [],
    }

    with patch.object(
        manager,
        "_railed_execute_single_tool_call",
        new_callable=AsyncMock,
        return_value=interrupt_result,
    ):
        interrupt_exec = await manager.execute(
            ctx=parent_ctx,
            tool_call=tool_call,
            session=MagicMock(),
        )

    _interrupt_tool_result, interrupt_tool_msg = interrupt_exec[0]
    context.add_message(interrupt_tool_msg)
    assert interrupt_tool_msg.content == _INTERRUPT_PENDING_TOOL_MESSAGE

    success_content = _build_success_tool_content(
        "file is empty",
        subagent_type="code",
        language="cn",
    )
    success_tool_msg = ToolMessage(content=success_content, tool_call_id=call_id)

    handler = ToolInterruptHandler(agent=object())
    state = ToolInterruptionState(
        ai_message=AssistantMessage(content="", tool_calls=[tool_call]),
        iteration=1,
        interrupted_tools={
            call_id: ToolInterruptEntry(
                tool_call=tool_call,
                interrupt_requests={},
                is_sub_agent=True,
            )
        },
    )
    ctx = AgentCallbackContext(agent=object(), inputs=ToolCallInputs())

    async def _execute_tool_call(ctx_arg, tools_to_execute, session, context_arg):
        context_arg.add_message(success_tool_msg)
        tool_output = ToolOutput(
            success=True,
            data={
                "content": success_content,
                "output": "file is empty",
                "agent_id": "sub-agent-id",
            },
            error=None,
        )
        rendered = AbilityManager._build_tool_message_content(tool_output)
        return [(tool_output, ToolMessage(content=rendered, tool_call_id=call_id))]

    resume_result = await handler.handle_resume(
        ResumeContext(
            state=state,
            user_input={"action": "allow_once"},
            ctx=ctx,
            context=context,
            session=None,
            invoke_inputs=InvokeInputs(query="resume"),
            execute_tool_call=_execute_tool_call,
        )
    )

    assert resume_result is None
    tool_messages = [m for m in context.get_messages() if getattr(m, "role", None) == "tool"]
    assert len(tool_messages) == 1
    assert "已完成任务" in tool_messages[0].content
    assert "file is empty" in tool_messages[0].content
    assert "interrupt_ids" not in tool_messages[0].content
    assert "ConfirmPayload" not in tool_messages[0].content


def test_build_sub_agent_resume_tool_call_injects_query() -> None:
    tool_call = ToolCall(
        id="call_resume",
        type="function",
        name="task_tool",
        arguments=json.dumps({"subagent_type": "code", "task_description": "read file"}),
    )
    user_input = {"action": "allow_once", "path": r"C:\tmp\file.txt"}

    resumed = ToolInterruptHandler._build_sub_agent_resume_tool_call(tool_call, user_input)
    args = resumed.arguments if isinstance(resumed.arguments, dict) else json.loads(resumed.arguments)
    assert args["query"] == user_input
    assert args["subagent_type"] == "code"
