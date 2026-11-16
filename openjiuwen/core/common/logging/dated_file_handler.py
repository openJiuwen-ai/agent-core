# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Dated-daily log file handler.

Layout mirrors the desktop logger (<dataRoot>/logs/YYYY-MM-DD/system.log):

    <base_dir>/<YYYY-MM-DD>/<filename>

- One directory per local calendar day; the date is resolved on every emit,
  so a long-lived process switches files at midnight.
- Within a day, a size cap rotates the active file aside to a numbered
  "<stem>.<NNN>.log" backup; the newest ``backup_count`` backups per day
  are kept (default 10) and older ones are pruned, so the per-day volume
  is bounded at (backup_count + 1) x max_bytes per file.
- Expired date directories are removed by cleanup_expired_dated_log_dirs
  (retention defaults to 90 days, same as the desktop audit logs) — this
  date-dir cleanup bounds the total log volume across days.

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

# 日内上限 10MB 对齐桌面端 logger.ts 的 MAX_BYTES 与 jiuwenswarm 侧
# utils._LOG_DATED_MAX_BYTES（2MB 时滚动过于频繁，排障窗口太短）。
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 10
DEFAULT_RETENTION_DAYS = 90

_DATED_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_backup_name(name: str, stem: str) -> bool:
    """Match ``<stem>.<NNN>.log`` numbered backup names."""
    return re.fullmatch(re.escape(stem) + r"\.\d+\.log", name) is not None


def _backup_number(path: Path, stem: str) -> int:
    """Return the sequence number of a ``<stem>.<NNN>.log`` backup."""
    return int(path.name[len(stem) + 1: -len(".log")])


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
    the day rotates the active file aside to a numbered
    ``<stem>.<NNN>.log`` backup; the newest ``backup_count`` backups per
    day are kept, older ones pruned.

    ``filename`` may contain subdirectories (e.g. ``run/jiuwen.log``).
    ``base_dir`` is exposed for idempotency checks by callers.

    Args:
        base_dir: Directory under which per-day subdirectories are created.
        filename: Log file path relative to the date directory.
        max_bytes: Per-day size cap; ``0`` disables rotation.
        backup_count: Numbered backups kept per day; ``<0`` keeps all.
        encoding: File encoding.
    """

    def __init__(
        self,
        base_dir: str | os.PathLike,
        filename: str,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
        encoding: str = "utf-8",
    ) -> None:
        self.base_dir = os.fspath(base_dir)
        self._relative_filename = filename
        self.max_bytes = max_bytes
        self.backup_count = backup_count
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
        """Roll the active file aside once it exceeds ``max_bytes``.

        The current content is copied to the next numbered backup and
        the active file is truncated in place (copy+truncate keeps the
        open stream valid on Windows, where rename of an open file fails).
        Afterwards the oldest backups beyond ``backup_count`` are pruned,
        bounding the per-day volume; the date-dir retention cleanup bounds
        the total volume across days.
        """
        if not self.stream or self.max_bytes <= 0:
            return
        try:
            message = "%s\n" % self.format(record)
            self.stream.seek(0, os.SEEK_END)
            if self.stream.tell() + len(message.encode(self.encoding or "utf-8")) < self.max_bytes:
                return
            shutil.copy2(self.baseFilename, self._next_backup_path())
            self.stream.seek(0)
            self.stream.truncate(0)
            self._prune_backups()
        except OSError:
            pass

    def _next_backup_path(self) -> Path:
        """Return ``<stem>.<NNN>.log`` with NNN = max existing + 1.

        Numbers are monotonically increasing within the day directory and
        zero-padded to three digits, so name sort order is chronological.
        A fresh number is minted per rotation and existing backups are
        never renamed — two processes sharing one directory (desktop mode
        runs gateway and agent side by side) can interleave without
        clobbering each other's backups; a same-number race just bumps to
        the next free number. The backup's content time is its mtime
        (``copy2`` preserves it).
        """
        active = Path(self.baseFilename)
        try:
            highest = max(
                (
                    _backup_number(p, active.stem)
                    for p in active.parent.iterdir()
                    if _is_backup_name(p.name, active.stem)
                ),
                default=0,
            )
        except OSError:
            highest = 0
        number = highest + 1
        backup = active.with_name(f"{active.stem}.{number:03d}.log")
        while backup.exists():
            number += 1
            backup = active.with_name(f"{active.stem}.{number:03d}.log")
        return backup

    def _prune_backups(self) -> None:
        """Delete the oldest numbered backups beyond ``backup_count``.

        Zero-padded numbers make name sort order chronological.
        ``backup_count < 0`` disables pruning (keep all).
        """
        if self.backup_count < 0:
            return
        active = Path(self.baseFilename)
        try:
            backups = sorted(
                (
                    p
                    for p in active.parent.iterdir()
                    if _is_backup_name(p.name, active.stem)
                ),
                key=lambda p: _backup_number(p, active.stem),
            )
            for stale in backups[: max(0, len(backups) - self.backup_count)]:
                stale.unlink(missing_ok=True)
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
