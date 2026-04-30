# coding: utf-8
"""Tests for role policy and system prompt construction."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.agent.policy import (
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
