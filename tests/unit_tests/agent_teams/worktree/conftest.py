# coding: utf-8

import subprocess

import pytest
from unittest.mock import AsyncMock

from openjiuwen.agent_teams.worktree.models import WorktreeConfig


@pytest.fixture
def worktree_config() -> WorktreeConfig:
    """Default worktree config for testing."""
    return WorktreeConfig(enabled=True)


@pytest.fixture
def mock_messager():
    """Mock Messager for testing."""
    return AsyncMock()


@pytest.fixture
def tmp_git_repo(tmp_path):
    """Create a temporary git repository for testing.

    Returns the repo root path as string.
    """
    repo = tmp_path / "test_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True, check=True)
    # Create initial commit
    readme = repo / "README.md"
    readme.write_text("# Test Repo")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo), capture_output=True, check=True)
    return str(repo)
