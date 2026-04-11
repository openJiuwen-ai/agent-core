# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import List

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.offloader.tool_result_budget_processor import (
    PERSISTED_OUTPUT_TAG,
    ToolResultBudgetProcessorConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage


def create_tool_call_list(ids: List[str], names: List[str]) -> List[ToolCall]:
    return [
        ToolCall(id=tool_call_id, name=tool_name, type="function", arguments="")
        for tool_call_id, tool_name in zip(ids, names)
    ]


async def create_context_with_tool_result_budget(
    config: ToolResultBudgetProcessorConfig,
    history_messages=None,
):
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=100))
    return await engine.create_context(
        "test_ctx",
        None,
        history_messages=history_messages or [],
        processors=[("ToolResultBudgetProcessor", config)],
    )


class TestToolResultBudgetProcessor:
    @pytest.mark.asyncio
    async def test_large_tool_result_is_offloaded_when_round_budget_is_exceeded(self):
        config = ToolResultBudgetProcessorConfig(
            tokens_threshold=40,
            large_message_threshold=20,
            trim_size=12,
        )
        ctx = await create_context_with_tool_result_budget(config)
        messages = [
            UserMessage(content="读取一个大文件"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-1"], ["grep"]),
            ),
            ToolMessage(content="A" * 90, tool_call_id="tc-1"),
            ToolMessage(content="B" * 80, tool_call_id="tc-1"),
            AssistantMessage(content="处理完成"),
        ]
        await ctx.add_messages(messages)

        result = ctx.get_messages()
        tool_messages = [msg for msg in result if isinstance(msg, ToolMessage)]
        assert tool_messages[0].content.startswith(PERSISTED_OUTPUT_TAG)

    @pytest.mark.asyncio
    async def test_allowlisted_tool_message_is_not_offloaded(self):
        config = ToolResultBudgetProcessorConfig(
            tokens_threshold=50,
            large_message_threshold=20,
            trim_size=8,
            tool_name_allowlist=["read_file"],
        )
        ctx = await create_context_with_tool_result_budget(config)
        messages = [
            UserMessage(content="读取允许白名单中的工具"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-1"], ["read_file"]),
            ),
            ToolMessage(content="X" * 100, tool_call_id="tc-1"),
            AssistantMessage(content="处理完成"),
        ]
        await ctx.add_messages(messages)

        result = ctx.get_messages()
        tool_message = next(msg for msg in result if isinstance(msg, ToolMessage))
        assert tool_message.content == "X" * 100
