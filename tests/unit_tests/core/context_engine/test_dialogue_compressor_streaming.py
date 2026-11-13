# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Focused coverage for the streaming branch of DialogueCompressor.

Only the behaviours that differ from the non-streaming branch are asserted
here; the shared compression flow is already covered by
tests/unit_tests/core/context_engine/test_dialogue_compressor.py.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
    DialogueCompressorConfig,
    _CompressTarget,
)
from openjiuwen.core.foundation.llm import (
    ModelClientConfig,
    ModelRequestConfig,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk

VALID_PAYLOAD = {"blocks": [{"block_id": "react_1", "summary": "Final Result: X."}]}
VALID_CONTENT = json.dumps(VALID_PAYLOAD, ensure_ascii=False)


def _build_compressor():
    """Build a compressor with only the attributes the streaming branch touches."""
    from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
        DialogueCompressor,
    )

    config = DialogueCompressorConfig(
        model=ModelRequestConfig(model="test-model"),
        model_client=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://test.local",
            verify_ssl=False,
        ),
        use_streaming=True,
    )
    compressor = DialogueCompressor.__new__(DialogueCompressor)
    compressor._config = config
    compressor._compressed_prompt = config.custom_compression_prompt or "prompt"
    compressor._compression_target_tokens = config.compression_target_tokens
    compressor._use_streaming = True
    compressor._compression_usage = None
    compressor._model = MagicMock()
    return compressor


def _targets():
    return [
        _CompressTarget(
            block_id="react_1",
            user_idx=0,
            start_idx=0,
            end_idx=0,
            messages=[UserMessage(content="ctx")],
        )
    ]


def _messages():
    return [UserMessage(content="ctx"), UserMessage(content="targets")]


def _usage():
    return UsageMetadata(
        model_name="test-model",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
    )


def _attach_stream(compressor, chunks):
    async def _stream(*args, **kwargs):
        for chunk in chunks:
            yield chunk

    compressor._model.stream = MagicMock(side_effect=_stream)
    compressor._model.invoke = AsyncMock()
    return compressor


class TestDialogueCompressorStreamingBranch:
    """Covers the error/degradation semantics of the streaming branch."""

    @pytest.mark.asyncio
    async def test_parse_exception_degrades_to_none_instead_of_failing_compression(self):
        """A raising parser must not abort the whole compression.

        The non-streaming path lets the model client swallow parser errors and
        keep parser_content=None, so the caller can still fall back to the raw
        text. The streaming branch must behave the same way instead of letting
        the exception escape into MODEL_CALL_FAILED.
        """
        compressor = _build_compressor()
        _attach_stream(
            compressor,
            [
                AssistantMessageChunk(content=VALID_CONTENT, finish_reason="null"),
                AssistantMessageChunk(content="", usage_metadata=_usage(), finish_reason="stop"),
            ],
        )

        def _raising_parse(*args, **kwargs):
            raise TypeError("parser exploded")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "openjiuwen.core.context_engine.processor.compressor.dialogue_compressor.JsonOutputParser.parse",
                _raising_parse,
            )
            response = await compressor._invoke_multi_block_compression(_messages(), _targets())

        assert response.parser_content is None
        assert response.content == VALID_CONTENT

    @pytest.mark.asyncio
    async def test_usage_is_taken_from_the_last_chunk_carrying_it(self):
        """Providers send usage on a trailing usage-only chunk.

        Unlike the non-streaming path, which reads usage straight off the whole
        response, the streaming path has to pick it up from that trailing chunk.
        Losing it would silently drop the compression usage statistics.
        """
        compressor = _build_compressor()
        _attach_stream(
            compressor,
            [
                AssistantMessageChunk(content=VALID_CONTENT, finish_reason="null"),
                AssistantMessageChunk(content="", usage_metadata=_usage(), finish_reason="stop"),
            ],
        )

        await compressor._invoke_multi_block_compression(_messages(), _targets())

        usage = compressor._current_compression_usage()
        assert usage is not None
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 20
        assert usage["total_tokens"] == 120
