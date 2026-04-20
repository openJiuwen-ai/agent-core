# coding: utf-8

"""Tests for openjiuwen.agent_teams.team_workspace.models."""

from datetime import datetime, timedelta, timezone

from openjiuwen.agent_teams.team_workspace.models import (
    ConflictStrategy,
    TeamWorkspaceConfig,
    WorkspaceFileLock,
    WorkspaceMode,
)
from tests.test_logger import logger


class TestWorkspaceMode:
    @pytest.mark.level0
    def test_enum_values(self):
        assert WorkspaceMode.LOCAL == "local"
        assert WorkspaceMode.DISTRIBUTED == "distributed"
        logger.info("WorkspaceMode enum values verified")

    @pytest.mark.level0
    def test_all_members(self):
        assert len(set(WorkspaceMode)) == 2


class TestConflictStrategy:
    @pytest.mark.level0
    def test_enum_values(self):
        assert ConflictStrategy.LOCK == "lock"
        assert ConflictStrategy.MERGE == "merge"
        assert ConflictStrategy.LAST_WRITE_WINS == "last_write_wins"
        logger.info("ConflictStrategy enum values verified")

    @pytest.mark.level0
    def test_all_members(self):
        assert len(set(ConflictStrategy)) == 3


class TestTeamWorkspaceConfig:
    @pytest.mark.level1
    def test_defaults(self):
        cfg = TeamWorkspaceConfig()
        assert cfg.enabled is False
        assert cfg.artifact_dirs == ["artifacts/code", "artifacts/docs", "artifacts/reports"]
        assert cfg.version_control is True
        assert cfg.conflict_strategy == ConflictStrategy.LOCK
        assert cfg.remote_url is None
        logger.info("TeamWorkspaceConfig defaults verified")

    @pytest.mark.level1
    def test_custom(self):
        cfg = TeamWorkspaceConfig(
            enabled=True,
            artifact_dirs=["out/"],
            version_control=False,
            conflict_strategy=ConflictStrategy.MERGE,
            remote_url="git@github.com:org/ws.git",
        )
        assert cfg.conflict_strategy == ConflictStrategy.MERGE
        assert cfg.remote_url == "git@github.com:org/ws.git"


class TestWorkspaceFileLock:
    @pytest.mark.level1
    def test_fields(self):
        lock = WorkspaceFileLock(
            file_path="src/main.py",
            holder_id="m1",
            holder_name="Alice",
            acquired_at="2025-01-01T00:00:00+00:00",
        )
        assert lock.file_path == "src/main.py"
        assert lock.holder_id == "m1"
        assert lock.timeout_seconds == 300
        logger.info("WorkspaceFileLock fields verified")

    @pytest.mark.level1
    def test_is_expired_false(self):
        now = datetime.now(timezone.utc)
        lock = WorkspaceFileLock(
            file_path="f.py",
            holder_id="m1",
            holder_name="Bob",
            acquired_at=now.isoformat(),
            timeout_seconds=600,
        )
        assert lock.is_expired() is False

    @pytest.mark.level1
    def test_is_expired_true(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=400)
        lock = WorkspaceFileLock(
            file_path="f.py",
            holder_id="m1",
            holder_name="Bob",
            acquired_at=past.isoformat(),
            timeout_seconds=300,
        )
        assert lock.is_expired() is True
        logger.info("WorkspaceFileLock.is_expired() verified")

    @pytest.mark.level1
    def test_is_expired_boundary(self):
        """Lock exactly at timeout boundary should be expired."""
        past = datetime.now(timezone.utc) - timedelta(seconds=301)
        lock = WorkspaceFileLock(
            file_path="f.py",
            holder_id="m1",
            holder_name="Bob",
            acquired_at=past.isoformat(),
            timeout_seconds=300,
        )
        assert lock.is_expired() is True
