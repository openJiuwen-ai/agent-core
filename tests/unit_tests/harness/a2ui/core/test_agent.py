# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.harness.a2ui.core.agent.build_agent.

build_agent() only constructs config objects and registers tools/rails --
it never opens a network connection, so this is safe to run without
credentials.
"""

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.harness.a2ui.core import config
from openjiuwen.harness.a2ui.core.agent import AGENT_ID, ALL_TOOLS, build_agent


class TestBuildAgent:
    @pytest.fixture(autouse=True)
    def _mock_api_key(self):
        # ModelClientConfig validates that top-level providers (e.g. OpenAI) carry
        # a non-empty api_key; build_agent() never calls out to the network so a
        # mock value is enough to satisfy construction.
        config.set_value("API_KEY", "mock-api-key-for-tests")
        yield
        config.set_value("API_KEY", "")

    @pytest.mark.asyncio
    async def test_builds_agent_with_expected_card(self):
        agent = await build_agent()
        assert agent.card.id == AGENT_ID
        assert agent.card.name == "A2UI ReAct Agent"

    @pytest.mark.asyncio
    async def test_registers_all_tools_on_ability_manager(self):
        agent = await build_agent()
        tool_names = {info.name for info in await agent.ability_manager.list_tool_info()}
        expected_names = {t.card.name for t in ALL_TOOLS}
        assert expected_names <= tool_names

    @pytest.mark.asyncio
    async def test_registers_all_tools_on_resource_manager(self):
        await build_agent()
        for t in ALL_TOOLS:
            # add_tool registers under the card's UUID id, not its name.
            assert Runner.resource_mgr.get_tool(t.card.id) is not None

    @pytest.mark.asyncio
    async def test_registers_a2ui_tool_event_rail(self):
        agent = await build_agent()
        manager = agent.agent_callback_manager
        assert manager.has_hooks(AgentCallbackEvent.BEFORE_TOOL_CALL)
        assert manager.has_hooks(AgentCallbackEvent.AFTER_TOOL_CALL)

    @pytest.mark.asyncio
    async def test_registers_search_and_browser_tools_beyond_all_tools(self):
        agent = await build_agent()
        tool_names = {info.name for info in await agent.ability_manager.list_tool_info()}
        assert {"free_search", "fetch_webpage", "browser_inspect_page"} <= tool_names

    @pytest.mark.asyncio
    async def test_applies_configured_temperature_and_seed(self):
        config.set_value("LLM_TEMPERATURE", 0.42)
        config.set_value("LLM_SEED", 7)
        try:
            agent = await build_agent()
        finally:
            config.set_value("LLM_TEMPERATURE", 1.0)
            config.set_value("LLM_SEED", 42)
        model_request_config = agent.config.model_config_obj
        assert model_request_config.temperature == 0.42
        assert model_request_config.seed == 7

    @pytest.mark.asyncio
    async def test_omits_reasoning_effort_when_not_configured(self):
        # Not every provider/model accepts this (e.g. DeepSeek's chat models
        # reject it outright) -- unset must mean the field is left off
        # ModelRequestConfig entirely, not sent as some guessed default.
        config.set_value("LLM_REASONING_EFFORT", None)
        agent = await build_agent()
        model_request_config = agent.config.model_config_obj
        assert "reasoning_effort" not in model_request_config.model_dump(exclude_unset=True)

    @pytest.mark.asyncio
    async def test_applies_configured_reasoning_effort(self):
        config.set_value("LLM_REASONING_EFFORT", "low")
        try:
            agent = await build_agent()
        finally:
            config.set_value("LLM_REASONING_EFFORT", None)
        model_request_config = agent.config.model_config_obj
        assert model_request_config.reasoning_effort == "low"
