# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Comprehensive tests for post-resume interrupt context cleanup (priority 1)."""

from __future__ import annotations

from typing import Any

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.handler import (
    ResumeContext,
    ToolInterruptHandler,
    _INTERRUPT_PENDING_TOOL_MESSAGE,
)
from openjiuwen.core.single_agent.interrupt.state import (
    RESUME_START_ITERATION_KEY,
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ToolCallInputs


class _FakeModelContext:
    def __init__(self, messages: list):
        self._messages = list(messages)

    def get_messages(self, size=None, with_history=True):
        if size is None:
            return list(self._messages)
        return list(self._messages[-size:])

    def set_messages(self, messages, with_history=True):
        self._messages = list(messages)


def _tool_call(call_id: str = "call_001", name: str = "task_tool") -> ToolCall:
    return ToolCall(id=call_id, type="function", name=name, arguments="{}")


def _sub_agent_state(call_id: str) -> ToolInterruptionState:
    return ToolInterruptionState(
        ai_message=AssistantMessage(content="", tool_calls=[_tool_call(call_id)]),
        iteration=2,
        interrupted_tools={
            call_id: ToolInterruptEntry(
                tool_call=_tool_call(call_id),
                interrupt_requests={},
                is_sub_agent=True,
            )
        },
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("", False),
        ("   ", False),
        ("子智能体已完成任务，文件内容为空。", False),
        (_INTERRUPT_PENDING_TOOL_MESSAGE, True),
        ("[INTERRUPTED - Waiting for user input]", True),
        ('{"result_type": "interrupt", "interrupt_ids": ["inner_1"]}', True),
        ("{'result_type': 'interrupt', 'interrupt_ids': ['inner_1']}", True),
        ("ConfirmPayload(...) interrupt_ids=['x']", True),
        ("ConfirmPayload only, no ids", False),
        ("only interrupt_ids field present", False),
    ],
)
def test_is_interrupt_tool_message_content(content: str, expected: bool) -> None:
    assert ToolInterruptHandler._is_interrupt_tool_message_content(content) is expected


class TestCleanupResolvedInterruptToolMessages:
    def test_no_op_when_resolved_ids_empty(self) -> None:
        context = _FakeModelContext([ToolMessage(content="x", tool_call_id="call_1")])
        ToolInterruptHandler._cleanup_resolved_interrupt_tool_messages(context, set(), [])
        assert len(context.get_messages()) == 1

    def test_no_op_when_messages_empty(self) -> None:
        context = _FakeModelContext([])
        ToolInterruptHandler._cleanup_resolved_interrupt_tool_messages(
            context,
            {"call_1"},
            [(None, ToolMessage(content="ok", tool_call_id="call_1"))],
        )
        assert context.get_messages() == []

    def test_drops_stale_interrupt_keeps_success_for_same_call_id(self) -> None:
        call_id = "call_task"
        interrupt = (
            "{'result_type': 'interrupt', 'interrupt_ids': ['inner'], "
            "'state': [ConfirmPayload(...)]}"
        )
        success = "子智能体「general-purpose」已完成任务。\n\nfile is empty"
        context = _FakeModelContext(
            [
                UserMessage(content="read desktop file"),
                ToolMessage(content=interrupt, tool_call_id=call_id),
                ToolMessage(content=success, tool_call_id=call_id),
            ]
        )
        ToolInterruptHandler._cleanup_resolved_interrupt_tool_messages(
            context,
            {call_id},
            [(None, ToolMessage(content=success, tool_call_id=call_id))],
        )
        messages = context.get_messages()
        tool_msgs = [m for m in messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == success
        assert messages[0].role == "user"

    def test_replaces_single_interrupt_message_when_only_one_exists(self) -> None:
        call_id = "call_only_interrupt"
        interrupt = _INTERRUPT_PENDING_TOOL_MESSAGE
        success = "Subagent completed. Content: hello"
        context = _FakeModelContext([ToolMessage(content=interrupt, tool_call_id=call_id)])
        ToolInterruptHandler._cleanup_resolved_interrupt_tool_messages(
            context,
            {call_id},
            [(None, ToolMessage(content=success, tool_call_id=call_id))],
        )
        messages = context.get_messages()
        assert len(messages) == 1
        assert messages[0].content == success

    def test_leaves_success_only_message_unchanged(self) -> None:
        call_id = "call_ok"
        success = "already clean success message"
        context = _FakeModelContext([ToolMessage(content=success, tool_call_id=call_id)])
        ToolInterruptHandler._cleanup_resolved_interrupt_tool_messages(
            context,
            {call_id},
            [(None, ToolMessage(content=success, tool_call_id=call_id))],
        )
        assert context.get_messages()[0].content == success

    def test_leaves_interrupt_when_no_success_result_available(self) -> None:
        call_id = "call_stuck"
        interrupt = _INTERRUPT_PENDING_TOOL_MESSAGE
        context = _FakeModelContext([ToolMessage(content=interrupt, tool_call_id=call_id)])
        ToolInterruptHandler._cleanup_resolved_interrupt_tool_messages(
            context,
            {call_id},
            [(None, None)],
        )
        assert context.get_messages()[0].content == interrupt

    def test_cleans_only_resolved_call_ids(self) -> None:
        resolved = "call_resolved"
        untouched = "call_other"
        interrupt = _INTERRUPT_PENDING_TOOL_MESSAGE
        success = "resolved success"
        context = _FakeModelContext(
            [
                ToolMessage(content=interrupt, tool_call_id=resolved),
                ToolMessage(content=success, tool_call_id=resolved),
                ToolMessage(content=interrupt, tool_call_id=untouched),
            ]
        )
        ToolInterruptHandler._cleanup_resolved_interrupt_tool_messages(
            context,
            {resolved},
            [(None, ToolMessage(content=success, tool_call_id=resolved))],
        )
        messages = context.get_messages()
        resolved_msgs = [m for m in messages if m.tool_call_id == resolved]
        other_msgs = [m for m in messages if m.tool_call_id == untouched]
        assert len(resolved_msgs) == 1
        assert resolved_msgs[0].content == success
        assert len(other_msgs) == 1
        assert other_msgs[0].content == interrupt

    def test_three_interrupt_messages_collapses_to_one_success(self) -> None:
        call_id = "call_triple"
        success = "final success from resume"
        context = _FakeModelContext(
            [
                ToolMessage(content=_INTERRUPT_PENDING_TOOL_MESSAGE, tool_call_id=call_id),
                ToolMessage(
                    content='{"result_type": "interrupt", "interrupt_ids": ["a"]}',
                    tool_call_id=call_id,
                ),
                ToolMessage(content=success, tool_call_id=call_id),
            ]
        )
        ToolInterruptHandler._cleanup_resolved_interrupt_tool_messages(
            context,
            {call_id},
            [(None, ToolMessage(content=success, tool_call_id=call_id))],
        )
        tool_msgs = [m for m in context.get_messages() if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == success


async def _resume_with_context(
    *,
    state: ToolInterruptionState,
    context: _FakeModelContext | None,
    results: list,
    user_input: Any = {"action": "allow_once"},
) -> Any:
    handler = ToolInterruptHandler(agent=object())
    ctx = AgentCallbackContext(agent=object(), inputs=ToolCallInputs())

    async def _execute_tool_call(ctx_arg, tools_to_execute, session, context_arg):
        return results

    return await handler.handle_resume(
        ResumeContext(
            state=state,
            user_input=user_input,
            ctx=ctx,
            context=context,
            session=None,
            invoke_inputs=InvokeInputs(query="resume"),
            execute_tool_call=_execute_tool_call,
        )
    )


@pytest.mark.asyncio
async def test_handle_resume_cleans_context_on_success() -> None:
    call_id = "call_resume_ok"
    success = "子智能体已完成。内容：empty file"
    context = _FakeModelContext(
        [
            ToolMessage(content=_INTERRUPT_PENDING_TOOL_MESSAGE, tool_call_id=call_id),
            ToolMessage(content=success, tool_call_id=call_id),
        ]
    )
    result = await _resume_with_context(
        state=_sub_agent_state(call_id),
        context=context,
        results=[(None, ToolMessage(content=success, tool_call_id=call_id))],
    )
    assert result is None
    assert len([m for m in context.get_messages() if m.role == "tool"]) == 1


@pytest.mark.asyncio
async def test_handle_resume_skips_cleanup_when_still_interrupted() -> None:
    from unittest.mock import AsyncMock

    call_id = "call_still_waiting"
    interrupt_dict = {
        "result_type": "interrupt",
        "interrupt_ids": ["inner_new"],
        "state": [],
    }
    context = _FakeModelContext(
        [ToolMessage(content=_INTERRUPT_PENDING_TOOL_MESSAGE, tool_call_id=call_id)]
    )
    handler = ToolInterruptHandler(agent=object())
    handler.commit_interrupt = AsyncMock(return_value={"result_type": "interrupt"})  # type: ignore[method-assign]
    ctx = AgentCallbackContext(agent=object(), inputs=ToolCallInputs())

    async def _execute_tool_call(ctx_arg, tools_to_execute, session, context_arg):
        return [(interrupt_dict, ToolMessage(content=str(interrupt_dict), tool_call_id=call_id))]

    result = await handler.handle_resume(
        ResumeContext(
            state=_sub_agent_state(call_id),
            user_input={"action": "allow_once"},
            ctx=ctx,
            context=context,
            session=None,
            invoke_inputs=InvokeInputs(query="resume"),
            execute_tool_call=_execute_tool_call,
        )
    )
    assert result is not None
    assert result["result_type"] == "interrupt"
    # Cleanup must not run when resume is still blocked on approval.
    assert len(context.get_messages()) == 1


@pytest.mark.asyncio
async def test_handle_resume_skips_cleanup_when_context_is_none() -> None:
    result = await _resume_with_context(
        state=_sub_agent_state("call_no_ctx"),
        context=None,
        results=[(None, ToolMessage(content="ok", tool_call_id="call_no_ctx"))],
    )
    assert result is None


@pytest.mark.asyncio
async def test_handle_resume_sets_resume_start_iteration() -> None:
    ctx = AgentCallbackContext(agent=object(), inputs=ToolCallInputs())
    handler = ToolInterruptHandler(agent=object())
    state = _sub_agent_state("call_iter")
    state.iteration = 4

    async def _execute_tool_call(ctx_arg, tools_to_execute, session, context_arg):
        return [(None, ToolMessage(content="done", tool_call_id="call_iter"))]

    result = await handler.handle_resume(
        ResumeContext(
            state=state,
            user_input={"action": "allow_once"},
            ctx=ctx,
            context=_FakeModelContext([]),
            session=None,
            invoke_inputs=InvokeInputs(query="resume"),
            execute_tool_call=_execute_tool_call,
        )
    )
    assert result is None
    assert ctx.extra[RESUME_START_ITERATION_KEY] == 5


@pytest.mark.asyncio
async def test_handle_resume_cleans_multiple_resolved_tools() -> None:
    call_a = "call_a"
    call_b = "call_b"
    success_a = "success A"
    success_b = "success B"
    context = _FakeModelContext(
        [
            ToolMessage(content=_INTERRUPT_PENDING_TOOL_MESSAGE, tool_call_id=call_a),
            ToolMessage(content=success_a, tool_call_id=call_a),
            ToolMessage(content=_INTERRUPT_PENDING_TOOL_MESSAGE, tool_call_id=call_b),
            ToolMessage(content=success_b, tool_call_id=call_b),
        ]
    )
    state = ToolInterruptionState(
        ai_message=AssistantMessage(
            content="",
            tool_calls=[_tool_call(call_a), _tool_call(call_b)],
        ),
        iteration=0,
        interrupted_tools={
            call_a: ToolInterruptEntry(
                tool_call=_tool_call(call_a),
                interrupt_requests={},
                is_sub_agent=True,
            ),
            call_b: ToolInterruptEntry(
                tool_call=_tool_call(call_b),
                interrupt_requests={},
                is_sub_agent=True,
            ),
        },
    )
    result = await _resume_with_context(
        state=state,
        context=context,
        results=[
            (None, ToolMessage(content=success_a, tool_call_id=call_a)),
            (None, ToolMessage(content=success_b, tool_call_id=call_b)),
        ],
    )
    assert result is None
    tool_msgs = [m for m in context.get_messages() if m.role == "tool"]
    assert len(tool_msgs) == 2
    by_id = {m.tool_call_id: m.content for m in tool_msgs}
    assert by_id[call_a] == success_a
    assert by_id[call_b] == success_b
