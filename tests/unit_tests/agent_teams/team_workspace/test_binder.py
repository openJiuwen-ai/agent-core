# coding: utf-8

from __future__ import annotations

import errno
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
from openjiuwen.agent_teams.skill.file_lock import lock_path_for
from openjiuwen.agent_teams.team_workspace.dir_links import is_dir_link
from openjiuwen.agent_teams.team_workspace.paths import member_real_dir
from openjiuwen.agent_teams.team_workspace.ref_store import REFS_FILE_NAME, MemberRefStore


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
    assert (apaths.get_agent_teams_home() / "shared").is_dir()
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
    """EACCES on create_dir_link → real dir is created in-team (v3 R2).

    ``create_dir_link`` is fully patched here, so ``os.name`` is untouched:
    monkeypatching it to "posix" would make ``pathlib.Path`` try to build a
    ``PosixPath`` on Windows and crash pytest's failure rendering.
    """
    def fake_create(*_args, **_kwargs):
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
    # leader (by role or name) → in-team real dir, no link.
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
    assert not is_dir_link(apaths.team_member_workspace_dir(team, "leader"))
    # predefined → shared independent workspace + link.
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
    assert is_dir_link(apaths.team_member_workspace_dir(team, "shared"))
    # teammate → dynamic real dir + link out of the team tree.
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
    assert is_dir_link(apaths.team_member_workspace_dir(team, "worker"))
    # human_agent → dynamic real dir + link out of the team tree.
    assert (
        prepare_member_workspace(
            team_name=team,
            member_name="human-1",
            role=TeamRole.HUMAN_AGENT,
            leader_member_name="leader",
            predefined_members={"shared"},
        )
        == str(apaths.team_member_workspace_dir(team, "human-1"))
    )
    assert is_dir_link(apaths.team_member_workspace_dir(team, "human-1"))
    # external_cli → stays in-team (role whitelist fallback), no link out.
    ext_root = apaths.team_member_workspace_dir(team, "claude-1")
    assert (
        prepare_member_workspace(
            team_name=team,
            member_name="claude-1",
            role=TeamRole.EXTERNAL_CLI,
            leader_member_name="leader",
            predefined_members={"shared"},
        )
        == str(ext_root)
    )
    assert not is_dir_link(ext_root), "external CLI member stays in-team, no link"


@pytest.mark.level0
def test_external_cli_member_not_linked_out_by_others(tmp_path: Path) -> None:
    """A teammate's configure pass must not link out an external CLI member's
    in-team workspace. Regression for the role-whitelist change: the old
    full-team migrator scan treated every non-leader/non-predefined member as
    dynamic and linked it out; the whitelist keeps EXTERNAL_CLI in-team.
    """
    team = "teamB"
    # Pre-create the external CLI member's in-team real dir + content, as the
    # A-block _prepare_external_cli_workspace path would.
    ext_dir = apaths.team_member_workspace_dir(team, "claude-1")
    ext_dir.mkdir(parents=True)
    (ext_dir / "cli_marker.txt").write_text("in-team", encoding="utf-8")
    # A plain teammate is configured afterwards — it must not touch claude-1.
    prepare_member_workspace(
        team_name=team,
        member_name="mate",
        role=TeamRole.TEAMMATE,
        leader_member_name="leader",
        predefined_members=set(),
    )
    assert not is_dir_link(ext_dir), "external CLI dir not linked out by mate's configure"
    assert (ext_dir / "cli_marker.txt").read_text(encoding="utf-8") == "in-team"
    assert not member_real_dir(team, "claude-1", MEMBER_MODE_DYNAMIC).exists(), (
        "no team-external real dir created for the external CLI member"
    )


@pytest.mark.level0
def test_cleanup_team_releases_dynamic_preserves_predefined() -> None:
    binder = MemberWorkspaceBinder()
    binder.setup(_binding("teamA", "shared", MEMBER_MODE_PREDEFINED))
    binder.setup(_binding("teamA", "worker", MEMBER_MODE_DYNAMIC))
    shared_real = apaths.get_agent_teams_home() / "shared"
    worker_real = member_real_dir("teamA", "worker", MEMBER_MODE_DYNAMIC)

    binder.cleanup_team("teamA")

    assert not worker_real.exists(), "dynamic real dir removed on zero"
    assert not is_dir_link(apaths.team_member_workspace_dir("teamA", "worker"))
    assert shared_real.is_dir(), "predefined shared dir preserved"
    assert not is_dir_link(apaths.team_member_workspace_dir("teamA", "shared"))
    # The disbanded team must drop its name from the predefined ref list too;
    # leaving it would leak a reference to a team that no longer exists.
    assert MemberRefStore().get_ref_teams("teamA", "shared", mode=MEMBER_MODE_PREDEFINED) == [], (
        "predefined ref list must drop the disbanded team"
    )


@pytest.mark.level0
def test_cleanup_team_drops_only_the_disbanded_team_from_shared_predefined() -> None:
    """A second team's reference survives; only the disbanded one is dropped."""
    binder = MemberWorkspaceBinder()
    # Same predefined member shared across two teams.
    binder.setup(_binding("teamA", "shared", MEMBER_MODE_PREDEFINED))
    binder.setup(_binding("teamB", "shared", MEMBER_MODE_PREDEFINED))
    shared_real = apaths.get_agent_teams_home() / "shared"

    binder.cleanup_team("teamA")

    assert shared_real.is_dir(), "predefined shared dir preserved across teams"
    # A predefined ref list is per-member (one .refs.json under the shared dir),
    # so either team_name resolves the same file: after disbanding teamA only
    # teamB's reference may remain.
    refs_after = MemberRefStore().get_ref_teams("teamA", "shared", mode=MEMBER_MODE_PREDEFINED)
    assert refs_after == ["teamB"], (
        f"disbanded teamA dropped, surviving teamB kept; got {refs_after}"
    )


# ── prefix=False cleanup + no-ref-for-linkless members ──────────────────────
#
# Two design invariants, each a regression target:
#   1. cleanup always starts from the in-team link and resolves the real dir
#      via the link — it never scans ``.agent_teams/`` by ``<team>#`` name
#      prefix. So ``member_workspace_prefix=False`` (real dir = ``<member>``
#      with no ``#``) must still be recycled on team delete.
#   2. A ``.refs.json`` only belongs to a member whose real dir is linked out
#      of the team tree and shared across teams — i.e. dynamic + predefined.
#      leader / external_cli real dirs live in-team, never link out, and are
#      never shared, so they carry no refs file.


def _binding_prefix_off(team: str, member: str, mode: str) -> TeamMemberBinding:
    return TeamMemberBinding(
        team_name=team,
        member_name=member,
        mode=mode,
        member_workspace_prefix=False,
    )


@pytest.mark.level0
def test_cleanup_team_recycles_prefix_off_dynamic_via_link() -> None:
    """prefix=False dynamic real dir (``.agent_teams/<member>``, no ``#``)
    must be recycled when the team is deleted.

    The old cleanup scanned ``.agent_teams/`` for ``<team>#`` prefixed dirs,
    so a prefix-off dir was invisible and leaked. The new cleanup resolves the
    real dir from the in-team link, so the prefix never matters.
    """
    binder = MemberWorkspaceBinder()
    binder.setup(_binding_prefix_off("teamA", "worker", MEMBER_MODE_DYNAMIC))
    real = member_real_dir(
        "teamA", "worker", MEMBER_MODE_DYNAMIC, member_workspace_prefix=False
    )
    assert real.is_dir(), "precondition: real dir created"

    binder.cleanup_team("teamA")

    assert not real.exists(), "prefix-off dynamic real dir recycled on team delete"
    assert not is_dir_link(
        apaths.team_member_workspace_dir("teamA", "worker")
    ), "member link removed"
    assert (
        MemberRefStore().get_ref_count(
            "teamA", "worker", member_workspace_prefix=False
        )
        == 0
    ), "refs cleared"


@pytest.mark.level0
def test_cleanup_team_prefix_off_preserves_other_team_ref() -> None:
    """Two teams sharing a prefix-off dynamic member dir: deleting one team
    drops only its ref; the other team's reference survives and the shared
    real dir is kept."""
    binder = MemberWorkspaceBinder()
    binder.setup(_binding_prefix_off("teamA", "shared-mem", MEMBER_MODE_DYNAMIC))
    binder.setup(_binding_prefix_off("teamB", "shared-mem", MEMBER_MODE_DYNAMIC))
    real = member_real_dir(
        "teamA", "shared-mem", MEMBER_MODE_DYNAMIC, member_workspace_prefix=False
    )

    binder.cleanup_team("teamA")

    assert real.is_dir(), "shared real dir kept while teamB still references it"
    assert MemberRefStore().get_ref_teams(
        "teamB", "shared-mem", member_workspace_prefix=False
    ) == ["teamB"], "surviving teamB ref intact"


@pytest.mark.level0
def test_leader_and_external_cli_have_no_refs() -> None:
    """leader / external_cli real dirs are in-team, never linked out, never
    shared across teams — so they must not carry a ``.refs.json``."""
    binder = MemberWorkspaceBinder()
    # leader
    binder.setup(_binding("teamA", "leader", MEMBER_MODE_LEADER))
    leader_real = apaths.team_member_workspace_dir("teamA", "leader")
    assert not (leader_real / ".refs.json").exists(), "leader has no refs file"
    # external_cli lands on MEMBER_MODE_LEADER (role-whitelist fallback).
    prepare_member_workspace(
        team_name="teamA",
        member_name="claude-1",
        role=TeamRole.EXTERNAL_CLI,
        leader_member_name="leader",
        predefined_members=set(),
    )
    ext_real = apaths.team_member_workspace_dir("teamA", "claude-1")
    assert not (ext_real / ".refs.json").exists(), "external_cli has no refs file"


@pytest.mark.level0
def test_cleanup_team_prefix_off_predefined_preserves_shared_dir() -> None:
    """prefix=False does not change predefined semantics: the shared indep
    dir is kept on team delete (shared-asset), only the link is removed and
    the disbanded team is dropped from the ref list."""
    binder = MemberWorkspaceBinder()
    binder.setup(_binding_prefix_off("teamA", "shared", MEMBER_MODE_PREDEFINED))
    indep = apaths.get_agent_teams_home() / "shared"

    binder.cleanup_team("teamA")

    assert indep.is_dir(), "predefined shared dir preserved"
    assert not is_dir_link(
        apaths.team_member_workspace_dir("teamA", "shared")
    ), "predefined link removed"
    assert MemberRefStore().get_ref_teams(
        "teamA", "shared", mode=MEMBER_MODE_PREDEFINED
    ) == [], "disbanded team dropped from predefined ref list"


@pytest.mark.level0
def test_cleanup_team_reclaims_dir_and_lock_sidecar_on_last_ref() -> None:
    """Regresses a lock-sidecar leak on a prefix-off dynamic member shared by
    two teams: after both teams are cleaned up, the real dir AND the
    ``..refs.json.lock`` sidecar must both be gone.

    The rmtree of the real dir reclaims the lock sidecar naturally (it is a
    child of the real dir); this test pins that the normal last-ref path does
    not leave the sidecar behind.
    """
    binder = MemberWorkspaceBinder()
    binder.setup(_binding_prefix_off("teamA", "worker", MEMBER_MODE_DYNAMIC))
    binder.setup(_binding_prefix_off("teamB", "worker", MEMBER_MODE_DYNAMIC))
    real = member_real_dir(
        "teamA", "worker", MEMBER_MODE_DYNAMIC, member_workspace_prefix=False
    )
    lock_sidecar = lock_path_for(real / REFS_FILE_NAME)
    assert lock_sidecar.exists(), "precondition: lock sidecar created on setup"

    binder.cleanup_team("teamA")
    assert real.is_dir(), "dir kept while teamB still references it"
    binder.cleanup_team("teamB")

    assert not real.exists(), "real dir reclaimed after last team ref released"
    assert not lock_sidecar.exists(), "lock sidecar removed with the real dir"
    assert not is_dir_link(
        apaths.team_member_workspace_dir("teamB", "worker")
    ), "teamB link removed"


@pytest.mark.level0
def test_cleanup_team_drops_lock_sidecar_for_predefined_without_rmtree() -> None:
    """A predefined shared dir is preserved on team delete (shared-asset
    semantics), but its ``.refs.json`` is removed when the last team ref drops
    (``remove_ref`` on zero). The lock sidecar must be removed too — otherwise
    a 0-byte ``..refs.json.lock`` lingers next to a dir whose payload is gone.

    The rmtree path can't cover this (the dir is intentionally kept), so the
    sidecar has to be dropped explicitly when ``.refs.json`` is unlinked.
    """
    binder = MemberWorkspaceBinder()
    binder.setup(_binding_prefix_off("teamA", "shared", MEMBER_MODE_PREDEFINED))
    real = member_real_dir(
        "teamA", "shared", MEMBER_MODE_PREDEFINED, member_workspace_prefix=False
    )
    lock_sidecar = lock_path_for(real / REFS_FILE_NAME)
    assert lock_sidecar.exists(), "precondition: lock sidecar created on setup"

    binder.cleanup_team("teamA")

    assert real.is_dir(), "predefined shared dir preserved (not rmtree'd)"
    assert not (real / REFS_FILE_NAME).exists(), "predefined refs file dropped on zero"
    assert not lock_sidecar.exists(), "lock sidecar removed even when dir is kept"
