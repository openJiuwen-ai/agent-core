# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression coverage for inherited versus explicitly pinned subagent models."""

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.schema.config import DeepAgentConfig, SubAgentConfig


def _model(name: str) -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://test.invalid/v1",
        ),
        model_config=ModelRequestConfig(model=name),
    )


@pytest.mark.parametrize("hot_reconfigure", [False, True])
def test_auto_general_purpose_uses_parent_model_at_spawn(tmp_path, hot_reconfigure):
    parent = create_deep_agent(
        model=_model("initial"),
        workspace=str(tmp_path),
        add_general_purpose_agent=not hot_reconfigure,
        auto_create_workspace=False,
    )
    if hot_reconfigure:
        parent.configure(
            DeepAgentConfig(
                model=_model("initial"),
                workspace=str(tmp_path),
                add_general_purpose_agent=True,
                auto_create_workspace=False,
            )
        )

    for name in ("flash", "think"):
        selected = _model(name)
        parent._deep_config.model = selected
        child = parent.create_subagent("general-purpose", f"session-{name}")
        assert child._deep_config.model is selected
        assert child._react_agent._config.model_name == name


def test_explicit_general_purpose_model_is_not_replaced(tmp_path):
    pinned = _model("specialist")
    parent = create_deep_agent(
        model=_model("initial"),
        workspace=str(tmp_path),
        auto_create_workspace=False,
        add_general_purpose_agent=True,
        subagents=[
            SubAgentConfig(
                agent_card=AgentCard(name="general-purpose", description="custom"),
                system_prompt="custom",
                model=pinned,
            )
        ],
    )
    parent._deep_config.model = _model("flash")
    child = parent.create_subagent("general-purpose", "pinned-session")
    assert child._deep_config.model is pinned
    assert len(parent._deep_config.subagents) == 1
