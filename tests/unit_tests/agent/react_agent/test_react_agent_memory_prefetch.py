# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""单元测试：ReActAgent._consume_memory_prefetch"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent


class _MockInputs:
    def __init__(self, messages):
        self.messages = messages


class _MockCtx:
    def __init__(self, extra, messages):
        self.extra = extra
        self.inputs = _MockInputs(messages)


class TestConsumeMemoryPrefetch:
    """_consume_memory_prefetch 行为测试。"""

    def test_appends_user_message_when_memory_prefetch_present(self):
        """ctx.extra['memory_prefetch'] 非空时，messages 末尾追加 UserMessage。"""
        ctx = _MockCtx(
            extra={
                "memory_prefetch": [
                    {
                        "content": "<memory-context>\n[System note: recalled memory.]\nrecall X\n</memory-context>",
                        "source": "mock_provider",
                    }
                ]
            },
            messages=[],
        )

        ReActAgent._consume_memory_prefetch(ctx)

        assert len(ctx.inputs.messages) == 1
        appended = ctx.inputs.messages[-1]
        assert isinstance(appended, UserMessage)
        assert "<memory-context>" in appended.content
        assert "recall X" in appended.content

    def test_multiple_entries_joined_by_blank_line(self):
        """多个 rail 写入时，各 entry content 用空行连接。"""
        ctx = _MockCtx(
            extra={
                "memory_prefetch": [
                    {"content": "<memory-context>A</memory-context>", "source": "p1"},
                    {"content": "<memory-context>B</memory-context>", "source": "p2"},
                ]
            },
            messages=[],
        )

        ReActAgent._consume_memory_prefetch(ctx)

        assert len(ctx.inputs.messages) == 1
        content = ctx.inputs.messages[-1].content
        assert "<memory-context>A</memory-context>" in content
        assert "<memory-context>B</memory-context>" in content
        assert content.index("A") < content.index("B")

    def test_no_append_when_memory_prefetch_missing(self):
        """ctx.extra 无 memory_prefetch 键时，不追加。"""
        ctx = _MockCtx(extra={}, messages=[])

        ReActAgent._consume_memory_prefetch(ctx)

        assert ctx.inputs.messages == []

    def test_no_append_when_memory_prefetch_empty_list(self):
        """ctx.extra['memory_prefetch'] 为空 list 时，不追加。"""
        ctx = _MockCtx(extra={"memory_prefetch": []}, messages=[])

        ReActAgent._consume_memory_prefetch(ctx)

        assert ctx.inputs.messages == []

    def test_pops_key_after_consume(self):
        """消费后 memory_prefetch 键应被 pop 掉，防止跨 invoke 累积。"""
        ctx = _MockCtx(
            extra={"memory_prefetch": [{"content": "x", "source": "p"}]},
            messages=[],
        )

        ReActAgent._consume_memory_prefetch(ctx)

        assert "memory_prefetch" not in ctx.extra

    def test_does_not_touch_environment_context(self):
        """消费 memory_prefetch 不应影响 environment_context 键。"""
        ctx = _MockCtx(
            extra={
                "memory_prefetch": [{"content": "<memory-context>x</memory-context>", "source": "p"}],
                "environment_context": [{"content": "env", "source": "s"}],
            },
            messages=[],
        )

        ReActAgent._consume_memory_prefetch(ctx)

        assert "environment_context" in ctx.extra
        assert ctx.extra["environment_context"] == [{"content": "env", "source": "s"}]
