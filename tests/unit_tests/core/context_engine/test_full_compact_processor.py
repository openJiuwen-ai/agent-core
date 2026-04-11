# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
    FullCompactProcessorConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage


def create_tool_call(tool_call_id: str, name: str, arguments: str = "") -> ToolCall:
    return ToolCall(id=tool_call_id, name=name, type="function", arguments=arguments)


async def create_context_with_full_compact(
    config: FullCompactProcessorConfig,
    history_messages=None,
):
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=100))
    return await engine.create_context(
        "test_ctx",
        None,
        history_messages=history_messages or [],
        processors=[("FullCompactProcessor", config)],
    )


class TestFullCompactProcessor:
    @pytest.mark.asyncio
    async def test_trigger_add_messages_true_when_combined_tokens_exceed_threshold(self):
        config = FullCompactProcessorConfig(
            trigger_total_tokens=20,
            compression_call_max_tokens=2000,
            messages_to_keep=2,
        )
        ctx = await create_context_with_full_compact(config)
        triggered = await ctx._processors[0].trigger_add_messages(  # type: ignore[attr-defined]
            ctx,
            [UserMessage(content="你好啊" * 30)],
        )
        assert triggered is True

    @pytest.mark.asyncio
    async def test_on_add_messages_compacts_and_rewrites_context(self):
        config = FullCompactProcessorConfig(
            trigger_total_tokens=20,
            compression_call_max_tokens=2000,
            messages_to_keep=2,
        )
        ctx = await create_context_with_full_compact(config)
        processor = ctx._processors[0]  # type: ignore[attr-defined]
        new_messages = [
            UserMessage(content="用户请求 " + ("背景" * 20)),
            AssistantMessage(
                content="",
                tool_calls=[
                    create_tool_call(
                        "tc-skill",
                        "read_file",
                        '{"file_path": "/skills/demo/SKILL.md"}',
                    )
                ],
            ),
            ToolMessage(
                content='{"content":"# Demo Skill\\n' + ("技能" * 40) + '"}',
                tool_call_id="tc-skill",
            ),
            AssistantMessage(content="最终回答 " + ("结论" * 20)),
        ]

        with patch(
            "openjiuwen.core.context_engine.processor.compressor.full_compact_processor.Model"
        ) as mock_model_cls:
            mock_model = MagicMock()
            mock_response = MagicMock()
            mock_response.content = "<summary>压缩后的总结</summary>"
            mock_model.invoke = AsyncMock(return_value=mock_response)
            mock_model_cls.return_value = mock_model

            event, returned_messages = await processor.on_add_messages(ctx, new_messages)

        assert event is not None
        assert returned_messages == []
        result = ctx.get_messages()
        assert any(
            getattr(msg, "content", "").startswith(config.marker)
            for msg in result
        )

    def test_group_messages_by_api_round_splits_user_and_following_assistant_tool_messages(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import FullCompactProcessor

        processor = FullCompactProcessor(FullCompactProcessorConfig())
        messages = [
            UserMessage(content="u1"),
            AssistantMessage(
                content="",
                tool_calls=[create_tool_call("tc-1", "read_file")],
            ),
            ToolMessage(content="tool-1", tool_call_id="tc-1"),
            AssistantMessage(content="a1"),
            UserMessage(content="u2"),
            AssistantMessage(content="a2"),
        ]

        groups = processor._group_messages_by_api_round(messages)
        assert len(groups) == 4
        assert [msg.content for msg in groups[0]] == ["u1"]
        assert [msg.content for msg in groups[1]] == ["", "tool-1", "a1"]
        assert [msg.content for msg in groups[2]] == ["u2"]
        assert [msg.content for msg in groups[3]] == ["a2"]

    def test_extract_tool_result_hint_respects_configured_tool_name_list(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        processor = FullCompactProcessor(
            FullCompactProcessorConfig(
                reinject_tool_result_hint_names=["read_file"],
            )
        )
        grep_hint = processor._extract_tool_result_hint("grep", '{"count": 3}')
        read_hint = processor._extract_tool_result_hint(
            "read_file",
            '{"file_path": "/tmp/a.txt", "line_count": 7}',
        )

        assert grep_hint == ""
        assert read_hint == "result_path=/tmp/a.txt lines=7"
