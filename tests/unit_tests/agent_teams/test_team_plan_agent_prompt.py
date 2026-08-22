# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.agent_teams.prompts.team_plan_agent import (
    DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT,
    TEAM_PLAN_AGENT_DESC,
    TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN,
    TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN,
    apply_team_plan_agent_prompt,
)
from openjiuwen.harness.subagents.plan_agent import build_plan_agent_config


_INTERNAL_TEAM_TOOL_NAMES = (
    "build_team",
    "list_members",
    "create_task",
    "spawn_teammate",
    "send_message",
)


def test_team_plan_agent_prompt_dict_matches_constants():
    assert DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT["cn"] == TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN
    assert DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT["en"] == TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN


def test_team_plan_agent_prompt_is_team_oriented():
    assert "团队执行方案" in TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN
    assert "强制团队执行语义" in TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN
    assert "用户审批后由 Leader 组织团队" in TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN
    assert "无需团队协作" in TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN
    assert "team execution plan" in TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN
    assert "MANDATORY TEAM EXECUTION SEMANTICS" in TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN
    assert "after user approval the Leader organizes the team" in TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN
    assert '"no team needed"' in TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN
    for tool_name in _INTERNAL_TEAM_TOOL_NAMES:
        assert tool_name not in TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN
        assert tool_name not in TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN


def test_apply_team_plan_agent_prompt_replaces_builtin_default():
    spec = build_plan_agent_config(language="en")

    changed = apply_team_plan_agent_prompt([spec], language="en")

    assert changed is True
    assert spec.agent_card.description == TEAM_PLAN_AGENT_DESC["en"]
    assert spec.system_prompt == TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN


def test_apply_team_plan_agent_prompt_preserves_custom_prompt():
    spec = build_plan_agent_config(system_prompt="custom", language="en")

    changed = apply_team_plan_agent_prompt([spec], language="en")

    assert changed is False
    assert spec.system_prompt == "custom"
