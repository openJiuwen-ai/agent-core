# coding: utf-8

"""Tests for Workspace worktree link management."""

import os

import pytest

from openjiuwen.harness.workspace.workspace import Workspace, WorkspaceNode
from tests.test_logger import logger


@pytest.fixture
def workspace(tmp_path):
    """Create a Workspace rooted in a temp directory."""
    root = str(tmp_path / "agent_workspace")
    os.makedirs(root)
    return Workspace(root_path=root)


@pytest.fixture
def worktree_dir(tmp_path):
    """Create a fake worktree directory."""
    d = str(tmp_path / "worktrees" / "feat-x")
    os.makedirs(d)
    return d


class TestWorkspaceNodeEnum:
    def test_team_links_value(self):
        # Name reserved for flat TeamWorkspaceManager mount (``.team``), not hub links.
        assert WorkspaceNode.TEAM_LINKS.value == ".team"

    def test_worktree_links_value(self):
        assert WorkspaceNode.WORKTREE_LINKS.value == ".worktree"


class TestLinkWorktree:
    def test_creates_symlink(self, workspace, worktree_dir):
        link = workspace.link_worktree("feat-x", worktree_dir)
        assert link.is_symlink()
        assert str(link.resolve()) == os.path.realpath(worktree_dir)
        logger.info("link_worktree created symlink at %s", link)

    def test_idempotent(self, workspace, worktree_dir):
        workspace.link_worktree("feat-x", worktree_dir)
        workspace.link_worktree("feat-x", worktree_dir)
        link = os.path.join(workspace.root_path, ".worktree", "feat-x")
        assert os.path.islink(link)


class TestUnlinkWorktree:
    def test_removes_symlink(self, workspace, worktree_dir):
        workspace.link_worktree("feat-x", worktree_dir)
        removed = workspace.unlink_worktree("feat-x")
        assert removed is True
        assert not os.path.exists(os.path.join(workspace.root_path, ".worktree", "feat-x"))

    def test_returns_false_when_missing(self, workspace):
        assert workspace.unlink_worktree("nonexistent") is False


class TestListWorktreeLinks:
    def test_list_worktree_links_empty(self, workspace):
        assert workspace.list_worktree_links() == []

    def test_list_worktree_links_resolves_target(self, workspace, worktree_dir):
        workspace.link_worktree("feat-x", worktree_dir)
        links = workspace.list_worktree_links()
        assert len(links) == 1
        slug, target = links[0]
        assert slug == "feat-x"
        assert target == os.path.realpath(worktree_dir)
        logger.info("Worktree link resolves to %s", target)
