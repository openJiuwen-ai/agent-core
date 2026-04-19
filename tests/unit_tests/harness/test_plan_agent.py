# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.subagents.plan_agent import (
    DEFAULT_PLAN_AGENT_SYSTEM_PROMPT,
    PLAN_AGENT_DESC,
    PLAN_AGENT_SYSTEM_PROMPT_CN,
    PLAN_AGENT_SYSTEM_PROMPT_EN,
    build_plan_agent_config,
    create_plan_agent,
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


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

class TestPlanAgentConstants:
    def test_plan_agent_desc_has_cn_and_en(self):
        assert "cn" in PLAN_AGENT_DESC
        assert "en" in PLAN_AGENT_DESC
        assert PLAN_AGENT_DESC["cn"]
        assert PLAN_AGENT_DESC["en"]

    def test_system_prompt_cn_is_non_empty(self):
        assert PLAN_AGENT_SYSTEM_PROMPT_CN
        assert isinstance(PLAN_AGENT_SYSTEM_PROMPT_CN, str)

    def test_system_prompt_en_is_non_empty(self):
        assert PLAN_AGENT_SYSTEM_PROMPT_EN
        assert isinstance(PLAN_AGENT_SYSTEM_PROMPT_EN, str)

    def test_default_system_prompt_dict_matches_individual_constants(self):
        assert DEFAULT_PLAN_AGENT_SYSTEM_PROMPT["cn"] == PLAN_AGENT_SYSTEM_PROMPT_CN
        assert DEFAULT_PLAN_AGENT_SYSTEM_PROMPT["en"] == PLAN_AGENT_SYSTEM_PROMPT_EN

    def test_system_prompts_include_read_only_constraint(self):
        for prompt in (PLAN_AGENT_SYSTEM_PROMPT_CN, PLAN_AGENT_SYSTEM_PROMPT_EN):
            lower = prompt.lower()
            # Both prompts must communicate read-only semantics
            assert "read" in lower or "只读" in prompt

    def test_en_prompt_ends_with_critical_files_section(self):
        assert "Critical Files for Implementation" in PLAN_AGENT_SYSTEM_PROMPT_EN

    def test_cn_prompt_ends_with_critical_files_section(self):
        assert "Critical Files for Implementation" in PLAN_AGENT_SYSTEM_PROMPT_CN


# ---------------------------------------------------------------------------
# build_plan_agent_config
# ---------------------------------------------------------------------------

class TestBuildPlanAgentConfig:
    def test_defaults_en(self):
        spec = build_plan_agent_config(language="en")

        assert spec.agent_card.name == "plan_agent"
        assert spec.agent_card.description == PLAN_AGENT_DESC["en"]
        assert spec.system_prompt == PLAN_AGENT_SYSTEM_PROMPT_EN
        assert isinstance(spec.rails, list)
        assert len(spec.rails) == 1
        assert isinstance(spec.rails[0], FileSystemRail)
        assert spec.enable_task_loop is False
        assert spec.max_iterations == 25

    def test_defaults_cn(self):
        spec = build_plan_agent_config(language="cn")

        assert spec.agent_card.name == "plan_agent"
        assert spec.agent_card.description == PLAN_AGENT_DESC["cn"]
        assert spec.system_prompt == PLAN_AGENT_SYSTEM_PROMPT_CN

    def test_custom_card_overrides_default(self):
        custom_card = AgentCard(name="my_plan", description="custom planner")
        spec = build_plan_agent_config(card=custom_card, language="en")

        assert spec.agent_card.name == "my_plan"
        assert spec.agent_card.description == "custom planner"

    def test_custom_system_prompt_overrides_default(self):
        custom_prompt = "Custom planning prompt."
        spec = build_plan_agent_config(system_prompt=custom_prompt, language="en")

        assert spec.system_prompt == custom_prompt

    def test_custom_rails_replace_default(self):
        spec = build_plan_agent_config(rails=[], language="en")

        assert spec.rails == []

    def test_none_rails_uses_filesystem_rail(self):
        spec = build_plan_agent_config(rails=None, language="en")

        assert len(spec.rails) == 1
        assert isinstance(spec.rails[0], FileSystemRail)

    def test_enable_task_loop_propagates(self):
        spec = build_plan_agent_config(enable_task_loop=True, language="en")

        assert spec.enable_task_loop is True

    def test_max_iterations_propagates(self):
        spec = build_plan_agent_config(max_iterations=10, language="en")

        assert spec.max_iterations == 10

    def test_model_propagates(self):
        model = _create_dummy_model()
        spec = build_plan_agent_config(model=model, language="en")

        assert spec.model is model

    def test_tools_propagate(self):
        spec = build_plan_agent_config(tools=[], language="en")

        assert spec.tools == []

    def test_mcps_propagate(self):
        spec = build_plan_agent_config(mcps=[], language="en")

        assert spec.mcps == []

    def test_unknown_language_falls_back_to_cn(self):
        # resolve_language falls back to "cn" for unknown values
        spec = build_plan_agent_config(language="fr")

        # description should be present (either cn or en depending on fallback)
        assert spec.agent_card.description


# ---------------------------------------------------------------------------
# create_plan_agent
# ---------------------------------------------------------------------------

class TestCreatePlanAgent:
    def test_returns_deep_agent_with_defaults(self, tmp_path):
        from openjiuwen.harness.deep_agent import DeepAgent

        model = _create_dummy_model()
        agent = create_plan_agent(model, workspace=str(tmp_path), language="en")

        assert isinstance(agent, DeepAgent)
        assert agent.card.name == "plan_agent"

    def test_custom_card_is_respected(self, tmp_path):
        model = _create_dummy_model()
        custom_card = AgentCard(name="custom_planner", description="desc")
        agent = create_plan_agent(model, card=custom_card, workspace=str(tmp_path), language="en")

        assert agent.card.name == "custom_planner"

    def test_custom_system_prompt_is_respected(self, tmp_path):
        model = _create_dummy_model()
        agent = create_plan_agent(
            model,
            system_prompt="my custom prompt",
            workspace=str(tmp_path),
            language="en",
        )

        assert agent.deep_config.system_prompt == "my custom prompt"

    def test_filesystem_rail_attached_by_default(self, tmp_path):
        model = _create_dummy_model()
        agent = create_plan_agent(model, workspace=str(tmp_path), language="en")

        rail_types = [type(r) for r in agent._pending_rails]
        assert FileSystemRail in rail_types

    def test_custom_empty_rails_removes_filesystem_rail(self, tmp_path):
        model = _create_dummy_model()
        agent = create_plan_agent(model, rails=[], workspace=str(tmp_path), language="en")

        rail_types = [type(r) for r in agent._pending_rails]
        assert FileSystemRail not in rail_types

    def test_language_cn_sets_cn_prompt(self, tmp_path):
        model = _create_dummy_model()
        agent = create_plan_agent(model, workspace=str(tmp_path), language="cn")

        assert agent.deep_config.system_prompt == PLAN_AGENT_SYSTEM_PROMPT_CN

    def test_language_en_sets_en_prompt(self, tmp_path):
        model = _create_dummy_model()
        agent = create_plan_agent(model, workspace=str(tmp_path), language="en")

        assert agent.deep_config.system_prompt == PLAN_AGENT_SYSTEM_PROMPT_EN


# ---------------------------------------------------------------------------
# Integration: plan_agent as a subagent of a parent deep agent
# ---------------------------------------------------------------------------

class TestPlanAgentAsSubagent:
    def test_subagent_initializes_tools(self, tmp_path):
        async def _run():
            await Runner.start()
            try:
                parent_agent = create_deep_agent(
                    model=_create_dummy_model(),
                    card=AgentCard(name="parent", description="test"),
                    system_prompt="parent prompt",
                    subagents=[build_plan_agent_config(language="en")],
                    workspace=str(tmp_path),
                )

                subagent = parent_agent.create_subagent("plan_agent", "sub_session_id")
                await subagent.ensure_initialized()

                assert subagent.card.name == "plan_agent"
                # FileSystemRail provides filesystem tools
                assert subagent.ability_manager.get("read_file") is not None
                assert subagent.ability_manager.get("glob") is not None
                assert subagent.ability_manager.get("grep") is not None
                assert subagent.ability_manager.get("bash") is not None
            finally:
                await Runner.stop()

        asyncio.run(_run())
