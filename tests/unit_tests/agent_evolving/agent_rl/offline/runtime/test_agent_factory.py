# -*- coding: utf-8 -*-
"""Unit tests for AgentFactory and build_agent_factory."""

import pytest

from openjiuwen.agent_evolving.agent_rl.config.offline_config import AgentRuntimeConfig
from openjiuwen.agent_evolving.agent_rl.schemas import RLTask
from openjiuwen.agent_evolving.agent_rl.offline.runtime.agent_factory import (
    AgentFactory,
    build_agent_factory,
)


class TestBuildAgentFactory:
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
