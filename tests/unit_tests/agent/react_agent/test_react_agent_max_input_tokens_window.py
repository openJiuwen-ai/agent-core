# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.context_engine import ContextEngineConfig
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _card() -> AgentCard:
    return AgentCard(id="window-agent", name="window-agent", description="window backfill")


def test_max_input_tokens_overrides_explicit_context_window_tokens():
    config = ReActAgentConfig(
        model_name="gpt-4",
        model_config_obj=ModelRequestConfig(model="gpt-4", max_input_tokens=12000),
        context_engine_config=ContextEngineConfig(context_window_tokens=8000),
    )

    resolved = ReActAgent._with_context_engine_model_name(config)

    assert resolved.context_engine_config.context_window_tokens == 12000


def test_explicit_context_window_tokens_kept_when_max_input_tokens_absent():
    config = ReActAgentConfig(
        model_name="gpt-4",
        model_config_obj=ModelRequestConfig(model="gpt-4"),
        context_engine_config=ContextEngineConfig(context_window_tokens=8000),
    )

    resolved = ReActAgent._with_context_engine_model_name(config)

    assert resolved.context_engine_config.context_window_tokens == 8000


def test_window_stays_unset_when_neither_field_is_provided():
    config = ReActAgentConfig(
        model_name="gpt-4",
        model_config_obj=ModelRequestConfig(model="gpt-4"),
    )

    resolved = ReActAgent._with_context_engine_model_name(config)

    assert resolved.context_engine_config.context_window_tokens is None
    assert resolved.context_engine_config.model_name == "gpt-4"


def test_configure_writes_max_input_tokens_into_context_engine():
    agent = ReActAgent(_card())
    config = ReActAgentConfig(
        model_name="gpt-4",
        model_config_obj=ModelRequestConfig(model="gpt-4", max_input_tokens=64000),
        context_engine_config=ContextEngineConfig(context_window_tokens=8000),
    )

    agent.configure(config)

    assert agent.config.context_engine_config.context_window_tokens == 64000
    assert agent.context_engine._config.context_window_tokens == 64000
