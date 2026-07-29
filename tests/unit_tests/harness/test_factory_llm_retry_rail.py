# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails.llm_retry_rail import LLMRetryRail


def _create_dummy_model() -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://test-base",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="test-model"),
    )


def test_create_deep_agent_auto_adds_llm_retry_rail_by_default() -> None:
    agent = create_deep_agent(
        model=_create_dummy_model(),
        auto_create_workspace=False,
    )

    llm_retry_count = sum(1 for rail in agent._pending_rails if isinstance(rail, LLMRetryRail))
    assert llm_retry_count == 1


def test_create_deep_agent_can_disable_default_llm_retry_rail() -> None:
    agent = create_deep_agent(
        model=_create_dummy_model(),
        auto_create_workspace=False,
        enable_llm_retry_rail=False,
    )

    assert not any(isinstance(rail, LLMRetryRail) for rail in agent._pending_rails)


def test_create_deep_agent_does_not_duplicate_manual_llm_retry_rail() -> None:
    manual_rail = LLMRetryRail()
    agent = create_deep_agent(
        model=_create_dummy_model(),
        rails=[manual_rail],
        auto_create_workspace=False,
    )

    llm_retry_rails = [rail for rail in agent._pending_rails if isinstance(rail, LLMRetryRail)]
    assert llm_retry_rails == [manual_rail]
