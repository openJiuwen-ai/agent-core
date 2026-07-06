# coding: utf-8

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from openjiuwen.agent_teams.workspace_layout import ensure_team_member_workspace_link


@pytest.mark.level0
def test_ensure_team_member_workspace_link_falls_back_to_copy(monkeypatch, tmp_path: Path):
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

    assert Path(resolved) == team_workspace
    assert team_workspace.is_dir()
    assert not team_workspace.is_symlink()
    assert (team_workspace / "README.md").read_text(encoding="utf-8") == "hello"


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
