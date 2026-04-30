# coding: utf-8

"""Tests for openjiuwen.agent_teams.worktree.slug."""

import pytest

from openjiuwen.agent_teams.worktree.slug import (
    MAX_SLUG_LENGTH,
    validate_slug,
    worktree_branch_name,
    worktree_path_for,
    worktrees_dir,
)
from tests.test_logger import logger


class TestValidateSlug:
    @pytest.mark.level0
    def test_valid_simple(self):
        validate_slug("feature-auth")
        logger.info("Simple slug accepted")

    @pytest.mark.level0
    def test_valid_with_dots_underscores(self):
        validate_slug("my_feature.v2")

    @pytest.mark.level0
    def test_valid_with_slash(self):
        validate_slug("user/feature-login")

    @pytest.mark.level0
    def test_valid_alphanumeric(self):
        validate_slug("abc123")

    @pytest.mark.level0
    def test_path_traversal_dotdot(self):
        with pytest.raises(ValueError, match="must not contain"):
            validate_slug("../evil")
        logger.info("Path traversal .. rejected")

    @pytest.mark.level0
    def test_path_traversal_dot(self):
        with pytest.raises(ValueError, match="must not contain"):
            validate_slug("./hidden")

    @pytest.mark.level0
    def test_path_traversal_nested(self):
        with pytest.raises(ValueError, match="must not contain"):
            validate_slug("a/../../etc/passwd")

    @pytest.mark.level1
    def test_absolute_path_rejected(self):
        """Absolute path contains '/' at start, producing empty segment."""
        with pytest.raises(ValueError, match="non-empty"):
            validate_slug("/etc/passwd")

    @pytest.mark.level1
    def test_shell_metacharacters_rejected(self):
        for char in [";", "&", "|", "$", "`", "(", ")", "{", "}", "<", ">", "!", " "]:
            with pytest.raises(ValueError):
                validate_slug(f"bad{char}slug")
        logger.info("Shell metacharacters rejected")

    @pytest.mark.level1
    def test_too_long(self):
        slug = "a" * (MAX_SLUG_LENGTH + 1)
        with pytest.raises(ValueError, match="characters or fewer"):
            validate_slug(slug)
        logger.info("Over-length slug rejected")

    @pytest.mark.level1
    def test_max_length_ok(self):
        slug = "a" * MAX_SLUG_LENGTH
        validate_slug(slug)  # should not raise

    @pytest.mark.level1
    def test_empty_segment_double_slash(self):
        with pytest.raises(ValueError, match="non-empty"):
            validate_slug("a//b")

    @pytest.mark.level1
    def test_empty_string(self):
        with pytest.raises(ValueError, match="non-empty"):
            validate_slug("")


class TestWorktreeBranchName:
    @pytest.mark.level1
    def test_simple(self):
        assert worktree_branch_name("feature-auth") == "worktree-feature-auth"
        logger.info("Branch name conversion verified")

    @pytest.mark.level1
    def test_with_slash(self):
        assert worktree_branch_name("user/feature-login") == "worktree-user+feature-login"

    @pytest.mark.level1
    def test_no_slash(self):
        assert worktree_branch_name("fix") == "worktree-fix"

    @pytest.mark.level1
    def test_multiple_slashes(self):
        assert worktree_branch_name("a/b/c") == "worktree-a+b+c"


class TestWorktreePathFor:
    @pytest.mark.level1
    def test_generates_correct_path(self):
        result = worktree_path_for("/home/user/workspace", "my-feature")
        assert result == "/home/user/workspace/.worktrees/my-feature"
        logger.info("worktree_path_for verified")

    @pytest.mark.level1
    def test_with_slash_slug(self):
        result = worktree_path_for("/ws", "user/feat")
        assert result == "/ws/.worktrees/user/feat"


class TestWorktreesDir:
    @pytest.mark.level1
    def test_generates_correct_path(self):
        result = worktrees_dir("/home/user/workspace")
        assert result == "/home/user/workspace/.worktrees"
        logger.info("worktrees_dir verified")
