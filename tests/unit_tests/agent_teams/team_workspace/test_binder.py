# coding: utf-8

from __future__ import annotations

import errno
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from openjiuwen.agent_teams import paths as apaths
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_workspace import (
    MEMBER_MODE_DYNAMIC,
    MEMBER_MODE_LEADER,
    MEMBER_MODE_PREDEFINED,
    MemberWorkspaceBinder,
    TeamMemberBinding,
    prepare_member_workspace,
)
from openjiuwen.agent_teams.team_workspace.dir_links import is_dir_link
from openjiuwen.agent_teams.team_workspace.paths import member_real_dir
from openjiuwen.agent_teams.team_workspace.ref_store import MemberRefStore


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path) -> Iterator[None]:
    apaths.configure_openjiuwen_home(tmp_path / "oj-home")
    yield
    apaths.reset_openjiuwen_home()


def _binding(team: str, member: str, mode: str) -> TeamMemberBinding:
    return TeamMemberBinding(team_name=team, member_name=member, mode=mode)


@pytest.mark.level0
def test_leader_stays_in_team_no_link() -> None:
    root = MemberWorkspaceBinder().setup(_binding("teamA", "leader", MEMBER_MODE_LEADER))
    assert root == apaths.team_member_workspace_dir("teamA", "leader")
    assert root.is_dir()
    assert not is_dir_link(root)


@pytest.mark.level0
def test_dynamic_creates_link_and_refs() -> None:
    binder = MemberWorkspaceBinder()
    root = binder.setup(_binding("teamA", "memX", MEMBER_MODE_DYNAMIC))
    assert root == apaths.team_member_workspace_dir("teamA", "memX")
    assert is_dir_link(root)
    real = member_real_dir("teamA", "memX", MEMBER_MODE_DYNAMIC)
    assert real.is_dir()
    assert MemberRefStore().get_ref_count("teamA", "memX") == 1


@pytest.mark.level0
def test_predefined_creates_link_to_independent() -> None:
    binder = MemberWorkspaceBinder()
    root = binder.setup(_binding("teamA", "shared", MEMBER_MODE_PREDEFINED))
    assert is_dir_link(root)
    assert apaths.independent_member_workspace("shared").is_dir()
    refs = MemberRefStore().get_ref_teams("teamA", "shared", mode=MEMBER_MODE_PREDEFINED)
    assert refs == ["teamA"]


@pytest.mark.level0
def test_setup_idempotent() -> None:
    binder = MemberWorkspaceBinder()
    binding = _binding("teamA", "memX", MEMBER_MODE_DYNAMIC)
    binder.setup(binding)
    first = member_real_dir("teamA", "memX", MEMBER_MODE_DYNAMIC)
    binder.setup(binding)
    assert MemberRefStore().get_ref_count("teamA", "memX") == 1
    assert first.is_dir()


@pytest.mark.level0
def test_link_failure_retreats_into_team(monkeypatch) -> None:
    """EACCES on create_dir_link → real dir is created in-team (v3 R2)."""
    monkeypatch.setattr(os, "name", "posix")

    def fake_create(*args, **kwargs):
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(
        "openjiuwen.agent_teams.team_workspace.binder.create_dir_link", fake_create
    )
    root = MemberWorkspaceBinder().setup(_binding("teamA", "memX", MEMBER_MODE_DYNAMIC))
    assert root == apaths.team_member_workspace_dir("teamA", "memX")
    assert root.is_dir()
    assert not is_dir_link(root), "retreat creates a real in-team directory"
    # No cross-team shared dir was created outside the team tree.
    assert not member_real_dir("teamA", "memX", MEMBER_MODE_DYNAMIC).exists()


@pytest.mark.level0
def test_cleanup_team_links_unlinks_only() -> None:
    binder = MemberWorkspaceBinder()
    binder.setup(_binding("teamA", "shared", MEMBER_MODE_PREDEFINED))
    indep = apaths.independent_member_workspace("shared")
    binder.cleanup_team_links("teamA")
    assert not is_dir_link(apaths.team_member_workspace_dir("teamA", "shared"))
    assert indep.is_dir(), "shared asset preserved"


@pytest.mark.level0
def test_release_and_delete_if_zero_per_mode() -> None:
    binder = MemberWorkspaceBinder()
    binder.setup(_binding("teamA", "shared", MEMBER_MODE_PREDEFINED))
    binder.setup(_binding("teamB", "shared", MEMBER_MODE_PREDEFINED))
    assert binder.release("teamA", "shared", mode=MEMBER_MODE_PREDEFINED) is False
    assert binder.release("teamB", "shared", mode=MEMBER_MODE_PREDEFINED) is True
    assert not binder.delete_if_zero("teamB", "shared", mode=MEMBER_MODE_PREDEFINED)

    binder.setup(_binding("teamA", "worker", MEMBER_MODE_DYNAMIC))
    assert binder.release("teamA", "worker") is True
    assert binder.delete_if_zero("teamA", "worker") is True
    assert not member_real_dir("teamA", "worker", MEMBER_MODE_DYNAMIC).exists()


@pytest.mark.level0
def test_prepare_member_workspace_classifies_modes() -> None:
    team = "teamA"
    assert (
        prepare_member_workspace(
            team_name=team,
            member_name="leader",
            role=TeamRole.LEADER,
            leader_member_name="leader",
            predefined_members={"shared"},
        )
        == str(apaths.team_member_workspace_dir(team, "leader"))
    )
    assert (
        prepare_member_workspace(
            team_name=team,
            member_name="shared",
            role=TeamRole.TEAMMATE,
            leader_member_name="leader",
            predefined_members={"shared"},
        )
        == str(apaths.team_member_workspace_dir(team, "shared"))
    )
    assert (
        prepare_member_workspace(
            team_name=team,
            member_name="worker",
            role=TeamRole.TEAMMATE,
            leader_member_name="leader",
            predefined_members={"shared"},
        )
        == str(apaths.team_member_workspace_dir(team, "worker"))
    )
