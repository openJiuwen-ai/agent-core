# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.agent_teams.prompts.team_plan_agent import (
    TEAM_PLAN_AGENT_DESC,
    TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN,
    TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN,
)
from openjiuwen.agent_teams.rails import TeamPlanModeRail
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.schema.state import DeepAgentState
from openjiuwen.harness.subagents.plan_agent import (
    PLAN_AGENT_DESC,
    PLAN_AGENT_SYSTEM_PROMPT_EN,
    build_plan_agent_config,
)


class _PromptBuilder:
    def __init__(self, language: str = "en") -> None:
        self.language = language
        self.sections = {}

    def add_section(self, section) -> None:
        self.sections[section.name] = section

    def remove_section(self, name: str) -> None:
        self.sections.pop(name, None)


def _make_agent(*, mode: str = "plan", language: str = "en", subagents=None):
    state = DeepAgentState()
    state.plan_mode.mode = mode
    builder = _PromptBuilder(language)
    agent = Mock()
    agent.system_prompt_builder = builder
    agent.deep_config = SimpleNamespace(subagents=list(subagents or []))
    agent.load_state.return_value = state
    agent.get_plan_file_path.return_value = None
    return agent, builder


@pytest.mark.asyncio
async def test_team_plan_mode_rail_injects_team_plan_instructions() -> None:
    agent, builder = _make_agent(mode="plan", language="en")
    rail = TeamPlanModeRail()
    rail.init(agent)

    await rail.before_model_call(SimpleNamespace(session=SimpleNamespace()))

    section = builder.sections[SectionName.MODE_INSTRUCTIONS]
    content = section.content["en"]
    assert "Team.plan mode is active" in content
    assert "Mandatory Team Execution Semantics" in content
    assert "build_team" in content
    assert "Leader can implement directly" in content


@pytest.mark.asyncio
async def test_team_plan_mode_rail_uses_language_override_over_builder_language() -> None:
    agent, builder = _make_agent(mode="plan", language="en")
    rail = TeamPlanModeRail(language="zh")
    rail.init(agent)

    await rail.before_model_call(SimpleNamespace(session=SimpleNamespace()))

    section = builder.sections[SectionName.MODE_INSTRUCTIONS]
    assert "cn" in section.content
    assert "Team.plan 模式已激活" in section.content["cn"]
    assert "Team.plan 模式已激活" in section.render("en")


@pytest.mark.asyncio
async def test_team_plan_mode_rail_skips_when_not_plan_mode() -> None:
    agent, builder = _make_agent(mode="normal", language="en")
    builder.sections[SectionName.MODE_INSTRUCTIONS] = "stale"
    rail = TeamPlanModeRail()
    rail.init(agent)

    await rail.before_model_call(SimpleNamespace(session=SimpleNamespace()))

    assert SectionName.MODE_INSTRUCTIONS not in builder.sections


def test_team_plan_mode_rail_specializes_default_plan_agent() -> None:
    spec = build_plan_agent_config(language="en")
    agent, _ = _make_agent(mode="plan", language="en", subagents=[spec])
    rail = TeamPlanModeRail()

    rail.init(agent)

    assert spec.agent_card.description == TEAM_PLAN_AGENT_DESC["en"]
    assert spec.system_prompt == TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN


def test_team_plan_mode_rail_preserves_custom_plan_agent() -> None:
    spec = build_plan_agent_config(system_prompt="custom", language="en")
    agent, _ = _make_agent(mode="plan", language="en", subagents=[spec])
    rail = TeamPlanModeRail()

    rail.init(agent)

    assert spec.agent_card.description == PLAN_AGENT_DESC["en"]
    assert spec.system_prompt == "custom"
    assert spec.system_prompt != PLAN_AGENT_SYSTEM_PROMPT_EN


@pytest.mark.asyncio
async def test_team_plan_mode_rail_specializes_late_default_plan_agent_with_override() -> None:
    agent, _ = _make_agent(mode="plan", language="en")
    rail = TeamPlanModeRail(language="zh")
    rail.init(agent)
    spec = build_plan_agent_config(language="en")
    agent.deep_config.subagents.append(spec)

    await rail.before_model_call(SimpleNamespace(session=SimpleNamespace()))

    assert spec.agent_card.description == TEAM_PLAN_AGENT_DESC["cn"]
    assert spec.system_prompt == TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN
