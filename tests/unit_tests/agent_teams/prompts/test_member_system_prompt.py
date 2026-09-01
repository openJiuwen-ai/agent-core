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
def test_static_sections_teammate_has_role_and_identity():
    sections = build_team_static_sections(
        role=TeamRole.TEAMMATE,
        member_prompt="follow the backend conventions",
        member_name="dev-1",
        language="en",
        include_member_specific=True,
    )
    names = {section.name for section in sections}
    assert TeamSectionName.ROLE in names
    # identity carries both the member_name and the private working agreement.
    assert TeamSectionName.IDENTITY in names
    # workflow / lifecycle are leader-only and absent for a teammate.
    assert TeamSectionName.WORKFLOW not in names
    assert TeamSectionName.LIFECYCLE not in names


@pytest.mark.level0
def test_static_sections_omit_member_specific_by_default():
    # In-process members get the identity section (member_name + private
    # working agreement) as a prompt attachment, so the shared system-prompt
    # prefix stays identical across the team.
    sections = build_team_static_sections(
        role=TeamRole.TEAMMATE,
        member_prompt="follow the backend conventions",
        member_name="dev-1",
        language="en",
    )
    names = {section.name for section in sections}
    assert TeamSectionName.IDENTITY not in names


@pytest.mark.level0
def test_static_sections_leader_includes_workflow_and_lifecycle():
    sections = build_team_static_sections(
        role=TeamRole.LEADER,
        member_prompt="",
        member_name="leader",
        lifecycle="temporary",
        language="en",
        include_member_specific=True,
    )
    names = {section.name for section in sections}
    assert TeamSectionName.ROLE in names
    assert TeamSectionName.WORKFLOW in names
    assert TeamSectionName.LIFECYCLE in names
    # A leader has a member_name but no private prompt, so identity is present
    # and carries the name alone.
    assert TeamSectionName.IDENTITY in names


@pytest.mark.level0
def test_static_sections_exclude_team_state():
    # Team metadata and the roster depend on live DB state and are not sections
    # at all — they are delivered into the member's conversation as they appear.
    sections = build_team_static_sections(
        role=TeamRole.LEADER,
        member_prompt="x",
        member_name="leader",
        language="en",
    )
    bodies = "\n".join(section.render("en") for section in sections)
    assert "# Team Info" not in bodies
    assert "# Relationships" not in bodies


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
def test_member_system_prompt_documents_the_team_state_tags():
    # Team state reaches an external CLI member as XML inside the messages it
    # receives, so the tag notice — not a prompt-attachment notice — is what
    # has to be there.
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_prompt="",
        member_name="dev-1",
        language="en",
    )
    assert "prompt-attachment" not in prompt
    assert "<team-context>" in prompt
    assert "roster-change" in prompt


@pytest.mark.level0
def test_member_system_prompt_uses_native_workspace_policy_by_default():
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_prompt="",
        member_name="dev-1",
        language="en",
    )
    assert "shared team deliverables directory" in prompt
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
    assert "shared team deliverables directory" not in prompt
    assert "given in the team info (`<team-context>`)" in prompt
    assert "workspace_meta" in prompt
