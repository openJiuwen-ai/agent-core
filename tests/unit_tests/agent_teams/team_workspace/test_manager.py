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


def _make_manager(tmp_path, *, config: TeamWorkspaceConfig | None = None) -> TeamWorkspaceManager:
    workspace_path = tmp_path / "shared-workspace"
    workspace_path.mkdir()
    return TeamWorkspaceManager(
        config=config or TeamWorkspaceConfig(),
        workspace_path=str(workspace_path),
        team_name="team-alpha",
    )


@pytest.mark.level0
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


@pytest.mark.level0
def test_mount_into_workspace_replaces_stale_directory_and_merges_files(monkeypatch, tmp_path):
    manager = _make_manager(tmp_path)
    workspace_root = tmp_path / "agent-workspace"
    stale_mount = workspace_root / ".team" / "team-alpha"
    stale_artifacts = stale_mount / "artifacts"
    canonical_artifacts = tmp_path / "shared-workspace" / "artifacts"
    stale_artifacts.mkdir(parents=True)
    canonical_artifacts.mkdir(parents=True)
    (stale_artifacts / "only-stale.md").write_text("from stale", encoding="utf-8")
    (stale_artifacts / "existing.md").write_text("old", encoding="utf-8")
    (canonical_artifacts / "existing.md").write_text("new", encoding="utf-8")
    calls = []

    def fake_mount(target, link):
        calls.append((target, link))

    monkeypatch.setattr(manager, "_mount_directory", fake_mount)

    manager.mount_into_workspace(str(workspace_root))

    expected_link = os.path.join(str(workspace_root), ".team", "team-alpha")
    assert calls == [(manager.workspace_path, expected_link)]
    assert (canonical_artifacts / "only-stale.md").read_text(encoding="utf-8") == "from stale"
    assert (canonical_artifacts / "existing.md").read_text(encoding="utf-8") == "new"
    assert not stale_mount.exists()
    backups = list((workspace_root / ".team").glob("team-alpha.stale-*"))
    assert len(backups) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows junction fallback only applies on Windows")
@pytest.mark.level0
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

@pytest.mark.skipif(os.name != "nt", reason="Windows junction fallback only applies on Windows")
@pytest.mark.level0
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


@pytest.mark.level0
def test_mount_worktree_creates_symlink(tmp_path):
    """``mount_worktree`` should expose the target at ``.worktree/{slug}``."""
    manager = _make_manager(tmp_path)
    target = tmp_path / "worktrees" / "wt-alpha"
    target.mkdir(parents=True)

    manager.mount_worktree("wt-alpha", str(target))

    link_path = os.path.join(manager.workspace_path, ".worktree", "wt-alpha")
    assert os.path.islink(link_path)
    assert os.path.realpath(link_path) == os.path.realpath(str(target))


@pytest.mark.level1
def test_mount_worktree_replaces_stale_symlink(tmp_path):
    """A stale symlink from a previous session must be replaced, not error out."""
    manager = _make_manager(tmp_path)
    old_target = tmp_path / "worktrees" / "wt-stale"
    new_target = tmp_path / "worktrees" / "wt-fresh"
    old_target.mkdir(parents=True)
    new_target.mkdir(parents=True)

    manager.mount_worktree("wt-shared", str(old_target))
    # Simulate the old target being removed underneath us.
    os.rmdir(str(old_target))

    manager.mount_worktree("wt-shared", str(new_target))

    link_path = os.path.join(manager.workspace_path, ".worktree", "wt-shared")
    assert os.path.realpath(link_path) == os.path.realpath(str(new_target))


@pytest.mark.level1
def test_mount_worktree_skips_when_collision_is_not_symlink(tmp_path):
    """A real directory at the mount path must not be clobbered."""
    manager = _make_manager(tmp_path)
    target = tmp_path / "worktrees" / "wt-collide"
    target.mkdir(parents=True)
    wt_dir = os.path.join(manager.workspace_path, ".worktree")
    os.makedirs(wt_dir, exist_ok=True)
    real_dir = os.path.join(wt_dir, "wt-collide")
    os.makedirs(real_dir)

    manager.mount_worktree("wt-collide", str(target))

    assert not os.path.islink(real_dir)


@pytest.mark.level0
def test_unmount_worktree_removes_symlink(tmp_path):
    """``unmount_worktree`` should drop the link without touching the worktree."""
    manager = _make_manager(tmp_path)
    target = tmp_path / "worktrees" / "wt-bye"
    target.mkdir(parents=True)
    manager.mount_worktree("wt-bye", str(target))

    manager.unmount_worktree("wt-bye")

    link_path = os.path.join(manager.workspace_path, ".worktree", "wt-bye")
    assert not os.path.lexists(link_path)
    # Worktree directory itself is left alone.
    assert target.is_dir()


@pytest.mark.level1
def test_unmount_worktree_noop_when_link_missing(tmp_path):
    """Unmounting a slug that was never mounted is a no-op."""
    manager = _make_manager(tmp_path)
    # Should not raise.
    manager.unmount_worktree("never-mounted")


class _GitCallRecorder:
    """Records calls to _run_git and returns a default OK result."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, args, *, cwd=None, check=False):
        self.calls.append(list(args))
        return SimpleNamespace(ok=True, stdout="", stderr="", returncode=0)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_initialize_without_version_control_skips_git(monkeypatch, tmp_path):
    recorder = _GitCallRecorder()
    monkeypatch.setattr(
        "openjiuwen.agent_teams.team_workspace.manager._run_git",
        recorder,
    )

    manager = _make_manager(
        tmp_path,
        config=TeamWorkspaceConfig(version_control=False),
    )

    await manager.initialize()

    assert recorder.calls == []
    assert not os.path.isdir(os.path.join(manager.workspace_path, ".git"))
    for d in manager.config.artifact_dirs:
        assert os.path.isdir(os.path.join(manager.workspace_path, d))
    assert os.path.isdir(os.path.join(manager.workspace_path, "skills"))


@pytest.mark.asyncio
@pytest.mark.level1
async def test_initialize_with_version_control_runs_git_init(monkeypatch, tmp_path):
    recorder = _GitCallRecorder()
    monkeypatch.setattr(
        "openjiuwen.agent_teams.team_workspace.manager._run_git",
        recorder,
    )

    manager = _make_manager(tmp_path)  # default version_control=True

    await manager.initialize()

    subcommands = [c[0] for c in recorder.calls]
    assert "init" in subcommands
    assert "commit" in subcommands


@pytest.mark.asyncio
@pytest.mark.level1
async def test_auto_commit_noop_when_version_control_disabled(monkeypatch, tmp_path):
    recorder = _GitCallRecorder()
    monkeypatch.setattr(
        "openjiuwen.agent_teams.team_workspace.manager._run_git",
        recorder,
    )

    manager = _make_manager(
        tmp_path,
        config=TeamWorkspaceConfig(version_control=False),
    )

    sha = await manager.auto_commit("artifacts/code/a.py", "alice")

    assert sha is None
    assert recorder.calls == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_pull_push_history_noop_when_version_control_disabled(monkeypatch, tmp_path):
    recorder = _GitCallRecorder()
    monkeypatch.setattr(
        "openjiuwen.agent_teams.team_workspace.manager._run_git",
        recorder,
    )

    manager = _make_manager(
        tmp_path,
        config=TeamWorkspaceConfig(version_control=False),
    )

    assert await manager.pull() is False
    assert await manager.push() is True
    assert await manager.get_history("artifacts/code/a.py") == []
    assert recorder.calls == []
