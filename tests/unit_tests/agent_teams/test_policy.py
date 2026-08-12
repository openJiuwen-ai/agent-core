# coding: utf-8
"""Tests for role policy markdown (leader_policy / teammate_policy templates)."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.prompts import (
    build_leader_bootstrap_section,
    build_leader_policy_disclosure,
    load_template,
)
from openjiuwen.agent_teams.agent.agent_configurator import (
    _TEAM_WORKTREE_BASH_DENY_PATTERNS,
    _apply_team_worktree_shell_guard,
    _has_team_worktree_shell_guard,
)
from openjiuwen.agent_teams.rails.builtin_elements import SYS_OPERATION
from openjiuwen.agent_teams.schema.deep_agent_spec import RailSpec
from openjiuwen.agent_teams.schema.team import TeamRole


def _leader_policy(language: str = "cn") -> str:
    """Render the leader policy the way a leader actually receives it.

    Since F_76 that is the ``build_team`` tool result, not the system prompt.
    """
    return build_leader_policy_disclosure(language=language)


def _leader_bootstrap(language: str = "cn", swarmflow_enabled: bool = True) -> str:
    """Render the leader's system-prompt section — the only one it gets."""
    section = build_leader_bootstrap_section(
        swarmflow_enabled=swarmflow_enabled,
        language=language,
    )
    return section.content[language]


def _teammate_policy(language: str = "cn") -> str:
    return load_template("teammate_policy", language).content


@pytest.mark.level0
def test_leader_policy_mentions_key_responsibilities():
    policy = _leader_policy()
    assert "DAG" in policy
    assert "create_task" in policy


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
def test_leader_bootstrap_carries_collaboration_mechanism_boundary_cn():
    bootstrap = _leader_bootstrap("cn")

    # The routing boundary is the one thing the leader must know *before*
    # it builds anything, so it rides the system prompt, not the disclosure.
    assert "协作机制选择" in bootstrap
    assert "涌现式" in bootstrap
    assert "swarmflow" in bootstrap
    # Concrete anti-pattern anchor: fixed-count sequential tasks stay on swarmflow.
    assert "顺序接力" in bootstrap
    assert "固定结束条件" in bootstrap


@pytest.mark.level1
def test_leader_bootstrap_carries_collaboration_mechanism_boundary_en():
    bootstrap = _leader_bootstrap("en")

    assert "Collaboration Mechanism" in bootstrap
    assert "emergent" in bootstrap
    assert "swarmflow" in bootstrap
    assert "sequential relay" in bootstrap
    assert "fixed end condition" in bootstrap


@pytest.mark.level1
def test_leader_bootstrap_hides_swarmflow_when_tool_is_gated_out_cn():
    bootstrap = _leader_bootstrap("cn", swarmflow_enabled=False)

    # Without the tool, the mechanism-choice guide must vanish entirely —
    # otherwise the leader deliberates over a mechanism it cannot invoke.
    assert "swarmflow" not in bootstrap.lower()
    assert "协作机制选择" not in bootstrap
    # The build_team instruction itself is untouched.
    assert "build_team" in bootstrap
    # And the policy it routes to is unaffected by the swarmflow gate.
    assert "核心职责" in _leader_policy("cn")
    assert "create_task" in _leader_policy("cn")


@pytest.mark.level1
def test_leader_bootstrap_hides_swarmflow_when_tool_is_gated_out_en():
    bootstrap = _leader_bootstrap("en", swarmflow_enabled=False)

    assert "swarmflow" not in bootstrap.lower()
    assert "Collaboration Mechanism" not in bootstrap
    assert "build_team" in bootstrap
    assert "Core Responsibilities" in _leader_policy("en")


@pytest.mark.level1
def test_leader_policy_is_disclosed_only_after_build_team():
    """The leader's prefix must not leak the policy it discloses later.

    The bootstrap may *name* what is coming ("core responsibilities, hand-off,
    ...") — that is how the leader knows to expect it. What it must not carry
    is the policy body itself, so the anchor is the section heading.
    """
    for language in ("cn", "en"):
        bootstrap = _leader_bootstrap(language)
        policy = _leader_policy(language)
        # Collaboration conventions live in the disclosure, never the prefix.
        marker = "## 核心职责" if language == "cn" else "## Core Responsibilities"
        assert marker in policy
        assert marker not in bootstrap
        # And the disclosure never repeats the routing guide.
        routing = "协作机制选择" if language == "cn" else "Collaboration Mechanism"
        assert routing not in policy


@pytest.mark.level1
def test_leader_workflow_never_mentions_swarmflow():
    # The workflow template is unconditional, so it must stay swarmflow-free;
    # the mechanism choice belongs to the gated leader_swarmflow section.
    for language in ("cn", "en"):
        for name in ("leader_workflow", "leader_workflow_predefined", "leader_workflow_hybrid"):
            assert "swarmflow" not in load_template(name, language).content.lower()


@pytest.mark.level1
def test_teammate_policy_omits_leader_collaboration_boundary():
    policy = _teammate_policy("cn")

    # The routing boundary is a leader-only concern; it must not leak to teammates.
    assert "协作机制选择" not in policy
    assert "涌现式" not in policy
    assert "顺序接力" not in policy
