# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``build_team_bridge_section``.

After the roster unification (bridge members are ordinary ``team_members``
entries), the bridge section is a self-contract rendered ONLY for the bridge
avatar itself (``role == BRIDGE_AGENT``); every other role gets ``None`` — peers
do not perceive a bridge member's remote backing.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.prompts import build_team_bridge_section
from openjiuwen.agent_teams.prompts.sections import TeamSectionName
from openjiuwen.agent_teams.schema.team import TeamRole


@pytest.mark.level0
@pytest.mark.parametrize("role", [TeamRole.LEADER, TeamRole.TEAMMATE, TeamRole.HUMAN_AGENT])
def test_non_bridge_roles_return_none(role):
    """Only the bridge avatar gets the self-contract; peers see no section."""
    assert build_team_bridge_section(role=role, self_member_name="x") is None


@pytest.mark.level0
def test_bridge_agent_self_section_cn_specifies_scheduler_contract():
    section = build_team_bridge_section(
        role=TeamRole.BRIDGE_AGENT,
        language="cn",
        self_member_name="codex",
    )
    assert section is not None
    body = section.content["cn"]
    # Self-identity hint (own member_name); no inline roster of other bridges.
    assert "`codex`" in body
    # The scheduling-only / no-rewrite contract must be explicit.
    assert "调度员" in body or "调度" in body
    assert "原样" in body
    # Auto-forward mention so the LLM doesn't try to forward manually.
    assert "自动转发" in body


@pytest.mark.level0
def test_bridge_agent_self_section_en_emits_verbatim_contract():
    section = build_team_bridge_section(
        role=TeamRole.BRIDGE_AGENT,
        language="en",
        self_member_name="codex",
    )
    assert section is not None
    body = section.content["en"]
    assert "scheduler" in body.lower()
    assert "verbatim" in body.lower()
    assert "auto-forwarded" in body.lower()


@pytest.mark.level0
def test_section_name_and_priority():
    section = build_team_bridge_section(role=TeamRole.BRIDGE_AGENT, self_member_name="codex")
    assert section is not None
    assert section.name == TeamSectionName.BRIDGE
    assert section.priority == 12
