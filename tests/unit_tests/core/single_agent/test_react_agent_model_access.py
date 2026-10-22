# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public access to the current ReAct model preserves existing selection rules."""

from unittest.mock import patch, sentinel

import pytest

from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig


@pytest.mark.level0
def test_get_llm_returns_the_current_explicit_model() -> None:
    agent = ReActAgent(AgentCard(name="model-access"))
    agent.set_llm(sentinel.first_model)
    assert agent.get_llm() is sentinel.first_model
    assert agent.get_llm() is sentinel.first_model

    agent.set_llm(sentinel.second_model)
    assert agent.get_llm() is sentinel.second_model


@pytest.mark.level0
def test_get_llm_initializes_the_configured_model_once() -> None:
    config = ReActAgentConfig().configure_model_client(
        provider="OpenAI",
        api_key="test",
        api_base="https://example.test/v1",
        model_name="test-model",
    )
    with patch(
        "openjiuwen.core.single_agent.agents.react_agent.Model", return_value=sentinel.lazy_model
    ) as model_factory:
        agent = ReActAgent(AgentCard(name="lazy-model-access")).configure(config)
        model_factory.assert_not_called()
        assert agent.get_llm() is sentinel.lazy_model
        assert agent.get_llm() is sentinel.lazy_model

    model_factory.assert_called_once_with(
        model_client_config=agent.config.model_client_config,
        model_config=agent.config.model_config_obj,
    )


@pytest.mark.level1
def test_get_llm_preserves_the_missing_configuration_error() -> None:
    agent = ReActAgent(AgentCard(name="missing-model-access"))
    with pytest.raises(ValueError, match="model_client_config is required"):
        agent.get_llm()


@pytest.mark.level1
def test_get_llm_respects_the_existing_model_resolution_hook() -> None:
    agent = ReActAgent(AgentCard(name="custom-model-access"))
    with patch.object(agent, "_get_llm", return_value=sentinel.custom_model) as resolve_model:
        assert agent.get_llm() is sentinel.custom_model
    resolve_model.assert_called_once_with()
