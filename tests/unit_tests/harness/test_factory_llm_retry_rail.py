# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.agent_teams.observability import ObservabilityConfig, ObservabilityRail
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


def test_create_deep_agent_observability_config_auto_adds_rail(monkeypatch) -> None:
    init_calls: list[ObservabilityConfig] = []

    monkeypatch.setattr("openjiuwen.agent_teams.observability.is_initialized", lambda: False)
    monkeypatch.setattr(
        "openjiuwen.agent_teams.observability.init_observability",
        lambda config: init_calls.append(config),
    )

    config = ObservabilityConfig(enabled=True, exporter="console")
    agent = create_deep_agent(
        model=_create_dummy_model(),
        observability_config=config,
        auto_create_workspace=False,
    )

    assert init_calls == [config]
    assert sum(1 for rail in agent._pending_rails if isinstance(rail, ObservabilityRail)) == 1


def test_create_deep_agent_observability_config_does_not_duplicate_manual_rail(monkeypatch) -> None:
    monkeypatch.setattr("openjiuwen.agent_teams.observability.is_initialized", lambda: True)
    monkeypatch.setattr(
        "openjiuwen.agent_teams.observability.init_observability",
        lambda config: (_ for _ in ()).throw(AssertionError("init should not be called")),
    )

    manual_rail = ObservabilityRail()
    agent = create_deep_agent(
        model=_create_dummy_model(),
        rails=[manual_rail],
        observability_config=ObservabilityConfig(enabled=True, exporter="console"),
        auto_create_workspace=False,
    )

    obs_rails = [rail for rail in agent._pending_rails if isinstance(rail, ObservabilityRail)]
    assert obs_rails == [manual_rail]
