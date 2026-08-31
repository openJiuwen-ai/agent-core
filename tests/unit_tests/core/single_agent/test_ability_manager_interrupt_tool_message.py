# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for concise interrupt tool messages in AbilityManager (priority 1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import ToolCall, ToolMessage
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.interrupt.handler import _INTERRUPT_PENDING_TOOL_MESSAGE
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext


@pytest.mark.asyncio
async def test_interrupt_dict_with_ids_uses_pending_message() -> None:
    manager = AbilityManager()
    parent_ctx = AgentCallbackContext(agent=MagicMock())
    tool_call = ToolCall(
        id="call_interrupt",
        type="function",
        name="task_tool",
        arguments='{"subagent_type":"code"}',
    )
    interrupt_result = {
        "result_type": "interrupt",
        "interrupt_ids": ["inner_permission_1"],
        "state": [{"payload": "ConfirmPayload(...)"}],
    }

    with patch.object(
        manager,
        "_railed_execute_single_tool_call",
        new_callable=AsyncMock,
        return_value=interrupt_result,
    ):
        results = await manager.execute(
            ctx=parent_ctx,
            tool_call=tool_call,
            session=MagicMock(),
        )

    assert len(results) == 1
    tool_result, tool_msg = results[0]
    assert tool_result == interrupt_result
    assert isinstance(tool_msg, ToolMessage)
    assert tool_msg.content == _INTERRUPT_PENDING_TOOL_MESSAGE
    assert "ConfirmPayload" not in tool_msg.content
    assert "interrupt_ids" not in tool_msg.content


@pytest.mark.asyncio
async def test_non_interrupt_dict_still_uses_stringified_result() -> None:
    manager = AbilityManager()
    parent_ctx = AgentCallbackContext(agent=MagicMock())
    tool_call = ToolCall(
        id="call_answer",
        type="function",
        name="workflow_tool",
        arguments="{}",
    )
    answer_result = {"result_type": "answer", "output": "done"}

    with patch.object(
        manager,
        "_railed_execute_single_tool_call",
        new_callable=AsyncMock,
        return_value=answer_result,
    ):
        results = await manager.execute(
            ctx=parent_ctx,
            tool_call=tool_call,
            session=MagicMock(),
        )

    _tool_result, tool_msg = results[0]
    assert isinstance(tool_msg, ToolMessage)
    assert tool_msg.content == str(answer_result)


@pytest.mark.asyncio
async def test_interrupt_dict_without_ids_still_uses_stringified_result() -> None:
    manager = AbilityManager()
    parent_ctx = AgentCallbackContext(agent=MagicMock())
    tool_call = ToolCall(
        id="call_workflow_interrupt",
        type="function",
        name="workflow",
        arguments="{}",
    )
    workflow_interrupt = {
        "result_type": "interrupt",
        "workflow_execution_state": {"step": 1},
    }

    with patch.object(
        manager,
        "_railed_execute_single_tool_call",
        new_callable=AsyncMock,
        return_value=workflow_interrupt,
    ):
        results = await manager.execute(
            ctx=parent_ctx,
            tool_call=tool_call,
            session=MagicMock(),
        )

    _tool_result, tool_msg = results[0]
    assert tool_msg.content == str(workflow_interrupt)


def test_build_tool_message_content_prefers_data_content() -> None:
    from openjiuwen.harness.tools.base_tool import ToolOutput

    content = (
        "子智能体「code」已完成任务。"
        "无需再次向用户确认或重复调用 task_tool / read_file。"
    )
    result = ToolOutput(
        success=True,
        data={"content": content, "output": "file body", "agent_id": "agent-1"},
        error=None,
    )
    assert AbilityManager._build_tool_message_content(result) == content
