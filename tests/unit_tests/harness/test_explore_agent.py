# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.subagents.explore_agent import build_explore_agent_config


def _create_dummy_model() -> Model:
    model_client_config = ModelClientConfig(
        client_provider="OpenAI",
        api_key="test-key",
        api_base="http://test-base",
        verify_ssl=False,
    )
    model_config = ModelRequestConfig(model="test-model")
    return Model(model_client_config=model_client_config, model_config=model_config)


def test_build_explore_agent_config_defaults():
    spec = build_explore_agent_config(language="en")

    assert spec.agent_card.name == "Explore"
    assert spec.agent_card.description
    assert spec.system_prompt
    assert isinstance(spec.rails, list)
    assert len(spec.rails) == 1
    assert isinstance(spec.rails[0], FileSystemRail)


def test_create_subagent_explore_initializes_tools(tmp_path):
    async def _run():
        await Runner.start()
        try:
            parent_agent = create_deep_agent(
                model=_create_dummy_model(),
                card=AgentCard(name="parent", description="test"),
                system_prompt="parent prompt",
                subagents=[build_explore_agent_config(language="en")],
                workspace=str(tmp_path),
            )

            subagent = parent_agent.create_subagent("Explore", "sub_session_id")
            await subagent.ensure_initialized()

            assert subagent.card.name == "Explore"
            assert subagent.ability_manager.get("read_file") is not None
            assert subagent.ability_manager.get("glob") is not None
            assert subagent.ability_manager.get("list_files") is not None
            assert subagent.ability_manager.get("grep") is not None
            assert subagent.ability_manager.get("bash") is not None
            assert subagent.ability_manager.get("write_file") is not None
            assert subagent.ability_manager.get("edit_file") is not None
            assert subagent.ability_manager.get("code") is not None
        finally:
            await Runner.stop()

    asyncio.run(_run())
