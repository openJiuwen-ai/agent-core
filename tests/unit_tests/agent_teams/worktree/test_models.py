# coding: utf-8

"""Tests for openjiuwen.agent_teams.worktree.models."""

from datetime import datetime, timedelta, timezone

import pytest

from openjiuwen.agent_teams.worktree.models import (
    WorktreeChangeSummary,
    WorktreeConfig,
    WorktreeCreateResult,
    WorktreeLifecyclePolicy,
    WorktreeSession,
)
from tests.test_logger import logger


class TestWorktreeLifecyclePolicy:
    def test_enum_values(self):
        assert WorktreeLifecyclePolicy.AUTO == "auto"
        assert WorktreeLifecyclePolicy.EPHEMERAL == "ephemeral"
        assert WorktreeLifecyclePolicy.DURABLE == "durable"
        logger.info("WorktreeLifecyclePolicy enum values verified")

    def test_all_members(self):
        members = set(WorktreeLifecyclePolicy)
        assert len(members) == 3


class TestWorktreeConfig:
    def test_defaults(self):
        cfg = WorktreeConfig()
        assert cfg.enabled is False
        assert cfg.base_dir is None
        assert cfg.sparse_paths is None
        assert cfg.symlink_directories is None
        assert cfg.include_patterns is None
        assert cfg.cleanup_after_days == 30
        assert cfg.auto_cleanup_on_shutdown is True
        assert cfg.lifecycle_policy == WorktreeLifecyclePolicy.AUTO
        logger.info("WorktreeConfig default values verified")

    def test_enabled(self, worktree_config):
        assert worktree_config.enabled is True

    def test_with_lifecycle_policy(self):
        cfg = WorktreeConfig(
            enabled=True,
            lifecycle_policy=WorktreeLifecyclePolicy.DURABLE,
        )
        assert cfg.lifecycle_policy == WorktreeLifecyclePolicy.DURABLE
        logger.info("WorktreeConfig with lifecycle_policy verified")

    def test_with_sparse_paths(self):
        cfg = WorktreeConfig(
            enabled=True,
            sparse_paths=["src/", "tests/"],
        )
        assert cfg.sparse_paths == ["src/", "tests/"]

    def test_with_all_fields(self):
        cfg = WorktreeConfig(
            enabled=True,
            base_dir="/tmp/wt",
            sparse_paths=["src/"],
            symlink_directories=[".venv"],
            include_patterns=[".env.local"],
            cleanup_after_days=7,
            auto_cleanup_on_shutdown=False,
            lifecycle_policy=WorktreeLifecyclePolicy.EPHEMERAL,
        )
        assert cfg.base_dir == "/tmp/wt"
        assert cfg.auto_cleanup_on_shutdown is False
        assert cfg.cleanup_after_days == 7


class TestWorktreeSession:
    def test_minimal(self):
        session = WorktreeSession(
            original_cwd="/home/user/repo",
            worktree_path="/home/user/repo/.agent_teams/worktrees/test",
            worktree_name="test",
        )
        assert session.original_cwd == "/home/user/repo"
        assert session.worktree_branch is None
        assert session.member_id is None
        assert session.hook_based is False
        assert session.lifecycle_policy == WorktreeLifecyclePolicy.AUTO
        assert session.creation_duration_ms is None
        assert session.used_sparse_paths is False
        logger.info("WorktreeSession minimal creation verified")

    def test_full(self):
        session = WorktreeSession(
            original_cwd="/repo",
            worktree_path="/repo/.agent_teams/worktrees/feat",
            worktree_name="feat",
            worktree_branch="worktree-feat",
            original_branch="main",
            original_head_commit="abc123",
            member_id="m1",
            team_id="t1",
            hook_based=True,
            lifecycle_policy=WorktreeLifecyclePolicy.DURABLE,
            team_lifecycle="persistent",
            creation_duration_ms=42.5,
            used_sparse_paths=True,
        )
        assert session.worktree_branch == "worktree-feat"
        assert session.original_head_commit == "abc123"
        assert session.hook_based is True
        assert session.creation_duration_ms == 42.5

    def test_serialization_roundtrip(self):
        session = WorktreeSession(
            original_cwd="/repo",
            worktree_path="/repo/.agent_teams/worktrees/test",
            worktree_name="test",
            worktree_branch="worktree-test",
            original_branch="main",
            member_id="m1",
        )
        data = session.model_dump()
        restored = WorktreeSession.model_validate(data)
        assert restored == session
        logger.info("WorktreeSession serialization roundtrip verified")

    def test_json_roundtrip(self):
        session = WorktreeSession(
            original_cwd="/repo",
            worktree_path="/repo/.agent_teams/worktrees/x",
            worktree_name="x",
        )
        json_str = session.model_dump_json()
        restored = WorktreeSession.model_validate_json(json_str)
        assert restored == session


class TestWorktreeCreateResult:
    def test_defaults(self):
        result = WorktreeCreateResult(worktree_path="/wt/test")
        assert result.worktree_path == "/wt/test"
        assert result.worktree_branch is None
        assert result.head_commit is None
        assert result.base_branch is None
        assert result.existed is False
        assert result.hook_based is False
        logger.info("WorktreeCreateResult defaults verified")

    def test_full(self):
        result = WorktreeCreateResult(
            worktree_path="/wt/test",
            worktree_branch="worktree-test",
            head_commit="deadbeef",
            base_branch="main",
            existed=True,
            hook_based=True,
        )
        assert result.existed is True
        assert result.head_commit == "deadbeef"


class TestWorktreeChangeSummary:
    def test_defaults(self):
        summary = WorktreeChangeSummary()
        assert summary.changed_files == 0
        assert summary.commits == 0
        logger.info("WorktreeChangeSummary defaults verified")

    def test_with_values(self):
        summary = WorktreeChangeSummary(changed_files=3, commits=2)
        assert summary.changed_files == 3
        assert summary.commits == 2


