# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Dated-daily log file handler.

Layout mirrors the desktop logger (<dataRoot>/logs/YYYY-MM-DD/system.log):

    <base_dir>/<YYYY-MM-DD>/<filename>

- One directory per local calendar day; the date is resolved on every emit,
  so a long-lived process switches files at midnight.
- Within a day, a 2 MB cap rotates the active file to "<stem>.old.log"
  (only the most recent backup is kept, same as the desktop).
- Expired date directories are removed by cleanup_expired_dated_log_dirs
  (retention defaults to 90 days, same as the desktop audit logs).

Failures are silently ignored per call: logging must never block business
flow.
"""

from __future__ import annotations

import datetime
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Optional

DEFAULT_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 90

_DATED_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def format_log_date(now: Optional[datetime.date] = None) -> str:
    """Return the local calendar date as ``YYYY-MM-DD``.

    Args:
        now: Reference date; defaults to today.

    Returns:
        The formatted date string.
    """
    return (now or datetime.date.today()).strftime("%Y-%m-%d")


class DatedDailyFileHandler(logging.FileHandler):
    """File handler that writes under a per-day date directory.

    The active file is ``<base_dir>/<YYYY-MM-DD>/<filename>`` where the
    date is resolved from the local clock on every emit. Crossing midnight
    closes the old stream and opens the new day's file. A size cap within
    the day rotates to ``<stem>.old.log``, keeping only one backup.

    ``filename`` may contain subdirectories (e.g. ``run/jiuwen.log``).
    ``base_dir`` is exposed for idempotency checks by callers.

    Args:
        base_dir: Directory under which per-day subdirectories are created.
        filename: Log file path relative to the date directory.
        max_bytes: Per-day size cap; ``0`` disables rotation.
        encoding: File encoding.
    """

    def __init__(
        self,
        base_dir: str | os.PathLike,
        filename: str,
        max_bytes: int = DEFAULT_MAX_BYTES,
        encoding: str = "utf-8",
    ) -> None:
        self.base_dir = os.fspath(base_dir)
        self._relative_filename = filename
        self.max_bytes = max_bytes
        self._current_date: Optional[str] = None
        super().__init__(self._current_path(), mode="a", encoding=encoding, delay=True)
        self._current_date = format_log_date()

    def _current_path(self) -> str:
        return os.path.join(self.base_dir, format_log_date(), self._relative_filename)

    def _open(self):
        # Every open (first emit, re-open, day switch) must ensure the
        # full date-directory chain exists; FileHandler._open does not
        # create directories (filename may contain subdirs like run/).
        try:
            Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return super()._open()

    def emit(self, record: logging.LogRecord) -> None:
        today = format_log_date()
        if today != self._current_date:
            self._switch_day(today)
        self._rotate_if_needed(record)
        super().emit(record)

    def _switch_day(self, today: str) -> None:
        """Close the old stream and reopen under the new day directory."""
        try:
            if self.stream:
                self.stream.close()
                self.stream = None
            self._current_date = today
            self.baseFilename = os.path.abspath(self._current_path())
            if not self.delay:
                self.stream = self._open()
        except OSError:
            # Keep writing to the old file: a failed switch must not
            # drop records.
            self._current_date = today
            if self.stream is None and not self.delay:
                try:
                    self.stream = self._open()
                except OSError:
                    pass

    def _rotate_if_needed(self, record: logging.LogRecord) -> None:
        if not self.stream or self.max_bytes <= 0:
            return
        try:
            message = "%s\n" % self.format(record)
            self.stream.seek(0, os.SEEK_END)
            if self.stream.tell() + len(message.encode(self.encoding or "utf-8")) < self.max_bytes:
                return
            backup = Path(self.baseFilename).with_suffix(".old.log")
            if backup.exists():
                backup.unlink()
            shutil.copy2(self.baseFilename, backup)
            self.stream.seek(0)
            self.stream.truncate(0)
        except OSError:
            pass


def cleanup_expired_dated_log_dirs(
    base_dir: str | os.PathLike,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: Optional[datetime.date] = None,
) -> int:
    """Remove date-named subdirectories older than the retention window.

    Only entries matching ``YYYY-MM-DD`` are considered; everything else
    in ``base_dir`` is left untouched. Best effort: failures to stat or
    remove a directory are skipped, never raised.

    Args:
        base_dir: Directory holding the per-day subdirectories.
        retention_days: Number of days to keep; ``0`` keeps only today.
        now: Reference date; defaults to today.

    Returns:
        Number of expired directories removed.
    """
    base_path = Path(base_dir)
    try:
        entries = list(base_path.iterdir())
    except OSError:
        return 0

    today = now or datetime.date.today()
    cutoff = today - datetime.timedelta(days=retention_days)
    removed = 0
    for entry in entries:
        if not _DATED_DIR_RE.match(entry.name):
            continue
        try:
            day = datetime.date.fromisoformat(entry.name)
        except ValueError:
            continue
        if day >= cutoff:
            continue
        if not entry.is_dir():
            continue
        shutil.rmtree(entry, ignore_errors=True)
        if not entry.exists():
            removed += 1
    return removed
