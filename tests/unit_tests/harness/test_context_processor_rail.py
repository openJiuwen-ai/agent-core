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
from openjiuwen.core.context_engine.processor.compressor.current_round_compressor import CurrentRoundCompressor
from openjiuwen.core.context_engine.processor.legacy.compressor import (
    LegacyCurrentRoundCompressor,
    LegacyCurrentRoundCompressorConfig,
    LegacyDialogueCompressorConfig,
    LegacyRoundLevelCompressorConfig,
    MicroCompactProcessorConfig,
)
from openjiuwen.core.context_engine.processor.legacy.context_processor_rail import (
    ContextProcessorRail as LegacyContextProcessorRail,
)
from openjiuwen.core.context_engine.processor.legacy.offloader import MessageSummaryOffloaderConfig
from openjiuwen.core.foundation.llm import ModelRequestConfig
from openjiuwen.harness.rails.context_engineer.context_processor_rail import ContextProcessorRail


def _make_agent():
    config = SimpleNamespace(
        context_processors=[],
        model_config_obj=ModelRequestConfig(model="test-model"),
        model_client_config=None,
        context_engine_config=SimpleNamespace(),
    )
    system_prompt_builder = SimpleNamespace(
        language="cn",
        sections={},
        add_section=lambda section: system_prompt_builder.sections.__setitem__(section.name, section),
        remove_section=lambda name: system_prompt_builder.sections.pop(name, None),
    )
    return SimpleNamespace(
        react_agent=SimpleNamespace(_config=config),
        system_prompt_builder=system_prompt_builder,
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


def test_legacy_context_processor_rail_default_processors_are_opt_in():
    rail = LegacyContextProcessorRail(preset=True)
    agent = _make_agent()

    rail.init(agent)

    processors = _processor_map(agent)
    assert list(processors) == [
        "MessageSummaryOffloader",
        "LegacyDialogueCompressor",
        "LegacyCurrentRoundCompressor",
        "LegacyRoundLevelCompressor",
    ]
    assert isinstance(processors["MessageSummaryOffloader"], MessageSummaryOffloaderConfig)
    assert isinstance(processors["LegacyDialogueCompressor"], LegacyDialogueCompressorConfig)
    assert isinstance(processors["LegacyCurrentRoundCompressor"], LegacyCurrentRoundCompressorConfig)
    assert isinstance(processors["LegacyRoundLevelCompressor"], LegacyRoundLevelCompressorConfig)


def test_legacy_context_processor_rail_maps_old_override_keys_to_legacy_types():
    rail = LegacyContextProcessorRail(
        preset=True,
        processors=[
            ("DialogueCompressor", {"tokens_threshold": 12345}),
            ("CurrentRoundCompressor", {"messages_to_keep": 4}),
        ],
    )
    agent = _make_agent()

    rail.init(agent)

    processors = _processor_map(agent)
    assert "DialogueCompressor" not in processors
    assert processors["LegacyDialogueCompressor"].tokens_threshold == 12345
    assert processors["LegacyCurrentRoundCompressor"].messages_to_keep == 4


@pytest.mark.asyncio
async def test_context_processor_rail_injects_offload_section_before_model_call():
    rail = ContextProcessorRail(preset=True)
    agent = _make_agent()

    rail.init(agent)
    await rail.before_model_call(SimpleNamespace(session=None))

    section = agent.system_prompt_builder.sections["offload"]
    assert "OFFLOAD" in section.content["cn"]
    assert "path=<path>" in section.content["cn"]
    assert "read_file" not in section.content["cn"]


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
    assert ContextEngine._PROCESSOR_MAP["CurrentRoundCompressor"] is CurrentRoundCompressor
    assert ContextEngine._PROCESSOR_MAP["LegacyCurrentRoundCompressor"] is LegacyCurrentRoundCompressor


@pytest.mark.asyncio
async def test_context_engine_can_create_legacy_processor_context():
    context = await ContextEngine().create_context(
        processors=[
            ("MicroCompactProcessor", MicroCompactProcessorConfig()),
        ],
    )

    assert context._processors[0].processor_type() == "MicroCompactProcessor"


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
