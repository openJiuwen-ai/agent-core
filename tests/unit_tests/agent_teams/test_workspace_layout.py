# coding: utf-8

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from openjiuwen.agent_teams.workspace_layout import (
    ensure_member_skill_copy,
    ensure_team_member_workspace_link,
)


@pytest.mark.level0
def test_ensure_team_member_workspace_link_keeps_independent_workspace_without_symlinks(
    monkeypatch,
    tmp_path: Path,
):
    independent_workspace = tmp_path / "independent"
    independent_workspace.mkdir()
    (independent_workspace / "README.md").write_text("hello", encoding="utf-8")
    team_workspace = tmp_path / "team-home" / "workspaces" / "alice_workspace"

    monkeypatch.setattr(
        "openjiuwen.agent_teams.workspace_layout.independent_member_workspace",
        lambda member_name: independent_workspace,
    )
    monkeypatch.setattr(
        "openjiuwen.agent_teams.workspace_layout.team_member_workspace_path",
        lambda team_name, member_name: team_workspace,
    )

    def fake_symlink(*args, **kwargs):
        error = OSError("operation not permitted")
        error.errno = errno.EPERM
        raise error

    monkeypatch.setattr(os, "symlink", fake_symlink)

    resolved = ensure_team_member_workspace_link("team-alpha", "alice")

    # No copytree of the whole workspace into the team tree: the member simply
    # keeps running where it already lives.
    assert Path(resolved) == independent_workspace
    assert not team_workspace.exists()


@pytest.mark.level0
def test_ensure_team_member_workspace_link_reraises_non_permission_symlink_error(monkeypatch, tmp_path: Path):
    independent_workspace = tmp_path / "independent"
    independent_workspace.mkdir()
    team_workspace = tmp_path / "team-home" / "workspaces" / "alice_workspace"

    monkeypatch.setattr(
        "openjiuwen.agent_teams.workspace_layout.independent_member_workspace",
        lambda member_name: independent_workspace,
    )
    monkeypatch.setattr(
        "openjiuwen.agent_teams.workspace_layout.team_member_workspace_path",
        lambda team_name, member_name: team_workspace,
    )

    def fake_symlink(*args, **kwargs):
        error = OSError("bad target")
        error.errno = errno.ENOENT
        raise error

    monkeypatch.setattr(os, "symlink", fake_symlink)

    with pytest.raises(OSError, match="bad target"):
        ensure_team_member_workspace_link("team-alpha", "alice")


@pytest.mark.level0
def test_ensure_member_skill_copy_replaces_global_link_and_preserves_private_copy(tmp_path: Path):
    global_skills = tmp_path / "global"
    member_skills = tmp_path / "member"
    source = global_skills / "xlsx"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# global\n", encoding="utf-8")
    member_skills.mkdir()
    (member_skills / "xlsx").symlink_to(source, target_is_directory=True)

    copied = ensure_member_skill_copy(
        member_skills_dir=member_skills,
        global_skills_dir=global_skills,
        skill_name="xlsx",
    )

    assert copied.is_dir()
    assert not copied.is_symlink()
    assert (copied / "SKILL.md").read_text(encoding="utf-8") == "# global\n"

    (copied / "SKILL.md").write_text("# private\n", encoding="utf-8")
    assert ensure_member_skill_copy(
        member_skills_dir=member_skills,
        global_skills_dir=global_skills,
        skill_name="xlsx",
    ) == copied
    assert (copied / "SKILL.md").read_text(encoding="utf-8") == "# private\n"
    assert (source / "SKILL.md").read_text(encoding="utf-8") == "# global\n"


@pytest.mark.level0
@pytest.mark.parametrize("skill_name", ["../xlsx", "xlsx/path", "", "."])
def test_ensure_member_skill_copy_rejects_unsafe_names(tmp_path: Path, skill_name: str):
    with pytest.raises(ValueError, match="unsafe Skill name"):
        ensure_member_skill_copy(
            member_skills_dir=tmp_path / "member",
            global_skills_dir=tmp_path / "global",
            skill_name=skill_name,
        )
