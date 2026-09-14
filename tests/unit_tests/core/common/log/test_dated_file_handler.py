# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the per-day dated log file handler."""

import datetime
import logging
import os

import pytest

from openjiuwen.core.common.logging.dated_file_handler import (
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
    def test_rotates_to_old_log_at_size_cap(tmp_path):
        handler = DatedDailyFileHandler(tmp_path, "app.log", max_bytes=200)
        logger = logging.getLogger("test_dated_rotate")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

        for i in range(20):
            logger.info("message %03d padded to exceed cap", i)
        handler.flush()

        backup = tmp_path / format_log_date() / "app.old.log"
        assert backup.exists()

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
    assert DEFAULT_MAX_BYTES == 2 * 1024 * 1024
