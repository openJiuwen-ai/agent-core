# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Recovery from SQLite connection closure during lock acquisition."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from openjiuwen.core.sys_operation.local._async_read_write_lock import (
    _ManagedReadWriteLock,
)


def test_configure_and_begin_recovers_from_closed_database(tmp_path: Path) -> None:
    """验证 _configure_and_begin 在 SQLite 连接被关闭后自动重建并重试。"""
    lock_file = tmp_path / "test_lock.db"
    lock = _ManagedReadWriteLock(str(lock_file))
    lock._con.close()

    with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
        lock._con.execute("SELECT 1")

    import time

    lock._configure_and_begin(
        "read",
        1.0,
        blocking=False,
        start_time=time.perf_counter(),
    )
    lock._con.execute("ROLLBACK;").close()
    lock._con.close()


def test_configure_and_begin_reraises_non_closed_database_errors(tmp_path: Path) -> None:
    """验证非 closed database 的 ProgrammingError 仍然抛出。"""
    lock_file = tmp_path / "test_lock2.db"
    lock = _ManagedReadWriteLock(str(lock_file))

    original = _ManagedReadWriteLock.__mro__[1]._configure_and_begin

    def _raise_unrelated(self, *args, **kwargs):
        raise sqlite3.ProgrammingError("some other error")

    with patch.object(_ManagedReadWriteLock.__mro__[1], "_configure_and_begin", _raise_unrelated):
        import time

        with pytest.raises(sqlite3.ProgrammingError, match="some other error"):
            lock._configure_and_begin(
                "read",
                1.0,
                blocking=False,
                start_time=time.perf_counter(),
            )
    lock._con.close()
