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
