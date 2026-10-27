# coding: utf-8

"""Tests for repository-scoped worktree lifecycle locking."""

import asyncio
import os

import pytest
from filelock import FileLock

from openjiuwen.harness.tools.worktree.lock import (
    WorktreeLockTimeout,
    _lock_file_path,
    clear_lock_buckets,
    repo_lock,
)


@pytest.fixture(autouse=True)
def _clear_buckets():
    clear_lock_buckets()
    yield
    clear_lock_buckets()


@pytest.mark.level0
def test_lock_timeout_matches_tool_runtime_error_contract():
    assert isinstance(WorktreeLockTimeout("timeout"), RuntimeError)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_repo_lock_serializes_same_repository(tmp_path):
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def first() -> None:
        async with repo_lock(str(tmp_path)):
            order.append("first-enter")
            first_entered.set()
            await release_first.wait()
            order.append("first-exit")

    async def second() -> None:
        await first_entered.wait()
        async with repo_lock(str(tmp_path)):
            order.append("second-enter")

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_entered.wait()
    await asyncio.sleep(0)
    assert order == ["first-enter"]

    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert order == ["first-enter", "first-exit", "second-enter"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_repo_lock_times_out_while_same_repository_is_held(tmp_path):
    async with repo_lock(str(tmp_path)):
        with pytest.raises(WorktreeLockTimeout, match="in-process"):
            async with repo_lock(str(tmp_path), timeout=0.01):
                pytest.fail("contended lock must not be acquired")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_disabled_repo_lock_is_noop(tmp_path):
    async with repo_lock(str(tmp_path), enabled=False):
        pass

    assert not (tmp_path / ".openjiuwen-worktree.lock").exists()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_repo_lock_times_out_on_cross_process_file_lock(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    lock_path = _lock_file_path(str(tmp_path))
    assert lock_path == os.path.join(str(git_dir), "openjiuwen-worktree.lock")

    external_lock = FileLock(lock_path)
    external_lock.acquire()
    try:
        with pytest.raises(WorktreeLockTimeout, match="cross-process"):
            async with repo_lock(str(tmp_path), timeout=0.01):
                pytest.fail("externally held file lock must not be acquired")
    finally:
        external_lock.release()
