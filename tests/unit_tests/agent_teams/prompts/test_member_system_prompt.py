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
    build_team_info_section,
    build_team_member_system_prompt,
    build_team_static_sections,
)
from openjiuwen.agent_teams.prompts.sections import TeamSectionName
from openjiuwen.agent_teams.schema.team import TeamRole


@pytest.mark.level0
def test_static_sections_teammate_has_role_and_private_prompt():
    sections = build_team_static_sections(
        role=TeamRole.TEAMMATE,
        member_prompt="follow the backend conventions",
        member_name="dev-1",
        language="en",
    )
    names = {section.name for section in sections}
    assert TeamSectionName.ROLE in names
    assert TeamSectionName.PRIVATE_PROMPT in names
    # workflow / lifecycle are leader-only and absent for a teammate.
    assert TeamSectionName.WORKFLOW not in names
    assert TeamSectionName.LIFECYCLE not in names


@pytest.mark.level0
def test_static_sections_leader_includes_workflow_and_lifecycle():
    sections = build_team_static_sections(
        role=TeamRole.LEADER,
        member_prompt="",
        member_name="leader",
        lifecycle="temporary",
        language="en",
    )
    names = {section.name for section in sections}
    assert TeamSectionName.ROLE in names
    assert TeamSectionName.WORKFLOW in names
    assert TeamSectionName.LIFECYCLE in names
    # empty private prompt produces no private-prompt section.
    assert TeamSectionName.PRIVATE_PROMPT not in names


@pytest.mark.level0
def test_static_sections_exclude_dynamic_info_and_members():
    # Dynamic info / members sections depend on live DB state and are NOT part
    # of static sections; they are attached at delivery time.
    sections = build_team_static_sections(
        role=TeamRole.LEADER,
        member_prompt="x",
        member_name="leader",
        language="en",
    )
    names = {section.name for section in sections}
    assert TeamSectionName.INFO not in names
    assert TeamSectionName.MEMBERS not in names


@pytest.mark.level0
def test_member_system_prompt_renders_private_prompt_and_member_name():
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_prompt="stay focused on backend work",
        member_name="dev-1",
        language="en",
    )
    assert prompt.strip()
    assert "stay focused on backend work" in prompt
    assert "dev-1" in prompt


@pytest.mark.level0
def test_member_system_prompt_nonempty_without_private_prompt():
    # Even with no private prompt, the role section alone yields a usable prompt.
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_prompt="",
        member_name="dev-1",
        language="en",
    )
    assert prompt.strip()


@pytest.mark.level0
def test_member_system_prompt_includes_attachment_notice():
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_prompt="",
        member_name="dev-1",
        language="en",
    )
    assert "prompt-attachment" in prompt
    assert "team_members" in prompt
    assert "team_info" in prompt


@pytest.mark.level0
def test_member_system_prompt_uses_native_workspace_policy_by_default():
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_prompt="",
        member_name="dev-1",
        language="en",
    )
    assert "under `.team/`" in prompt
    assert "workspace_meta" in prompt


@pytest.mark.level0
def test_member_system_prompt_uses_external_workspace_policy():
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_prompt="",
        member_name="dev-1",
        language="en",
        workspace_prompt_variant="external",
    )
    assert "under `.team/`" not in prompt
    assert "provided by the latest `team_info` attachment" in prompt
    assert "workspace_meta" in prompt


@pytest.mark.level0
def test_team_info_section_keeps_native_workspace_mount():
    section = build_team_info_section(
        team_info={"team_name": "demo", "display_name": "Demo", "desc": "Ship it"},
        team_workspace_mount=".team/demo/",
        team_workspace_path="/tmp/demo-workspace",
        language="en",
    )
    assert section is not None
    content = section.content["en"]
    assert "`.team/demo/`" in content
    assert "Absolute path: `/tmp/demo-workspace`" in content


@pytest.mark.level0
def test_team_info_section_supports_path_only_workspace():
    section = build_team_info_section(
        team_info={"team_name": "demo", "display_name": "Demo", "desc": "Ship it"},
        team_workspace_path="/tmp/demo-workspace",
        language="en",
    )
    assert section is not None
    content = section.content["en"]
    assert "`.team/" not in content
    assert "Team Shared Workspace: `/tmp/demo-workspace`" in content
