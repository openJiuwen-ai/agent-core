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
        member_name="dev-1",
        language="en",
    )
    names = {section.name for section in sections}
    assert TeamSectionName.ROLE in names
    # workflow / lifecycle are leader-only and absent for a teammate.
    assert TeamSectionName.WORKFLOW not in names
    assert TeamSectionName.LIFECYCLE not in names


@pytest.mark.level0
def test_static_sections_leader_includes_workflow_and_lifecycle():
    sections = build_team_static_sections(
        role=TeamRole.LEADER,
        member_name="leader",
        lifecycle="temporary",
        language="en",
    )
    names = {section.name for section in sections}
    assert TeamSectionName.ROLE in names
    assert TeamSectionName.WORKFLOW in names
    assert TeamSectionName.LIFECYCLE in names


@pytest.mark.level0
def test_static_sections_exclude_team_state():
    # The member's own identity, the team metadata and the roster all depend on
    # live DB state and are not sections at all — they are delivered into the
    # member's conversation as they appear.
    sections = build_team_static_sections(
        role=TeamRole.LEADER,
        member_name="leader",
        language="en",
    )
    bodies = "\n".join(section.render("en") for section in sections)
    assert "# Team Info" not in bodies
    assert "# Relationships" not in bodies


@pytest.mark.level0
def test_member_system_prompt_omits_who_the_member_is():
    # The standing policy is shared by every teammate; the member's own name
    # and private working agreement arrive as <team-context> instead.
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_name="dev-1",
        language="en",
    )
    assert prompt.strip()
    assert "dev-1" not in prompt


@pytest.mark.level0
def test_member_system_prompt_is_nonempty():
    # The role section alone yields a usable prompt.
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
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
        member_name="dev-1",
        language="en",
    )
    assert "shared team deliverables directory" in prompt
    assert "workspace_meta" in prompt


@pytest.mark.level0
def test_member_system_prompt_uses_external_workspace_policy():
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_name="dev-1",
        language="en",
        workspace_prompt_variant="external",
    )
    assert "shared team deliverables directory" not in prompt
    assert "given in the team info (`<team-context>`)" in prompt
    assert "workspace_meta" in prompt


@pytest.mark.level0
def test_member_system_prompt_declares_the_server_its_bare_tool_names_belong_to():
    # A CLI member reaches the team's tools through MCP, under a namespace,
    # and may have a built-in tool named like one of them. Naming the server
    # once says which reading of every bare name in the policy is the right
    # one — without the policy having to spell any tool out.
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_name="dev-1",
        language="en",
        workspace_prompt_variant="external",
        mcp_server_name="openjiuwen-team",
    )
    assert prompt.startswith('<team-policy tools="openjiuwen-team">')
    assert prompt.endswith("</team-policy>")
    assert '<team-note kind="tool-namespace">' in prompt
    assert "the MCP server `openjiuwen-team` provides" in prompt
    # How that server's tools are actually addressed is the provider's to say.
    assert "mcp__" not in prompt


@pytest.mark.level0
def test_the_declaration_covers_the_message_blocks_too():
    # The policy is not the only place a tool is named by its bare name: the
    # reply hints and task notices a member receives do it as well. They are
    # the same family of blocks, so one declaration reaches all of them.
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_name="dev-1",
        language="en",
        workspace_prompt_variant="external",
        mcp_server_name="openjiuwen-team",
    )
    declaration = prompt.split("</team-note>")[0]
    for block in ("team-inbound", "team-event", "team-context", "team-note"):
        assert block in declaration


@pytest.mark.level0
def test_an_in_process_member_reads_the_policy_unwrapped():
    # Its tools are called by the bare name, so there is nothing to declare.
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_name="dev-1",
        language="en",
    )
    assert "<team-policy" not in prompt
    assert "tool-namespace" not in prompt
