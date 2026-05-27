# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the unified team-member system prompt builder.

Both in-process DeepAgent members (via ``TeamPolicyRail``) and external CLI
members share :func:`build_team_static_sections`; the external CLI path renders
them standalone via :func:`build_team_member_system_prompt`, excluding the
other DeepAgent rails.
"""

import pytest

from openjiuwen.agent_teams.prompts import (
    build_team_member_system_prompt,
    build_team_static_sections,
)
from openjiuwen.agent_teams.prompts.sections import TeamSectionName
from openjiuwen.agent_teams.schema.team import TeamRole


@pytest.mark.level0
def test_static_sections_teammate_has_role_and_persona():
    sections = build_team_static_sections(
        role=TeamRole.TEAMMATE,
        persona="backend expert",
        member_name="dev-1",
        language="en",
    )
    names = {section.name for section in sections}
    assert TeamSectionName.ROLE in names
    assert TeamSectionName.PERSONA in names
    # workflow / lifecycle are leader-only and absent for a teammate.
    assert TeamSectionName.WORKFLOW not in names
    assert TeamSectionName.LIFECYCLE not in names


@pytest.mark.level0
def test_static_sections_leader_includes_workflow_and_lifecycle():
    sections = build_team_static_sections(
        role=TeamRole.LEADER,
        persona="",
        member_name="leader",
        lifecycle="temporary",
        language="en",
    )
    names = {section.name for section in sections}
    assert TeamSectionName.ROLE in names
    assert TeamSectionName.WORKFLOW in names
    assert TeamSectionName.LIFECYCLE in names
    # empty persona produces no persona section.
    assert TeamSectionName.PERSONA not in names


@pytest.mark.level0
def test_static_sections_exclude_dynamic_info_and_members():
    # Dynamic info / members sections depend on live DB state and are NOT part
    # of the static spawn-time prompt (the member fetches the roster via MCP).
    sections = build_team_static_sections(
        role=TeamRole.LEADER,
        persona="x",
        member_name="leader",
        language="en",
    )
    names = {section.name for section in sections}
    assert TeamSectionName.INFO not in names
    assert TeamSectionName.MEMBERS not in names


@pytest.mark.level0
def test_member_system_prompt_renders_persona_and_member_name():
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        persona="backend expert",
        member_name="dev-1",
        language="en",
    )
    assert prompt.strip()
    assert "backend expert" in prompt
    assert "dev-1" in prompt


@pytest.mark.level0
def test_member_system_prompt_nonempty_without_persona():
    # Even with no persona, the role section alone yields a usable prompt.
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        persona="",
        member_name="dev-1",
        language="en",
    )
    assert prompt.strip()
