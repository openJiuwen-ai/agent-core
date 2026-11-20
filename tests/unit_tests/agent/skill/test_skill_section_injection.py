# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

SKILLS_SECTION = "legacy_skills"


def _make_agent() -> ReActAgent:
    """ReActAgent with no system message configured (``prompt_template = []``).

    Each case passes the system prompt string straight to the method under
    test, so prompt rendering itself is not exercised here.
    """
    agent = ReActAgent(card=AgentCard(name="t", description="d"))
    cfg = ReActAgentConfig()
    cfg.prompt_template = []
    cfg.sys_operation_id = "sys_1"
    agent.configure(cfg)
    agent._warn_missing_skill_read_file_tool = AsyncMock()
    return agent


def _set_skill_util(agent: ReActAgent, has_skill: bool) -> None:
    skill_util = MagicMock()
    skill_util.has_skill.return_value = has_skill
    skill_util.get_skill_prompt.return_value = "SKILL PROMPT"
    agent._skill_util = skill_util


class TestSkillSectionInjection:
    """Skills section injection depends only on whether a skill is registered."""

    @pytest.mark.asyncio
    async def test_section_kept_when_skills_registered_and_system_prompt_empty(self):
        """A registered skill must stay in the prompt even with an empty system prompt."""
        agent = _make_agent()
        _set_skill_util(agent, has_skill=True)

        await agent._update_skill_prompt_builder_section("")

        assert agent.prompt_builder.has_section(SKILLS_SECTION)
        assert "SKILL PROMPT" in agent.prompt_builder.build()

    @pytest.mark.asyncio
    async def test_section_kept_when_skills_registered_and_system_prompt_non_empty(self):
        """Regression anchor: the pre-existing working path stays working."""
        agent = _make_agent()
        _set_skill_util(agent, has_skill=True)

        await agent._update_skill_prompt_builder_section("You are an assistant.")

        assert agent.prompt_builder.has_section(SKILLS_SECTION)
        assert "SKILL PROMPT" in agent.prompt_builder.build()

    @pytest.mark.asyncio
    async def test_section_removed_when_no_skill(self):
        """No registered skill -> the section is removed as before."""
        agent = _make_agent()
        _set_skill_util(agent, has_skill=False)

        await agent._update_skill_prompt_builder_section("")

        assert not agent.prompt_builder.has_section(SKILLS_SECTION)
