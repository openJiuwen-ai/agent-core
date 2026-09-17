# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compression states name the messages a processor rewrote and where their originals went."""

import json

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.context.processor_state_recorder import (
    ContextProcessorStateInput,
    ContextProcessorStateRecorder,
)
from openjiuwen.core.context_engine.processor.forked.offloader.message_offloader import (
    MessageSummaryOffloaderConfig,
)
from openjiuwen.core.context_engine.schema.messages import OffloadMixin, create_offload_message
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, ToolCall, ToolMessage, UserMessage


def _state_input(
    before_messages: list[BaseMessage],
    after_messages: list[BaseMessage] | None,
    status: str = "completed",
) -> ContextProcessorStateInput:
    return ContextProcessorStateInput(
        operation_id="op-1",
        status=status,
        phase="add_messages",
        trigger="passive",
        processor=None,
        reason="processor_completed",
        before_messages=before_messages,
        after_messages=after_messages,
        started_at=1.0,
        ended_at=None if after_messages is None else 2.0,
        error=None,
        messages_to_modify=[],
        force=False,
        context_max=1000,
    )


def _recorder() -> ContextProcessorStateRecorder:
    return ContextProcessorStateRecorder(
        session_id="session-a",
        context_id="context-a",
        get_session_ref=lambda: None,
    )


def test_offloaded_message_reports_its_id_and_offload_handle():
    user = UserMessage(content="read it", metadata={"context_message_id": "u1"})
    tool = ToolMessage(content="x" * 500, tool_call_id="call-1", metadata={"context_message_id": "t1"})
    offloaded = create_offload_message(
        role="tool",
        content="preview [[OFFLOAD: handle=h1, type=filesystem]]",
        offload_handle="h1",
        offload_type="filesystem",
        tool_call_id="call-1",
        metadata={"context_message_id": "t1"},
    )

    state = _recorder().build_state(_state_input([user, tool], [user, offloaded]))

    assert [message.model_dump() for message in state.modified_messages] == [
        {
            "message_id": "t1",
            "role": "tool",
            "tool_call_id": "call-1",
            "offload_handle": "h1",
            "offload_type": "filesystem",
        }
    ]


def test_in_place_rewrite_without_offload_has_no_handle():
    before = ToolMessage(content="long output", tool_call_id="call-1", metadata={"context_message_id": "t1"})
    after = ToolMessage(content="short", tool_call_id="call-1", metadata={"context_message_id": "t1"})

    state = _recorder().build_state(_state_input([before], [after]))

    assert len(state.modified_messages) == 1
    assert state.modified_messages[0].message_id == "t1"
    assert state.modified_messages[0].offload_handle is None


def test_unchanged_removed_and_new_messages_are_not_reported():
    kept = UserMessage(content="same", metadata={"context_message_id": "u1"})
    copied = UserMessage(content="same", metadata={"context_message_id": "u1"})
    removed = AssistantMessage(content="old answer", metadata={"context_message_id": "a1"})
    created = UserMessage(content="summary", metadata={"context_message_id": "summary"})
    unidentified = UserMessage(content="no id")

    state = _recorder().build_state(_state_input([kept, removed, unidentified], [copied, created, unidentified]))

    assert state.modified_messages == []


def test_started_state_reports_no_modified_messages():
    tool = ToolMessage(content="x", tool_call_id="call-1", metadata={"context_message_id": "t1"})

    state = _recorder().build_state(_state_input([tool], None, status="started"))

    assert state.modified_messages == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("refactored_context_processors")
async def test_offloader_completion_state_names_the_offloaded_tool_message(tmp_path):
    engine = ContextEngine(
        ContextEngineConfig(context_window_tokens=100, enable_tiktoken_counter=True),
        workspace=type("Workspace", (), {"root_path": str(tmp_path)})(),
    )
    context = await engine.create_context(
        "test_ctx",
        processors=[("MessageSummaryOffloader", MessageSummaryOffloaderConfig())],
    )
    tool_call = AssistantMessage(
        content="searching",
        tool_calls=[
            ToolCall(
                id="call-grep",
                name="grep",
                type="function",
                arguments=json.dumps({"pattern": "token"}),
            ),
        ],
    )

    await context.add_messages([tool_call, ToolMessage(content="x" * 300, tool_call_id="call-grep")])

    message = context.get_messages()[1]
    assert isinstance(message, OffloadMixin)
    completed = context._processor_state_recorder.history()[-1]
    assert completed["status"] == "completed"
    assert completed["modified_messages"] == [
        {
            "message_id": message.metadata["context_message_id"],
            "role": "tool",
            "tool_call_id": "call-grep",
            "offload_handle": message.offload_handle,
            "offload_type": "filesystem",
        }
    ]
