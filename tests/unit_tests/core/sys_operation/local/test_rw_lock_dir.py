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


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX-only: no uid to scope by")
def test_lock_dir_is_scoped_per_uid(monkeypatch):
    """Two users on one machine must not be sent to the same directory."""
    monkeypatch.setattr(os, "geteuid", lambda: 4242)
    first = ReadWriteLockManager._lock_dir_name()
    monkeypatch.setattr(os, "geteuid", lambda: 4243)
    second = ReadWriteLockManager._lock_dir_name()

    assert first != second
    assert "/" not in first and "/" not in second


def test_lock_dir_is_stable_for_one_user(fake_tmp):
    """One user's processes must all derive the same directory, and reuse it.

    The databases exist to coordinate across processes, so a per-process
    directory would hand every process a private lock and silently defeat the
    mutual exclusion. Guards against "fix it with mkdtemp".
    """
    first = ReadWriteLockManager.ensure_lock_dir()
    ReadWriteLockManager._lock_dir = None
    second = ReadWriteLockManager.ensure_lock_dir()

    assert first == second
    assert first.parent == fake_tmp


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_a_symlink_planted_at_the_lock_dir_name_is_refused(fake_tmp, tmp_path):
    """The name is predictable; another account can create it first as a symlink.

    Following it would put the lock databases in a directory of that account's
    choosing, and the 0700 chmod would re-mode that directory.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    elsewhere.chmod(0o755)
    (fake_tmp / ReadWriteLockManager._lock_dir_name()).symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(PermissionError, match="owned by the current user"):
        ReadWriteLockManager.ensure_lock_dir()

    assert elsewhere.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX-only: no uid to check ownership against")
def test_a_lock_dir_owned_by_another_uid_is_refused(fake_tmp, monkeypatch):
    """A directory at our name that another uid created is not ours to use."""
    other_uid = os.geteuid() + 1
    monkeypatch.setattr(os, "geteuid", lambda: other_uid)
    planted = fake_tmp / ReadWriteLockManager._lock_dir_name()
    planted.mkdir()  # owned by the effective uid, i.e. not by ``other_uid``
    planted.chmod(0o755)

    with pytest.raises(PermissionError, match=str(planted)):
        ReadWriteLockManager.ensure_lock_dir()

    assert planted.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX-only: no uid to check ownership against")
def test_lock_dir_is_checked_against_the_effective_uid(fake_tmp, monkeypatch):
    """The effective uid owns what mkdir creates, so it is the uid to check.

    A process whose real uid differs (a setuid launcher, a daemon that drops
    privileges with seteuid) must still accept the directory it just created.
    """
    monkeypatch.setattr(os, "getuid", lambda: os.geteuid() + 1)

    lock_dir = ReadWriteLockManager.ensure_lock_dir()

    assert lock_dir.name.endswith(f"-{os.geteuid()}")
    assert lock_dir.is_dir()


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX-only: mode bits")
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


def test_lock_dir_without_geteuid_is_scoped_by_a_sanitized_login_name(fake_tmp, monkeypatch):
    """Windows has no geteuid, so the login name has to do the scoping.

    It may carry a domain separator and spaces, none of which may end up
    creating a nested path or a second directory component.
    """
    monkeypatch.delattr(os, "geteuid", raising=False)

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


@pytest.mark.parametrize("failure", [ImportError, KeyError, OSError])
def test_lock_dir_survives_a_login_name_that_cannot_be_read(fake_tmp, monkeypatch, failure):
    """No discoverable account name must not take file locking down with it.

    ``getpass.getuser()`` reads four environment variables and then the
    password database. A stripped container or a service context can satisfy
    neither, and it raises there instead of returning an empty string. Every
    ``fs().read_file()`` derives this directory, so an exception would stop
    all file access. One shared name is what this module used before the
    per-user suffix, so the degraded case is no worse than the starting point.
    """
    monkeypatch.delattr(os, "geteuid", raising=False)

    def _raise():
        raise failure("no account name is discoverable")

    monkeypatch.setattr(getpass, "getuser", _raise)
    ReadWriteLockManager._lock_dir = None

    lock_dir = ReadWriteLockManager.ensure_lock_dir()

    assert lock_dir.parent == fake_tmp
    assert lock_dir.is_dir()


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX-only: mode bits, and no uid to scope by")
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
