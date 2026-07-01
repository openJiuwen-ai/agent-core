# coding: utf-8
"""Tests for role policy and system prompt construction."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.prompts import (
    build_system_prompt,
    role_policy,
)
from openjiuwen.agent_teams.schema.team import TeamRole


@pytest.mark.level0
def test_leader_policy_mentions_key_responsibilities():
    policy = role_policy(TeamRole.LEADER)
    assert "DAG" in policy
    assert "create_task" in policy


@pytest.mark.level1
def test_teammate_policy_mentions_task_workflow():
    policy = role_policy(TeamRole.TEAMMATE)
    assert "view_task" in policy


@pytest.mark.level1
def test_build_system_prompt_includes_all_parts():
    prompt = build_system_prompt(
        role=TeamRole.LEADER,
        persona="PM Expert",
    )
    assert "PM Expert" in prompt
    assert "create_task" in prompt


@pytest.mark.level1
def test_leader_prompt_carries_collaboration_mechanism_boundary_cn():
    prompt = build_system_prompt(
        role=TeamRole.LEADER,
        persona="PM Expert",
        language="cn",
    )

    # Leader must see the swarmflow vs build_team routing boundary.
    assert "协作机制选择" in prompt
    assert "涌现式" in prompt
    assert "swarmflow" in prompt
    # Concrete anti-pattern anchor: fixed-count sequential tasks stay on swarmflow.
    assert "顺序接力" in prompt
    assert "固定结束条件" in prompt


@pytest.mark.level1
def test_leader_prompt_carries_collaboration_mechanism_boundary_en():
    prompt = build_system_prompt(
        role=TeamRole.LEADER,
        persona="PM Expert",
        language="en",
    )

    assert "Collaboration Mechanism" in prompt
    assert "emergent" in prompt
    assert "swarmflow" in prompt
    assert "sequential relay" in prompt
    assert "fixed end condition" in prompt


@pytest.mark.level1
def test_teammate_prompt_omits_leader_collaboration_boundary():
    prompt = build_system_prompt(
        role=TeamRole.TEAMMATE,
        persona="Dev",
        language="cn",
    )

    # The routing boundary is a leader-only concern; it must not leak to teammates.
    assert "协作机制选择" not in prompt
    assert "涌现式" not in prompt
    assert "顺序接力" not in prompt
