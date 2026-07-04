# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.core.context_engine import (
    ContextEngine,
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


def test_context_processor_rail_filters_legacy_offloader_config_fields():
    rail = ContextProcessorRail(
        preset=True,
        processors=[
            (
                "MessageOffloader",
                {
                    "protected_tool_names": ["read_file"],
                    "keep_last_round": False,
                },
            ),
        ],
    )
    agent = _make_agent()

    rail.init(agent)

    offloader_config = _processor_map(agent)["MessageOffloader"]
    assert isinstance(offloader_config, MessageOffloaderConfig)
    assert offloader_config.protected_tool_names == ["read_file"]


def test_context_processor_rail_filters_legacy_compactor_config_fields():
    rail = ContextProcessorRail(
        preset=True,
        processors=[
            (
                "DialogueCompressor",
                {
                    "compression_target_tokens": 4096,
                },
            ),
        ],
    )
    agent = _make_agent()

    rail.init(agent)

    dialogue_config = _processor_map(agent)["DialogueCompressor"]
    assert not hasattr(dialogue_config, "compression_target_tokens")


def test_context_processor_rail_maps_messages_to_keep_to_keep_recent_messages():
    rail = ContextProcessorRail(
        preset=True,
        processors=[
            ("CurrentRoundCompressor", {"messages_to_keep": 3}),
            ("RoundLevelCompressor", {"messages_to_keep": 5, "keep_recent_messages": 7}),
        ],
    )
    agent = _make_agent()

    rail.init(agent)

    processors = _processor_map(agent)
    assert processors["CurrentRoundCompressor"].keep_recent_messages == 3
    assert processors["RoundLevelCompressor"].keep_recent_messages == 7


def test_context_processor_registry_contains_current_processor_names():
    assert "MessageOffloader" in ContextEngine._PROCESSOR_MAP
    assert "DialogueCompressor" in ContextEngine._PROCESSOR_MAP
    assert "CurrentRoundCompressor" in ContextEngine._PROCESSOR_MAP
    assert "RoundLevelCompressor" in ContextEngine._PROCESSOR_MAP


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
