# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.agent_teams.prompts.team_plan_agent import (
    TEAM_PLAN_AGENT_DESC,
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


def _loaded_prompt(language: str) -> str:
    """Framework default prompt, read through the default loader — the value
    the lazy design serves when no per-team loader closure is bound."""
    return str(load_template("team_plan_agent", language).content).strip()


def test_team_plan_agent_prompt_is_team_oriented():
    cn = _loaded_prompt("cn")
    en = _loaded_prompt("en")
    assert "团队执行方案" in cn
    assert "强制团队执行语义" in cn
    assert "用户审批后由 Leader 组织团队" in cn
    assert "无需团队协作" in cn
    assert "team execution plan" in en
    assert "MANDATORY TEAM EXECUTION SEMANTICS" in en
    assert "after user approval the Leader organizes the team" in en
    assert '"no team needed"' in en
    for tool_name in _INTERNAL_TEAM_TOOL_NAMES:
        assert tool_name not in cn
        assert tool_name not in en


def test_apply_team_plan_agent_prompt_replaces_builtin_default():
    spec = build_plan_agent_config(language="en")

    changed = apply_team_plan_agent_prompt([spec], language="en")

    assert changed is True
    assert spec.agent_card.description == TEAM_PLAN_AGENT_DESC["en"]
    assert spec.system_prompt == _loaded_prompt("en")


def test_apply_team_plan_agent_prompt_preserves_custom_prompt():
    spec = build_plan_agent_config(system_prompt="custom", language="en")

    changed = apply_team_plan_agent_prompt([spec], language="en")

    assert changed is False
    assert spec.system_prompt == "custom"
