# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails.model_anomaly_detection_rail import ModelAnomalyDetectionRail


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


def test_create_deep_agent_auto_adds_model_anomaly_detection_rail_by_default() -> None:
    agent = create_deep_agent(
        model=_create_dummy_model(),
        auto_create_workspace=False,
    )

    anomaly_rail_count = sum(1 for rail in agent._pending_rails if isinstance(rail, ModelAnomalyDetectionRail))
    assert anomaly_rail_count == 1


def test_create_deep_agent_can_disable_default_model_anomaly_detection_rail() -> None:
    agent = create_deep_agent(
        model=_create_dummy_model(),
        auto_create_workspace=False,
        enable_model_anomaly_detection_rail=False,
    )

    assert not any(isinstance(rail, ModelAnomalyDetectionRail) for rail in agent._pending_rails)


def test_create_deep_agent_does_not_duplicate_manual_model_anomaly_detection_rail() -> None:
    manual_rail = ModelAnomalyDetectionRail()
    agent = create_deep_agent(
        model=_create_dummy_model(),
        rails=[manual_rail],
        auto_create_workspace=False,
    )

    anomaly_rails = [rail for rail in agent._pending_rails if isinstance(rail, ModelAnomalyDetectionRail)]
    assert anomaly_rails == [manual_rail]
