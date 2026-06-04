# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``build_team_bridge_section``.

Covers the three role-specific bodies (leader / teammate / bridge_agent),
the cn/en split, and the empty-roster → ``None`` behaviour that
``TeamPolicyRail`` relies on to skip injection when no bridge member
is registered.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.prompts import build_team_bridge_section
from openjiuwen.agent_teams.prompts.sections import TeamSectionName
from openjiuwen.agent_teams.schema.team import TeamRole


@pytest.mark.level0
def test_empty_roster_returns_none():
    assert (
        build_team_bridge_section(
            role=TeamRole.LEADER,
            bridge_agent_names=[],
        )
        is None
    )
    assert (
        build_team_bridge_section(
            role=TeamRole.LEADER,
            bridge_agent_names=None,
        )
        is None
    )


@pytest.mark.level0
def test_leader_section_cn_lists_bridges_as_teammates():
    section = build_team_bridge_section(
        role=TeamRole.LEADER,
        bridge_agent_names=["codex", "claudecode"],
        language="cn",
    )
    assert section is not None
    body = section.content["cn"]
    # Lists every registered bridge inline.
    assert "`codex`" in body
    assert "`claudecode`" in body
    # The contract for the leader: treat as ordinary teammate.
    assert "teammate" in body or "完全一致" in body


@pytest.mark.level0
def test_teammate_section_cn_mentions_remote_executor():
    section = build_team_bridge_section(
        role=TeamRole.TEAMMATE,
        bridge_agent_names=["codex"],
        language="cn",
    )
    assert section is not None
    body = section.content["cn"]
    assert "`codex`" in body
    assert "send_message" in body


@pytest.mark.level0
def test_bridge_agent_self_section_cn_specifies_scheduler_contract():
    section = build_team_bridge_section(
        role=TeamRole.BRIDGE_AGENT,
        bridge_agent_names=["codex"],
        language="cn",
        self_member_name="codex",
    )
    assert section is not None
    body = section.content["cn"]
    # Self-identity hint.
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
        bridge_agent_names=["codex"],
        language="en",
        self_member_name="codex",
    )
    assert section is not None
    body = section.content["en"]
    assert "scheduler" in body.lower()
    assert "verbatim" in body.lower()
    assert "auto-forwarded" in body.lower()


@pytest.mark.level0
def test_unknown_role_returns_none():
    """Stray roles (e.g. HUMAN_AGENT) should produce no section."""
    assert (
        build_team_bridge_section(
            role=TeamRole.HUMAN_AGENT,
            bridge_agent_names=["codex"],
        )
        is None
    )


@pytest.mark.level0
def test_section_name_and_priority():
    section = build_team_bridge_section(
        role=TeamRole.LEADER,
        bridge_agent_names=["codex"],
    )
    assert section is not None
    assert section.name == TeamSectionName.BRIDGE
    assert section.priority == 12
