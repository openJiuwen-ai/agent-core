# coding: utf-8

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from openjiuwen.agent_teams import paths as apaths
from openjiuwen.agent_teams.team_workspace.dir_links import is_dir_link
from openjiuwen.agent_teams.team_workspace.migrator import TeamWorkspaceMigrator
from openjiuwen.agent_teams.team_workspace.paths import (
    MEMBER_MODE_DYNAMIC,
    member_real_dir,
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path) -> Iterator[None]:
    apaths.configure_openjiuwen_home(tmp_path / "oj-home")
    yield
    apaths.reset_openjiuwen_home()


def _legacy_dir(team: str, member: str) -> Path:
    """Legacy layout: real dir inside the team at the link position."""
    return apaths.team_member_workspace_dir(team, member)


@pytest.mark.level0
def test_migrates_dynamic_legacy_dir() -> None:
    legacy = _legacy_dir("teamA", "memX")
    legacy.mkdir(parents=True)
    (legacy / "artifact.txt").write_text("keep", encoding="utf-8")
    moved = TeamWorkspaceMigrator().migrate("teamA")
    assert moved is True
    link = apaths.team_member_workspace_dir("teamA", "memX")
    assert is_dir_link(link), "legacy position is now a link"
    real = member_real_dir("teamA", "memX", MEMBER_MODE_DYNAMIC)
    assert real.is_dir() and (real / "artifact.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.level0
def test_leader_never_migrated() -> None:
    leader = _legacy_dir("teamA", "leader")
    leader.mkdir(parents=True)
    moved = TeamWorkspaceMigrator().migrate("teamA", leader_member_name="leader")
    assert moved is False
    assert leader.is_dir() and not is_dir_link(leader)


@pytest.mark.level0
def test_predefined_member_moves_to_independent() -> None:
    legacy = _legacy_dir("teamA", "shared")
    legacy.mkdir(parents=True)
    moved = TeamWorkspaceMigrator().migrate("teamA", predefined_members={"shared"})
    assert moved is True
    assert is_dir_link(apaths.team_member_workspace_dir("teamA", "shared"))
    assert (apaths.get_agent_teams_home() / "shared").is_dir()


@pytest.mark.level0
def test_migrate_idempotent() -> None:
    legacy = _legacy_dir("teamA", "memX")
    legacy.mkdir(parents=True)
    mig = TeamWorkspaceMigrator()
    assert mig.migrate("teamA") is True
    assert mig.migrate("teamA") is False, "second run moves nothing"


@pytest.mark.level0
def test_migrate_skips_non_roster_dirs() -> None:
    legacy = _legacy_dir("teamA", "leftover")
    legacy.mkdir(parents=True)
    moved = TeamWorkspaceMigrator().migrate("teamA", persistent_members={"leader", "memX"})
    assert moved is False
    assert legacy.is_dir(), "worker leftover kept in place, not migrated"


@pytest.mark.level0
def test_migrate_rolls_back_when_link_fails(monkeypatch) -> None:
    legacy = _legacy_dir("teamA", "memX")
    legacy.mkdir(parents=True)

    def fake_create(*args, **kwargs):
        raise OSError(13, "permission denied")

    monkeypatch.setattr(
        "openjiuwen.agent_teams.team_workspace.migrator.create_dir_link", fake_create
    )
    moved = TeamWorkspaceMigrator().migrate("teamA")
    assert moved is False
    assert legacy.is_dir(), "directory rolled back into the team tree on link failure"
    assert not member_real_dir("teamA", "memX", MEMBER_MODE_DYNAMIC).exists()


@pytest.mark.level0
def test_migrate_honors_prefix_off() -> None:
    """prefix=False teams migrate dynamic legacy dirs to ``.agent_teams/<m>``."""
    legacy = _legacy_dir("teamA", "memX")
    legacy.mkdir(parents=True)
    moved = TeamWorkspaceMigrator().migrate("teamA", member_workspace_prefix=False)
    assert moved is True
    real = member_real_dir(
        "teamA", "memX", MEMBER_MODE_DYNAMIC, member_workspace_prefix=False
    )
    assert real.is_dir()
    assert not member_real_dir("teamA", "memX", MEMBER_MODE_DYNAMIC).exists()


@pytest.mark.level0
def test_migrate_reuses_existing_shared_target() -> None:
    """Target already migrated by another team → drop legacy residue, reuse."""
    shared = apaths.get_agent_teams_home() / "shared"
    shared.mkdir(parents=True)
    (shared / "artifact.txt").write_text("live", encoding="utf-8")
    legacy = _legacy_dir("teamA", "shared")
    legacy.mkdir(parents=True)
    moved = TeamWorkspaceMigrator().migrate("teamA", predefined_members={"shared"})
    assert moved is True
    assert is_dir_link(apaths.team_member_workspace_dir("teamA", "shared"))
    assert (shared / "artifact.txt").read_text(encoding="utf-8") == "live"
    assert is_dir_link(legacy), "stale in-team real dir replaced by a transparent link"


@pytest.mark.level0
def test_migrate_reuse_retreats_when_link_fails(monkeypatch) -> None:
    """Existing shared target + link failure → in-team real dir, no crash."""
    shared = apaths.get_agent_teams_home() / "shared"
    shared.mkdir(parents=True)
    legacy = _legacy_dir("teamA", "shared")
    legacy.mkdir(parents=True)

    def fake_create(*_args, **_kwargs):
        raise OSError(13, "permission denied")

    monkeypatch.setattr(
        "openjiuwen.agent_teams.team_workspace.migrator.create_dir_link", fake_create
    )
    moved = TeamWorkspaceMigrator().migrate("teamA", predefined_members={"shared"})
    assert moved is False
    assert legacy.is_dir(), "retreat re-creates the in-team real dir"
    assert shared.is_dir(), "shared dir untouched"
