# coding: utf-8

"""Tests for openjiuwen.agent_teams.worktree.git."""

import pytest

from openjiuwen.agent_teams.worktree.git import (
    GitError,
    GitResult,
    _git_env,
    count_commits_since,
    find_git_root,
    get_current_branch,
    read_worktree_head_sha,
    rev_parse,
    status_porcelain,
)
from tests.test_logger import logger


class TestGitError:
    @pytest.mark.level0
    def test_message_format(self):
        err = GitError(["rev-parse", "HEAD"], returncode=128, stderr="fatal: bad ref")
        assert "rev-parse" in str(err)
        assert "128" in str(err)
        assert "fatal: bad ref" in str(err)
        assert err.command == ["rev-parse", "HEAD"]
        assert err.returncode == 128
        assert err.stderr == "fatal: bad ref"
        logger.info("GitError message format verified")

    @pytest.mark.level0
    def test_is_exception(self):
        err = GitError(["status"], returncode=1, stderr="")
        assert isinstance(err, Exception)


class TestGitResult:
    @pytest.mark.level0
    def test_ok_true(self):
        r = GitResult(returncode=0, stdout="output", stderr="")
        assert r.ok is True
        logger.info("GitResult.ok=True verified")

    @pytest.mark.level0
    def test_ok_false(self):
        r = GitResult(returncode=1, stdout="", stderr="error")
        assert r.ok is False

    @pytest.mark.level0
    def test_ok_nonzero(self):
        r = GitResult(returncode=128, stdout="", stderr="fatal")
        assert r.ok is False

    @pytest.mark.level0
    def test_frozen(self):
        r = GitResult(returncode=0, stdout="", stderr="")
        with pytest.raises(AttributeError):
            r.returncode = 1  # type: ignore[misc]


class TestGitEnv:
    @pytest.mark.level0
    def test_contains_terminal_prompt(self):
        env = _git_env()
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        logger.info("_git_env GIT_TERMINAL_PROMPT verified")

    @pytest.mark.level1
    def test_contains_askpass(self):
        env = _git_env()
        assert env["GIT_ASKPASS"] == ""

    @pytest.mark.level1
    def test_inherits_environment(self):
        """Should inherit the existing environment."""
        import os

        env = _git_env()
        assert "PATH" in env
        assert env["PATH"] == os.environ["PATH"]


@pytest.mark.asyncio
class TestFindGitRoot:
    @pytest.mark.level1
    async def test_in_git_repo(self, tmp_git_repo):
        root = await find_git_root(tmp_git_repo)
        assert root is not None
        assert root == tmp_git_repo
        logger.info("find_git_root in repo verified")

    @pytest.mark.level1
    async def test_not_in_git_repo(self, tmp_path):
        root = await find_git_root(str(tmp_path))
        assert root is None


@pytest.mark.asyncio
class TestGetCurrentBranch:
    @pytest.mark.level1
    async def test_returns_branch(self, tmp_git_repo):
        branch = await get_current_branch(tmp_git_repo)
        assert branch is not None
        # Default branch is typically "main" or "master" depending on git config
        assert isinstance(branch, str)
        assert len(branch) > 0
        logger.info(f"get_current_branch returned: {branch}")


@pytest.mark.asyncio
class TestRevParse:
    @pytest.mark.level1
    async def test_resolve_head(self, tmp_git_repo):
        sha = await rev_parse("HEAD", tmp_git_repo)
        assert sha is not None
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)
        logger.info(f"rev_parse HEAD resolved to: {sha}")

    @pytest.mark.level1
    async def test_invalid_ref(self, tmp_git_repo):
        sha = await rev_parse("nonexistent-ref-xyz", tmp_git_repo)
        assert sha is None


@pytest.mark.asyncio
class TestReadWorktreeHeadSha:
    @pytest.mark.level1
    async def test_not_a_worktree(self, tmp_git_repo):
        """Regular repo (not a worktree) has .git directory, not .git file."""
        result = await read_worktree_head_sha(tmp_git_repo)
        assert result is None
        logger.info("read_worktree_head_sha returns None for non-worktree")

    @pytest.mark.level1
    async def test_nonexistent_path(self, tmp_path):
        result = await read_worktree_head_sha(str(tmp_path / "nonexistent"))
        assert result is None


@pytest.mark.asyncio
class TestStatusPorcelain:
    @pytest.mark.level1
    async def test_clean_repo(self, tmp_git_repo):
        lines = await status_porcelain(tmp_git_repo)
        assert lines == []
        logger.info("status_porcelain on clean repo verified")

    @pytest.mark.level1
    async def test_with_changes(self, tmp_git_repo):
        import os

        new_file = os.path.join(tmp_git_repo, "new.txt")
        with open(new_file, "w") as f:
            f.write("hello")
        lines = await status_porcelain(tmp_git_repo)
        assert len(lines) > 0
        assert any("new.txt" in line for line in lines)


@pytest.mark.asyncio
class TestCountCommitsSince:
    @pytest.mark.level1
    async def test_zero_commits(self, tmp_git_repo):
        head = await rev_parse("HEAD", tmp_git_repo)
        count = await count_commits_since(head, tmp_git_repo)
        assert count == 0
        logger.info("count_commits_since with 0 commits verified")

    @pytest.mark.level1
    async def test_with_commits(self, tmp_git_repo):
        import subprocess

        head_before = await rev_parse("HEAD", tmp_git_repo)
        # Add a commit
        new_file = f"{tmp_git_repo}/extra.txt"
        with open(new_file, "w") as f:
            f.write("content")
        subprocess.run(["git", "add", "."], cwd=tmp_git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "second"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )
        count = await count_commits_since(head_before, tmp_git_repo)
        assert count == 1

    @pytest.mark.level1
    async def test_invalid_base(self, tmp_git_repo):
        count = await count_commits_since("0" * 40, tmp_git_repo)
        assert count is None
