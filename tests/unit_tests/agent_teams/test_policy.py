# coding: utf-8
"""Tests for role policy and system prompt construction."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.prompts import (
    build_system_prompt,
    role_policy,
)
from openjiuwen.agent_teams.agent.agent_configurator import (
    _TEAM_WORKTREE_BASH_DENY_PATTERNS,
    _apply_team_worktree_shell_guard,
    _has_team_worktree_shell_guard,
)
from openjiuwen.agent_teams.rails.builtin_elements import SYS_OPERATION
from openjiuwen.agent_teams.schema.deep_agent_spec import RailSpec
from openjiuwen.agent_teams.schema.team import TeamRole


@pytest.mark.level0
def test_leader_policy_mentions_key_responsibilities():
    policy = role_policy(TeamRole.LEADER)
    assert "DAG" in policy
    assert "create_task" in policy


@pytest.mark.level1
def test_leader_policy_forbids_manual_worktree_creation():
    policy = role_policy(TeamRole.LEADER, language="en")
    assert "do not run `git worktree add`" in policy
    assert "do not create `.worktrees/` under the project" in policy


@pytest.mark.level1
def test_teammate_policy_mentions_task_workflow():
    policy = role_policy(TeamRole.TEAMMATE)
    assert "view_task" in policy


@pytest.mark.level1
def test_teammate_policy_forbids_manual_worktree_creation():
    policy = role_policy(TeamRole.TEAMMATE, language="en")
    assert "Do not run `git worktree add`" in policy
    assert "do not create an extra review worktree" in policy


@pytest.mark.level1
def test_team_worktree_shell_guard_is_added_when_sys_operation_absent():
    rails = _apply_team_worktree_shell_guard([], enabled=True)

    assert _has_team_worktree_shell_guard(rails)
    sys_operation_rails = [rail for rail in rails if rail.type == SYS_OPERATION]
    assert len(sys_operation_rails) == 1
    assert sys_operation_rails[0].params["bash_deny_patterns"] == _TEAM_WORKTREE_BASH_DENY_PATTERNS


@pytest.mark.level1
def test_team_worktree_shell_guard_merges_existing_sys_operation():
    rails = _apply_team_worktree_shell_guard(
        [RailSpec(type=SYS_OPERATION, params={"bash_deny_patterns": ["existing"]})],
        enabled=True,
    )

    assert _has_team_worktree_shell_guard(rails)
    assert rails[0].params["bash_deny_patterns"][0] == "existing"


@pytest.mark.level1
def test_build_system_prompt_includes_all_parts():
    prompt = build_system_prompt(
        role=TeamRole.LEADER,
    )
    assert "create_task" in prompt


@pytest.mark.level1
def test_leader_prompt_carries_collaboration_mechanism_boundary_cn():
    prompt = build_system_prompt(
        role=TeamRole.LEADER,
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
        language="cn",
    )

    # The routing boundary is a leader-only concern; it must not leak to teammates.
    assert "协作机制选择" not in prompt
    assert "涌现式" not in prompt
    assert "顺序接力" not in prompt
