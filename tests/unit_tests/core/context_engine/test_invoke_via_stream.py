# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Direct unit tests for _invoke_via_stream.

Covers: empty stream, single chunk, multi-chunk content concatenation,
tool_calls fragment merging, usage_metadata propagation, reasoning_content
concatenation, and parser_content last-wins behaviour.
"""

import pytest

from openjiuwen.core.context_engine.processor.base import _invoke_via_stream
from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall


class _FakeModel:
    """Minimal model double whose ``stream`` yields pre-set chunks."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def stream(self, messages=None, **kwargs):
        for chunk in self._chunks:
            yield chunk


def _chunk(content="", **kwargs):
    """Helper to build an AssistantMessageChunk with sensible defaults."""
    kwargs.setdefault("finish_reason", "null")
    return AssistantMessageChunk(content=content, **kwargs)


class TestInvokeViaStream:
    @pytest.mark.asyncio
    async def test_empty_stream_returns_empty_message(self):
        model = _FakeModel(chunks=[])
        result = await _invoke_via_stream(model, [])
        assert isinstance(result, AssistantMessage)
        assert result.content == ""

    @pytest.mark.asyncio
    async def test_single_chunk(self):
        model = _FakeModel([_chunk("hello", finish_reason="stop")])
        result = await _invoke_via_stream(model, [])
        assert result.content == "hello"
        assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_multi_chunk_content_concatenation(self):
        model = _FakeModel([
            _chunk("Hello ", finish_reason="null"),
            _chunk("world!", finish_reason="stop"),
        ])
        result = await _invoke_via_stream(model, [])
        assert result.content == "Hello world!"
        assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_multi_chunk_tool_calls_fragment_merge(self):
        chunk1 = _chunk("", tool_calls=[
            ToolCall(id="call_1", type="function", name="get_weather", arguments='{"ci', index=0),
        ])
        chunk2 = _chunk("", tool_calls=[
            ToolCall(id="call_1", type="function", name="", arguments='ty":"Beijing"}', index=0),
        ])
        model = _FakeModel([chunk1, chunk2])
        result = await _invoke_via_stream(model, [])
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "call_1"
        assert result.tool_calls[0].name == "get_weather"
        assert result.tool_calls[0].arguments == '{"city":"Beijing"}'

    @pytest.mark.asyncio
    async def test_multi_chunk_usage_metadata_propagation(self):
        usage1 = UsageMetadata(input_tokens=10, output_tokens=5)
        usage2 = UsageMetadata(input_tokens=20, output_tokens=15)
        model = _FakeModel([
            _chunk("a", usage_metadata=usage1),
            _chunk("b", usage_metadata=usage2, finish_reason="stop"),
        ])
        result = await _invoke_via_stream(model, [])
        assert result.usage_metadata is not None
        # __add__ takes other.usage_metadata or self.usage_metadata (last non-None wins)
        assert result.usage_metadata.input_tokens == 20

    @pytest.mark.asyncio
    async def test_multi_chunk_reasoning_content_concatenation(self):
        model = _FakeModel([
            _chunk("a", reasoning_content="thinking part 1"),
            _chunk("b", reasoning_content=" thinking part 2", finish_reason="stop"),
        ])
        result = await _invoke_via_stream(model, [])
        assert result.reasoning_content == "thinking part 1 thinking part 2"

    @pytest.mark.asyncio
    async def test_multi_chunk_parser_content_last_wins(self):
        model = _FakeModel([
            _chunk("part1", parser_content={"key": "val1"}),
            _chunk("part2", parser_content={"key": "val2"}, finish_reason="stop"),
        ])
        result = await _invoke_via_stream(model, [])
        # __add__ takes other.parser_content or self.parser_content (last non-None wins)
        assert result.parser_content == {"key": "val2"}
