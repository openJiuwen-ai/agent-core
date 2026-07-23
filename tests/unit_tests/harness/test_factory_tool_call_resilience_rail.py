# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory wiring tests for ToolCallResilienceRail.
"""

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails.tool_call_resilience_rail import (
    ToolCallResilienceRail,
)


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


def test_create_deep_agent_auto_adds_tool_resilience_rail_by_default() -> None:
    agent = create_deep_agent(
        model=_create_dummy_model(),
        auto_create_workspace=False,
    )

    count = sum(1 for rail in agent._pending_rails if isinstance(rail, ToolCallResilienceRail))
    assert count == 1


def test_create_deep_agent_can_disable_default_tool_resilience_rail() -> None:
    agent = create_deep_agent(
        model=_create_dummy_model(),
        auto_create_workspace=False,
        enable_tool_resilience_rail=False,
    )

    assert not any(isinstance(rail, ToolCallResilienceRail) for rail in agent._pending_rails)


def test_create_deep_agent_does_not_duplicate_manual_tool_resilience_rail() -> None:
    manual_rail = ToolCallResilienceRail()
    agent = create_deep_agent(
        model=_create_dummy_model(),
        rails=[manual_rail],
        auto_create_workspace=False,
    )

    rails = [rail for rail in agent._pending_rails if isinstance(rail, ToolCallResilienceRail)]
    assert rails == [manual_rail]
