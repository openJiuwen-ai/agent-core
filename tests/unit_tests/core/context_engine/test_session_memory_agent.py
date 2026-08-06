from types import SimpleNamespace

import pytest

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.context.session_memory_manager import SessionMemoryConfig, SessionMemoryManager
from openjiuwen.core.context_engine.processor.forked.compressor.session_memory_agent import (
    SessionMemoryAbilityManager,
    SessionMemoryAgent,
)
from openjiuwen.core.context_engine.processor.forked.compressor.session_memory_compressor import (
    SessionMemoryCompressor,
    SessionMemoryCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
    CompressionExecutor,
    CompressionRequest,
)
from openjiuwen.core.context_engine.processor.forked.compressor.support.forked_agent import ForkedAgent
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ModelClientConfig,
    ModelRequestConfig,
    ToolCall,
    UserMessage,
)
from openjiuwen.core.foundation.tool import ToolInfo


def test_forked_agent_inherits_compression_executor():
    assert issubclass(ForkedAgent, CompressionExecutor)


def test_session_memory_threshold_defaults_are_independent():
    assert SessionMemoryConfig().update_trigger_context_ratio == 0.7
    assert SessionMemoryCompressorConfig().trigger_context_ratio == 0.8


class _FixedTokenCounter:
    def __init__(self, value):
        self.value = value

    def count_messages(self, messages):
        _ = messages
        return self.value


def test_session_memory_update_uses_ratio_without_tool_call_threshold():
    session_state = {}

    class _Session:
        def get_state(self, key):
            return session_state.get(key)

        def update_state(self, update):
            session_state.update(update)

        def get_session_id(self):
            return "session-1"

    context = SimpleNamespace(
        token_counter=lambda: _FixedTokenCounter(70),
        _context_window_tokens=100,
    )
    window = ContextWindow(context_messages=[UserMessage(content="request"), AssistantMessage(content="response")])
    manager = SessionMemoryManager(SessionMemoryConfig())

    assert manager.should_update(_Session(), context, window) is True


def test_session_memory_processor_uses_independent_ratio_threshold(tmp_path):
    notes_path = tmp_path / "session_context.md"
    notes_path.write_text("# Current State\nworking", encoding="utf-8")
    session = SimpleNamespace(
        get_state=lambda key: (
            {
                "memory_path": str(notes_path),
                "notes_upto_message_id": "m1",
            }
            if key == "__session_memory__"
            else None
        ),
    )
    context = SimpleNamespace(
        get_session_ref=lambda: session,
        set_messages=lambda messages: None,
        token_counter=lambda: _FixedTokenCounter(79),
        _context_window_tokens=100,
    )
    messages = [
        UserMessage(content="old", metadata={"context_message_id": "m1"}),
        UserMessage(content="new", metadata={"context_message_id": "m2"}),
    ]
    window = ContextWindow(context_messages=messages)
    processor = SessionMemoryCompressor(
        SessionMemoryCompressorConfig(enabled=True, session_memory_path=str(notes_path))
    )

    assert processor._context_reaches_threshold(context, window) is False
    context.token_counter = lambda: _FixedTokenCounter(80)
    assert processor._context_reaches_threshold(context, window) is True


def test_build_context_messages_does_not_duplicate_agent_prompt():
    request = CompressionRequest(
        prompt="update notes",
        system_messages=[UserMessage(content="system-like")],
        context_messages=[UserMessage(content="history")],
    )

    context_messages = CompressionExecutor.build_context_messages(request)

    assert [message.content for message in context_messages] == ["system-like", "history"]
    assert [message.content for message in CompressionExecutor.build_messages(request)] == [
        "system-like",
        "history",
        "update notes",
    ]


@pytest.mark.asyncio
async def test_session_memory_manager_preserves_model_tools_and_rejects_other_tools(tmp_path):
    manager = SessionMemoryAbilityManager(owner_id="session-memory")
    tools = [
        ToolInfo(name="read_file", description="read", parameters={}),
        ToolInfo(name="edit_file", description="edit", parameters={}),
    ]
    manager.set_model_tools(tools)
    manager.set_allowed_notes_path(tmp_path / "pending.md")

    assert await manager.list_tool_info() == tools

    rejected_call = ToolCall(
        id="call-read",
        type="function",
        name="read_file",
        arguments='{"file_path": "anything"}',
    )
    results = await manager.execute(
        ctx=None,
        tool_call=rejected_call,
        session=None,
    )

    assert len(results) == 1
    assert results[0][1].tool_call_id == "call-read"
    assert "Only edit_file" in results[0][1].content
    assert manager.rejected_tool_calls[0]["name"] == "read_file"

    second_call = ToolCall(
        id="call-other",
        type="function",
        name="write_file",
        arguments="{}",
    )
    ordered = await manager.execute(ctx=None, tool_call=[rejected_call, second_call], session=None)
    assert [item[1].tool_call_id for item in ordered] == ["call-read", "call-other"]


@pytest.mark.asyncio
async def test_session_memory_manager_rejects_edit_outside_pending_file(tmp_path):
    manager = SessionMemoryAbilityManager(owner_id="session-memory")
    manager.set_allowed_notes_path(tmp_path / "pending.md")
    call = ToolCall(
        id="call-edit",
        type="function",
        name="edit_file",
        arguments='{"file_path": "other.md", "old_string": "a", "new_string": "b"}',
    )

    results = await manager.execute(ctx=None, tool_call=call, session=None)

    assert len(results) == 1
    assert "pending notes file" in results[0][1].content


@pytest.mark.asyncio
async def test_session_memory_compressor_renders_committed_notes(tmp_path):
    notes_path = tmp_path / "session_context.md"
    notes_path.write_text("# Current State\nworking", encoding="utf-8")
    session = SimpleNamespace(
        get_state=lambda key: (
            {
                "memory_path": str(notes_path),
                "notes_upto_message_id": "m1",
            }
            if key == "__session_memory__"
            else None
        ),
    )
    context = SimpleNamespace(
        get_session_ref=lambda: session,
        set_messages=lambda messages: None,
        _context_window_tokens=1,
    )
    messages = [
        UserMessage(content="old context padding here", metadata={"context_message_id": "m1"}),
        UserMessage(content="new", metadata={"context_message_id": "m2"}),
    ]
    window = ContextWindow(context_messages=messages)
    processor = SessionMemoryCompressor(
        SessionMemoryCompressorConfig(enabled=True, session_memory_path=str(notes_path))
    )

    event, updated = await processor.on_get_context_window(context, window)

    assert event is not None
    assert len(updated.context_messages) == 2
    assert updated.context_messages[0].content.startswith("<memory_block_session>")
    assert updated.context_messages[1].content == "new"


@pytest.mark.asyncio
async def test_session_memory_agent_uses_react_agent_and_preserves_request_tools(tmp_path):
    class FakeModel:
        def __init__(self):
            self.calls = []

        async def invoke(self, **kwargs):
            self.calls.append(kwargs)
            return AssistantMessage(content="done", tool_calls=[])

    model = FakeModel()
    agent = SessionMemoryAgent(
        model,
        model_config=ModelRequestConfig(model_name="fake"),
        model_client_config=ModelClientConfig(
            client_provider="openai",
            api_key="test",
            api_base="http://localhost",
        ),
        workspace_root=tmp_path,
    )
    tools = [
        ToolInfo(name="read_file", description="read", parameters={}),
        ToolInfo(name="edit_file", description="edit", parameters={}),
    ]
    agent.configure_request(tools=tools, allowed_notes_path=tmp_path / "pending.md")

    result = await agent.invoke(
        CompressionRequest(
            prompt="update the notes",
            context_messages=[UserMessage(content="history")],
            tools=tools,
        )
    )

    assert result.response["output"] == "done"
    assert [tool.name for tool in model.calls[0]["tools"]] == ["read_file", "edit_file"]
    assert model.calls[0]["messages"][-1].content == "update the notes"
