# coding: utf-8
"""Tests for role policy and system prompt construction."""
from __future__ import annotations

from openjiuwen.agent_teams.agent.policy import (
    build_system_prompt,
    leader_tool_guide,
    role_policy,
    teammate_tool_guide,
)
from openjiuwen.agent_teams.schema.team import TeamRole


def test_leader_policy_mentions_key_responsibilities():
    policy = role_policy(TeamRole.LEADER)
    assert "DAG" in policy
    assert "task_manager" in policy


def test_teammate_policy_mentions_task_workflow():
    policy = role_policy(TeamRole.TEAMMATE)
    assert "view_task" in policy


def test_leader_tool_guide_lists_management_tools():
    guide = leader_tool_guide()
    assert "task_manager" in guide
    assert "send_message" in guide


def test_teammate_tool_guide_lists_execution_tools():
    guide = teammate_tool_guide()
    assert "claim_task" in guide
    assert "complete_task" in guide
    assert "send_message" in guide


def test_build_system_prompt_includes_all_parts():
    prompt = build_system_prompt(
        role=TeamRole.LEADER,
        persona="PM Expert",
        domain="project_management",
    )
    assert "PM Expert" in prompt
    assert "project_management" in prompt
    assert "task_manager" in prompt
