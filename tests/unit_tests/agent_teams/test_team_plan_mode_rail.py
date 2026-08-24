# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.agent_teams.prompts.team_plan_agent import TEAM_PLAN_AGENT_DESC
from openjiuwen.agent_teams.rails import TeamPlanModeRail
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.schema.state import DeepAgentState
from openjiuwen.harness.subagents.plan_agent import (
    PLAN_AGENT_DESC,
    PLAN_AGENT_SYSTEM_PROMPT_EN,
    build_plan_agent_config,
)


_INTERNAL_TEAM_TOOL_NAMES = (
    "build_team",
    "list_members",
    "create_task",
    "spawn_teammate",
    "send_message",
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
    agent.prompt_attachment_manager = PromptAttachmentManager(language=language)
    agent.deep_config = SimpleNamespace(subagents=list(subagents or []))
    agent.load_state.return_value = state
    agent.get_plan_file_path.return_value = None
    return agent, builder


@pytest.mark.asyncio
async def test_team_plan_mode_rail_injects_team_plan_instructions() -> None:
    agent, builder = _make_agent(mode="plan", language="en")
    rail = TeamPlanModeRail()
    rail.init(agent)

    await rail.before_model_call(SimpleNamespace(session=SimpleNamespace(session_id="sess1")))

    assert SectionName.MODE_INSTRUCTIONS not in builder.sections
    [attachment] = await agent.prompt_attachment_manager.collect_for_session("sess1")
    content = attachment.content
    assert "Team.plan mode is active" in content
    assert "Mandatory Team Execution Semantics" in content
    assert "after user approval the Leader will organize the team" in content
    assert all(tool_name not in content for tool_name in _INTERNAL_TEAM_TOOL_NAMES)
    assert "Leader can implement directly" in content


@pytest.mark.asyncio
async def test_team_plan_mode_rail_uses_language_override_over_builder_language() -> None:
    agent, builder = _make_agent(mode="plan", language="en")
    rail = TeamPlanModeRail(language="zh")
    rail.init(agent)

    await rail.before_model_call(SimpleNamespace(session=SimpleNamespace(session_id="sess1")))

    assert SectionName.MODE_INSTRUCTIONS not in builder.sections
    [attachment] = await agent.prompt_attachment_manager.collect_for_session("sess1")
    assert "Team.plan 模式已激活" in attachment.content


@pytest.mark.asyncio
async def test_team_plan_mode_rail_skips_when_not_plan_mode() -> None:
    agent, builder = _make_agent(mode="normal", language="en")
    builder.sections[SectionName.MODE_INSTRUCTIONS] = "stale"
    rail = TeamPlanModeRail()
    rail.init(agent)

    await rail.before_model_call(SimpleNamespace(session=SimpleNamespace(session_id="sess1")))

    assert SectionName.MODE_INSTRUCTIONS not in builder.sections


def test_team_plan_mode_rail_specializes_default_plan_agent() -> None:
    spec = build_plan_agent_config(language="en")
    agent, _ = _make_agent(mode="plan", language="en", subagents=[spec])
    rail = TeamPlanModeRail()

    rail.init(agent)

    assert spec.agent_card.description == TEAM_PLAN_AGENT_DESC["en"]
    assert spec.system_prompt == str(load_template("team_plan_agent", "en").content).strip()


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

    await rail.before_model_call(SimpleNamespace(session=SimpleNamespace(session_id="sess1")))

    assert spec.agent_card.description == TEAM_PLAN_AGENT_DESC["cn"]
    assert spec.system_prompt == str(load_template("team_plan_agent", "cn").content).strip()
