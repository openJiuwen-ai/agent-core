# coding: utf-8

"""Tests for Workspace link management (.team/ and .worktree/ symlinks)."""

import os
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from openjiuwen.harness.workspace.workspace import (
    ERROR_PRIVILEGE_NOT_HELD,
    Workspace,
    WorkspaceNode,
)
from tests.test_logger import logger


WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-specific link behavior is only valid on Windows.",
)


@pytest.fixture
def workspace(tmp_path):
    """Create a Workspace rooted in a temp directory."""
    root = str(tmp_path / "agent_workspace")
    os.makedirs(root)
    return Workspace(root_path=root)


@pytest.fixture
def team_workspace_dir(tmp_path):
    """Create a fake team workspace directory."""
    d = str(tmp_path / "team_workspace" / "team_abc")
    os.makedirs(d)
    return d


@pytest.fixture
def worktree_dir(tmp_path):
    """Create a fake worktree directory."""
    d = str(tmp_path / "worktrees" / "feat-x")
    os.makedirs(d)
    return d


class TestWorkspaceNodeEnum:
    def test_team_links_value(self):
        assert WorkspaceNode.TEAM_LINKS.value == ".team"

    def test_worktree_links_value(self):
        assert WorkspaceNode.WORKTREE_LINKS.value == ".worktree"


class TestLinkTeam:
    def test_creates_directory_link(self, workspace, team_workspace_dir):
        link = workspace.link_team("team_abc", team_workspace_dir)
        assert workspace._is_directory_link(link)
        assert str(link.resolve()) == os.path.realpath(team_workspace_dir)
        logger.info("link_team created directory link at %s", link)

    def test_idempotent(self, workspace, team_workspace_dir):
        workspace.link_team("team_abc", team_workspace_dir)
        workspace.link_team("team_abc", team_workspace_dir)
        link = os.path.join(workspace.root_path, ".team", "team_abc")
        assert workspace._is_directory_link(Path(link))

    def test_multiple_teams(self, workspace, tmp_path):
        for tid in ("team_1", "team_2", "team_3"):
            d = str(tmp_path / "tw" / tid)
            os.makedirs(d)
            workspace.link_team(tid, d)
        links = workspace.list_team_links()
        assert len(links) == 3
        names = [name for name, _ in links]
        assert names == ["team_1", "team_2", "team_3"]
        logger.info("Multiple team links: %s", names)


class TestUnlinkTeam:
    def test_removes_symlink(self, workspace, team_workspace_dir):
        workspace.link_team("team_abc", team_workspace_dir)
        removed = workspace.unlink_team("team_abc")
        assert removed is True
        assert not os.path.exists(os.path.join(workspace.root_path, ".team", "team_abc"))

    def test_returns_false_when_missing(self, workspace):
        assert workspace.unlink_team("nonexistent") is False


class TestLinkWorktree:
    def test_creates_directory_link(self, workspace, worktree_dir):
        link = workspace.link_worktree("feat-x", worktree_dir)
        assert workspace._is_directory_link(link)
        assert str(link.resolve()) == os.path.realpath(worktree_dir)
        logger.info("link_worktree created directory link at %s", link)

    def test_idempotent(self, workspace, worktree_dir):
        workspace.link_worktree("feat-x", worktree_dir)
        workspace.link_worktree("feat-x", worktree_dir)
        link = os.path.join(workspace.root_path, ".worktree", "feat-x")
        assert workspace._is_directory_link(Path(link))


class TestUnlinkWorktree:
    def test_removes_symlink(self, workspace, worktree_dir):
        workspace.link_worktree("feat-x", worktree_dir)
        removed = workspace.unlink_worktree("feat-x")
        assert removed is True
        assert not os.path.exists(os.path.join(workspace.root_path, ".worktree", "feat-x"))

    def test_returns_false_when_missing(self, workspace):
        assert workspace.unlink_worktree("nonexistent") is False


class TestListLinks:
    def test_list_team_links_empty(self, workspace):
        assert workspace.list_team_links() == []

    def test_list_worktree_links_empty(self, workspace):
        assert workspace.list_worktree_links() == []

    def test_list_team_links_sorted(self, workspace, tmp_path):
        for tid in ("beta", "alpha", "gamma"):
            d = str(tmp_path / "tw" / tid)
            os.makedirs(d)
            workspace.link_team(tid, d)
        names = [name for name, _ in workspace.list_team_links()]
        assert names == ["alpha", "beta", "gamma"]

    def test_list_worktree_links_resolves_target(self, workspace, worktree_dir):
        workspace.link_worktree("feat-x", worktree_dir)
        links = workspace.list_worktree_links()
        assert len(links) == 1
        slug, target = links[0]
        assert slug == "feat-x"
        assert target == os.path.realpath(worktree_dir)
        logger.info("Worktree link resolves to %s", target)

    @WINDOWS_ONLY
    def test_list_team_links_includes_windows_directory_links(self, workspace, tmp_path, monkeypatch):
        target_dir = tmp_path / "team-target"
        target_dir.mkdir()
        junction_path = os.path.join(workspace.root_path, ".team", "team_junction")
        os.makedirs(junction_path)
        regular_path = os.path.join(workspace.root_path, ".team", "team_regular")
        os.makedirs(regular_path)

        def fake_is_directory_link(entry):
            return entry.name == "team_junction"

        monkeypatch.setattr(workspace, "_is_directory_link", fake_is_directory_link)
        monkeypatch.setattr(Path, "resolve", lambda self: target_dir if self.name == "team_junction" else self)

        assert workspace.list_team_links() == [("team_junction", os.path.realpath(target_dir))]


class TestWindowsFallback:
    @WINDOWS_ONLY
    def test_create_directory_link_falls_back_to_junction_on_windows_1314(self, workspace, monkeypatch):
        calls = []

        def fake_symlink(*args, **kwargs):
            error = OSError("missing privilege")
            error.winerror = ERROR_PRIVILEGE_NOT_HELD
            raise error

        def fake_create_windows_junction(target_path, link_path):
            calls.append((target_path, link_path))

        monkeypatch.setattr(os, "symlink", fake_symlink)
        monkeypatch.setattr(os, "name", "nt", raising=False)
        monkeypatch.setattr(workspace, "_create_windows_junction", fake_create_windows_junction)

        link_path = Path(workspace.root_path) / ".team" / "team_abc"
        workspace._create_directory_link("C:\\target", link_path)

        assert calls == [("C:\\target", str(link_path))]
