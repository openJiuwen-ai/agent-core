# -*- coding: utf-8 -*-
"""Unit tests for AgentFactory and build_agent_factory per test suggestion report §4.13."""

import pytest

from openjiuwen.dev_tools.agentrl.config.schemas import AgentRuntimeConfig
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RLTask
from openjiuwen.dev_tools.agentrl.agent_runtime.agent_factory import (
    AgentFactory,
    build_agent_factory,
)


class TestBuildAgentFactory:
    """build_agent_factory returns AgentFactory (public surface only; no protected fields)."""

    @staticmethod
    def test_build_agent_factory_returns_factory():
        cfg = AgentRuntimeConfig(
            system_prompt="You are a helpful assistant.",
            temperature=0.7,
            top_p=0.9,
            max_new_tokens=512,
        )
        factory = build_agent_factory(cfg, tools=[], tool_names=[])
        assert isinstance(factory, AgentFactory)
        assert factory.proxy_url is None


class TestAgentFactoryCallWithoutProxyUrl:
    """__call__ without proxy_url set raises (per report §4.13)."""

    @staticmethod
    def test_call_without_proxy_url_raises():
        factory = AgentFactory(
            system_prompt="test",
            tools=[],
            tool_names=[],
            temperature=0.7,
            max_new_tokens=128,
            top_p=0.9,
            presence_penalty=0.0,
            frequency_penalty=0.0,
        )
        task = RLTask(task_id="t1", origin_task_id="o1", task_sample={})
        with pytest.raises(Exception) as exc_info:
            factory(task)
        assert "proxy_url" in str(exc_info.value).lower() or "proxy" in str(exc_info.value).lower()
