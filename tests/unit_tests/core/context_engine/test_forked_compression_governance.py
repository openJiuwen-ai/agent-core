# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Forked 压缩链的上下文增长治理回归（2026-09-30 / 2026-10-09 事故族）。

大窗口模型下 ratio-only 触发让历史无界增长（1M 窗口 ~800K 才压缩）；
压缩调用自带全量上下文 + TIMEOUT 同款重试构成死亡螺旋。本文件覆盖
触发绝对上限、压缩调用预算截断、TIMEOUT 重试策略。
"""

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.context_engine.processor.forked.compressor.base import (
    PrefixCompactProcessorConfig,
)
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


def _compressor_with_limit(context_max: int, config: PrefixCompactProcessorConfig | None = None):
    """DialogueCompressor + mock context：_resolve_trigger_token_limit 直接可测。"""
    context = MagicMock()
    context._context_window_tokens = context_max
    context._model_name = None
    context._model_context_window_tokens = None
    compressor = DialogueCompressor(config or DialogueCompressorConfig())
    return compressor, context


def test_trigger_absolute_tokens_caps_large_window():
    """1M 窗口：触发阈值 = min(0.8×窗口, 180000) = 180000。"""
    compressor, context = _compressor_with_limit(1_000_000)
    assert compressor._resolve_trigger_token_limit(1_000_000) == 180000


def test_trigger_absolute_tokens_none_restores_ratio():
    """显式 None 恢复纯比例（大窗口部署 opt-out）。"""
    config = DialogueCompressorConfig(trigger_absolute_tokens=None)
    compressor, _ = _compressor_with_limit(1_000_000, config)
    assert compressor._resolve_trigger_token_limit(1_000_000) == 800_000


def test_small_window_unaffected_by_cap():
    """200K 窗口：比例阈值 160K < 180K，min 语义不放大。"""
    compressor, _ = _compressor_with_limit(200_000)
    assert compressor._resolve_trigger_token_limit(200_000) == 160_000


@pytest.mark.parametrize(
    ("make_config", "name"),
    [
        (CurrentRoundCompressorConfig, "CurrentRoundCompressor"),
        (RoundLevelCompressorConfig, "RoundLevelCompressor"),
    ],
)
def test_current_round_and_round_level_inherit_cap(make_config, name):
    """三个 forked 压缩器统一继承绝对上限（防子类回退纯比例）。"""
    assert make_config().trigger_absolute_tokens == 180000, f"{name} must inherit the cap"


def _make_span(messages_to_compress, protected_tail=()):
    from openjiuwen.core.context_engine.processor.forked.compressor.base import PrefixCompactSpan

    return PrefixCompactSpan(
        preserved_prefix=[],
        messages_to_compress=list(messages_to_compress),
        protected_tail=list(protected_tail),
    )


def _budget_context():
    """mock context：token_counter 按字符数/3 估（chars//3 的可控近似）。"""
    context = MagicMock()
    context._context_window_tokens = 1_000_000
    context._model_name = None
    context._model_context_window_tokens = None

    class _Counter:
        def count_messages(self, messages):
            return sum(len(str(getattr(m, "content", ""))) for m in messages) // 3

    context.token_counter.return_value = _Counter()
    return context


def _context_window_with(system=(), tools=()):
    window = MagicMock()
    window.system_messages = list(system)
    window.tools = list(tools)
    return window


@pytest.mark.asyncio
async def test_compression_input_shrunk_to_budget():
    """预算内装不下的最旧消息移入 protected_tail（留在主上下文，不丢数据）。"""
    from openjiuwen.core.foundation.llm import UserMessage

    compressor = DialogueCompressor(DialogueCompressorConfig())
    context = _budget_context()
    window = _context_window_with(system=[UserMessage(content="S" * 300)])

    msgs = [UserMessage(content=f"M{i} " + "x" * 300) for i in range(10)]  # 每条 ~100 token
    span = _make_span(msgs)

    shrunk = compressor._shrink_span_to_budget(
        context=context,
        context_window=window,
        span=span,
        prompt="P" * 300,  # prompt ~100 token
        budget_tokens=500,  # 减去 system+prompt ~200，可容纳 ~3 条
    )
    assert shrunk is not None
    assert 1 <= len(shrunk.messages_to_compress) < 10
    # 未进摘要的消息保留在 protected_tail（留在主上下文），且原有 tail 排在其后。
    assert len(shrunk.protected_tail) == 10 - len(shrunk.messages_to_compress)
    assert shrunk.protected_tail[0] is msgs[len(shrunk.messages_to_compress)]


def test_no_shrink_when_within_budget():
    """预算内全部装得下 → 返回 None（无需截断）。"""
    from openjiuwen.core.foundation.llm import UserMessage

    compressor = DialogueCompressor(DialogueCompressorConfig())
    context = _budget_context()
    window = _context_window_with()
    msgs = [UserMessage(content="short")] * 3
    span = _make_span(msgs)

    assert (
        compressor._shrink_span_to_budget(
            context=context,
            context_window=window,
            span=span,
            prompt="summarize",
            budget_tokens=10_000,
        )
        is None
    )


def test_force_shrink_keeps_overflow_retry_progress():
    """force_shrink=True（overflow 重试路径）：装得下也至少移出一条。"""
    from openjiuwen.core.foundation.llm import UserMessage

    compressor = DialogueCompressor(DialogueCompressorConfig())
    context = _budget_context()
    window = _context_window_with()
    msgs = [UserMessage(content="short")] * 3
    span = _make_span(msgs)

    shrunk = compressor._shrink_span_to_budget(
        context=context,
        context_window=window,
        span=span,
        prompt="summarize",
        budget_tokens=10_000,
        force_shrink=True,
    )
    assert shrunk is not None
    assert len(shrunk.messages_to_compress) == 2


@pytest.mark.asyncio
async def test_proactive_shrink_bounds_first_compression_call():
    """_invoke_compression_with_retries 首调前按预算主动截断。"""
    from openjiuwen.core.context_engine.processor.forked.compressor.base import PrefixCompactSpan
    from openjiuwen.core.foundation.llm import UserMessage

    compressor = DialogueCompressor(DialogueCompressorConfig(compression_call_max_tokens=500))
    context = _budget_context()
    window = _context_window_with(system=[UserMessage(content="S" * 300)])
    msgs = [UserMessage(content=f"M{i} " + "x" * 300) for i in range(10)]
    span = PrefixCompactSpan(preserved_prefix=[], messages_to_compress=list(msgs), protected_tail=[])

    captured = {}

    class _StubExecutor:
        async def invoke(self, request):
            captured["context_messages"] = list(request.context_messages)
            captured["tools"] = getattr(request, "tools", "unset")
            result = MagicMock()
            result.response = MagicMock()
            return result

    compressor._compression_executor = _StubExecutor()
    outcome = await compressor._invoke_compression_with_retries(
        context=context, context_window=window, span=span, prompt="P" * 300
    )
    assert outcome is not None
    _result, used_span, _request = outcome
    assert len(used_span.messages_to_compress) < 10, "span must have been proactively shrunk"
    # 被移出摘要的消息留在主上下文（protected_tail），无数据丢失。
    assert len(used_span.protected_tail) == 10 - len(used_span.messages_to_compress)
    # 首调确实发出（stub 收到请求）。
    assert "context_messages" in captured


@pytest.mark.asyncio
async def test_executor_invoke_passes_no_tools():
    """CompressionExecutor.invoke 不向模型传工具定义（摘要无需工具表）。"""
    from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
        CompressionExecutor,
    )

    captured_kwargs = {}

    from openjiuwen.core.foundation.llm import AssistantMessage

    class _StubModel:
        # Model.stream 必须是普通方法返回 async generator（async def 会返回协程）。
        def stream(self, **kwargs):
            captured_kwargs.update(kwargs)
            chunk = AssistantMessage(content="summary", finish_reason="stop")

            async def _agen():
                yield chunk

            return _agen()

    request = MagicMock()
    request.tools = [{"name": "bash"}]
    request.system_messages = []
    request.context_messages = []
    request.exclude_recent_messages = 0
    request.prompt = "summarize"
    request.output_parser = None

    executor = CompressionExecutor(_StubModel())
    await executor.invoke(request)
    assert captured_kwargs["tools"] is None
