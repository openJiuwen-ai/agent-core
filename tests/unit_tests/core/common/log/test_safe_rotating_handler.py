# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""SafeRotatingFileHandler rollover safety tests.

The copy+truncate rollover must never truncate the active log when the
backup copy failed: truncating anyway destroys the only remaining copy of
that segment (e.g. ENOSPC while copying).
"""

import logging
import os
from unittest.mock import patch

import pytest

from openjiuwen.core.common.logging.default.default_impl import SafeRotatingFileHandler


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "app.log"
    path.write_text("", encoding="utf-8")
    return str(path)


def _make_handler(log_file: str, backup_count: int = 1) -> SafeRotatingFileHandler:
    handler = SafeRotatingFileHandler(
        log_file,
        mode="a",
        maxBytes=1024,
        backupCount=backup_count,
        encoding="utf-8",
    )
    return handler


class TestRolloverKeepsActiveLogWhenBackupCopyFails:
    def test_truncation_skipped_when_copy_fails(self, log_file):
        """A failed backup copy must leave the active file intact."""
        handler = _make_handler(log_file)
        try:
            payload = "x" * 100
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(payload)

            with patch("shutil.copy2", side_effect=OSError(28, "No space left on device")):
                handler.doRollover()

            with open(log_file, "r", encoding="utf-8") as f:
                content_after = f.read()
            assert content_after == payload, (
                "active log must not be truncated when the backup copy failed"
            )
        finally:
            handler.close()

    def test_truncation_runs_when_copy_succeeds(self, log_file):
        """A successful backup copy is followed by truncation as before."""
        handler = _make_handler(log_file)
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("y" * 50)

            handler.doRollover()

            with open(log_file, "r", encoding="utf-8") as f:
                assert f.read() == "", "active log is truncated after a successful copy"
            backup = log_file + ".1"
            assert os.path.exists(backup)
            with open(backup, "r", encoding="utf-8") as f:
                assert f.read() == "y" * 50
        finally:
            handler.close()

    def test_handler_remains_usable_after_failed_rollover(self, log_file):
        """Logging continues into the intact active file after a failed copy."""
        handler = _make_handler(log_file)
        logger = logging.getLogger("rollover_test")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        try:
            with patch("shutil.copy2", side_effect=OSError(28, "No space left on device")):
                handler.doRollover()

            logger.info("after failed rollover")
            handler.flush()

            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read()
            assert "after failed rollover" in content
        finally:
            logger.removeHandler(handler)
            handler.close()


class TestRolloverRetryThrottle:
    """While the backup copy keeps failing, rollover attempts are throttled.

    stdlib RotatingFileHandler.emit re-triggers doRollover on every record
    once the active file stays over maxBytes (it is no longer truncated on
    copy failure). Without a throttle each log line would retry a full
    copy of the active file.
    """

    def test_consecutive_failures_are_throttled(self, log_file):
        handler = _make_handler(log_file)
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("x" * 100)

            with patch("shutil.copy2", side_effect=OSError(28, "No space left on device")) as mock_copy:
                handler.doRollover()
                first_calls = mock_copy.call_count
                # File has not grown: the next rollover attempt must be skipped.
                handler.doRollover()
                assert mock_copy.call_count == first_calls, (
                    "unchanged file size must not trigger another copy attempt"
                )
                # Growing past the recorded failure size re-arms the attempt.
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write("y" * 500)
                handler.doRollover()
                assert mock_copy.call_count == first_calls + 1
        finally:
            handler.close()

    def test_successful_rollover_resets_throttle(self, log_file):
        handler = _make_handler(log_file)
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("x" * 100)

            with patch("shutil.copy2", side_effect=OSError(28, "No space left on device")):
                handler.doRollover()
            assert handler._rollover_failed_size is not None

            # Growing past 2x the failed size re-arms the attempt; the copy
            # now succeeds and the throttle state must reset.
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("y" * 300)
            handler.doRollover()
            assert handler._rollover_failed_size is None

            with open(log_file, "r", encoding="utf-8") as f:
                assert f.read() == ""
        finally:
            handler.close()
