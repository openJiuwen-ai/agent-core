# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for model metadata copied into ContextEngineConfig."""

from openjiuwen.core.context_engine import ContextEngineConfig
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig


def test_explicit_context_engine_model_name_is_preserved():
    config = ReActAgentConfig(
        model_name="selected-model",
        context_engine_config=ContextEngineConfig(model_name="explicit-context-model"),
    )

    resolved = ReActAgent._with_context_engine_model_name(config)

    assert resolved is config
    assert resolved.context_engine_config.model_name == "explicit-context-model"


def test_context_engine_model_name_is_filled_when_empty():
    config = ReActAgentConfig(
        model_name="selected-model",
        context_engine_config=ContextEngineConfig(),
    )

    resolved = ReActAgent._with_context_engine_model_name(config)

    assert resolved.context_engine_config.model_name == "selected-model"


def test_explicit_context_window_override_is_preserved_without_model_metadata():
    config = ReActAgentConfig(
        context_engine_config=ContextEngineConfig(
            model_context_window_tokens_override=131072,
        ),
        model_config_obj=ModelRequestConfig(model="selected-model"),
    )

    resolved = ReActAgent._with_context_engine_model_window(config)

    assert resolved is config
    assert resolved.context_engine_config.model_context_window_tokens_override == 131072


def test_model_context_window_is_copied_when_metadata_is_valid():
    config = ReActAgentConfig(
        context_engine_config=ContextEngineConfig(),
        model_config_obj=ModelRequestConfig(
            model="selected-model",
            context_window=131072,
        ),
    )

    resolved = ReActAgent._with_context_engine_model_window(config)

    assert resolved.context_engine_config.model_context_window_tokens_override == 131072
