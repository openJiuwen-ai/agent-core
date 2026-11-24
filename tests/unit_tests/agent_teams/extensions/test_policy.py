# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The default policy builder against the direct section assembly."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.extensions.models import TeamPolicyBuildInput, TeamPolicyContext
from openjiuwen.agent_teams.extensions.policy import (
    build_default_policy_sections,
    render_policy_sections,
    select_policy_disclosure_sections,
    select_policy_prefix_sections,
)
from openjiuwen.agent_teams.prompts import build_leader_bootstrap_section, build_team_static_sections
from openjiuwen.agent_teams.prompts.sections import TeamSectionName
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent.prompts.builder import PromptSection


def _section_names(sections) -> set[str]:
    return {s.name for s in sections}


@pytest.mark.level0
def test_teammate_sections_match_direct_assembly() -> None:
    """The extracted builder returns exactly what build_team_static_sections did."""
    build_input = TeamPolicyBuildInput(
        team_name="research-team",
        role=TeamRole.TEAMMATE,
        member_name="researcher",
    )
    context = TeamPolicyContext(
        language="cn",
        lifecycle="temporary",
        team_mode="default",
        dispatch_mode="autonomous",
        teammate_mode="build_mode",
    )
    sections = build_default_policy_sections(build_input, context)
    expected = build_team_static_sections(
        role=TeamRole.TEAMMATE,
        member_name="researcher",
        lifecycle="temporary",
        teammate_mode="build_mode",
        team_mode="default",
        dispatch_mode="autonomous",
        language="cn",
        hitt_enabled=False,
    )
    assert isinstance(sections, tuple)
    assert _section_names(sections) == _section_names(expected)
    assert TeamSectionName.ROLE in _section_names(sections)
    assert TeamSectionName.INBOUND_TAGS in _section_names(sections)


@pytest.mark.level0
def test_hitt_gate_controls_hitt_section() -> None:
    """The HITT contract appears only when the effective flag is on."""
    build_input = TeamPolicyBuildInput(team_name="t", role=TeamRole.TEAMMATE, member_name="m")
    with_hitt = build_default_policy_sections(build_input, TeamPolicyContext(hitt_enabled=True))
    without_hitt = build_default_policy_sections(build_input, TeamPolicyContext(hitt_enabled=False))
    assert TeamSectionName.HITT in _section_names(with_hitt)
    assert TeamSectionName.HITT not in _section_names(without_hitt)


@pytest.mark.level0
def test_leader_full_set_and_its_two_views() -> None:
    """The leader build returns the full set; prefix and disclosure split it."""
    build_input = TeamPolicyBuildInput(team_name="t", role=TeamRole.LEADER)
    full = build_default_policy_sections(build_input, TeamPolicyContext(language="cn"))
    names = _section_names(full)
    # Full set: bootstrap + every collaboration convention + the caller's extra.
    assert {TeamSectionName.BOOTSTRAP, TeamSectionName.ROLE, TeamSectionName.WORKFLOW,
            TeamSectionName.DISPATCH, TeamSectionName.LIFECYCLE, TeamSectionName.INBOUND_TAGS,
            } <= names
    with_extra = build_default_policy_sections(
        build_input,
        TeamPolicyContext(base_prompt="answer in English"),
    )
    assert TeamSectionName.EXTRA in _section_names(with_extra)

    # Prefix view: bootstrap + extra only (progressive disclosure keeps the
    # rest out of the cache-stable system prompt).
    prefix = select_policy_prefix_sections(with_extra)
    assert _section_names(prefix) == {TeamSectionName.BOOTSTRAP, TeamSectionName.EXTRA}

    # Disclosure view: everything except the prefix sections; extension
    # sections ride along.
    extra_section = PromptSection(name="business_extra", content={"cn": "extra"}, priority=50)
    disclosure = select_policy_disclosure_sections((*with_extra, extra_section))
    disclosure_names = _section_names(disclosure)
    assert TeamSectionName.BOOTSTRAP not in disclosure_names
    assert TeamSectionName.EXTRA not in disclosure_names
    assert TeamSectionName.WORKFLOW in disclosure_names
    assert "business_extra" in disclosure_names

    # Rendering joins the view into one prompt text.
    text = render_policy_sections(disclosure, language="cn")
    assert "business_extra" not in text  # section name is not the content
    assert text.strip()


@pytest.mark.level1
def test_leader_swarmflow_slot_matches_direct_builder() -> None:
    """The swarmflow capability slot renders the same as the direct builder."""
    build_input = TeamPolicyBuildInput(team_name="t", role=TeamRole.LEADER)
    enabled = build_default_policy_sections(build_input, TeamPolicyContext(language="cn", swarmflow_enabled=True))
    disabled = build_default_policy_sections(build_input, TeamPolicyContext(language="cn", swarmflow_enabled=False))
    enabled_direct = build_leader_bootstrap_section(swarmflow_enabled=True, language="cn")
    disabled_direct = build_leader_bootstrap_section(swarmflow_enabled=False, language="cn")
    assert enabled[0].content == enabled_direct.content
    assert disabled[0].content == disabled_direct.content
    assert enabled[0].content != disabled[0].content


@pytest.mark.level1
def test_language_switches_template_set() -> None:
    """English context renders the English template content."""
    build_input = TeamPolicyBuildInput(team_name="t", role=TeamRole.TEAMMATE, member_name="m")
    cn = build_default_policy_sections(build_input, TeamPolicyContext(language="cn"))
    en = build_default_policy_sections(build_input, TeamPolicyContext(language="en"))
    cn_role = next(s for s in cn if s.name == TeamSectionName.ROLE)
    en_role = next(s for s in en if s.name == TeamSectionName.ROLE)
    assert cn_role.content != en_role.content
