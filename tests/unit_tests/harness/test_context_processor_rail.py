# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.core.context_engine import (
    CurrentRoundCompressorConfig,
    DialogueCompressorConfig,
    MessageOffloaderConfig,
    RoundLevelCompressorConfig,
)
from openjiuwen.core.foundation.llm import ModelRequestConfig
from openjiuwen.harness.rails.context_engineer.context_processor_rail import ContextProcessorRail


def _make_agent():
    config = SimpleNamespace(
        context_processors=[],
        model_config_obj=ModelRequestConfig(model="test-model"),
        model_client_config=None,
        context_engine_config=SimpleNamespace(enable_reload=False),
    )
    return SimpleNamespace(
        react_agent=SimpleNamespace(_config=config),
        system_prompt_builder=SimpleNamespace(remove_section=lambda _name: None),
    )


def _processor_map(agent):
    return dict(agent.react_agent._config.context_processors)


def test_context_processor_rail_default_processors_are_the_only_preset():
    rail = ContextProcessorRail(preset=True)
    agent = _make_agent()

    rail.init(agent)

    processors = _processor_map(agent)
    assert list(processors) == [
        "MessageOffloader",
        "DialogueCompressor",
        "CurrentRoundCompressor",
        "RoundLevelCompressor",
    ]
    assert isinstance(processors["MessageOffloader"], MessageOffloaderConfig)
    assert isinstance(processors["DialogueCompressor"], DialogueCompressorConfig)
    assert isinstance(processors["CurrentRoundCompressor"], CurrentRoundCompressorConfig)
    assert isinstance(processors["RoundLevelCompressor"], RoundLevelCompressorConfig)


def test_context_processor_rail_merges_dict_overrides_for_default_processors():
    rail = ContextProcessorRail(
        preset=True,
        processors=[
            ("DialogueCompressor", {"trigger_context_ratio": 0.25}),
            ("RoundLevelCompressor", {"keep_recent_messages": 7}),
        ],
    )
    agent = _make_agent()

    rail.init(agent)

    processors = _processor_map(agent)
    assert processors["DialogueCompressor"].trigger_context_ratio == 0.25
    assert processors["RoundLevelCompressor"].keep_recent_messages == 7


def test_context_processor_rail_preset_false_accepts_complete_configs_only():
    rail = ContextProcessorRail(
        preset=False,
        processors=[("DialogueCompressor", DialogueCompressorConfig())],
    )
    agent = _make_agent()

    rail.init(agent)

    processors = _processor_map(agent)
    assert list(processors) == ["DialogueCompressor"]
    assert isinstance(processors["DialogueCompressor"], DialogueCompressorConfig)


def test_context_processor_rail_rejects_named_presets():
    with pytest.raises(ValueError, match="no longer supports named presets"):
        ContextProcessorRail(preset=True, preset_name="forked")


def test_context_processor_rail_uninit_clears_processors():
    rail = ContextProcessorRail(preset=True)
    agent = _make_agent()
    rail.init(agent)

    rail.uninit(agent)

    assert agent.react_agent._config.context_processors == []
