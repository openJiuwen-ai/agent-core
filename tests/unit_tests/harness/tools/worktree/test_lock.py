# coding: utf-8

"""Tests for repository-scoped worktree lifecycle locking."""

import asyncio

import pytest

from openjiuwen.harness.tools.worktree.lock import (
    WorktreeLockTimeout,
    clear_lock_buckets,
    repo_lock,
)


@pytest.fixture(autouse=True)
def _clear_buckets():
    clear_lock_buckets()
    yield
    clear_lock_buckets()


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
