# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the per-day dated log file handler."""

import datetime
import logging
import os

import pytest

from openjiuwen.core.common.logging.dated_file_handler import (
    DEFAULT_BACKUP_COUNT,
    DEFAULT_MAX_BYTES,
    DatedDailyFileHandler,
    cleanup_expired_dated_log_dirs,
    format_log_date,
)


class TestDatedDailyFileHandler:
    """Behavior of the dated file handler."""

    @staticmethod
    def test_writes_under_today_date_dir(tmp_path):
        handler = DatedDailyFileHandler(tmp_path, "run/jiuwen.log")
        logger = logging.getLogger("test_dated_today")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.info("hello dated")

        handler.flush()
        expected = tmp_path / format_log_date() / "run" / "jiuwen.log"
        assert expected.exists()
        assert "hello dated" in expected.read_text(encoding="utf-8")

    @staticmethod
    def test_switches_file_across_days(tmp_path, monkeypatch):
        state = {"date": "2026-09-11"}
        monkeypatch.setattr(
            "openjiuwen.core.common.logging.dated_file_handler.format_log_date",
            lambda now=None: state["date"],
        )
        handler = DatedDailyFileHandler(tmp_path, "app.log")

        logger = logging.getLogger("test_dated_switch")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

        logger.info("day one")
        handler.flush()

        state["date"] = "2026-09-12"
        logger.info("day two")
        handler.flush()

        day_one = tmp_path / "2026-09-11" / "app.log"
        day_two = tmp_path / "2026-09-12" / "app.log"
        assert "day one" in day_one.read_text(encoding="utf-8")
        assert "day two" in day_two.read_text(encoding="utf-8")

    @staticmethod
    def test_rotates_to_numbered_backup_at_size_cap(tmp_path):
        """Exceeding the daily cap rolls aside to a numbered backup."""
        handler = DatedDailyFileHandler(tmp_path, "app.log", max_bytes=200)
        logger = logging.getLogger("test_dated_rotate")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

        for i in range(20):
            logger.info("message %03d padded to exceed cap", i)
        handler.flush()

        day = tmp_path / format_log_date()
        backups = list(day.glob("app.*.log"))
        assert backups, "expected at least one numbered backup"
        assert all(b.suffix == ".log" and b.stem.rsplit(".", 1)[1].isdigit() for b in backups)
        assert not (day / "app.old.log").exists()

    @staticmethod
    def test_rotation_prunes_oldest_beyond_backup_count(tmp_path):
        """Only the newest ``backup_count`` backups survive per day."""
        handler = DatedDailyFileHandler(tmp_path, "app.log", max_bytes=200, backup_count=3)
        logger = logging.getLogger("test_dated_rotate_prune")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

        for i in range(40):
            logger.info("message %03d padded to exceed cap", i)
        handler.flush()
        handler.close()

        day = tmp_path / format_log_date()
        backups = sorted(day.glob("app.*.log"))
        assert len(backups) == 3, f"expected 3 backups, got {backups}"
        # 存活的三个备份是序号最高（最新）的三个，且序号连续。
        numbers = [int(b.stem.rsplit(".", 1)[1]) for b in backups]
        assert numbers == sorted(numbers)
        assert numbers[-1] - numbers[0] == 2

        # The surviving backups are the newest: the earliest messages are
        # pruned away, the latest ones are still on disk.
        contents = (day / "app.log").read_text(encoding="utf-8")
        for backup in backups:
            contents += backup.read_text(encoding="utf-8")
        assert "message 000" not in contents
        assert "message 039" in contents

    @staticmethod
    def test_negative_backup_count_keeps_all_without_loss(tmp_path):
        """``backup_count < 0`` disables pruning; every backup is kept."""
        handler = DatedDailyFileHandler(tmp_path, "app.log", max_bytes=200, backup_count=-1)
        logger = logging.getLogger("test_dated_rotate_all")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

        total = 40
        for i in range(total):
            logger.info("message %03d padded to exceed cap", i)
        handler.flush()
        handler.close()

        day = tmp_path / format_log_date()
        backups = sorted(day.glob("app.*.log"))
        assert len(backups) >= 3, f"expected multiple backups, got {backups}"

        contents = (day / "app.log").read_text(encoding="utf-8")
        for backup in backups:
            contents += backup.read_text(encoding="utf-8")
        for i in range(total):
            assert f"message {i:03d}" in contents

    @staticmethod
    def test_default_backup_count_is_ten(tmp_path):
        handler = DatedDailyFileHandler(tmp_path, "app.log")
        assert handler.backup_count == DEFAULT_BACKUP_COUNT == 10

    @staticmethod
    def test_base_dir_exposed_for_idempotency(tmp_path):
        handler = DatedDailyFileHandler(tmp_path, "app.log")
        assert os.fspath(handler.base_dir) == os.fspath(tmp_path)


class TestCleanupExpiredDatedLogDirs:
    """Behavior of the retention cleanup."""

    @staticmethod
    @pytest.mark.parametrize(
        "retention_days,kept",
        [
            (90, {"2026-06-15", "2026-06-14", "2026-09-12"}),
            (0, {"2026-09-12"}),
        ],
    )
    def test_removes_only_expired_dirs(tmp_path, retention_days, kept):
        now = datetime.date(2026, 9, 12)
        for name in ("2026-06-15", "2026-06-14", "2026-09-12"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "app.log").write_text("x", encoding="utf-8")

        removed = cleanup_expired_dated_log_dirs(tmp_path, retention_days=retention_days, now=now)

        assert removed == 3 - len(kept)
        remaining = {p.name for p in tmp_path.iterdir() if p.is_dir()}
        assert remaining == kept

    @staticmethod
    def test_leaves_non_dated_entries_alone(tmp_path):
        now = datetime.date(2026, 9, 12)
        old_day = tmp_path / "2025-01-01"
        old_day.mkdir()
        (old_day / "app.log").write_text("x", encoding="utf-8")
        plain_dir = tmp_path / "not-a-date"
        plain_dir.mkdir()
        plain_file = tmp_path / "20260101.txt"
        plain_file.write_text("x", encoding="utf-8")

        removed = cleanup_expired_dated_log_dirs(tmp_path, now=now)

        assert removed == 1
        assert plain_dir.exists()
        assert plain_file.exists()

    @staticmethod
    def test_missing_base_dir_returns_zero(tmp_path):
        missing = tmp_path / "nope"
        assert cleanup_expired_dated_log_dirs(missing) == 0


def test_default_max_bytes_matches_desktop():
    assert DEFAULT_MAX_BYTES == 10 * 1024 * 1024
