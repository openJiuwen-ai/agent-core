# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for the tool list the general-purpose subagent advertises."""

# pylint: disable=protected-access
from __future__ import annotations

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.tools import WebFreeSearchTool


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


@pytest.fixture(autouse=True)
def _clear_search_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from a known state: no inherited FREE_SEARCH_* setting from the developer shell."""
    monkeypatch.delenv("FREE_SEARCH_DDG_ENABLED", raising=False)
    monkeypatch.delenv("FREE_SEARCH_BING_ENABLED", raising=False)


def _general_purpose_spec(agent: DeepAgent) -> SubAgentConfig:
    specs = [
        spec
        for spec in (agent.deep_config.subagents or [])
        if isinstance(spec, SubAgentConfig) and spec.agent_card.name == "general-purpose"
    ]
    assert len(specs) == 1
    return specs[0]


def _tool_names(spec: SubAgentConfig) -> list[str]:
    return [getattr(tool, "name", None) or getattr(getattr(tool, "card", None), "name", None) for tool in spec.tools]


def _broadcast(agent: DeepAgent) -> str:
    """The ``available_agents`` text SubagentRail feeds into the task_tool description."""
    return SubagentRail()._build_available_agents_description(agent.deep_config.subagents)


def test_disabled_free_search_is_not_inherited_by_general_purpose_subagent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "false")
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "false")
    read_file = ToolCard(id="read_file_card", name="read_file", description="read file")

    agent = create_deep_agent(
        model=_create_dummy_model(),
        tools=[WebFreeSearchTool(language="cn", agent_id="disabled"), read_file],
        add_general_purpose_agent=True,
        auto_create_workspace=False,
    )

    spec = _general_purpose_spec(agent)
    assert "free_search" not in _tool_names(spec)
    # The rest of the parent's tool list is still inherited untouched.
    assert "read_file" in _tool_names(spec)
    assert "free_search" not in _broadcast(agent)
    assert "read_file" in _broadcast(agent)


def test_enabled_free_search_is_still_inherited_as_a_tool_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
    tool = WebFreeSearchTool(language="cn", agent_id="enabled")

    agent = create_deep_agent(
        model=_create_dummy_model(),
        tools=[tool],
        add_general_purpose_agent=True,
        auto_create_workspace=False,
    )

    try:
        spec = _general_purpose_spec(agent)
        assert "free_search" in _tool_names(spec)
        # Keep the Tool instance: create_subagent binds the child's own instances from it.
        assert any(isinstance(inherited, Tool) and inherited is tool for inherited in spec.tools)
        assert "free_search" in _broadcast(agent)
    finally:
        if Runner.resource_mgr.get_tool(tool.card.id) is not None:
            Runner.resource_mgr.remove_tool(tool.card.id)


def test_user_provided_subagent_tools_are_not_filtered() -> None:
    """The filter applies to the injected general-purpose spec only."""
    stub = SubAgentConfig(
        agent_card=AgentCard(name="stub_agent", description="Stub description"),
        system_prompt="Stub prompt",
        tools=[ToolCard(id="stub_tool", name="grep", description="grep")],
    )

    agent = create_deep_agent(
        model=_create_dummy_model(),
        tools=[ToolCard(id="read_file_card", name="read_file", description="read file")],
        subagents=[stub],
        add_general_purpose_agent=True,
        auto_create_workspace=False,
    )

    subagents = agent.deep_config.subagents or []
    assert _general_purpose_spec(agent) is subagents[0]
    assert stub in subagents
    assert "grep" in _tool_names(stub)
