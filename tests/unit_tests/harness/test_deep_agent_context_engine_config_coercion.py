# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``DeepAgentSpec.context_engine_config`` 的 JSON round-trip 归一化。

team 分布式成员的 spec 以 ``model_dump(mode="json")`` 传输，接收侧
``model_validate`` 后该字段是 dict（字段类型为 ``Any``）。若不归一化，
ReActAgent 读取属性时会失败。
"""

from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.harness.deep_agent import _coerce_context_engine_config


def test_dict_is_coerced_back_to_model() -> None:
    original = ContextEngineConfig(enable_tiktoken_counter=True, context_window_tokens=32000)

    restored = _coerce_context_engine_config(original.model_dump(mode="json"))

    assert isinstance(restored, ContextEngineConfig)
    assert restored.enable_tiktoken_counter is True
    assert restored.context_window_tokens == 32000


def test_model_instance_passes_through() -> None:
    original = ContextEngineConfig(enable_tiktoken_counter=True)

    assert _coerce_context_engine_config(original) is original


def test_none_passes_through() -> None:
    assert _coerce_context_engine_config(None) is None
