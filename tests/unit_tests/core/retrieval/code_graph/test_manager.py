# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Concurrent single-flight invariants for CodeGraphManager / CodeGraphService."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager, reset_code_graph_manager
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.core.retrieval.code_graph.service import CodeGraphService

pytestmark = pytest.mark.level0
pytest.importorskip("tree_sitter_language_pack")

SAMPLE = '''\
class UserService:
    def create_user(self, name: str) -> str:
        return name
'''


def _write_repo(root: Path) -> Path:
    src = root / "src"
    src.mkdir(parents=True)
    (src / "user.py").write_text(SAMPLE, encoding="utf-8")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _write_repo(tmp_path / "repo")


@pytest.fixture(autouse=True)
def _reset_manager() -> None:
    reset_code_graph_manager()
    yield
    reset_code_graph_manager()


def _count_builds(monkeypatch: pytest.MonkeyPatch, delay: float = 0.05):
    builds = {"n": 0}
    real = build_index

    def counting(repo_root, config):
        builds["n"] += 1
        if delay:
            time.sleep(delay)
        return real(repo_root, config)

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.service.build_index",
        counting,
    )
    return builds


@pytest.mark.asyncio
async def test_twenty_concurrent_search_code_builds_once(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = _count_builds(monkeypatch)
    service = CodeGraphService(repo, CodeGraphConfig(cache_dir=None, max_files=100))
    results = await asyncio.gather(*[service.search_code("UserService") for _ in range(20)])
    assert builds["n"] == 1
    assert all(item["status"] == "COMPLETE" for item in results)


@pytest.mark.asyncio
async def test_manager_ensure_ready_single_flight(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = _count_builds(monkeypatch)
    manager = CodeGraphManager(max_cached_repos=4)
    config = CodeGraphConfig(cache_dir=None, max_files=100)
    services = await asyncio.gather(
        *[manager.get_service(repo, config, ensure=True) for _ in range(20)]
    )
    assert builds["n"] == 1
    assert len({id(item) for item in services}) == 1


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_build(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = build_index
    entered = threading.Event()
    gate = threading.Event()
    builds = {"n": 0}

    def blocked(repo_root, config):
        builds["n"] += 1
        entered.set()
        assert gate.wait(timeout=5)
        return real(repo_root, config)

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.service.build_index",
        blocked,
    )
    service = CodeGraphService(repo, CodeGraphConfig(cache_dir=None, max_files=100))
    waiter = asyncio.create_task(service.search_code("UserService"))
    await asyncio.to_thread(entered.wait, 5)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    gate.set()
    result = await service.search_code("UserService")
    assert result["status"] == "COMPLETE"
    assert builds["n"] == 1


@pytest.mark.asyncio
async def test_failed_flight_cleans_up_and_retries(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = build_index
    attempts = {"n": 0}

    def flaky(repo_root, config):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("index boom")
        return real(repo_root, config)

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.service.build_index",
        flaky,
    )
    service = CodeGraphService(repo, CodeGraphConfig(cache_dir=None, max_files=100))
    with pytest.raises(Exception, match="index boom"):
        await service.ensure_ready()
    index = await service.ensure_ready()
    assert index is not None
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_different_repos_build_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = build_index
    overlap = {"current": 0, "max": 0}
    lock = threading.Lock()

    def overlapping(repo_root, config):
        with lock:
            overlap["current"] += 1
            overlap["max"] = max(overlap["max"], overlap["current"])
        time.sleep(0.08)
        try:
            return real(repo_root, config)
        finally:
            with lock:
                overlap["current"] -= 1

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.service.build_index",
        overlapping,
    )
    repo_a = _write_repo(tmp_path / "a")
    repo_b = _write_repo(tmp_path / "b")
    cfg = CodeGraphConfig(cache_dir=None, max_files=100)
    await asyncio.gather(
        CodeGraphService(repo_a, cfg).ensure_ready(),
        CodeGraphService(repo_b, cfg).ensure_ready(),
    )
    assert overlap["max"] >= 2


@pytest.mark.asyncio
async def test_changed_snapshot_does_not_reuse_old_index(repo: Path) -> None:
    service = CodeGraphService(repo, CodeGraphConfig(cache_dir=None, max_files=100))
    first = await service.ensure_ready()
    (repo / "src" / "user.py").write_text(SAMPLE + "\n# changed\n", encoding="utf-8")
    second = await service.ensure_ready()
    assert first.snapshot != second.snapshot


@pytest.mark.asyncio
async def test_lru_skips_in_flight_and_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = build_index
    entered = threading.Event()
    gate = threading.Event()

    def blocked(repo_root, config):
        entered.set()
        assert gate.wait(timeout=5)
        return real(repo_root, config)

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.service.build_index",
        blocked,
    )
    manager = CodeGraphManager(max_cached_repos=1)
    cfg = CodeGraphConfig(cache_dir=None, max_files=100)
    repo_a = _write_repo(tmp_path / "a")
    repo_b = _write_repo(tmp_path / "b")
    task_a = asyncio.create_task(manager.get_service(repo_a, cfg, ensure=True))
    await asyncio.to_thread(entered.wait, 5)
    pinned = any(str(repo_a.resolve()) in key for key in manager._pins)
    cached = any(str(repo_a.resolve()) in key for key in manager._services)
    assert pinned and cached
    task_b = asyncio.create_task(manager.get_service(repo_b, cfg, ensure=True))
    await asyncio.sleep(0.01)
    still_cached = any(str(repo_a.resolve()) in key for key in manager._services)
    assert still_cached
    gate.set()
    service_a, service_b = await asyncio.gather(task_a, task_b)
    assert service_a is not service_b
