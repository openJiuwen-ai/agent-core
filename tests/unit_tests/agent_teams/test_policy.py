# coding: utf-8
"""Tests for role policy markdown (leader_policy / teammate_policy templates)."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.prompts import load_template
from openjiuwen.agent_teams.agent.agent_configurator import (
    _TEAM_WORKTREE_BASH_DENY_PATTERNS,
    _apply_team_worktree_shell_guard,
    _has_team_worktree_shell_guard,
)
from openjiuwen.agent_teams.rails.builtin_elements import SYS_OPERATION
from openjiuwen.agent_teams.schema.deep_agent_spec import RailSpec


def _leader_policy(language: str = "cn") -> str:
    return load_template("leader_policy", language).content


def _teammate_policy(language: str = "cn") -> str:
    return load_template("teammate_policy", language).content


@pytest.mark.level0
def test_leader_policy_mentions_key_responsibilities():
    policy = _leader_policy()
    assert "DAG" in policy
    assert "create_task" in policy


@pytest.mark.level0
def test_all_done_prompts_carry_full_presentation_spec():
    """all-done 提示词内嵌最终呈现规范（不再引用 leader_policy 节）+
    已交付则原样重发的兜底。"""
    from openjiuwen.agent_teams import i18n

    original = i18n.get_language()
    try:
        i18n.set_language("cn")
        for key in ("dispatcher.all_done_persistent", "dispatcher.all_done_temporary"):
            text = i18n.t(key)
            assert "最终呈现" in text
            assert "复述关键结果" in text
            assert "原样重发一遍作为最终答复" in text
        i18n.set_language("en")
        for key in ("dispatcher.all_done_persistent", "dispatcher.all_done_temporary"):
            text = i18n.t(key)
            assert "final presentation" in text
            assert "restate key results" in text
            assert "re-send that complete answer verbatim" in text
    finally:
        i18n.set_language(original)


@pytest.mark.level1
def test_leader_policy_forbids_manual_worktree_creation():
    policy = _leader_policy("en")
    assert "do not run `git worktree add`" in policy
    assert "do not create `.worktrees/` under the project" in policy


@pytest.mark.level1
def test_teammate_policy_mentions_task_workflow():
    policy = _teammate_policy()
    assert "view_task" in policy


@pytest.mark.level1
def test_teammate_policy_forbids_manual_worktree_creation():
    policy = _teammate_policy("en")
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
def test_leader_policy_carries_collaboration_mechanism_boundary_cn():
    policy = _leader_policy("cn")

    # Leader must see the swarmflow vs build_team routing boundary.
    assert "协作机制选择" in policy
    assert "涌现式" in policy
    assert "swarmflow" in policy
    # Concrete anti-pattern anchor: fixed-count sequential tasks stay on swarmflow.
    assert "顺序接力" in policy
    assert "固定结束条件" in policy


@pytest.mark.level1
def test_leader_policy_carries_collaboration_mechanism_boundary_en():
    policy = _leader_policy("en")

    assert "Collaboration Mechanism" in policy
    assert "emergent" in policy
    assert "swarmflow" in policy
    assert "sequential relay" in policy
    assert "fixed end condition" in policy


@pytest.mark.level1
def test_teammate_policy_omits_leader_collaboration_boundary():
    policy = _teammate_policy("cn")

    # The routing boundary is a leader-only concern; it must not leak to teammates.
    assert "协作机制选择" not in policy
    assert "涌现式" not in policy
    assert "顺序接力" not in policy
