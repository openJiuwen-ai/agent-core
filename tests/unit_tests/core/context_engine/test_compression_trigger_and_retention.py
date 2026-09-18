# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the absolute-token trigger and target-retention-ratio knobs."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor import (
    CurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.round_level_compressor import (
    RoundLevelCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
    CompressionExecutor,
    CompressionRequest,
)
from openjiuwen.core.context_engine.processor.forked.compressor.base import (
    PrefixCompactSpan,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    UserMessage,
)


def test_trigger_token_threshold_overrides_ratio():
    compressor = DialogueCompressor(DialogueCompressorConfig(trigger_token_threshold=200000, trigger_context_ratio=0.1))
    assert compressor._resolve_trigger_token_limit(100000) == 200000


def test_trigger_token_threshold_none_falls_back_to_ratio():
    compressor = DialogueCompressor(DialogueCompressorConfig(trigger_token_threshold=None, trigger_context_ratio=0.8))
    assert compressor._resolve_trigger_token_limit(100000) == 80000


def test_trigger_token_threshold_default_none():
    compressor = DialogueCompressor(DialogueCompressorConfig())
    assert compressor.config.trigger_token_threshold is None
    assert compressor.config.target_retention_ratio is None


def test_target_retention_ratio_computes_target_tokens(monkeypatch):
    compressor = DialogueCompressor(DialogueCompressorConfig(target_retention_ratio=0.3))
    monkeypatch.setattr(
        "openjiuwen.core.context_engine.processor.forked.compressor.base.count_messages_tokens",
        MagicMock(return_value=1000),
    )
    span = PrefixCompactSpan(
        preserved_prefix=[],
        messages_to_compress=[UserMessage(content="a")],
        protected_tail=[],
    )
    context = MagicMock()
    assert compressor._resolve_compression_target_tokens(span, context) == 300


def test_target_retention_ratio_none_returns_none():
    compressor = DialogueCompressor(DialogueCompressorConfig())
    span = PrefixCompactSpan(
        preserved_prefix=[],
        messages_to_compress=[UserMessage(content="a")],
        protected_tail=[],
    )
    assert compressor._resolve_compression_target_tokens(span, MagicMock()) is None


def test_target_retention_ratio_floor_is_one(monkeypatch):
    compressor = DialogueCompressor(DialogueCompressorConfig(target_retention_ratio=0.3))
    monkeypatch.setattr(
        "openjiuwen.core.context_engine.processor.forked.compressor.base.count_messages_tokens",
        MagicMock(return_value=2),
    )
    span = PrefixCompactSpan(
        preserved_prefix=[],
        messages_to_compress=[UserMessage(content="a")],
        protected_tail=[],
    )
    assert compressor._resolve_compression_target_tokens(span, MagicMock()) == 1


@pytest.mark.asyncio
async def test_compression_request_passes_max_tokens_when_ratio_set():
    model = MagicMock()
    model.invoke = AsyncMock(return_value=AssistantMessage(content="Short summary"))
    compressor = DialogueCompressor(DialogueCompressorConfig(target_retention_ratio=0.3))
    compressor._compression_executor = CompressionExecutor(model)

    history = [
        UserMessage(content="Earlier request"),
        AssistantMessage(content="Earlier answer"),
        UserMessage(content="Current request"),
    ]
    window = ContextWindow(context_messages=history, tools=[])
    span = compressor._build_span(history)
    assert span.has_target

    result = await compressor._invoke_compression_with_retries(
        context=MagicMock(),
        context_window=window,
        span=span,
        prompt="Summarize this history",
        summary_target_tokens=123,
    )

    assert result is not None
    assert model.invoke.await_args.kwargs["max_tokens"] == 123


@pytest.mark.asyncio
async def test_compression_request_omits_max_tokens_when_ratio_unset():
    model = MagicMock()
    model.invoke = AsyncMock(return_value=AssistantMessage(content="Short summary"))
    compressor = DialogueCompressor(DialogueCompressorConfig())
    compressor._compression_executor = CompressionExecutor(model)

    history = [
        UserMessage(content="Earlier request"),
        AssistantMessage(content="Earlier answer"),
        UserMessage(content="Current request"),
    ]
    window = ContextWindow(context_messages=history, tools=[])
    span = compressor._build_span(history)
    assert span.has_target

    result = await compressor._invoke_compression_with_retries(
        context=MagicMock(),
        context_window=window,
        span=span,
        prompt="Summarize this history",
    )

    assert result is not None
    assert "max_tokens" not in model.invoke.await_args.kwargs


def test_compression_executor_invoke_passes_max_tokens_through():
    model = MagicMock()
    model.invoke = AsyncMock(return_value=AssistantMessage(content="ok"))
    executor = CompressionExecutor(model)
    request = CompressionRequest(
        prompt="summarize",
        context_messages=[UserMessage(content="a")],
        tools=[],
        max_tokens=42,
    )

    # Run the coroutine to completion without an event loop helper dependency.
    import asyncio

    asyncio.run(executor.invoke(request))

    assert model.invoke.await_args.kwargs["max_tokens"] == 42


def test_compression_executor_invoke_omits_max_tokens_when_none():
    model = MagicMock()
    model.invoke = AsyncMock(return_value=AssistantMessage(content="ok"))
    executor = CompressionExecutor(model)
    request = CompressionRequest(
        prompt="summarize",
        context_messages=[UserMessage(content="a")],
        tools=[],
    )

    import asyncio

    asyncio.run(executor.invoke(request))

    assert "max_tokens" not in model.invoke.await_args.kwargs


@pytest.mark.parametrize(
    "config_cls",
    [DialogueCompressorConfig, CurrentRoundCompressorConfig, RoundLevelCompressorConfig],
)
def test_all_compressor_configs_accept_new_fields(config_cls):
    config = config_cls(trigger_token_threshold=200000, target_retention_ratio=0.3)
    assert config.trigger_token_threshold == 200000
    assert config.target_retention_ratio == 0.3


def test_trigger_token_threshold_rejects_invalid_values():
    with pytest.raises(Exception):
        DialogueCompressorConfig(trigger_token_threshold=0)
    with pytest.raises(Exception):
        DialogueCompressorConfig(target_retention_ratio=1.5)
