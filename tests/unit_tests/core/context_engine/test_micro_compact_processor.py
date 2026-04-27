# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import List

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.compressor.micro_compact_processor import (
    MicroCompactProcessorConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage


def create_tool_call_list(ids: List[str], names: List[str]) -> List[ToolCall]:
    return [
        ToolCall(id=tool_call_id, name=tool_name, type="function", arguments="")
        for tool_call_id, tool_name in zip(ids, names)
    ]


async def create_context_with_micro_compact(
    config: MicroCompactProcessorConfig,
    history_messages=None,
):
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=100))
    return await engine.create_context(
        "test_ctx",
        None,
        history_messages=history_messages or [],
        processors=[("MicroCompactProcessor", config)],
    )


class TestMicroCompactProcessor:
    @pytest.mark.asyncio
    async def test_trigger_add_messages_clears_old_tool_messages(self):
        """Test that MicroCompactProcessor clears old tool messages when threshold exceeded."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file1", "read_file2"],
            keep_recent_per_tool=1,
        )
        ctx = await create_context_with_micro_compact(config)

        # Add 3 read_file1 + 3 read_file2 in a single batch (exceeds limit of 2)
        batch1 = [
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-1"], ["read_file1"]),
            ),
            ToolMessage(content="file-content-1", tool_call_id="tc-1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-2"], ["read_file1"]),
            ),
            ToolMessage(content="file-content-2", tool_call_id="tc-2"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-3"], ["read_file1"]),
            ),
            ToolMessage(content="file-content-3", tool_call_id="tc-3"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-4"], ["read_file2"]),
            ),
            ToolMessage(content="file-content-4", tool_call_id="tc-4"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-5"], ["read_file2"]),
            ),
            ToolMessage(content="file-content-5", tool_call_id="tc-5"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-6"], ["read_file2"]),
            ),
            ToolMessage(content="file-content-6", tool_call_id="tc-6"),
        ]
        await ctx.add_messages(batch1)

        agent_messages = ctx.get_messages()
        tool_messages = [msg for msg in agent_messages if isinstance(msg, ToolMessage)]
        # Keep last `keep` (1) per tool, clear the rest: fc-3 and fc-6 survive.
        assert [msg.content for msg in tool_messages] == [
            config.cleared_marker,
            config.cleared_marker,
            "file-content-3",
            config.cleared_marker,
            config.cleared_marker,
            "file-content-6",
        ]

    @pytest.mark.asyncio
    async def test_keep_recent_per_tool_is_applied_per_tool_name(self):
        """Test that keep_recent_per_tool is applied per tool name."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file", "grep"],
            keep_recent_per_tool=1,
        )
        ctx = await create_context_with_micro_compact(config)

        # Add 3 read_file + 3 grep messages in a single batch
        batch = [
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["r1"], ["read_file"]),
            ),
            ToolMessage(content="read-1", tool_call_id="r1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["r2"], ["read_file"]),
            ),
            ToolMessage(content="read-2", tool_call_id="r2"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["r3"], ["read_file"]),
            ),
            ToolMessage(content="read-3", tool_call_id="r3"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["g1"], ["grep"]),
            ),
            ToolMessage(content="grep-1", tool_call_id="g1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["g2"], ["grep"]),
            ),
            ToolMessage(content="grep-2", tool_call_id="g2"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["g3"], ["grep"]),
            ),
            ToolMessage(content="grep-3", tool_call_id="g3"),
        ]
        await ctx.add_messages(batch)
        await ctx.add_messages([UserMessage(content="trigger")])

        agent_messages = ctx.get_messages()
        tool_messages = [msg for msg in agent_messages if isinstance(msg, ToolMessage)]
        # With trigger=1, keep=1: limit=2. 3 > 2, keep last 1 per tool, clear the rest.
        assert [msg.content for msg in tool_messages] == [
            config.cleared_marker,
            config.cleared_marker,
            "read-3",
            config.cleared_marker,
            config.cleared_marker,
            "grep-3",
        ]

    @pytest.mark.asyncio
    async def test_keep_recent_per_tool_is_applied_independently_across_tools(self):
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file", "grep", "glob"],
            keep_recent_per_tool=2,
        )
        ctx = await create_context_with_micro_compact(config)
        messages = [
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["read-1"], ["read_file"]),
            ),
            ToolMessage(content="read-old", tool_call_id="read-1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["grep-1"], ["grep"]),
            ),
            ToolMessage(content="grep-old", tool_call_id="grep-1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["glob-1"], ["glob"]),
            ),
            ToolMessage(content="glob-newer", tool_call_id="glob-1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["read-2"], ["read_file"]),
            ),
            ToolMessage(content="read-newest", tool_call_id="read-2"),
        ]
        await ctx.add_messages(messages)

        window = await ctx.get_context_window()
        tool_messages = [msg for msg in window.context_messages if isinstance(msg, ToolMessage)]
        assert [msg.content for msg in tool_messages] == [
            "read-old",
            "grep-old",
            "glob-newer",
            "read-newest",
        ]

    @pytest.mark.asyncio
    async def test_cleared_messages_are_not_reprocessed(self):
        """Test that already cleared messages are not reprocessed."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file"],
            keep_recent_per_tool=1,
        )
        history = [
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-1"], ["read_file"]),
            ),
            ToolMessage(content=config.cleared_marker, tool_call_id="tc-1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-2"], ["read_file"]),
            ),
            ToolMessage(content="fresh-content", tool_call_id="tc-2"),
        ]
        ctx = await create_context_with_micro_compact(config, history_messages=history)

        agent_messages = ctx.get_messages()
        tool_messages = [msg for msg in agent_messages if isinstance(msg, ToolMessage)]
        # Cleared marker should remain, fresh content should remain
        assert [msg.content for msg in tool_messages] == [
            config.cleared_marker,
            "fresh-content",
        ]
