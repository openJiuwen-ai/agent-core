# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.agent_teams.prompts import (
    TEAM_PLAN_MODE_PROMPT_CN,
    TEAM_PLAN_MODE_PROMPT_EN,
    get_team_plan_mode_prompt,
)


def test_team_plan_mode_prompt_is_team_oriented():
    assert "Team.plan 模式已激活" in TEAM_PLAN_MODE_PROMPT_CN
    assert "真实的 Team Leader" in TEAM_PLAN_MODE_PROMPT_CN
    assert "强制团队执行语义" in TEAM_PLAN_MODE_PROMPT_CN
    assert "审批后第一步必须调用 `build_team`" in TEAM_PLAN_MODE_PROMPT_CN
    assert "禁止建议“不启动团队”“无需团队协作”" in TEAM_PLAN_MODE_PROMPT_CN
    assert "Team.plan mode is active" in TEAM_PLAN_MODE_PROMPT_EN
    assert "real Team Leader" in TEAM_PLAN_MODE_PROMPT_EN
    assert "Mandatory Team Execution Semantics" in TEAM_PLAN_MODE_PROMPT_EN
    assert "first execution step must be `build_team`" in TEAM_PLAN_MODE_PROMPT_EN
    assert 'Never recommend "no team needed"' in TEAM_PLAN_MODE_PROMPT_EN


def test_get_team_plan_mode_prompt_chooses_language():
    assert get_team_plan_mode_prompt("cn") == TEAM_PLAN_MODE_PROMPT_CN
    assert get_team_plan_mode_prompt("en") == TEAM_PLAN_MODE_PROMPT_EN
