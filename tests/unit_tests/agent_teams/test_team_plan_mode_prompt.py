# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.agent_teams.prompts import get_team_plan_mode_prompt


_INTERNAL_TEAM_TOOL_NAMES = (
    "build_team",
    "list_members",
    "create_task",
    "spawn_teammate",
    "send_message",
)


def test_team_plan_mode_prompt_is_team_oriented():
    cn = get_team_plan_mode_prompt("cn")
    en = get_team_plan_mode_prompt("en")
    assert "Team.plan 模式已激活" in cn
    assert "真实的 Team Leader" in cn
    assert "强制团队执行语义" in cn
    assert "用户审批后由 Leader 组织团队" in cn
    assert "禁止建议“不启动团队”“无需团队协作”" in cn
    assert "Team.plan mode is active" in en
    assert "real Team Leader" in en
    assert "Mandatory Team Execution Semantics" in en
    assert "after user approval the Leader will organize the team" in en
    assert 'Never recommend "no team needed"' in en
    for tool_name in _INTERNAL_TEAM_TOOL_NAMES:
        assert tool_name not in cn
        assert tool_name not in en


def test_get_team_plan_mode_prompt_chooses_language():
    # The lazy design serves the framework default through the default
    # loader; the two languages must resolve to distinct templates.
    assert get_team_plan_mode_prompt("cn") != get_team_plan_mode_prompt("en")
