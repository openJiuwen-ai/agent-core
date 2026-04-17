# coding: utf-8

"""Tests for openjiuwen.agent_teams.team_workspace.manager."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.team_workspace.manager import (
    ERROR_PRIVILEGE_NOT_HELD,
    TeamWorkspaceManager,
)
from openjiuwen.agent_teams.team_workspace.models import TeamWorkspaceConfig


def _make_manager(tmp_path) -> TeamWorkspaceManager:
    workspace_path = tmp_path / "shared-workspace"
    workspace_path.mkdir()
    return TeamWorkspaceManager(
        config=TeamWorkspaceConfig(),
        workspace_path=str(workspace_path),
        team_name="team-alpha",
    )


def test_mount_into_workspace_uses_symlink(monkeypatch, tmp_path):
    manager = _make_manager(tmp_path)
    workspace_root = tmp_path / "agent-workspace"
    workspace_root.mkdir()
    calls = []

    def fake_symlink(target, link_name, target_is_directory=False):
        calls.append((target, link_name, target_is_directory))

    monkeypatch.setattr(os, "symlink", fake_symlink)

    manager.mount_into_workspace(str(workspace_root))

    expected_link = os.path.join(str(workspace_root), ".team", "team-alpha")
    assert calls == [(manager.workspace_path, expected_link, True)]


@pytest.mark.skip(reason="Temporarily skipped in Linux CI due Windows path simulation causing pytest internal errors")
def test_mount_into_workspace_falls_back_to_junction_on_windows_1314(monkeypatch, tmp_path):
    manager = _make_manager(tmp_path)
    workspace_root = tmp_path / "agent-workspace"
    workspace_root.mkdir()
    junction_calls = []

    def fake_symlink(*args, **kwargs):
        error = OSError("missing privilege")
        error.winerror = ERROR_PRIVILEGE_NOT_HELD
        raise error

    def fake_run(command, capture_output, text, check):
        junction_calls.append(
            {
                "command": command,
                "capture_output": capture_output,
                "text": text,
                "check": check,
            }
        )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(os, "symlink", fake_symlink)
    monkeypatch.setattr(os, "name", "nt", raising=False)
    monkeypatch.setattr("openjiuwen.agent_teams.team_workspace.manager.subprocess.run", fake_run)

    manager.mount_into_workspace(str(workspace_root))

    expected_link = os.path.join(str(workspace_root), ".team", "team-alpha")
    assert junction_calls == [
        {
            "command": ["cmd", "/c", "mklink", "/J", expected_link, manager.workspace_path],
            "capture_output": True,
            "text": True,
            "check": False,
        }
    ]


def test_mount_into_workspace_reraises_non_1314_symlink_error(monkeypatch, tmp_path):
    manager = _make_manager(tmp_path)
    workspace_root = tmp_path / "agent-workspace"
    workspace_root.mkdir()

    def fake_symlink(*args, **kwargs):
        error = OSError("unexpected failure")
        error.winerror = 5
        raise error

    monkeypatch.setattr(os, "symlink", fake_symlink)
    monkeypatch.setattr(os, "name", "nt", raising=False)

    with pytest.raises(OSError, match="unexpected failure"):
        manager.mount_into_workspace(str(workspace_root))
