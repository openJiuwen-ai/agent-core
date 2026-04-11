# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import List

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.compressor.micro_compact_processor import (
    MicroCompactProcessorConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage


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
    async def test_trigger_get_context_window_true_when_candidates_reach_threshold(self):
        config = MicroCompactProcessorConfig(
            trigger_threshold=2,
            compactable_tool_names=["read_file"],
            keep_recent_per_tool=1,
        )
        ctx = await create_context_with_micro_compact(config)
        messages = [
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-1"], ["read_file"]),
            ),
            ToolMessage(content="file-content-1", tool_call_id="tc-1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-2"], ["read_file"]),
            ),
            ToolMessage(content="file-content-2", tool_call_id="tc-2"),
        ]
        await ctx.add_messages(messages)

        window = await ctx.get_context_window()
        cleared = [msg for msg in window.context_messages if isinstance(msg, ToolMessage)]
        assert cleared[0].content == config.cleared_marker
        assert cleared[1].content == "file-content-2"

    @pytest.mark.asyncio
    async def test_keep_recent_per_tool_is_applied_per_tool_name(self):
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file", "grep"],
            keep_recent_per_tool=1,
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
                tool_calls=create_tool_call_list(["read-2"], ["read_file"]),
            ),
            ToolMessage(content="read-new", tool_call_id="read-2"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["grep-2"], ["grep"]),
            ),
            ToolMessage(content="grep-new", tool_call_id="grep-2"),
        ]
        await ctx.add_messages(messages)

        window = await ctx.get_context_window()
        tool_messages = [msg for msg in window.context_messages if isinstance(msg, ToolMessage)]
        assert [msg.content for msg in tool_messages] == [
            config.cleared_marker,
            config.cleared_marker,
            "read-new",
            "grep-new",
        ]

    @pytest.mark.asyncio
    async def test_cleared_messages_are_not_reprocessed(self):
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

        window = await ctx.get_context_window()
        tool_messages = [msg for msg in window.context_messages if isinstance(msg, ToolMessage)]
        assert [msg.content for msg in tool_messages] == [
            config.cleared_marker,
            "fresh-content",
        ]
