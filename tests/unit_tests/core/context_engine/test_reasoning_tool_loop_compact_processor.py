# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.compressor.reasoning_tool_loop_compact_processor import (
    ReasoningToolLoopCompactProcessorConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage


def _extract_json_block(content: str, heading: str) -> list:
    marker = f"{heading}\n---\n"
    idx = content.find(marker)
    assert idx >= 0, f"missing JSON block under heading: {heading}"
    start = idx + len(marker)
    obj, _ = json.JSONDecoder().raw_decode(content, start)
    assert isinstance(obj, list)
    return obj


REASONING = (
    "用户要求我组建研究团队并完成个性化联邦学习研究想法。"
    "先分析五个 agent 背景，然后创建任务清单来跟踪进度。"
) * 2


def _tool_call(tool_id: str, name: str, arguments: str) -> ToolCall:
    return ToolCall(id=tool_id, name=name, type="function", arguments=arguments)


def _todo_round(index: int, *, reasoning: str = REASONING, arguments: str | None = None) -> list:
    args = arguments or (
        '{"tasks":[{"id":"literature_review","content":"文献综述",'
        '"activeForm":"正在进行文献综述","description":"分析Introduction"}]}'
    )
    tool_id = f"tc-{index}"
    return [
        AssistantMessage(
            content="我来组织这个研究团队协作完成任务。首先创建任务清单来跟踪进度。",
            reasoning_content=reasoning,
            tool_calls=[_tool_call(tool_id, "todo_create", args)],
        ),
        ToolMessage(
            content=f"Successfully created task(s) round-{index}",
            tool_call_id=tool_id,
        ),
    ]


def _loop_summary_messages(messages) -> list[AssistantMessage]:
    return [
        msg for msg in messages
        if isinstance(msg, AssistantMessage)
        and not msg.tool_calls
        and "请跳出多轮重复执行" in str(msg.content)
    ]


async def _create_context(config: ReasoningToolLoopCompactProcessorConfig, history=None):
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=200))
    return await engine.create_context(
        "test_reasoning_tool_loop",
        None,
        history_messages=history or [],
        processors=[("ReasoningToolLoopCompactProcessor", config)],
    )


class TestReasoningToolLoopCompactProcessor:
    @pytest.mark.asyncio
    async def test_compacts_identical_reasoning_and_tool_rounds(self):
        config = ReasoningToolLoopCompactProcessorConfig(consecutive_threshold=3)
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 4):
            await ctx.add_messages(_todo_round(index))

        messages = ctx.get_messages()
        summaries = _loop_summary_messages(messages)
        assert len(summaries) == 1
        content = summaries[0].content
        tool_calls = _extract_json_block(content, "工具调用命令如下：")
        tool_results = _extract_json_block(content, "工具执行结果如下：")
        assert tool_calls == [{
            "name": "todo_create",
            "arguments": {
                "tasks": [{
                    "id": "literature_review",
                    "content": "文献综述",
                    "activeForm": "正在进行文献综述",
                    "description": "分析Introduction",
                }],
            },
        }]
        assert tool_results == [{
            "name": "todo_create",
            "content": "Successfully created task(s) round-3",
        }]
        assert "Successfully created task(s) round-1" not in content
        assert "Successfully created task(s) round-2" not in content

        # All matched tool rounds removed; only the summary Assistant remains.
        assert not any(
            isinstance(msg, AssistantMessage) and msg.tool_calls
            for msg in messages
        )
        assert not any(isinstance(msg, ToolMessage) for msg in messages)

    @pytest.mark.asyncio
    async def test_does_not_compact_below_threshold(self):
        config = ReasoningToolLoopCompactProcessorConfig(consecutive_threshold=3)
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        await ctx.add_messages(_todo_round(1))
        await ctx.add_messages(_todo_round(2))

        messages = ctx.get_messages()
        assert not _loop_summary_messages(messages)
        assert sum(1 for msg in messages if isinstance(msg, AssistantMessage) and msg.tool_calls) == 2

    @pytest.mark.asyncio
    async def test_requires_both_reasoning_and_tool_name_set_match(self):
        config = ReasoningToolLoopCompactProcessorConfig(consecutive_threshold=3)
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        await ctx.add_messages(_todo_round(1))
        await ctx.add_messages(_todo_round(2))
        await ctx.add_messages(
            _todo_round(
                3,
                arguments=(
                    '{"tasks":[{"id":"brainstorming","content":"头脑风暴",'
                    '"activeForm":"正在头脑风暴","description":"协作想法"}]}'
                ),
            )
        )

        messages = ctx.get_messages()
        summaries = _loop_summary_messages(messages)
        assert len(summaries) == 1
        tool_calls = _extract_json_block(summaries[0].content, "工具调用命令如下：")
        assert tool_calls[0]["arguments"]["tasks"][0]["id"] == "brainstorming"
        assert "literature_review" not in summaries[0].content
        assert not any(
            isinstance(msg, AssistantMessage) and msg.tool_calls
            for msg in messages
        )

    @pytest.mark.asyncio
    async def test_different_tool_name_set_breaks_streak(self):
        config = ReasoningToolLoopCompactProcessorConfig(consecutive_threshold=3)
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        await ctx.add_messages(_todo_round(1))
        await ctx.add_messages(_todo_round(2))
        tool_id = "tc-3"
        await ctx.add_messages([
            AssistantMessage(
                content="更新任务状态",
                reasoning_content=REASONING,
                tool_calls=[_tool_call(tool_id, "todo_modify", '{"action":"update"}')],
            ),
            ToolMessage(content="Successfully updated", tool_call_id=tool_id),
        ])

        messages = ctx.get_messages()
        assert not _loop_summary_messages(messages)
        assert sum(1 for msg in messages if isinstance(msg, AssistantMessage) and msg.tool_calls) == 3

    @pytest.mark.asyncio
    async def test_multi_tool_round_matches_by_tool_name_set(self):
        config = ReasoningToolLoopCompactProcessorConfig(consecutive_threshold=3)
        ctx = await _create_context(config)

        def _multi_tool_round(index: int) -> list:
            return [
                AssistantMessage(
                    content="并行执行",
                    reasoning_content=REASONING,
                    tool_calls=[
                        _tool_call(f"tc-{index}-a", "todo_modify", '{"action":"update"}'),
                        _tool_call(f"tc-{index}-b", "todo_list", "{}"),
                    ],
                ),
                ToolMessage(content=f"updated-{index}", tool_call_id=f"tc-{index}-a"),
                ToolMessage(content=f"listed-{index}", tool_call_id=f"tc-{index}-b"),
            ]

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 4):
            await ctx.add_messages(_multi_tool_round(index))

        messages = ctx.get_messages()
        summaries = _loop_summary_messages(messages)
        assert len(summaries) == 1
        tool_calls = _extract_json_block(summaries[0].content, "工具调用命令如下：")
        tool_results = _extract_json_block(summaries[0].content, "工具执行结果如下：")
        assert tool_calls == [
            {"name": "todo_modify", "arguments": {"action": "update"}},
            {"name": "todo_list", "arguments": {}},
        ]
        assert tool_results == [
            {"name": "todo_modify", "content": "updated-3"},
            {"name": "todo_list", "content": "listed-3"},
        ]
        assert "updated-1" not in summaries[0].content
        assert "listed-1" not in summaries[0].content
        assert not any(
            isinstance(msg, AssistantMessage) and msg.tool_calls
            for msg in messages
        )

    @pytest.mark.asyncio
    async def test_english_summary_when_language_set(self):
        config = ReasoningToolLoopCompactProcessorConfig(
            consecutive_threshold=3,
            language="en",
        )
        ctx = await _create_context(config)

        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 4):
            await ctx.add_messages(_todo_round(index))

        messages = ctx.get_messages()
        summaries = [
            msg for msg in messages
            if isinstance(msg, AssistantMessage)
            and not msg.tool_calls
            and "Break out of the multi-turn repeated execution" in str(msg.content)
        ]
        assert len(summaries) == 1
        tool_calls = _extract_json_block(summaries[0].content, "Tool calls:")
        tool_results = _extract_json_block(summaries[0].content, "Tool results:")
        assert tool_calls[0]["name"] == "todo_create"
        assert tool_results == [{
            "name": "todo_create",
            "content": "Successfully created task(s) round-3",
        }]
        assert "Successfully created task(s) round-1" not in summaries[0].content

    @pytest.mark.asyncio
    async def test_disabled_config_skips_compaction(self):
        config = ReasoningToolLoopCompactProcessorConfig(
            enabled=False,
            consecutive_threshold=3,
        )
        ctx = await _create_context(config)
        await ctx.add_messages(UserMessage(content="请生成研究想法"))
        for index in range(1, 4):
            await ctx.add_messages(_todo_round(index))

        messages = ctx.get_messages()
        assert sum(1 for msg in messages if isinstance(msg, AssistantMessage) and msg.tool_calls) == 3
        assert not _loop_summary_messages(messages)
