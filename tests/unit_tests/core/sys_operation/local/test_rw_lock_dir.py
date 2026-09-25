# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the placement and permissions of the cross-process lock directory.

The directory holds the SQLite databases that coordinate file locking between
processes, so it has two requirements that pull against each other: every
process of one user must derive the *same* path, and a directory another user
created must never be the path this user is sent to.
"""

import getpass
import os
import pathlib
import sqlite3
import tempfile

import pytest

from openjiuwen.core.sys_operation.local._rw_lock_manager import ReadWriteLockManager


@pytest.fixture
def fake_tmp(tmp_path, monkeypatch):
    """Redirect the lock directory into a private temp root, then restore it."""
    root = tmp_path / "tmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(root))
    monkeypatch.setattr(ReadWriteLockManager, "_lock_dir", None)
    yield root
    ReadWriteLockManager._lock_dir = None


def _derive(uid: int | None = None, monkeypatch=None) -> pathlib.Path:
    """Force a fresh derivation of the lock directory."""
    ReadWriteLockManager._lock_dir = None
    if uid is not None:
        monkeypatch.setattr(os, "getuid", lambda: uid, raising=False)
    return ReadWriteLockManager.ensure_lock_dir()


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX-only: no uid to scope by")
def test_lock_dir_is_scoped_per_uid(fake_tmp, monkeypatch):
    """Two users on one machine must not be sent to the same directory."""
    first = _derive(4242, monkeypatch)
    second = _derive(4243, monkeypatch)

    assert first != second
    assert first.parent == second.parent == fake_tmp


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX-only: no uid to scope by")
def test_lock_dir_is_stable_for_one_uid(fake_tmp, monkeypatch):
    """One user's processes must all derive the same directory.

    The databases exist to coordinate across processes, so a per-process
    directory would hand every process a private lock and silently defeat the
    mutual exclusion. Guards against "fix it with mkdtemp".
    """
    first = _derive(4242, monkeypatch)
    second = _derive(4242, monkeypatch)

    assert first == second


def test_lock_dir_is_owner_only(fake_tmp):
    """The databases sit in a world-readable temp root, so restrict the directory."""
    lock_dir = ReadWriteLockManager.ensure_lock_dir()

    assert lock_dir.stat().st_mode & 0o777 == 0o700


def test_lock_dir_survives_a_chmod_failure(fake_tmp, monkeypatch):
    """A filesystem that refuses chmod must not take file locking down with it."""
    def refuse(self, mode):
        raise OSError("chmod not supported")

    monkeypatch.setattr(pathlib.Path, "chmod", refuse)

    lock_dir = ReadWriteLockManager.ensure_lock_dir()

    assert lock_dir.is_dir()


def test_lock_dir_without_getuid_is_scoped_by_a_sanitized_login_name(fake_tmp, monkeypatch):
    """Windows has no getuid, so the login name has to do the scoping.

    It may carry a domain separator and spaces, none of which may end up
    creating a nested path or a second directory component.
    """
    monkeypatch.delattr(os, "getuid", raising=False)

    monkeypatch.setattr(getpass, "getuser", lambda: r"DOMAIN\Admin User")
    ReadWriteLockManager._lock_dir = None
    first = ReadWriteLockManager.ensure_lock_dir()

    monkeypatch.setattr(getpass, "getuser", lambda: r"DOMAIN\Other User")
    ReadWriteLockManager._lock_dir = None
    second = ReadWriteLockManager.ensure_lock_dir()

    assert first != second
    assert first.parent == second.parent == fake_tmp
    for lock_dir in (first, second):
        assert not any(char in lock_dir.name for char in "\\/ ")
        assert lock_dir.is_dir()


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX-only: mode bits, and no uid to scope by")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores the mode bits this test relies on",
)
async def test_a_lock_dir_owned_by_another_user_does_not_deny_us(fake_tmp, tmp_path):
    """The production failure: another account owns the shared directory.

    The first user to run creates it and owns it; a later user finds a
    directory it cannot write to, SQLite cannot create its database, and every
    ``fs().read_file()`` fails with "unable to open database file" -- several
    layers away from anything that mentions locking.

    A directory that denies writes stands in for one owned by another uid:
    what breaks the second user is the permission, not the ownership.
    """
    foreign = fake_tmp / "openjiuwen-fs-rwlocks"
    foreign.mkdir()
    foreign.chmod(0o555)
    target = tmp_path / "target.txt"
    target.write_text("payload", encoding="utf-8")

    try:
        async with ReadWriteLockManager.lock_guard(target, "read", timeout=5.0):
            pass
        assert ReadWriteLockManager._lock_dir != foreign
    except sqlite3.OperationalError as exc:  # pragma: no cover - the unfixed path
        pytest.fail(f"lock acquisition was denied by another user's directory: {exc}")
    finally:
        foreign.chmod(0o755)
        await ReadWriteLockManager.stop()
