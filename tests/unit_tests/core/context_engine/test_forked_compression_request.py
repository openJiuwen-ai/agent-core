from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor import (
    CurrentRoundCompressor,
    CurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.round_level_compressor import (
    RoundLevelCompressor,
    RoundLevelCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import CompressionExecutor
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.tool import ToolInfo


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "compressor, expected_context",
    [
        (
            DialogueCompressor(DialogueCompressorConfig()),
            ["Earlier request", "Earlier answer"],
        ),
        (
            CurrentRoundCompressor(CurrentRoundCompressorConfig(keep_recent_messages=1)),
            ["Earlier request", "Earlier answer", "Current request", "Called tool", "Tool result"],
        ),
        (
            RoundLevelCompressor(RoundLevelCompressorConfig(keep_recent_messages=1)),
            ["Earlier request", "Earlier answer", "Current request", "Called tool", "Tool result"],
        ),
    ],
    ids=["dialogue", "current-round", "round-level"],
)
async def test_compression_model_receives_history_but_not_main_agent_system_or_tools(compressor, expected_context):
    model = MagicMock()
    model.invoke = AsyncMock(return_value=AssistantMessage(content="Short summary"))
    compressor._compression_executor = CompressionExecutor(model)

    main_system = SystemMessage(content="Main agent system instructions")
    read_tool = ToolInfo(name="read_file", description="Read a file", parameters={})
    history = [
        UserMessage(content="Earlier request"),
        AssistantMessage(content="Earlier answer"),
        UserMessage(content="Current request"),
        AssistantMessage(
            content="Called tool",
            tool_calls=[ToolCall(id="call-1", name="read_file", type="function", arguments="{}")],
        ),
        ToolMessage(content="Tool result", tool_call_id="call-1"),
        AssistantMessage(content="Recent status"),
    ]
    window = ContextWindow(system_messages=[main_system], context_messages=history, tools=[read_tool])
    span = compressor._build_span(history)
    assert span.has_target

    result = await compressor._invoke_compression_with_retries(
        context=MagicMock(),
        context_window=window,
        span=span,
        prompt="Summarize this history",
    )

    assert result is not None
    sent = model.invoke.await_args.kwargs
    assert [message.content for message in sent["messages"]] == [
        *expected_context,
        "Summarize this history",
    ]
    assert not any(isinstance(message, SystemMessage) for message in sent["messages"])
    assert sent["tools"] == []
    assert window.system_messages == [main_system]
    assert window.tools == [read_tool]
